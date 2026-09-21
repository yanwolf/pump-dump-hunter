"""實盤出場管理：逐根收盤 K 棒重現 backtest.simulate 的出場規則，讓實盤跟回測是同一套策略。

分工：
- 停損觸發 → 交易所上的停損單（不靠輪詢，插針也能即時出場）
- 1R 減碼一半 / 保本 / 追蹤停損 / 時間出場 / 出場後冷卻 → 這裡，每輪迴圈跑一次
規則順序、判斷條件和 backtest.simulate 一模一樣；改其中一邊，另一邊要一起改。
"""
import time
from . import binance as B, config as C, store, telegram
from .signals import ENGINE_TF

TF_MS = {"1m": 60_000, "5m": 300_000}

def tf_of(eid): return ENGINE_TF.get(eid, "5m")
def rules(eid): return {**C.RISK, **C.EXIT.get(eid, {})}
def _now(): return int(time.time() * 1000)
def num(v):
    """是數字才回傳，否則 None。`.get(鍵, 預設)` 擋不住「鍵存在、值是 None」（清單第 8 條 r27），計算前一律先過這一關。"""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def _log(msg): store.push("errors", f"{time.strftime('%m-%d %H:%M')} {msg}")

def nag(n):
    """需要人處理的持續狀態，提醒節奏三專案統一（清單第 8 條 r8）：第 1、5、30 次，之後第 30 次起每隔 120 次
    （150、270、390…）。本專案迴圈 60 秒一輪（POLL_SEC），120 次約 2 小時。"""
    return n in (1, 5, 30) or (n > 30 and (n - 30) % 120 == 0)

def closed_bars(sym, tf, limit=150):
    """只留已收盤的 K 棒（Binance 最後一根是進行中的）。"""
    cut = _now()
    return [b for b in B.klines(sym, tf, limit + 1) if b["t"] + TF_MS[tf] <= cut]

def cooling(sym, eid):
    """回測：同引擎出場後 cooldown_bars 根內不再進場。"""
    t = store.get().get("cool", {}).get(f"{sym}:{eid}")
    return bool(t) and _now() - t < rules(eid)["cooldown_bars"] * TF_MS[tf_of(eid)]

def trades_after(sym, pos):
    """這個部位「已採用的最後一筆成交之後」的平倉方向成交（清單第 8 條 r30～r36）。
    - 有成交 id 界線（開倉成交後、認領當下、每次採用後記下的 trade_mark）：帶 fromId 往後查，**不再用時間篩**——
      交易所時鐘比本機慢時，平倉成交的時間戳會早於帳上的開倉時間，用時間篩會被篩掉（r36）。
      也不用「最近 N 筆」：持倉期間同幣成交一多，界線之後的平倉成交會掉出查詢範圍（r35）。
    - 沒有 id 界線才退回用時間（開倉時間往後）；時間戳也無效 → 未知，不能退成 0 把歷史上的平倉算進來（r33～r35）。
    - 平倉成交用方向判斷（不用 realizedPnl ≠ 0）；雙向模式比對 positionSide（r31、r34）。
    - 有基準部位 → 未知；查詢失敗 → 未知（None）；查到但沒有 → []。"""
    if (num(pos.get("base_qty")) or 0) > 1e-9: return None
    side, my_ps = ("SELL" if pos.get("side") == "LONG" else "BUY"), pos.get("side")
    mark, since = num(pos.get("trade_mark")), num(pos.get("ts"))
    try:
        if mark is not None and mark > 0:
            rows, frm = [], int(mark) + 1
            for _ in range(20):                                   # 分頁：每頁最多 1000 筆
                page = B.user_trades(sym, from_id=frm)
                rows += page
                if len(page) < 1000: break
                frm = int(max(num(t.get("id")) or 0 for t in page)) + 1
            keep = lambda t: (num(t.get("id")) or 0) > mark
        elif since is not None and since > 0:
            rows = B.user_trades(sym)
            keep = lambda t: (num(t.get("time")) or 0) >= since
        else:
            _log(f"{sym} 沒有有效的成交界線（成交 id 與開倉時間都無效），出場價記未知")
            return None
    except Exception as e: _log(f"{sym} 查成交明細失敗 {e}"); return None
    return [t for t in rows if t.get("side") == side and keep(t)
            # 雙向模式：成交明細有 positionSide，別人同幣反方向的開倉（方向同樣是 SELL/BUY）不是我的平倉（清單第 7 條）
            and t.get("positionSide", "BOTH") in ("BOTH", my_ps)]

