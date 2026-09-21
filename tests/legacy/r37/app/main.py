"""Zeabur 入口：HTTP 狀態頁 + 背景 paper/live 迴圈。"""
import os, time
os.environ["TZ"] = os.environ.get("APP_TZ", "CST-8")   # 台灣 UTC+8、無夏令時間；POSIX 寫法不需要 tzdata
time.tzset()
import base64, hmac, json, threading, traceback
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from . import backtest, binance as B, config as C, manager, params, presets, preflight, risk, scanner, store, sweep, telegram
from .signals import ENGINES, LONG_ENGINES, ENGINE_TF

ENABLED = set(os.environ.get("ENGINES", "C,F,G").split(","))
TRADE = os.environ.get("TRADE", "0") == "1"
STOP_FAIL_CLOSE = os.environ.get("STOP_FAIL", "close") == "close"   # 停損單掛不上時：close=立刻平倉 keep=只告警
PASSWORD = os.environ.get("DASH_PASSWORD", "")                        # 網頁密碼；沒設定時手動操作全部停用
SCAN_SEC = int(os.environ.get("SCAN_SEC", "1800")); POLL_SEC = int(os.environ.get("POLL_SEC", "60"))

_rc = dict(t=0, n=0, ex=[])

def _say(build, who="*"):
    """發通知是獨立的一步（清單第 8 條 r21）：組字串或送出出錯，不能中斷前面已經做完的動作（例如已經結帳、撤停損），
    也不能被吞掉——照節奏推播一則「通知出錯」。build 是組訊息的函式，讓格式化也在 try 裡。"""
    try: telegram.send(build())
    except Exception as e: manager._step_err(who, "通知", e)

PENDING_TTL = 180          # 送單結果不明的 pending 最多等幾秒；過了交易所還沒有這個部位才放棄

def _row(ex, sym, side):
    """交易所部位表裡「這個幣、這一側」那一列。單向模式看 positionAmt 正負（清單第 7 條 r12）。"""
    return next((p for p in ex if p["symbol"] == sym and B.side_of(p) == side), None)

def _protect(sym, pos):
    """立刻掛停損（認領回來的部位用；不等守衛連 3 輪）。失敗交給守衛，照節奏告警。"""
    try:
        # 走共用送停損處；交易所那一列就是「部位在」的正向證據，直接傳進去（清單第 2 條 r20、r21）
        st, so = manager.place_stop(sym, pos, pos["stop"], known_left=pos["qty"])
        if st != "ok": return False
        pos.update(stop_id=so.get("orderId"), stop_via=so.get("via")); return True
    except Exception as e:
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 認領後掛停損失敗 {e}"); return False

