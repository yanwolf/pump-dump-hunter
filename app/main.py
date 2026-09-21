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

def reconcile(force=False):
    """跟交易所對帳：自己開的倉不在了 → 標記平倉；下單當下掛掉、沒記到帳的倉 → 認領回來。
    回傳 (自己還在場的倉數, 交易所全部持倉)。"""
    if not force and time.time() - _rc["t"] < 20: return _rc["n"], _rc["ex"]   # 20 秒內重用，positionRisk 權重高，打太兇會被 418
    st = store.get()
    own, pend = st.get("open", {}), dict(st.get("pending", {}))
    if not C.API_KEY: return 0, []
    ex = B.open_positions()
    ex_keys = {(p["symbol"], B.side_of(p)) for p in ex}      # 雙向模式下同幣可能有兩側，只認自己那一側（清單第 7 條）
    ex_syms = {p["symbol"] for p in ex}
    if pend:                                   # pending = 送出市價單前先寫的紀錄
        own = dict(own)
        for sym, rec in pend.items():
            if (sym, rec.get("side")) in ex_keys and sym not in own:
                own[sym] = dict(rec, adopted=True)
                store.push("errors", f"{time.strftime('%m-%d %H:%M')} 認領無紀錄持倉 {sym}（引擎{rec.get('engine')}）")
                telegram.send(f"♻️ 認領 {sym} 引擎{rec.get('engine')}：下單後紀錄遺失，已補記帳。請確認停損單是否存在")
        store.update(open=own, pending={})
    still, changed = {}, False
    for sym, rec in own.items():
        if (sym, rec.get("side")) in ex_keys: still[sym] = rec
        else:
            rec = manager.record_close(sym, rec, "停損單"); changed = True
            telegram.send(f"🏁 {sym} 引擎{rec.get('engine')} 停損單觸發出場" +
                          (f" @ {rec['exit']:.6g}，損益 {rec['pnl']:+.2f} U" if rec.get("exit") and rec.get("pnl") is not None else "") +
                          (f"（{rec['r']:+.2f}R）" if rec.get("r") is not None else ""))
    if changed or len(still) != len(own): store.update(open=still)
    store.update(exchange=[dict(symbol=p["symbol"], amt=float(p["positionAmt"]), entry=round(float(p["entryPrice"]), 8),
                                upnl=round(float(p.get("unRealizedProfit") or 0), 2),
                                owner="本策略" if p["symbol"] in still and still[p["symbol"]].get("side") == B.side_of(p) else "其他")
                          for p in ex])
    _rc.update(t=time.time(), n=len(still), ex=ex)
    return len(still), ex

def place(sym, eid, sig, sz, rec):
    """下單。順序很重要：市價單送出前先寫 pending，成交後立刻記帳，最後才掛停損，
    這樣任何一步炸掉都不會出現『倉在交易所、程式卻不知道』的孤兒倉。"""
    is_long = sig.side == "LONG"
    close_side = "SELL" if is_long else "BUY"
    store.update(pending={sym: dict(engine=eid, side=sig.side, time=rec["time"], entry=sig.entry, stop=sig.stop)})
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
        store.update(pending={})
        rec["skipped"] = f"下單失敗: {e}"
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 下單失敗 {e}")
        telegram.send(f"❌ {sym} 引擎{eid} 下單失敗：{e}")
        return rec
    qty = float(o.get("executedQty") or 0) or float(B.round_qty(sym, sz["qty"]))
    fill = float(o.get("avgPrice") or 0) or None
    rec["executed"] = True; rec["fill"] = fill; rec["qty"] = qty
    rec["slip_pct"] = round((fill / sig.entry - 1) * 100 * (-1 if is_long else 1), 3) if fill else None   # 負=比訊號價差（對自己不利）
    own = dict(store.get().get("open", {}))
    try: stop_px = float(B.round_price(sym, sig.stop))       # 實際掛出去的停損價（照 tickSize）
    except Exception: stop_px = sig.stop
    own[sym] = dict(engine=eid, side=sig.side, time=rec["time"], ts=int(time.time() * 1000) - 5000,
                    bar_t=rec.get("bar_t"), last_t=rec.get("bar_t"), r_unit=abs(sig.entry - stop_px),   # R 用取整後的停損算（清單第 4 條）
                    entry=sig.entry, fill=fill, stop=stop_px, qty=qty,
                    risk_usdt=round(abs(sig.entry - stop_px) * qty, 4), state="初始")
    store.update(open=own, pending={})
    store.push("trades", rec)

    err = None                                  # 停損單：失敗重試一次
    for _ in range(2):
        try:
            so = B.stop_order(sym, close_side, qty, stop_px); err = None
            own = dict(store.get().get("open", {}))
            if sym in own: own[sym] = dict(own[sym], stop_id=so.get("orderId"), stop_via=so.get("via")); store.update(open=own)
            break
        except Exception as e: err = str(e); time.sleep(1)
    if not err: return rec
    rec["stop_error"] = err
    store.push("errors", f"{time.strftime('%m-%d %H:%M')} {sym} 停損單失敗 {err}")
    if STOP_FAIL_CLOSE:
        try:
            B.market_order(sym, close_side, qty, reduce_only=True)
            own = dict(store.get().get("open", {})); own.pop(sym, None); store.update(open=own)
            rec["skipped"] = f"停損掛不上已平倉: {err}"
            telegram.send(f"🛑 {sym} 引擎{eid} 停損單掛不上（{err}），已立即市價平倉")
        except Exception as e2:
            telegram.send(f"🚨 {sym} 引擎{eid} 停損掛不上、平倉也失敗（{err} / {e2}）— 倉位無保護，請手動處理")
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
    watch, last, last_scan = {}, {}, 0
    while True:
        try:
            if time.time() - last_scan > SCAN_SEC:
                w, o = scanner.scan(verbose=False)
                watch = {r["symbol"]: r for r in w}
                last_scan = time.time()
                store.update(watch=w, observe=o, last_scan=time.strftime("%Y-%m-%d %H:%M:%S"))
            if TRADE and C.API_KEY:
                try: reconcile(force=True); manager.run()      # 先對帳（被停損掉的移除）再管理出場
                except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} reconcile/出場管理 {e}")
            t0 = time.time()
            for s in list(watch):
              try:
                k = manager.closed_bars(s, "5m", 150)      # 只用已收盤的棒，跟回測一致
                k1m = manager.closed_bars(s, "1m", 120) if any(ENGINE_TF.get(e) == "1m" for e in ENABLED) else None
                for eid in ENABLED:
                    kk = k1m if ENGINE_TF.get(eid) == "1m" else k
                    if not kk: continue
                    tag = f"{s}:{eid}"
                    if last.get(tag) == kk[-1]["t"]: continue     # 同一根不重複判斷
                    last[tag] = kk[-1]["t"]
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
            store.update(loop=dict(took=took, symbols=len(watch), at=time.strftime("%H:%M:%S")))
            if took > POLL_SEC * 0.7:
                msg = f"⚠️ 引擎迴圈 {took}s / {len(watch)} 檔，接近輪詢間隔 {POLL_SEC}s，可能漏 K 線"
                store.push("errors", f"{time.strftime('%m-%d %H:%M')} {msg}")
                if took > POLL_SEC: telegram.send(msg)
        except Exception as e:
            store.push("errors", f"{time.strftime('%m-%d %H:%M')} {e}"); traceback.print_exc()
        time.sleep(POLL_SEC - time.time() % POLL_SEC + 2)     # 對齊整分後 2 秒：K 棒剛收完就判斷