def mark_now(sym):
    """成交明細目前最後一筆的 id——開倉成交之後、認領當下記下來當起始界線（清單第 8 條 r32、r35）。
    查不到、或查到的是空清單（剛成交完不該是空的）→ None，之後退回用時間。"""
    try:
        ids = [num(t.get("id")) for t in B.user_trades(sym)]
        ids = [i for i in ids if i is not None]
        return max(ids) if ids else None
    except Exception as e:
        _log(f"{sym} 記起始界線時查成交明細失敗 {e}"); return None

def _segment(trades):
    """一段成交 → (平均成交價, 損益＝已實現−手續費, 數量, 最後一筆 id)。"""
    q = sum(float(t["qty"]) for t in trades)
    px = sum(float(t["price"]) * float(t["qty"]) for t in trades) / q
    pnl = sum(float(t.get("realizedPnl") or 0) for t in trades) - sum(float(t.get("commission") or 0) for t in trades)
    return float(f"{px:.6g}"), round(pnl, 2), q, max(num(t.get("id")) or 0 for t in trades)

def adopt_partial(sym, pos, cut, px_hint=None):
    """記一筆部分出場（App 手動減碼、ADL、1R 減碼）。損益只用實際成交（成交明細）；查不到、分不出是誰的就記未知。
    不用標記價、不用觸發價、不退回進場價（清單第 8 條 r30）。採用了的成交推進界線，最後出場時不會再算一次。"""
    tr = trades_after(sym, pos)
    part = dict(qty=cut, at=time.strftime("%m-%d %H:%M"), pnl=None, px=None)
    if tr:
        px, pnl, q, last = _segment(tr)
        part.update(px=px, pnl=pnl); pos["trade_mark"] = last
    pos["partials"] = (pos.get("partials") or []) + [part]
    return part

def close_info(sym, pos):
    """最後一段出場：只看界線之後的成交（出場價不被前面的部分出場拉偏）。
    整筆損益 = 各段部分出場 + 最後一段；任何一段未知 → 整筆未知（清單第 8 條 r27、r30）。"""
    try:
        tr = trades_after(sym, pos)
        if not tr: return {}
        px, pnl, q, last = _segment(tr)
        parts = [num((x or {}).get("pnl")) for x in pos.get("partials") or []]
        total = round(pnl + sum(parts), 2) if all(p is not None for p in parts) else None
        out = dict(exit=px, pnl=total)
        if total is not None and num(pos.get("risk_usdt")): out["r"] = round(total / pos["risk_usdt"], 2)
        return out
    except Exception as e:
        _log(f"{sym} 抓出場價失敗 {e}"); return {}