def reconcile(force=False):
    """跟交易所對帳：
    - 自己開的倉不在了 → 標記平倉
    - pending（送單結果不明）→ 交易所有這一側的部位就認領，數量與均價拿交易所的；沒有就等到 PENDING_TTL
    - 數量變少（在 App 手動減碼、或交易所端執行）→ 記一筆部分出場、通知、更新帳上數量
    回傳 (自己還在場的倉數, 交易所全部持倉)。"""
    if not force and time.time() - _rc["t"] < 20: return _rc["n"], _rc["ex"]   # 20 秒內重用，positionRisk 權重高，打太兇會被 418
    st = store.get()
    own, pend = dict(st.get("open", {})), dict(st.get("pending", {}))
    if not C.API_KEY: return 0, []
    ex = B.open_positions()

    now_ms = int(time.time() * 1000)
    for sym, rec in list(pend.items()):
        try:
            if sym in own: pend.pop(sym); continue
            row = _row(ex, sym, rec.get("side"))
            if not row:
                # 全量表找不到 → 逐幣再查（全量表可能回空清單，清單第 2 條 r15）；逐幣也查不到就這輪不動
                try: row = _row(B.position_rows(sym), sym, rec.get("side"))
                except Exception: continue
            base = rec.get("base_qty") or 0.0
            mine = abs(float(row["positionAmt"])) - base if row else 0.0
            if row and mine > 1e-9:
                # 認領數量 = 交易所這一側 − 送單前的基準（清單第 3 條 r14）
                qty, avg = mine, float(row["entryPrice"])
                merged = base > 1e-9
                if merged and rec.get("base_px"):          # 交易所均價是合併過的，反推這張單的成交價（估計值）
                    fill = round((avg * (mine + base) - rec["base_px"] * base) / mine, 10)
                else: fill = avg
                stop = rec["stop"]
                pos = dict(engine=rec.get("engine"), side=rec["side"], time=rec.get("time"), ts=(rec["ts"] if manager.num(rec.get("ts")) is not None else now_ms),   # 值是 None 時 .get 的預設擋不住（r33）
                           bar_t=rec.get("bar_t"), last_t=rec.get("bar_t"), entry=rec["entry"], fill=fill, stop=stop, qty=qty,
                           base_qty=base, r_unit=abs(rec["entry"] - stop), risk_usdt=round(abs(rec["entry"] - stop) * qty, 4),
                           state="初始", adopted=True)
                pos["trade_mark"] = manager.mark_now(sym)             # 起始界線：認領當下成交明細的最後一筆（清單第 8 條 r32）
                ok = _protect(sym, pos)
                own[sym] = pos; pend.pop(sym)
                store.push("errors", f"{time.strftime('%m-%d %H:%M')} 認領 {sym}（引擎{pos['engine']}）數量 {qty:g} 均價 {fill:g}")
                _say(lambda: f"♻️ 認領 {sym} 引擎{pos['engine']}：送單結果不明或記帳中斷，交易所上確實有部位，"
                              f"已照交易所數量 {qty:g}、均價 {fill:g} 記帳"
                              + (f"（這一側送單前已有 {base:g}，交易所均價 {avg:g} 是合併過的，{fill:g} 是反推的估計值，"
                                 f"不是這張單的實際成交價）" if merged else "")
                              + ("，停損已掛上" if ok else "，⚠️ 停損還沒掛上（守衛會重試）"), sym)
            elif manager.num(rec.get("ts")) is None:                    # 時間戳不是數字：無法判斷何時送出，不能讓它每輪出錯卡著
                pend.pop(sym)
                _say(lambda: f"ℹ️ {sym} 引擎{rec.get('engine')} 的 pending 沒有有效的時間戳，交易所上也沒有這個部位，判定逾時未成交", sym)
            elif now_ms - rec["ts"] > PENDING_TTL * 1000:               # 上面已經逐幣確認過沒有
                pend.pop(sym)
                telegram.send(f"ℹ️ {sym} 引擎{rec.get('engine')} 送單結果不明，{PENDING_TTL} 秒內交易所都沒有這個部位，判定未成交")
            manager._step_ok(sym, "對帳認領")
        except Exception as e:
            manager._step_err(sym, "對帳認領", e)   # 一筆出錯不中斷整個對帳，照節奏推播（清單第 8 條 r21）
            # pending 保留，下一輪再試

    still, changed = {}, False
    for sym, rec in own.items():
        try:
            row = _row(ex, sym, rec.get("side"))
            if not row:
                # 全量表裡找不到：可能真的平了，也可能是全量表異常回空清單（清單第 2 條 r15）→ 逐幣再查一次
                try: row = _row(B.position_rows(sym), sym, rec.get("side"))
                except Exception as e:
                    store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 全量表找不到、逐幣查詢也失敗 {e}，這輪不動")
                    still[sym] = rec; continue
            base = rec.get("base_qty") or 0.0
            left = abs(float(row["positionAmt"])) - base if row else 0.0     # 自己的 = 這一側 − 基準（第 7 條 r15）
            if left > 1e-9:
                if rec.get("qty") and left < rec["qty"] - 1e-9:          # 數量變少：記部分出場（清單第 8 條 r12，已扣基準）
                    cut = rec["qty"] - left
                    # 損益只用實際成交（成交明細、界線之後）；不用標記價、不退回進場價（清單第 8 條 r30）
                    rec = dict(rec); part = manager.adopt_partial(sym, rec, cut); rec["qty"] = left
                    changed = True
                    _say(lambda: f"✂️ {sym} 引擎{rec.get('engine')} 交易所上的數量減少 {left + cut:g} → {left:g}"
                                 f"（不是本程式送的單，可能是在 App 手動減碼），已記一筆部分出場並更新帳上數量；"
                                 + (f"這段成交 {part['px']:.6g}、損益 {part['pnl']:+.2f} U" if manager.num(part.get("pnl")) is not None
                                    else "這段損益未知（成交明細查不到，或同側有別人的部位分不出來）"), sym)
                still[sym] = rec
            else:
                rec = manager.record_close(sym, rec, "停損單"); changed = True
                _say(lambda: f"🏁 {sym} 引擎{rec.get('engine')} 停損單觸發出場" +
                              (f" @ {rec['exit']:.6g}" if manager.num(rec.get("exit")) else "") +
                              (f"，損益 {rec['pnl']:+.2f} U" if manager.num(rec.get("pnl")) is not None else "，損益未知") +
                              (f"（{rec['r']:+.2f}R）" if rec.get("r") is not None else ""), sym)
            manager._step_ok(sym, "對帳")
        except Exception as e:
            manager._step_err(sym, "對帳", e)   # 一筆出錯不中斷整個對帳，照節奏推播（清單第 8 條 r21）
            still[sym] = rec                                      # 出錯的這筆保守保留在帳上，下一輪再對
    store.update(open=still, pending=pend)
    def _mine_upnl(p):
        """本策略那一列的未實現損益：自己的數量 ×（標記價 − 自己的成交價）。交易所那一列的
        unRealizedProfit 照合併均價算、含同側別人的部位，不能直接當成自己的（清單第 3 條 r18）。"""
        r = still.get(p["symbol"])
        if not r or r.get("side") != B.side_of(p): return None
        try: mark = float(p.get("markPrice") or 0)
        except Exception: return None
        if not mark: return None
        return round(r["qty"] * (mark - (r.get("fill") or r["entry"])) * (1 if r["side"] == "LONG" else -1), 2)
    store.update(exchange=[dict(symbol=p["symbol"], amt=float(p["positionAmt"]), entry=round(float(p["entryPrice"]), 8),
                                upnl=round(float(p.get("unRealizedProfit") or 0), 2), upnl_mine=_mine_upnl(p),
                                base=(still.get(p["symbol"]) or {}).get("base_qty") or None,
                                owner="本策略" if p["symbol"] in still and still[p["symbol"]].get("side") == B.side_of(p) else "其他")
                          for p in ex])
    _rc.update(t=time.time(), n=len(still) + len(pend), ex=ex)
    return len(still) + len(pend), ex

