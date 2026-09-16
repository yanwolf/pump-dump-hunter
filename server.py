"""Zeabur 入口：HTTP 狀態頁 + 背景 paper/live 迴圈。"""
import json, os, threading, time, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import binance as B, config as C, risk, scanner, store, telegram
from signals import ENGINES, LONG_ENGINES

ENABLED = set(os.environ.get("ENGINES", "A,B,C,D,E").split(","))
TRADE = os.environ.get("TRADE", "0") == "1"          # 0=只通知 1=真的下單（testnet/live 看 USE_TESTNET）
SCAN_SEC = int(os.environ.get("SCAN_SEC", "1800")); POLL_SEC = int(os.environ.get("POLL_SEC", "60"))

def loop():
    store.update(started=time.strftime("%Y-%m-%d %H:%M:%S"))
    telegram.send(f"🎯 pump-dump-hunter 啟動 engines={sorted(ENABLED)} trade={TRADE} testnet={C.USE_TESTNET}")
    watch, last, last_scan = {}, {}, 0
    while True:
        try:
            if time.time() - last_scan > SCAN_SEC:
                watch = {r["symbol"]: r for r in scanner.scan(verbose=False)}
                last_scan = time.time()
                store.update(watch=list(watch.values()), last_scan=time.strftime("%Y-%m-%d %H:%M:%S"))
            for s in list(watch):
                k = B.klines(s, "5m", 300)
                if last.get(s) == k[-1]["t"]: continue
                last[s] = k[-1]["t"]
                for eid in ENABLED:
                    sig = ENGINES[eid](k, len(k) - 1)
                    if not sig: continue
                    sz = risk.size(sig)
                    rec = dict(time=time.strftime("%m-%d %H:%M"), symbol=s, **sig.__dict__, **sz, executed=False)
                    if TRADE and C.API_KEY:
                        is_long = sig.side == "LONG"
                        B.set_leverage(s, sz["leverage"])
                        B.market_order(s, "BUY" if is_long else "SELL", round(sz["qty"]))
                        B.stop_order(s, "SELL" if is_long else "BUY", round(sz["qty"]), round(sig.stop, 6))
                        rec["executed"] = True; store.push("trades", rec)
                    store.push("signals", rec)
                    telegram.send(f"{'✅下單' if rec['executed'] else '👀訊號'} {s} 引擎{eid} {'多' if sig.side == 'LONG' else '空'} @{sig.entry:.5g} "
                                  f"止損{sig.stop:.5g}({sz['stop_pct']}%) {sz['leverage']}x {sz['notional']}U\n{sig.reason}")
                    break
        except Exception as e:
            store.push("errors", f"{time.strftime('%m-%d %H:%M')} {e}"); traceback.print_exc()
        time.sleep(POLL_SEC)

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>pump-dump-hunter</title><style>body{font:14px -apple-system,sans-serif;background:#111;color:#ddd;margin:12px}
h2{margin:14px 0 6px;font-size:15px;color:#f66}table{border-collapse:collapse;width:100%%;font-size:12px}
td,th{padding:4px 6px;border-bottom:1px solid #333;text-align:left}pre{color:#888;font-size:11px}</style>
<div id=app>載入中…</div><script>
fetch('/api/state').then(r=>r.json()).then(s=>{const t=(rows,cols)=>'<table><tr>'+cols.map(c=>'<th>'+c).join('')+'</tr>'+
rows.slice().reverse().map(r=>'<tr>'+cols.map(c=>'<td>'+(r[c]??'')).join('')+'</tr>').join('')+'</table>';
document.getElementById('app').innerHTML=`<b>pump-dump-hunter</b> 啟動 ${s.started} · 上次掃描 ${s.last_scan}
<h2>多頭擁擠名單 (${s.watch.length})</h2>${t(s.watch,['symbol','score','gain48','ma20_dev','oi_growth','funding'])}
<h2>訊號 (${s.signals.length})</h2>${t(s.signals,['time','symbol','engine','side','entry','stop','stop_pct','leverage','executed','reason'])}
<h2>錯誤</h2><pre>${s.errors.slice(-10).join('\\n')}</pre>`});</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/state": body, ct = json.dumps(store.get(), ensure_ascii=False).encode(), "application/json"
        elif self.path == "/health": body, ct = b"ok", "text/plain"
        else: body, ct = PAGE.replace("%%", "%").encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    HTTPServer(("0.0.0.0", port), H).serve_forever()