def record_close(sym, pos, by, info=None):
    """持倉結束（交易所上確實已經沒有這個部位之後才呼叫）。
    順序（清單第 8 條 r24）：
      1. 算平倉紀錄——只用 .get()，缺欄位時損益記為未知，不能丟例外
      2. 寫紀錄、移出帳、記冷卻、清計數  ← 界線：到這裡這筆就結完帳了
      3. 撤剩下的停損單、收尾通知——各自 try，出錯推播，但不能讓這筆「結不了帳」
    舊順序是「撤單 → 通知 → 算損益 → 寫紀錄」：通知一出錯，停損撤了、紀錄沒寫、部位還在帳上，
    下一輪對帳再結一次、再錯一次，這筆永遠結不了帳。"""
    try: info = close_info(sym, pos) if info is None else info
    except Exception: info = {}
    rec = dict(pos, symbol=sym, closed=time.strftime("%m-%d %H:%M"), by=by, **(info or {}))
    # ---- 界線 ----
    store.push("closed", rec)
    own = dict(store.get().get("open", {})); own.pop(sym, None); store.update(open=own)
    cool = dict(store.get().get("cool", {})); cool[f"{sym}:{pos.get('engine')}"] = _now(); store.update(cool=cool)
    _missing.pop(sym, None)                          # 補掛連續次數不能留給同幣下一筆（清單第 8 條 r12）
    for k in [k for k in _errs if k[0] == sym]: _errs.pop(k)   # 出錯次數也一樣
    # ---- 界線之後：不可逆的動作與通知，各自 try ----
    queued = set(store.get().get("leftover", {}))
    for oid, via in [(pos.get("stop_id"), pos.get("stop_via"))] + [tuple(x) for x in pos.get("stale_ids", [])]:
        if not oid or str(oid) in queued: continue       # 已在待撤清單的交給 sweep_leftovers，不重複告警
        try: B.cancel_order(sym, oid, via)          # 已觸發／已撤（-2011）在 cancel_order 裡視為正常
        except Exception as e:
            try: queue_leftover(sym, oid, via, e)
            except Exception as e2: _once_err(sym, "撤殘留停損", e2)
    try:
        # 失敗中的狀態隨部位平倉結束 → 收尾通知，不能無聲消失（清單第 8 條 r11）
        open_fail = [f"移損到 {pos.get('want_stop'):.6g} 失敗 {pos['want_fail']} 次" if pos.get("want_fail") and pos.get("want_stop") is not None else None,
                     f"補掛停損失敗 {pos['guard_fail']} 次" if pos.get("guard_fail") else None]
        open_fail = [x for x in open_fail if x]
        if open_fail:
            telegram.send(f"ℹ️ {sym} 引擎{pos.get('engine')} 已平倉（{by}），" + "、".join(open_fail) + " 的狀態隨平倉結束")
    except Exception as e: _once_err(sym, "收尾通知", e)
    return rec

def _once_err(sym, stage, e):
    """已結帳部位的後續步驟出錯：推播一次，不留計數給同幣下一筆。"""
    _step_err(sym, stage, e); _errs.pop((sym, stage), None)

class CloseFailed(Exception):
    """平倉沒完成；close_now 已計數並依節奏告警，部位與停損都保留。"""

def remaining(sym, side, base=0.0):
    """交易所上「自己的」這一側還剩多少：這一側總數扣掉送單前就有的基準數量（清單第 3 條 r14、第 7 條 r15）。
    - 帶 symbol 逐幣查，不用全量表：全量表可能回 200 加空清單（清單第 2 條 r15）。
    - 查詢失敗回 None——不能當成 0（清單第 2 條）。"""
    try: total = B.side_qty(sym, side)
    except Exception: return None
    return max(0.0, round(total - (base or 0.0), 10))