def place(sym, eid, sig, sz, rec):
    """下單。順序：送單前寫 pending → 成交後立刻記帳 → 最後掛停損（清單第 3 條）。
    - 送單結果不明（逾時、5xx）→ 保留 pending 交給對帳；只有交易所明確拒絕才清掉（第 3 條 r12）
    - 成交後任何步驟丟例外都不能改寫「已成交」（第 8 條 r11）"""
    is_long = sig.side == "LONG"
    try: stop_px = float(B.round_price(sym, sig.stop))       # 實際掛出去的停損價（照 tickSize，第 4 條）
    except Exception: stop_px = sig.stop
    now_ms = int(time.time() * 1000)
    # 送單前記下這一側原有的數量與均價（清單第 3 條 r14）：認領與平倉都要扣掉，才不會把別人的部位算成自己的
    base_qty, base_px = 0.0, None
    try:
        rows = [p for p in B.position_rows(sym) if B.side_of(p) == sig.side]
        base_qty = sum(abs(float(p["positionAmt"])) for p in rows)
        base_px = float(rows[0]["entryPrice"]) if rows else None
    except Exception as e:
        rec["skipped"] = f"送單前查不到這個幣的部位（{str(e)[:60]}），無法記基準，這次不下單"
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} {rec['skipped']}")
        return rec
    store.update(pending={**store.get().get("pending", {}),
                          sym: dict(engine=eid, side=sig.side, time=rec["time"], ts=now_ms, bar_t=rec.get("bar_t"),
                                    entry=sig.entry, stop=stop_px, base_qty=base_qty, base_px=base_px)})
    try:
        lev, note = B.set_leverage(sym, sz["leverage"])
        if note: store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} {note}")
        if lev and lev != sz["leverage"]:                      # 槓桿被降 → 保證金會超出單筆上限，先縮量
            cap = sz["usable"] / C.SIZING["max_positions"]
            if sz["notional"] / lev > cap:
                k = cap * lev / sz["notional"]
                sz = dict(sz, qty=sz["qty"] * k, notional=round(sz["notional"] * k, 2),
                          risk_usdt=round(sz["risk_usdt"] * k, 2))
            sz = dict(sz, leverage=lev); rec.update(leverage=lev, qty=sz["qty"], notional=sz["notional"])
        o = B.market_order(sym, "BUY" if is_long else "SELL", sz["qty"])
    except Exception as e:
        if B.definite_reject(e):
            pend = dict(store.get().get("pending", {})); pend.pop(sym, None); store.update(pending=pend)
            rec["skipped"] = f"下單失敗: {e}"
            store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 下單失敗 {e}")
            telegram.send(f"❌ {sym} 引擎{eid} 下單失敗：{e}")
        else:
            rec["skipped"] = f"送單結果不明（{str(e)[:60]}），交給下一輪對帳確認"
            store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 送單結果不明 {e}")
            telegram.send(f"⏳ {sym} 引擎{eid} 送單結果不明（{str(e)[:80]}）— 可能已成交，下一輪對帳會確認並認領")
        return rec

    rec["executed"] = True                              # 從這裡開始，任何例外都不能把這筆改成失敗
    try:
        qty = float(o.get("executedQty") or 0) or float(B.round_qty(sym, sz["qty"]))
        fill = float(o.get("avgPrice") or 0) or None
        rec["fill"] = fill; rec["qty"] = qty
        rec["slip_pct"] = round((fill / sig.entry - 1) * 100 * (-1 if is_long else 1), 3) if fill else None   # 負=比訊號價差（對自己不利）
        own = dict(store.get().get("open", {}))
        own[sym] = dict(engine=eid, side=sig.side, time=rec["time"], ts=now_ms - 5000,
                        bar_t=rec.get("bar_t"), last_t=rec.get("bar_t"), r_unit=abs(sig.entry - stop_px),   # R 用取整後的停損算（第 4 條）
                        entry=sig.entry, fill=fill, stop=stop_px, qty=qty, base_qty=base_qty,
                        risk_usdt=round(abs(sig.entry - stop_px) * qty, 4), state="初始")
        pend = dict(store.get().get("pending", {})); pend.pop(sym, None)
        store.update(open=own, pending=pend)
        m0 = manager.mark_now(sym)                             # 起始界線：開倉成交之後成交明細的最後一筆（清單第 8 條 r32）
        if m0 is not None:
            own = dict(store.get().get("open", {})); own[sym] = dict(own[sym], trade_mark=m0); store.update(open=own)
    except Exception as e:
        # 帳沒記成：pending 還在，下一輪對帳會照交易所數量認領並立刻掛停損
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 成交後記帳失敗 {e}")
        telegram.send(f"⚠️ {sym} 引擎{eid} 已成交，但記帳時出錯（{e}）— 下一輪對帳會認領並掛停損")
        return rec

    err = None                                  # 停損單：失敗重試一次
    for _ in range(2):
        try:
            # 剛成交的回應就是正向證據（成交數量 qty），走共用送停損處（清單第 2 條 r20）
            st, so = manager.place_stop(sym, dict(side=sig.side, qty=qty, base_qty=base_qty), stop_px, known_left=qty); err = None
            own = dict(store.get().get("open", {}))
            if sym in own: own[sym] = dict(own[sym], stop_id=so.get("orderId"), stop_via=so.get("via")); store.update(open=own)
            break
        except Exception as e: err = str(e); time.sleep(1)

    try: store.push("trades", rec)              # 非關鍵步驟：失敗只記錄，不影響已成交
    except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 寫成交紀錄失敗 {e}")

    if not err: return rec
    rec["stop_error"] = err
    store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 停損單失敗 {err}")
    if STOP_FAIL_CLOSE:
        pos = dict(store.get().get("open", {}).get(sym) or {})
        try:
            manager.close_now(sym, pos, "停損掛不上")          # 一樣看結果：確實平掉才記帳
            rec["skipped"] = f"停損掛不上已平倉: {err}"
            telegram.send(f"🛑 {sym} 引擎{eid} 停損單掛不上（{err}），已立即市價平倉")
        except manager.CloseFailed:
            own = dict(store.get().get("open", {}))
            if sym in own: own[sym] = pos; store.update(open=own)     # 保留 want_close，每輪重試
            telegram.send(f"🚨 {sym} 引擎{eid} 停損掛不上、平倉也沒完成（{err}）— 倉位無保護，每輪重試平倉，請同時手動處理")
    else:
        telegram.send(f"🚨 {sym} 引擎{eid} 已進場但停損單掛不上（{err}）— 倉位無保護，請手動處理")
    return rec

