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
                w, o = scanner.scan(verbose=False)
                watch = {r["symbol"]: r for r in w}
                last_scan = time.time()
                store.update(watch=w, observe=o, last_scan=time.strftime("%Y-%m-%d %H:%M:%S"))
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
<title>pump-dump-hunter</title><style>
body{font:14px -apple-system,sans-serif;background:#111;color:#ddd;margin:12px}
h2{margin:16px 0 6px;font-size:15px;color:#f66}.wrap{overflow-x:auto}
table{border-collapse:collapse;font-size:12px;white-space:nowrap}
td,th{padding:5px 8px;border-bottom:1px solid #333;text-align:right}td:first-child,th:first-child{text-align:left}
.hot{color:#f66;font-weight:bold}.warm{color:#fc6}.long{color:#6d6}.short{color:#f66}
.meta{color:#888;font-size:12px}.pill{display:inline-block;background:#222;border-radius:8px;padding:2px 8px;margin:2px 4px 2px 0;font-size:12px}
</style><div id=app>載入中…</div><script>
const T=(rows,cols,cls)=>rows.length?'<div class=wrap><table><tr>'+cols.map(c=>'<th>'+c).join('')+'</tr>'+
rows.map(r=>'<tr class="'+(cls?cls(r):'')+'">'+cols.map(c=>'<td>'+(r[c]??'')).join('')+'</tr>').join('')+'</table></div>':'<div class=meta>（無）</div>';
async function load(){const s=await (await fetch('/api/state')).json();const sig=s.signals.slice().reverse();
document.getElementById('app').innerHTML=`<b>pump-dump-hunter</b>
<div class=meta>啟動 ${s.started} · 上次掃描 ${s.last_scan||'—'} · 每 60 秒刷新</div>
<div><span class=pill>擁擠名單 ${s.watch.length}</span><span class=pill>觀察 ${s.observe.length}</span><span class=pill>訊號 ${s.signals.length}</span><span class=pill>已下單 ${s.trades.length}</span></div>
<h2>擁擠名單（引擎正在盯）</h2>${T(s.watch,['symbol','score','hits','chg24','gain48','ma20_dev','oi_growth','funding','vol24'])}
<h2>觀察名單（24h 漲幅前 40，依熱度排）</h2><div class=meta>hits: g=48h漲幅 d=偏離MA20 o=OI增幅 f=資金費率 💥=24h跌超30%（崩後引擎盯） · 百分比單位</div>
${T(s.observe,['symbol','score','hits','chg24','gain48','ma20_dev','oi_growth','funding','vol24'],r=>r.score>=3?'hot':r.score==2?'warm':'')}
<h2>訊號（最新在上）</h2>${T(sig,['time','symbol','engine','side','entry','stop','stop_pct','leverage','notional','executed','reason'],r=>r.side=='LONG'?'long':'short')}
<h2>錯誤</h2><div class=meta>${s.errors.slice(-10).reverse().join('<br>')||'（無）'}</div>`}
load();setInterval(load,60000);</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/state": body, ct = json.dumps(store.get(), ensure_ascii=False).encode(), "application/json"
        elif self.path.startswith("/api/why"):
            sym = self.path.split("s=")[-1].upper() if "s=" in self.path else ""
            try: res = scanner.why(sym) if sym else dict(usage="/api/why?s=AINUSDT")
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json"
        elif self.path == "/health": body, ct = b"ok", "text/plain"
        else: body, ct = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    HTTPServer(("0.0.0.0", port), H).serve_forever()