def close_now(sym, pos, by):
    """市價平掉剩餘部位並記帳。平倉單送出後一定看結果（清單第 8 條 r12）：
    - 不管回應成功、被拒、逾時，都再查一次交易所部位；確實沒了才記帳、撤停損。
    - 還有剩（數量不符被拒、部分成交）→ 用交易所實際數量重送一次。
    - 還是沒平掉 → 保留部位與停損、依節奏告警、拋 CloseFailed；呼叫端每輪會再試（want_close）。"""
    side = pos["side"]; close_side = "SELL" if side == "LONG" else "BUY"
    base = pos.get("base_qty") or 0.0
    px, err = None, None
    # 送單前先確認自己還有多少（清單第 7 條 r15）：扣掉基準後沒有了就不送——
    # 單向共用帳號裡同側有別人的部位時，reduceOnly 單會把別人的平掉。數量取「交易所這一側 − 基準」與帳上的小者。
    left = remaining(sym, side, base)
    if left is None: qty = None
    elif left == 0: qty = 0
    else: qty = min(pos["qty"], left)
    for attempt in (1, 2):
        if not qty: break                            # 查不到（None）或自己已經沒了（0）→ 不送單
        try:
            o = B.market_order(sym, close_side, qty, reduce_only=True)
            px = float(o.get("avgPrice") or 0) or px
        except Exception as e: err = e
        time.sleep(0.5)                              # 讓交易所的部位表跟上
        left = remaining(sym, side, base)
        if left is None or left == 0: break
        if attempt == 1 and left != qty: qty = min(left, pos["qty"]); continue   # 部分成交或帳實不符 → 用自己的實際剩餘重送
        if attempt == 1 and err is not None and not B.definite_reject(err): continue   # 逾時／5xx 且數量沒變 → 再送一次
        break
    if left != 0:
        if left: pos["qty"] = left
        pos["want_close"] = by
        n = pos["close_fail"] = pos.get("close_fail", 0) + 1
        why = "無法確認交易所部位" if left is None else f"交易所上還有 {left:g}"
        if nag(n):
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} {by}平倉未完成（第 {n} 次，{why}"
                          + (f"，{str(err)[:100]}" if err else "") + "）— 部位與停損都保留，每輪重試")
        raise CloseFailed(why)

    was = pos.pop("close_fail", 0); pos.pop("want_close", None)
    # 交易所上已經平掉了——從這裡開始不能丟例外（清單第 8 條 r24）：算損益只用 .get()，缺欄位記為未知
    entry, q = num(pos.get("fill")), num(pos.get("qty"))   # 估算只用實際成交價；查不到就記未知，交給成交明細（r27）
    parts = [num((x or {}).get("pnl")) for x in pos.get("partials", [])]
    try:
        # 任何一段損益未知 → 整筆未知（不能把未知那段當 0 加總，清單第 8 條 r27）
        pnl = round((px - entry) * q * (1 if side == "LONG" else -1) + sum(parts), 2) \
              if px and entry and q and all(p is not None for p in parts) else None
    except Exception: pnl = None
    info = dict(exit=px, pnl=pnl)
    try: info.update({k: v for k, v in close_info(sym, pos).items() if v is not None})   # 成交明細優先，分段加總
    except Exception: pass
    rec = record_close(sym, pos, by, info)                 # 寫紀錄、移出帳都在裡面
    try:
        telegram.send(f"🏁 {sym} 引擎{pos.get('engine')} {by}出場" +
                      (f" @ {rec['exit']:.6g}" if num(rec.get("exit")) else "") +
                      (f"，損益 {rec['pnl']:+.2f} U" if num(rec.get("pnl")) is not None else "，損益未知（成交明細查不到、估算缺資料）") +
                      (f"（{rec['r']:+.2f}R）" if rec.get("r") is not None else "") +
                      (f"（先前平倉失敗 {was} 次，已恢復）" if was >= 1 else ""))
    except Exception as e: _once_err(sym, "出場通知", e)
    return rec

def retry_close(sym, pos):
    """上次平倉沒完成 → 每輪重試，直到交易所上確實沒有這個部位。"""
    if not pos.get("want_close"): return "nothing"          # 沒有待平倉（回傳原因，清單第 2 條 r27）
    try: close_now(sym, pos, pos["want_close"]); return "closed"
    except CloseFailed: return "pending"

def place_stop(sym, pos, price, qty=None, known_left=None):
    """所有「掛一張新停損」都走這裡：第一次掛、認領、手動認領、移損掛新、守衛補掛（清單第 2 條 r20、r21）。
    - 先確認自己的部位還在（扣基準）。呼叫端手上有正向證據（剛成交的回應、交易所那一列）時傳 known_left，免得再查一次。
      危險的是「空清單被當成沒有」，不是反過來——正向證據可以直接用。
    - 查不到 → ("unknown", None)：這輪不掛、不算失敗（第 2 條 r17）
    - 自己的部位已經沒了 → ("gone", None)：不掛，交給對帳結帳（否則就是孤兒單，第 7、13 條）
    - 還在 → 數量取「交易所−基準」與帳上／指定數量的小者，reduce-only 帶數量（不用 closePosition，有基準時不會平到別人的，r21）
    交易所拒絕照樣往上拋，讓呼叫端照自己的規則計數、重試。"""
    left = known_left if known_left is not None else remaining(sym, pos["side"], pos.get("base_qty") or 0.0)
    if left is None: return "unknown", None
    if left <= 0: return "gone", None
    q = min(qty or pos["qty"], left)
    return "ok", B.stop_order(sym, "SELL" if pos["side"] == "LONG" else "BUY", q, price)