def loop():
    try: lf = presets.apply_live()
    except Exception as e:
        lf = {}; store.push("errors", f"{time.strftime('%m-%d %H:%M')} 實盤參數覆蓋載入失敗 {e}")
    store.update(started=time.strftime("%Y-%m-%d %H:%M:%S"), live_overrides=lf)
    if lf: telegram.send(f"⚙️ 套用實盤參數覆蓋 {len(lf)} 項：" + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in lf.items()))
    telegram.send(f"🎯 pump-dump-hunter 啟動 engines={sorted(ENABLED)} trade={TRADE} testnet={C.USE_TESTNET}")
    try: preflight.run_and_report()          # 交易所 API 有沒有又改，開機就知道
    except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} 自檢失敗 {e}")
    state = dict(watch={}, last={}, last_scan=0)
    while True:
        run_tick(state)
        try: time.sleep(POLL_SEC - time.time() % POLL_SEC + 2)   # 對齊整分後 2 秒：K 棒剛收完就判斷
        except Exception: time.sleep(POLL_SEC)

def run_tick(state):
    """背景執行緒的進入點包一層（清單第 8 條 r23）：tick 裡每一步都有各自的 try，但 tick 本身（或 _loop_step）
    出錯時，例外會讓整條執行緒結束——整支機器人停止運作，只在標準錯誤印一行。這裡接住、照節奏推播，下一輪照跑。"""
    _loop_step("整輪", lambda: tick(state))