def _refresh():
    """手動動作後立刻重抓帳號持倉，畫面不用等下一輪迴圈。"""
    try: time.sleep(0.5); reconcile(force=True)
    except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} 對帳更新失敗 {e}")

def manage(act, sym, eid="?", stop=None):
    """手動處理帳號裡的孤兒倉（修正前留下的、或別的原因沒記到帳的）。"""
    mine = store.get().get("open", {}).get(sym)
    cands = [p for p in B.open_positions() if p["symbol"] == sym]
    pos = next((p for p in cands if mine and B.side_of(p) == mine.get("side")), cands[0] if cands else None)
    if not pos: return dict(error=f"{sym} 帳號內沒有持倉")
    amt = float(pos["positionAmt"]); is_long = amt > 0; qty = abs(amt)
    entry = float(pos["entryPrice"])
    own = dict(store.get().get("open", {}))
    if act == "close":
        rec = own.pop(sym, None) or dict(engine=eid, side="LONG" if is_long else "SHORT", entry=entry, fill=entry)
        rec["qty"] = qty
        store.update(open=own)
        r = manager.close_now(sym, rec, "手動")
        _refresh()
        return dict(ok=True, msg=f"{sym} 已平倉" + (f" @ {r['exit']:.6g}，損益 {r['pnl']:+.2f} U" if r.get("exit") and r.get("pnl") is not None else ""))
    if act == "adopt":
        if not stop: return dict(error="請填停損價")
        stop = float(stop)
        if (is_long and stop >= entry) or (not is_long and stop <= entry):
            return dict(error=f"停損價方向不對（{'多' if is_long else '空'}單進場 {entry}）")
        so = B.stop_order(sym, "SELL" if is_long else "BUY", qty, stop)   # via 一起記，撤單才知道打哪個端點
        own[sym] = dict(engine=eid, side="LONG" if is_long else "SHORT", time=time.strftime("%m-%d %H:%M"),
                        ts=int(time.time() * 1000), entry=entry, fill=entry, stop=stop, qty=qty, r_unit=abs(entry - stop),
                        risk_usdt=round(abs(entry - stop) * qty, 2), adopted=True, stop_id=so.get("orderId"), stop_via=so.get("via"), state="初始")
        store.update(open=own); _refresh()
        telegram.send(f"♻️ 手動認領 {sym} 引擎{eid}，已補掛停損 {stop}")
        return dict(ok=True, msg=f"{sym} 已認領並補掛停損 {stop}")
    return dict(error="未知動作")

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
            try: res = manage(q.get("act", [""])[0], norm_symbol(q.get("s", [""])[0]),
                              q.get("e", ["?"])[0], q.get("stop", [""])[0] or None)
            except Exception as e: res = dict(error=str(e))
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
    except Exception as e: print("preset boot:", e)
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()

if __name__ == "__main__":
    run()