class StopMoveFailed(Exception):
    """移損失敗；move_stop 已計數並依節奏告警，呼叫端不要再發告警。"""

def move_stop(sym, pos, new_stop, qty=None):
    """先掛新停損、再撤舊的，中間不會有沒保護的空窗。
    失敗計數、告警、恢復通知都在這裡——不管是出場判斷、重試、還是哪條路徑呼叫，
    第一次失敗都算第 1 次，任何一次成功都會發恢復（清單第 8 條 r8）。"""
    pos["want_stop"] = new_stop                     # 先記意圖：掛不上或這輪查不到部位時，下一輪 retry_stop 會重試
    try:
        st, o = place_stop(sym, pos, new_stop, qty)
        if st != "ok": return st                     # unknown：這輪不動、不算失敗；gone：交給對帳（清單第 2 條 r20）
    except Exception as e:
        if "-2021" in str(e): raise                  # 價格已穿過 → 呼叫端直接出場，不算移損失敗
        n = pos["want_fail"] = pos.get("want_fail", 0) + 1
        if nag(n):
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} 停損應移到 {new_stop:.6g} 未成功（第 {n} 次，{e}）"
                          f"— 目前停損還在 {pos.get('stop'):.6g}")
        raise StopMoveFailed(str(e)) from None
    old, old_via = pos.get("stop_id"), pos.get("stop_via")
    if old:
        try: B.cancel_order(sym, old, old_via)
        except Exception as e:
            pos.setdefault("stale_ids", []).append([old, old_via])   # 平倉時也會再撤一次
            queue_leftover(sym, old, old_via, e, why="移損後舊停損")
    was = pos.get("want_fail", 0)
    pos.update(stop=new_stop, stop_id=o.get("orderId"), stop_via=o.get("via"), want_stop=None, want_fail=0)
    if was >= 1:
        telegram.send(f"✅ {sym} 停損已移到 {new_stop:.6g}（先前失敗 {was} 次，已恢復）")
    return "moved"

def retry_stop(sym, pos):
    """上次移損沒成功 → 每輪重試。計數、告警、恢復通知都在 move_stop 裡。
    crypto-screener 曾因移損失敗只試一次而『移損到成本』安靜失效好幾天。"""
    want = pos.get("want_stop")
    if want is None or want == pos.get("stop"): pos["want_stop"] = None; return "nothing"
    try: return move_stop(sym, pos, want)             # moved／unknown／gone
    except StopMoveFailed: return "failed"
    except Exception as e:
        if "-2021" in str(e):                       # 價格已穿過想要的停損 = 本來就該出場
            try: close_now(sym, pos, "停損"); return "closed"
            except CloseFailed: return "close_pending"
        _log(f"{sym} 重試移損 {e}")
        return "error"

_missing = {}          # symbol → 連續幾輪確認停損不在