_loop_errs = {}

def _loop_step(name, fn):
    """背景迴圈的一步。每一步各自 try：前面一步出錯，後面的對帳、守衛照樣跑（清單第 8 條 r18）。
    出錯照節奏推播，不能只進錯誤區（第 14 條）；恢復時通知。"""
    try:
        r = fn()
        n = _loop_errs.pop(name, 0)
        if n >= 1: telegram.send(f"✅ 背景迴圈「{name}」已恢復（先前連續出錯 {n} 次）")
        return r
    except Exception as e:
        n = _loop_errs[name] = _loop_errs.get(name, 0) + 1
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {name}出錯（第 {n} 次）{type(e).__name__}: {e}")
        traceback.print_exc()
        if manager.nag(n):
            telegram.send(f"🐞 背景迴圈「{name}」出錯（第 {n} 次）{type(e).__name__}: {str(e)[:120]}— 其他步驟照常執行")

def _scan(state):
    if time.time() - state["last_scan"] > SCAN_SEC:
        w, o = scanner.scan(verbose=False)
        state["watch"] = {r["symbol"]: r for r in w}
        state["last_scan"] = time.time()
        store.update(watch=w, observe=o, last_scan=time.strftime("%Y-%m-%d %H:%M:%S"))

def tick(state):
    """背景迴圈的一輪：掃描 → 對帳 → 出場管理與守衛 → 訊號與下單，四步各自 try。"""
    _loop_step("掃描", lambda: _scan(state))
    if TRADE and C.API_KEY:
        _loop_step("對帳", lambda: reconcile(force=True))       # 先對帳（被停損掉的移除）
        _loop_step("出場管理", manager.run)                       # 對帳出錯，守衛照樣跑
    _loop_step("訊號", lambda: _signals(state))

def _signals(state):
    """掃描觀察名單、跑引擎、下單。每個幣各自 try（原本就是）。"""
    t0 = time.time()
    for s in list(state["watch"]):
      try:
        k = manager.closed_bars(s, "5m", 150)      # 只用已收盤的棒，跟回測一致
        k1m = manager.closed_bars(s, "1m", 120) if any(ENGINE_TF.get(e) == "1m" for e in ENABLED) else None
        for eid in ENABLED:
            kk = k1m if ENGINE_TF.get(eid) == "1m" else k
            if not kk: continue
            tag = f"{s}:{eid}"
            if state["last"].get(tag) == kk[-1]["t"]: continue     # 同一根不重複判斷
            state["last"][tag] = kk[-1]["t"]
            if manager.cooling(s, eid): continue           # 同引擎出場後冷卻（回測 cooldown_bars）
            sig = ENGINES[eid](kk, len(kk) - 1)
            if not sig: continue
            sz = risk.size(sig)
            if not sz: continue                      # 止損距離超過上限，略過
            rec = dict(time=time.strftime("%m-%d %H:%M"), symbol=s, **sig.__dict__, **sz, executed=False, bar_t=kk[-1]["t"])
            if TRADE and C.API_KEY:
                n_own, ex = reconcile(force=True)   # 要下單了，用最新的
                mine = store.get().get("open", {})
                if s in mine:
                    rec["skipped"] = f"本策略已持有此幣（引擎{mine[s].get('engine')}）"
                elif s in store.get().get("pending", {}):
                    rec["skipped"] = "上一張單結果還沒確認（等對帳）"
                elif n_own >= C.SIZING["max_positions"]:
                    rec["skipped"] = f"本策略持倉已 {n_own} 筆"
                elif any(p["symbol"] == s for p in ex):
                    rec["skipped"] = "該幣帳號內已有倉（其他專案）"
                if rec.get("skipped"):
                    store.push("signals", rec); telegram.send(f"⏸ 略過 {s} 引擎{eid}：{rec['skipped']}"); break
                rec = place(s, eid, sig, sz, rec)
            store.push("signals", rec)
            telegram.send(f"{'✅下單' if rec['executed'] else '👀訊號'} {s} 引擎{eid} {'多' if sig.side == 'LONG' else '空'} @{sig.entry:.5g} "
                          f"止損{sig.stop:.5g}({sz['stop_pct']}%) {sz['leverage']}x {sz['notional']}U" + (f" 成交{rec['fill']:.5g} 滑價{rec['slip_pct']}%" if rec.get("fill") else "") + f"\n{sig.reason}")
            break
      except Exception as e:
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {s} {e}"); traceback.print_exc()
    took = round(time.time() - t0, 1)
    store.update(loop=dict(took=took, symbols=len(state["watch"]), at=time.strftime("%H:%M:%S")))
    if took > POLL_SEC * 0.7:
        msg = f"⚠️ 引擎迴圈 {took}s / {len(state["watch"])} 檔，接近輪詢間隔 {POLL_SEC}s，可能漏 K 線"
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {msg}")
        if took > POLL_SEC: telegram.send(msg)

