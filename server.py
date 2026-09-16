"""Zeabur 入口：HTTP 狀態頁 + 背景 paper/live 迴圈。"""
import json, os, threading, time, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import binance as B, config as C, risk, scanner, store, telegram, backtest, sweep, params
from signals import ENGINES, LONG_ENGINES, ENGINE_TF

ENABLED = set(os.environ.get("ENGINES", "B,C").split(","))
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
                k1m = B.klines(s, "1m", 120) if any(ENGINE_TF.get(e) == "1m" for e in ENABLED) else None
                for eid in ENABLED:
                    kk = k1m if ENGINE_TF.get(eid) == "1m" else k
                    if not kk: continue
                    tag = f"{s}:{eid}"
                    if last.get(tag) == kk[-1]["t"]: continue     # 同一根不重複判斷
                    last[tag] = kk[-1]["t"]
                    sig = ENGINES[eid](kk, len(kk) - 1)
                    if not sig: continue
                    sz = risk.size(sig)
                    if not sz: continue                      # 止損距離超過上限，略過
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
input,button{background:#222;color:#ddd;border:1px solid #444;border-radius:6px;padding:6px;font-size:14px}
</style>
<div id=head>載入中…</div>
<h2>回測（5m，三十天內）</h2>
<div><input id=bs placeholder="AINUSDT" value="AINUSDT" style="width:110px"> <input id=bd type=number value=3 style="width:50px"> 天
<button onclick="bt()">跑</button> <button onclick="dg()">A 診斷</button></div>
<div id=btout class=meta>（結果會留在這裡，不受自動刷新影響）</div>
<h2>歷史事件掃描（全市場，3 天漲一倍後跌四成，最多 90 天；每個事件回測高點前 10 天～後 5 天，獨立程序執行）</h2>
<div><input id=sd type=number value=30 style="width:50px"> 天 <input id=sl placeholder="這次的標籤（可空）" style="width:140px">
<button onclick="sw()">開始掃描</button> <button onclick="swload()">重新整理</button> <button onclick="swtoggle()">收合/展開</button> <button onclick="swclear()">清除</button></div>
<div class=meta style="margin-top:6px">調參：改哪個就填哪個，沒動的用預設（括號內）。<button onclick="pform(true)">全部還原</button> <button onclick="ptoggle()">顯示/隱藏參數</button></div>
<div id=pform style="display:none"></div>
<div id=swout class=meta>（尚未執行）</div>
<div id=live></div>
<script>
const T=(rows,cols,cls)=>rows.length?'<div class=wrap><table><tr>'+cols.map(c=>'<th>'+c).join('')+'</tr>'+
rows.map(r=>'<tr class="'+(cls?cls(r):'')+'">'+cols.map(c=>'<td>'+(r[c]??'')).join('')+'</tr>').join('')+'</table></div>':'<div class=meta>（無）</div>';
async function load(){const s=await (await fetch('/api/state')).json();const sig=s.signals.slice().reverse();
document.getElementById('head').innerHTML=`<b>pump-dump-hunter</b>
<div class=meta>啟動 ${s.started} · 上次掃描 ${s.last_scan||'—'} · 即時區每 60 秒刷新</div>
<div><span class=pill>擁擠名單 ${s.watch.length}</span><span class=pill>觀察 ${s.observe.length}</span><span class=pill>訊號 ${s.signals.length}</span><span class=pill>已下單 ${s.trades.length}</span></div>`;
document.getElementById('live').innerHTML=`
<h2>擁擠名單（引擎正在盯）</h2>${T(s.watch,['symbol','score','hits','chg24','gain48','ma20_dev','oi_growth','funding','vol24'])}
<h2>訊號（最新在上）</h2>${T(sig,['time','symbol','engine','side','entry','stop','stop_pct','leverage','notional','executed','reason'],r=>r.side=='LONG'?'long':'short')}
<h2>觀察名單（24h 漲幅前 40，依熱度排）</h2><div class=meta>score = g/d/o/f 四項各 1 分，≥3 進擁擠名單 · hits: g=48h漲幅 d=偏離MA20 o=OI增幅 f=資金費率 💥=24h跌超30%（崩後引擎盯） · 百分比單位</div>
${T(s.observe,['symbol','score','hits','chg24','gain48','ma20_dev','oi_growth','funding','vol24'],r=>r.score>=3?'hot':r.score==2?'warm':'')}
<h2>錯誤</h2><div class=meta>${s.errors.slice(-10).reverse().join('<br>')||'（無）'}</div>`}
async function bt(){const o=document.getElementById('btout');o.innerHTML='跑中…';
const s=document.getElementById('bs').value,d=document.getElementById('bd').value;
const r=await (await fetch('/api/backtest?s='+s+'&d='+d)).json();
if(r.error){o.innerHTML='錯誤: '+r.error;return}
o.innerHTML=`${r.symbol} ${r.bars} 根<br>`+T(r.summary,['engine','n','win','exp','pf','best','worst'])+'<br>'+
T(r.trades.slice().reverse(),['time','engine','side','entry','exit','r','reason','bars'],x=>x.r>0?'long':'short')}
async function dg(){const o=document.getElementById('btout');o.innerHTML='診斷中…';
const s=document.getElementById('bs').value,d=document.getElementById('bd').value;
const r=await (await fetch('/api/diag?s='+s+'&d='+d)).json();
if(r.error){o.innerHTML='錯誤: '+r.error;return}
const c=r.counts;o.innerHTML=`${r.symbol} ${c.bars} 根 · 成立次數：hot ${c.hot} · pivot ${c.pivot} · top ${c.top} · vol ${c.vol} · (div ${c.div}，參考) · 跌破中樞 ${c.brk} · 全部成立 ${c.all}<br>跌破中樞的棒（最近 40 根，UTC）：<br>`+
T(r.breaks.slice().reverse(),['time','close','zd','zg','width','hot','pivot','top','vol','div','fire'],x=>x.fire=='✅'?'long':'')}
let swOpen=true;function swtoggle(){swOpen=!swOpen;swload()}
let PS=[];function ptoggle(){const p=document.getElementById('pform');p.style.display=p.style.display=='none'?'block':'none'}
async function pform(reset){if(!PS.length)PS=await (await fetch('/api/params')).json();
let g='',h='';for(const s of PS){if(s.g!=g){g=s.g;h+='<h2 style="font-size:13px;color:#fc6">'+g+'</h2>'}
h+=`<div style="margin:4px 0 8px"><b>${s.label}</b> <span class=meta>(${s.default}${s.unit?' '+s.unit:''})</span>
<input class=pv data-k="${s.k}" type=number step="${s.step}" placeholder="${s.default}" style="width:90px;margin-left:6px"><br><span class=meta>${s.help}</span></div>`}
document.getElementById('pform').innerHTML=h}
function pcollect(){const o={};document.querySelectorAll('.pv').forEach(i=>{if(i.value!=='')o[i.dataset.k]=Number(i.value)});return o}
async function swclear(){if(!confirm('清除掃描結果？（K 線快取保留）'))return;await fetch('/api/sweep/clear');swload()}
async function sw(){const o=pcollect();const lbl=document.getElementById('sl').value||Object.entries(o).map(([k,v])=>k.split('.').slice(-2).join('.')+'='+v).join(' ')||'預設';
const r=await (await fetch('/api/sweep/start?d='+document.getElementById('sd').value+'&l='+encodeURIComponent(lbl)+'&o='+encodeURIComponent(Object.keys(o).length?JSON.stringify(o):''))).json();
if(r.error){alert(r.error);return}if(!r.started){alert('已有掃描在跑');return}setTimeout(swload,1500)}
async function swload(){const o=document.getElementById('swout');const r=await (await fetch('/api/sweep')).json();
if(!r.status){o.innerHTML='（尚未執行）'+(r.runs&&r.runs.length?'<br>歷次比較：'+T(r.runs,['label','days','time','n','total','A','B','C','D','E','F']):'');return}
let h='<b>'+r.status+'</b>';
if(r.runs&&r.runs.length)h+='<br>歷次比較（total=全部 R 合計）：'+T(r.runs,['label','days','time','n','total','A','B','C','D','E','F'],x=>x.total>0?'long':'');
if(!swOpen){o.innerHTML=h+'<br>（已收合）';return}
if(r.summary&&r.summary.length)h+='<br>各引擎彙整：'+T(r.summary,['engine','n','win','exp','pf','best','worst']);
if(r.results&&r.results.length)h+='<br>每檔事件（R 為該引擎在該幣的合計 R）：'+T(r.results,['symbol','peak_day','pump','dump','trades','R_A','R_B','R_C','R_D','R_E','R_F'],x=>['R_A','R_B','R_C','R_D','R_E','R_F'].some(k=>x[k]>0)?'long':'');
else if(r.events&&r.events.length)h+='<br>事件：'+T(r.events,['symbol','peak_day','pump','dump']);
if(r.trades&&r.trades.length)h+='<br>最大單筆（|R| 前 60）：'+T(r.trades,['symbol','time','engine','side','entry','exit','r','reason'],x=>x.r>0?'long':'short');
o.innerHTML=h;if(r.status&&!r.status.startsWith('完成')&&!r.status.startsWith('失敗'))setTimeout(swload,5000)}
load();swload();pform();setInterval(load,60000);</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/state": body, ct = json.dumps(store.get(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/backtest"):
            q = dict(p.split("=") for p in self.path.split("?")[-1].split("&") if "=" in p) if "?" in self.path else {}
            try: res = backtest.run(q.get("s", "AINUSDT").upper(), min(int(q.get("d", "3")), 30))
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
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
            ok = False if err else sweep.start(min(d, 90), ov, q.get("l", [""])[0])
            body, ct = json.dumps(dict(started=ok, error=err), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/sweep"):
            body, ct = json.dumps(sweep.state(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/diag"):
            q = dict(p.split("=") for p in self.path.split("?")[-1].split("&") if "=" in p) if "?" in self.path else {}
            try: res = backtest.diag_a(q.get("s", "AINUSDT").upper(), min(int(q.get("d", "3")), 30))
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path.startswith("/api/why"):
            sym = self.path.split("s=")[-1].upper() if "s=" in self.path else ""
            try: res = scanner.why(sym) if sym else dict(usage="/api/why?s=AINUSDT")
            except Exception as e: res = dict(error=str(e))
            body, ct = json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8"
        elif self.path == "/health": body, ct = b"ok", "text/plain"
        else: body, ct = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    HTTPServer(("0.0.0.0", port), H).serve_forever()