def ensure_stop(sym, pos):
    """確認停損單還掛著，不在就補掛。

    設計原則直接沿用 crypto-screener 那次誤平倉的教訓：
    1. 查詢失敗 ≠ 停損不在。查不到就跳過，下一輪再查。
    2. 連續 3 輪都確認不在才動作，單次讀取錯誤不該觸發任何事。
    3. 補掛被拒且原因是「已存在」→ 代表停損其實在，是判斷錯了，不動作。
    4. 只補掛、不平倉。補不上就大聲告警，交給人決定。
    """
    stops, ok = B.open_stops(sym)
    if not ok: return "query_failed"                      # 原則 1
    oid = lambda o: o.get("algoId") or o.get("orderId")
    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    mine_ids = {pos.get("stop_id")} | {x[0] for x in pos.get("stale_ids", [])}
    # 只認自己記過 id 的單；沒記 id（舊版認領的倉）才退而用方向判斷。共用帳號裡同幣的別人單不能碰（清單第 7 條）
    mine = [o for o in stops if oid(o) in mine_ids] or \
           ([o for o in stops if o.get("side") == close_side][:1] if not pos.get("stop_id") else [])
    if mine:
        _missing[sym] = 0
        was = pos.pop("guard_fail", 0)
        if was >= 1:                                      # 補掛失敗過，現在停損在了（例如上次逾時但交易所端其實成功）
            telegram.send(f"🔧 {sym} 引擎{pos.get('engine')} 停損單已確認存在（先前補掛失敗 {was} 次，已恢復）")
        cur = next((o for o in mine if oid(o) == pos.get("stop_id")), mine[0])
        pos["stop_id"] = oid(cur)
        for extra in mine:                                # 只撤自己記錄在案的殘留
            if oid(extra) == pos["stop_id"]: continue
            try: B.cancel_order(sym, oid(extra), pos.get("stop_via")); _log(f"{sym} 撤掉殘留停損 {oid(extra)}")
            except Exception as e: queue_leftover(sym, oid(extra), pos.get("stop_via"), e, why="移損後舊停損")   # 不能靜靜略過（r36）
        pos["stale_ids"] = [x for x in pos.get("stale_ids", []) if x[0] != pos["stop_id"] and x[0] in {oid(o) for o in stops}]
        return "present"
    n = _missing.get(sym, 0) + 1; _missing[sym] = n
    if n < 3: return "waiting"                            # 原則 2
    # 補掛前逐幣確認自己的部位還在（扣基準）。部位其實已經沒了（對帳這輪出錯沒偵測到）時補掛，
    # 就是一張孤兒 reduce-only 單，單向共用帳號裡還可能平到別人同側的倉（清單第 7、13 條）。
    try:
        st, o = place_stop(sym, pos, pos["stop"])        # 確認部位、扣基準都在共用處（清單第 2 條 r19、r20）
        if st != "ok": return st                          # unknown：查不到這輪不動；gone：已沒了交給對帳（回傳原因，清單第 2 條 r24）
        pos["stop_id"] = o.get("orderId"); pos["stop_via"] = o.get("via"); _missing[sym] = 0
        was = pos.pop("guard_fail", 0)
        _log(f"{sym} 停損單不見了，已補掛 {pos['stop']}")
        telegram.send(f"🔧 {sym} 引擎{pos.get('engine')} 停損單不見了，已補掛 {pos['stop']:.6g}" +
                      (f"（先前失敗 {was} 次，已恢復）" if was else ""))
        return "placed"
    except Exception as e:
        msg = str(e).lower()
        if "-2021" in msg:                                # 價格已穿過停損：停損本來就該觸發了 → 直接出場
            try: close_now(sym, pos, "停損"); return "closed"
            except CloseFailed: return "close_pending"
        if "existing" in msg or "already" in msg:         # 原則 3
            _missing[sym] = 0; _log(f"{sym} 停損其實在（補掛回報已存在），誤判")
            was = pos.pop("guard_fail", 0)
            if was >= 1: telegram.send(f"🔧 {sym} 停損單已確認存在（先前補掛失敗 {was} 次，已恢復）")
            return "false_alarm"
        k = pos["guard_fail"] = pos.get("guard_fail", 0) + 1
        if nag(k):                                                          # 原則 4：只告警、不平倉
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} 沒有停損單、補掛失敗（第 {k} 次）"
                          f"應掛 {pos['stop']:.6g}，錯誤：{e} — 請手動處理")
        return "place_failed"