def _refresh():
    """手動動作後立刻重抓帳號持倉，畫面不用等下一輪迴圈。"""
    try: time.sleep(0.5); reconcile(force=True)
    except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} 對帳更新失敗 {e}")

def manage(act, sym, eid="?", stop=None):
    """手動處理帳號裡的部位。
    - 帳上有紀錄：只認交易所上「同一側」那一列；找不到就不動作（可能已被停損，交給對帳）
    - 帳上沒紀錄（孤兒倉）：同幣只有一列才處理；雙向模式兩側都有時分不出哪一側是孤兒，不自己挑（清單第 7 條）"""
    own = dict(store.get().get("open", {}))
    mine = own.get(sym)
    cands = [p for p in B.open_positions() if p["symbol"] == sym]
    if mine:
        pos = next((p for p in cands if B.side_of(p) == mine.get("side")), None)
        base = mine.get("base_qty") or 0.0
        if not pos or abs(float(pos["positionAmt"])) - base <= 1e-9:
            return dict(error=f"{sym} 帳上是{'多' if mine.get('side') == 'LONG' else '空'}單，但交易所這一側"
                              + (f"扣掉送單前就有的 {base:g} 後" if base > 1e-9 else "")
                              + "已經沒有自己的部位（可能剛被停損），不送單，交給對帳處理")
    else:
        if not cands: return dict(error=f"{sym} 帳號內沒有持倉")
        if len(cands) > 1: return dict(error=f"{sym} 同幣兩側都有部位，無法判斷哪一側是孤兒倉，請到交易所處理")
        pos = cands[0]
    side = B.side_of(pos); qty = abs(float(pos["positionAmt"])); entry = float(pos["entryPrice"])
    if act == "close":
        rec = dict(mine) if mine else dict(engine=eid, side=side, entry=entry, fill=entry)
        if not mine: rec["qty"] = qty                    # 孤兒倉：整列都是要處理的
        # 帳上有紀錄時數量用帳上的；close_now 會再跟「交易所這一側 − 基準」取小的
        try: r = manager.close_now(sym, rec, "手動")     # 看結果：確實平掉才記帳、撤停損（清單第 8 條 r12）
        except manager.CloseFailed as e:
            if mine:
                own = dict(store.get().get("open", {})); own[sym] = rec; store.update(open=own)   # 保留部位與 want_close
            return dict(error=f"{sym} 平倉沒有完成（{e}），部位與停損都保留" + ("，每輪會自動重試" if mine else ""))
        _refresh()
        return dict(ok=True, msg=f"{sym} 已平倉" + (f" @ {r['exit']:.6g}，損益 {r['pnl']:+.2f} U" if r.get("exit") and r.get("pnl") is not None else ""))
    if act == "adopt":
        if mine: return dict(error=f"{sym} 已經在帳上，不需要認領")
        if not stop: return dict(error="請填停損價")
        stop = float(stop); is_long = side == "LONG"
        if (is_long and stop >= entry) or (not is_long and stop <= entry):
            return dict(error=f"停損價方向不對（{'多' if is_long else '空'}單進場 {entry}）")
        # 交易所那一列是正向證據；走共用送停損處（清單第 2 條 r20）。via 一起記，撤單才知道打哪個端點
        st, so = manager.place_stop(sym, dict(side=side, qty=qty, base_qty=0), stop, known_left=qty)
        own = dict(store.get().get("open", {}))
        own[sym] = dict(engine=eid, side=side, time=time.strftime("%m-%d %H:%M"),
                        ts=int(time.time() * 1000), entry=entry, fill=entry, stop=stop, qty=qty, r_unit=abs(entry - stop),
                        risk_usdt=round(abs(entry - stop) * qty, 2), adopted=True, stop_id=so.get("orderId"), stop_via=so.get("via"), state="初始",
                        trade_mark=manager.mark_now(sym))   # 起始界線（r32）
        store.update(open=own); _refresh()
        telegram.send(f"♻️ 手動認領 {sym} 引擎{eid}，已補掛停損 {stop}")
        return dict(ok=True, msg=f"{sym} 已認領並補掛停損 {stop}")
    return dict(error="未知動作")

def trade_action(act, sym, eid="?", stop=None):
    """網頁觸發的交易操作入口（清單第 8 條 r24）。請求處理本身在另一條執行緒，例外穿出去時網頁只看到錯誤、
    沒有推播，使用者要平倉的意圖也沒留下。這裡接住：回錯誤給網頁、照節奏推播；平倉在結帳前出錯就記待平倉，
    之後每輪重試（manage 內容不動，只在外面包一層）。"""
    try:
        r = manage(act, sym, eid, stop)
        manager._step_ok(sym, f"手動{act}")
        return r
    except Exception as e:
        manager._step_err(sym, f"手動{act}", e)
        note = ""
        if act == "close":
            try:
                own = dict(store.get().get("open", {}))
                if sym in own:                               # 還沒結帳（結帳會把它移出帳）→ 記待平倉
                    own[sym] = dict(own[sym], want_close=own[sym].get("want_close") or "手動")
                    store.update(open=own); note = "，已記為待平倉、每輪自動重試"
            except Exception as e2: manager._step_err(sym, "記待平倉", e2)
        return dict(error=f"{sym} 手動{act}出錯：{type(e).__name__}: {e}{note}")

def norm_symbol(s):
    """AIN / ain / AINUSDT / ain/usdt 都變 AINUSDT；全是 U 本位。"""
    s = (s or "").strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    return s if s.endswith("USDT") else s + "USDT"

PAGE = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")