def step(sym, pos):
    """處理這筆持倉自上次以來新收盤的 K 棒。回傳 "closed" 或 None（pos 會就地更新）。"""
    eid = pos.get("engine"); R = rules(eid); tf = tf_of(eid); ms = TF_MS[tf]
    d = 1 if pos["side"] == "LONG" else -1
    # 訊號棒 = 回測的 t["i"]。值是 None 才用預設——0 是合法的值，不能用 `or`（清單第 8 條 r27）
    start = num(pos.get("bar_t"))
    if start is None: start = ((num(pos.get("ts")) if num(pos.get("ts")) is not None else _now()) // ms) * ms
    r_unit = pos.get("r_unit") or abs(pos["entry"] - pos["stop"])
    if not r_unit: return None
    k = closed_bars(sym, tf, min(R["max_hold_bars"] + R["trail_bars"] + 10, 1400))
    for j, bar in enumerate(k):
        last = num(pos.get("last_t"))
        if bar["t"] <= (start if last is None else last): continue
        pos["last_t"] = bar["t"]
        n = (bar["t"] - start) // ms                                     # = 回測的 i - t["i"]
        fav = bar["h"] if d > 0 else bar["l"]
        r_now = d * (fav - pos["entry"]) / r_unit
        try:
            if R["tp1_r"] is not None and not pos.get("tp1") and r_now >= R["tp1_r"]:
                half = float(B.round_qty(sym, pos["qty"] / 2))
                o = B.market_order(sym, "SELL" if d > 0 else "BUY", half, reduce_only=True)
                done = num(float(o.get("executedQty") or 0)) or half
                pos["qty"] = round(pos["qty"] - done, 8); pos["tp1"] = True; pos["state"] = "已減碼"
                try: adopt_partial(sym, pos, done)            # 1R 減碼也是一段出場：損益用實際成交（清單第 8 條 r30）
                except Exception as e: _log(f"{sym} 記 1R 減碼損益失敗 {e}")
                telegram.send(f"✂️ {sym} 引擎{eid} 到 {R['tp1_r']}R 減碼一半，停損移到成本 {pos['entry']:.6g}")   # 減碼已成交，先通知
                move_stop(sym, pos, pos["entry"])
            elif R["tp1_r"] is None and not pos.get("be") and R.get("be_r") and r_now >= R["be_r"]:
                pos["be"] = True; pos["state"] = "保本"
                move_stop(sym, pos, pos["entry"])
                telegram.send(f"🛡 {sym} 引擎{eid} 到 {R['be_r']}R，停損移到成本 {pos['entry']:.6g}")
            elif n >= R["max_hold_bars"]:
                close_now(sym, pos, "時間"); return "closed"      # 失敗會拋 CloseFailed（已告警、已記 want_close）
            elif (pos.get("tp1") or pos.get("be")) and r_now >= R["trail_after_r"] and j >= R["trail_bars"]:
                seg = k[j - R["trail_bars"]:j + 1]
                ns = max(pos["stop"], min(x["l"] for x in seg)) if d > 0 else min(pos["stop"], max(x["h"] for x in seg))
                if ns != pos["stop"]:
                    move_stop(sym, pos, ns); pos["state"] = "追蹤"
        except StopMoveFailed:
            return None                              # 已記下想要的停損並告警，下一輪 retry_stop 重試
        except CloseFailed:
            return None                              # 已記下 want_close 並告警，下一輪 retry_close 重試
        except Exception as e:
            msg = str(e)
            if "-2021" in msg:                       # 新停損價已經被穿過 = 本來就該停損了
                try: close_now(sym, pos, "停損"); return "closed"
                except CloseFailed: return None
            _log(f"{sym} 出場管理 {msg}")
            telegram.send(f"⚠️ {sym} 引擎{eid} 出場管理失敗：{msg}（原停損單仍在）")
            return None
    return None

def queue_leftover(sym, oid, via, err, why="平倉後停損"):
    """撤不掉的條件單放進引擎層級的待撤清單：當下就是第 1 次、要告警，之後每輪重撤（清單第 8、13 條）。"""
    lo = dict(store.get().get("leftover", {}))
    if str(oid) in lo: return
    lo[str(oid)] = dict(symbol=sym, via=via, n=1, err=str(err)[:120], why=why)
    store.update(leftover=lo)
    _log(f"{sym} {why}單 {oid} 撤不掉 {err}（之後每輪重撤）")
    telegram.send(f"⚠️ {sym} {why}單 {oid} 撤不掉（第 1 次，{str(err)[:120]}）— 之後每輪重撤，可能動到同幣其他倉")

def sweep_leftovers():
    """平倉後沒撤掉的條件單：每輪重撤，照節奏提醒直到清掉（清單第 13 條 + 第 8 條 r6 告警節奏）。"""
    lo = dict(store.get().get("leftover", {}))
    if not lo: return
    for oid, x in list(lo.items()):
        # 每一筆各自 try：某一筆資料壞掉，其他筆照樣重撤；壞掉的那筆留在清單、照節奏推播，不能被靜靜丟掉（清單第 8 條 r21）。
        # 告警訊息只用 .get() 組，except 裡不能再拋。
        x = x if isinstance(x, dict) else {}
        sym = x.get("symbol", "?")
        try:
            B.cancel_order(x["symbol"], int(oid) if str(oid).isdigit() else oid, x.get("via"))
            n = x.get("n", 0)
            lo.pop(oid)
            if n >= 1: telegram.send(f"✅ {sym} 殘留停損單 {oid} 已撤掉（先前失敗 {n} 次，已恢復）")
        except Exception as e:
            x["n"] = x.get("n", 0) + 1; x["err"] = f"{type(e).__name__}: {e}"[:120]; lo[oid] = x
            if nag(x["n"]):
                telegram.send(f"⚠️ {sym} 殘留停損單 {oid} 撤不掉／處理出錯（第 {x['n']} 次，{x['err']}）"
                              f"— 可能動到同幣其他倉，請到交易所手動撤")
    store.update(leftover=lo)

_errs = {}               # (symbol, 步驟) → 連續出錯次數

def _step_err(sym, stage, e):
    """守衛迴圈裡某個部位的某一步出錯：記錄並照節奏推播（清單第 8 條 r18、第 14 條：不能只進錯誤區）。"""
    k = (sym, stage); n = _errs[k] = _errs.get(k, 0) + 1
    _log(f"{sym} {stage}出錯（第 {n} 次）{type(e).__name__}: {e}")
    if nag(n):
        telegram.send(f"🐞 {sym} {stage}出錯（第 {n} 次）{type(e).__name__}: {str(e)[:120]}"
                      "— 這一步沒完成，其他步驟與其他部位照常執行")

def _step_ok(sym, stage):
    n = _errs.pop((sym, stage), 0)
    if n >= 1: telegram.send(f"✅ {sym} {stage}已恢復（先前連續出錯 {n} 次）")

def run():
    """每輪迴圈呼叫一次（在 reconcile 之後，已被交易所停損掉的倉不會進來）。"""
    try: sweep_leftovers(); _step_ok("*", "重撤殘留單")
    except Exception as e: _step_err("*", "重撤殘留單", e)
    for sym in list(store.get().get("open", {})):
        pos = dict(store.get().get("open", {}).get(sym) or {})
        if not pos: continue
        # 三段各自 try：前一段出錯，後面的守衛照樣跑（清單第 8 條 r18）
        res = None
        try:
            res = retry_close(sym, pos)                  # 上次平倉沒完成 → 先重試
            if res == "pending": res = None
            elif res != "closed": res = retry_stop(sym, pos)
            _step_ok(sym, "重試平倉／移損")
        except Exception as e: _step_err(sym, "重試平倉／移損", e)
        if res != "closed":
            try:
                if ensure_stop(sym, pos) == "closed": res = "closed"   # 等平倉期間停損也要一直在；補掛遇 -2021 直接出場
                _step_ok(sym, "停損守衛")
            except Exception as e: _step_err(sym, "停損守衛", e)
        if res != "closed" and not pos.get("want_close"):
            try:
                res = step(sym, pos)
                _step_ok(sym, "出場判斷")
            except Exception as e: _step_err(sym, "出場判斷", e)
        own = dict(store.get().get("open", {}))
        if res == "closed": own.pop(sym, None)
        elif sym in own: own[sym] = pos
        store.update(open=own)