WRITE = ("/api/pos", "/api/signals/clear", "/api/sweep/start", "/api/sweep/clear")
_fail = dict(n=0, t=0)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _authed(self):
        if not PASSWORD: return True
        h = self.headers.get("Authorization", "")
        try: pw = base64.b64decode(h[6:]).decode().split(":", 1)[1] if h.startswith("Basic ") else ""
        except Exception: pw = ""
        return hmac.compare_digest(pw.encode(), PASSWORD.encode())
    def _is_write(self):
        p = self.path
        return p.startswith(WRITE) or (p.startswith("/api/presets") and "act=" in p)
    def _deny(self, code, msg, basic=False):
        self.send_response(code)
        if basic: self.send_header("WWW-Authenticate", 'Basic realm="pump-dump-hunter", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers()
        self.wfile.write(json.dumps(dict(error=msg), ensure_ascii=False).encode())
    def do_GET(self):
        if self.path != "/health" and not self._authed():
            if time.time() - _fail["t"] > 600: _fail.update(n=0)
            _fail["n"] += 1; _fail["t"] = time.time()
            time.sleep(min(10, _fail["n"]))                       # 猜密碼越猜越慢
            return self._deny(401, "需要密碼", basic=True)
        if self._is_write():
            if not PASSWORD: return self._deny(200, "尚未設定 DASH_PASSWORD，手動操作已停用")
            if self.headers.get("X-PDH") != "1": return self._deny(403, "拒絕：非本頁發出的操作")   # 擋外站連結/圖片觸發
        return self._do_get()
    def _do_get(self):
        try: body, ct = self._route()
        except Exception as e:
            traceback.print_exc()
            manager._step_err("*", "網頁請求", e)             # 只印 traceback 的話，錯誤區與推播都看不到（清單用法第 5 點 r29）
            body, ct = json.dumps(dict(error=f"{type(e).__name__}: {e}"), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)
    def _route(self):
        if self.path == "/api/state":
            s = store.get()
            s["live_overrides"] = presets.live(); s["auth_on"] = bool(PASSWORD)
            try: s["live_preset"] = presets.all().get("live_name") or ("自訂" if presets.all().get("live") else None)
            except Exception: pass
            try: eq, bal = risk.equity_now(); s["equity"] = dict(tier=eq, balance=bal, error=risk._bal.get("error"), **C.SIZING)
            except Exception as e: s["equity"] = dict(error=str(e))
            body, ct = json.dumps(s, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/backtest"):
            q = dict(p.split("=") for p in self.path.split("?")[-1].split("&") if "=" in p) if "?" in self.path else {}
            try: res = backtest.run(norm_symbol(q.get("s", "AIN")), min(int(q.get("d", "3")), 30))
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/signals/clear"):
            store.update(signals=[], closed=[]); body, ct = b'{"ok":true}', "application/json; charset=utf-8"
        elif self.path.startswith("/api/presets"):
            import urllib.parse
            q = urllib.parse.parse_qs(self.path.split("?")[-1]) if "?" in self.path else {}
            act = q.get("act", [""])[0]; name = q.get("name", [""])[0]
            try: form = json.loads(q.get("form", ["{}"])[0] or "{}")
            except Exception: form = {}
            if act == "save" and name: presets.save_preset(name, form)
            elif act == "del" and name: presets.delete_preset(name)
            elif act == "live":
                presets.set_live(form); presets.apply_live(); store.update(live_overrides=form)
                telegram.send(f"⚙️ 實盤參數覆蓋更新：{len(form)} 項" if form else "⚙️ 實盤參數覆蓋已清空，回到預設")
            body = json.dumps(dict(presets=presets.all_presets(), live=presets.live()), ensure_ascii=False).encode()
            ct = "application/json; charset=utf-8"
        elif self.path.startswith("/api/pos"):
            import urllib.parse
            q = urllib.parse.parse_qs(self.path.split("?")[-1]) if "?" in self.path else {}
            try: res = trade_action(q.get("act", [""])[0], norm_symbol(q.get("s", [""])[0]),
                                    q.get("e", ["?"])[0], q.get("stop", [""])[0] or None)
            except Exception as e: res = dict(error=str(e))      # 參數解析本身出錯（trade_action 自己不會往外拋）
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/preflight"):
            body, ct = json.dumps(preflight.check_throttled(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/params"):
            body, ct = json.dumps(params.schema(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/sweep/clear"):
            sweep.clear(); body, ct = b'{"ok":true}', "application/json; charset=utf-8"
        elif self.path.startswith("/api/sweep/start"):
            import urllib.parse
            q = urllib.parse.parse_qs(self.path.split("?")[-1]) if "?" in self.path else {}
            d = int(q.get("d", ["30"])[0]); ov = None; err = None
            try:
                raw = json.loads(q["o"][0]) if q.get("o") and q["o"][0].strip() else None
                ov = params.to_overrides(raw) if raw and not any(x in raw for x in ("ENGINE_A", "RISK", "EXIT")) else raw
            except Exception as e: err = f"參數格式錯誤: {e}"
            ok = False if err else sweep.start(min(d, 90), ov, q.get("l", [""])[0], q.get("m", ["pump"])[0])
            body, ct = json.dumps(dict(started=ok, error=err), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/sweep"):
            body, ct = json.dumps(sweep.state(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/diag"):
            q = dict(p.split("=") for p in self.path.split("?")[-1].split("&") if "=" in p) if "?" in self.path else {}
            try: res = backtest.diag_a(norm_symbol(q.get("s", "AIN")), min(int(q.get("d", "3")), 30))
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/why"):
            sym = norm_symbol(self.path.split("s=")[-1]) if "s=" in self.path else ""
            try: res = scanner.why(sym) if sym else dict(usage="/api/why?s=AINUSDT")
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path == "/health": body, ct = b"ok", "text/plain"
        else: body, ct = PAGE.encode(), "text/html; charset=utf-8"
        return body, ct

def run():
    try:
        _p = presets.boot()
        if _p.get("live"): print("套用實盤參數覆蓋:", _p.get("live_name") or "自訂", _p["live"])
    except Exception as e:
        print("preset boot:", e); store.push("errors", f"{time.strftime('%m-%d %H:%M')} 開機套用參數失敗 {e}")
    if store.LOAD_ERROR: telegram.send(f"🐞 {store.LOAD_ERROR}")   # 狀態檔讀取失敗：開機就講（r36）
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()

if __name__ == "__main__":
    run()
