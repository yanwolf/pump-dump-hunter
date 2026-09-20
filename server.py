"""Zeabur 入口：HTTP 狀態頁 + 背景 paper/live 迴圈。"""
import json, os, threading, time, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
import binance as B, config as C, risk, scanner, store, telegram, backtest, sweep, params, presets
from signals import ENGINES, LONG_ENGINES, ENGINE_TF

ENABLED = set(os.environ.get("ENGINES", "C,F,G").split(","))
TRADE = os.environ.get("TRADE", "0") == "1"
STOP_FAIL_CLOSE = os.environ.get("STOP_FAIL", "close") == "close"   # 停損單掛不上時：close=立刻平倉 keep=只告警          # 0=只通知 1=真的下單（testnet/live 看 USE_TESTNET）
SCAN_SEC = int(os.environ.get("SCAN_SEC", "1800")); POLL_SEC = int(os.environ.get("POLL_SEC", "60"))

def reconcile():
    """跟交易所對帳：自己開的倉不在了 → 標記平倉；下單當下掛掉、沒記到帳的倉 → 認領回來。
    回傳 (自己還在場的倉數, 交易所全部持倉)。"""
    st = store.get()
    own, pend = st.get("open", {}), dict(st.get("pending", {}))
    if not C.API_KEY: return 0, []
    ex = B.open_positions()
    ex_syms = {p["symbol"] for p in ex}
    if pend:                                   # pending = 送出市價單前先寫的紀錄
        own = dict(own)
        for sym, rec in pend.items():
            if sym in ex_syms and sym not in own:
                own[sym] = dict(rec, adopted=True)
                store.push("errors", f"{time.strftime('%m-%d %H:%M')} 認領無紀錄持倉 {sym}（引擎{rec.get('engine')}）")
                telegram.send(f"♻️ 認領 {sym} 引擎{rec.get('engine')}：下單後紀錄遺失，已補記帳。請確認停損單是否存在")
        store.update(open=own, pending={})
    still, changed = {}, False
    for sym, rec in own.items():
        if sym in ex_syms: still[sym] = rec
        else:
            rec = dict(rec, closed=time.strftime("%m-%d %H:%M")); store.push("closed", rec); changed = True
            telegram.send(f"🏁 {sym} 引擎{rec.get('engine')} 已平倉（止損/追蹤觸發）")
    if changed or len(still) != len(own): store.update(open=still)
    store.update(exchange=[dict(symbol=p["symbol"], amt=float(p["positionAmt"]), entry=round(float(p["entryPrice"]), 8),
                                upnl=round(float(p.get("unRealizedProfit") or 0), 2),
                                owner="本策略" if p["symbol"] in still else "其他") for p in ex])
    return len(still), ex

def place(sym, eid, sig, sz, rec):
    """下單。順序很重要：市價單送出前先寫 pending，成交後立刻記帳，最後才掛停損，
    這樣任何一步炸掉都不會出現『倉在交易所、程式卻不知道』的孤兒倉。"""
    is_long = sig.side == "LONG"
    close_side = "SELL" if is_long else "BUY"
    store.update(pending={sym: dict(engine=eid, side=sig.side, time=rec["time"], entry=sig.entry, stop=sig.stop)})
    try:
        B.set_leverage(sym, sz["leverage"])
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
    own[sym] = dict(engine=eid, side=sig.side, time=rec["time"], entry=sig.entry, fill=fill, stop=sig.stop, qty=qty)
    store.update(open=own, pending={})
    store.push("trades", rec)

    err = None                                  # 停損單：失敗重試一次
    for _ in range(2):
        try: B.stop_order(sym, close_side, qty, sig.stop); err = None; break
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
    watch, last, last_scan = {}, {}, 0
    while True:
        try:
            if time.time() - last_scan > SCAN_SEC:
                w, o = scanner.scan(verbose=False)
                watch = {r["symbol"]: r for r in w}
                last_scan = time.time()
                store.update(watch=w, observe=o, last_scan=time.strftime("%Y-%m-%d %H:%M:%S"))
            if TRADE and C.API_KEY:
                try: reconcile()
                except Exception as e: store.push("errors", f"{time.strftime('%m-%d %H:%M')} reconcile {e}")
            t0 = time.time()
            for s in list(watch):
              try:
                k = B.klines(s, "5m", 150)      # 引擎最多回看 ~60 根，150 夠用且省一半傳輸
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
                        n_own, ex = reconcile()
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
        time.sleep(max(1, POLL_SEC - (time.time() - t0 if "t0" in dir() else 0)))

def norm_symbol(s):
    """AIN / ain / AINUSDT / ain/usdt 都變 AINUSDT；全是 U 本位。"""
    s = (s or "").strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    return s if s.endswith("USDT") else s + "USDT"

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>pump-dump-hunter</title><style>
:root{--bg:#0f1114;--card:#171a1f;--line:#262b33;--txt:#e6e8eb;--dim:#8b939e;--gold:#e8b339;--up:#3ecf8e;--down:#f2616b}
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:0 0 40px}
.wrap{padding:12px;max-width:1100px;margin:0 auto}
header{position:sticky;top:0;z-index:9;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 12px}
h1{font-size:15px;margin:0 0 4px;color:var(--gold);letter-spacing:.5px}
h2{font-size:13px;margin:0 0 8px;color:var(--gold);font-weight:600}
.meta{color:var(--dim);font-size:12px}
.stats{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 10px;font-size:12px}
.stat b{color:var(--txt);font-size:13px;margin-left:4px}
.tabs{display:flex;gap:4px;margin-top:10px}
.tab{flex:1;text-align:center;padding:7px 4px;border-radius:8px;background:var(--card);border:1px solid var(--line);color:var(--dim);font-size:13px;cursor:pointer}
.tab.on{color:var(--bg);background:var(--gold);border-color:var(--gold);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px}
table{border-collapse:collapse;font-size:12px;white-space:nowrap;width:100%}
th{color:var(--dim);font-weight:500;text-align:right;padding:5px 8px;border-bottom:1px solid var(--line)}
td{text-align:right;padding:6px 8px;border-bottom:1px solid #1e2228}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--card);max-width:150px;overflow:hidden;text-overflow:ellipsis}
#swout td:first-child,#swout th:first-child{max-width:110px}
td.lbl{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:last-child td{border-bottom:none}
.pos{color:var(--up)}.neg{color:var(--down)}.hot{color:var(--gold);font-weight:600}
.empty{color:var(--dim);font-size:12px;padding:6px 0}
input,select,button,textarea{background:#1e2228;color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:7px 9px;font-size:13px;font-family:inherit}
button{cursor:pointer}button:active{opacity:.7}
button.go{background:var(--gold);color:#0f1114;border-color:var(--gold);font-weight:600}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.pgrp{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.pgrp h3{font-size:12px;color:var(--gold);margin:0 0 8px}
.p{margin-bottom:10px}.p label{display:block;font-size:13px;margin-bottom:3px}
.p .d{color:var(--dim);font-size:11px;margin-top:2px}
.p input{width:110px}
</style>
<header>
<h1>PUMP-DUMP HUNTER</h1>
<div id=hmeta class=meta>載入中…</div>
<div id=hstats class=stats></div>
<div class=tabs>
  <div class="tab on" data-t=watch>監控</div><div class=tab data-t=trade>交易</div><div class=tab data-t=lab>研究</div>
</div>
</header>
<div class=wrap>
<div id=watch class=pane></div>
<div id=trade class=pane style=display:none></div>
<div id=lab class=pane style=display:none>
  <div class=card><h2>單幣回測（5m，最多 30 天）</h2>
    <div class=row><input id=bs value="AIN" style="width:85px"><span class=meta>USDT</span>
      <input id=bd type=number value=3 style="width:55px"><span class=meta>天</span>
      <button class=go onclick="bt()">跑回測</button><button onclick="dg()">A 診斷</button></div>
    <div id=btout class=meta>結果會留在這裡，不受自動刷新影響</div>
  </div>
  <div class=card><h2>歷史事件掃描（全市場，背景執行）</h2>
    <div class=row><input id=sd type=number value=90 style="width:55px"><span class=meta>天</span>
      <select id=sm style="flex:1;min-width:200px">
        <option value=crash>崩盤日 · 單日跌30% · 測 C/F</option>
        <option value=pumpday>暴漲日 · 單日漲30% · 測 E/G</option>
        <option value=pump>拉高崩盤事件 · 3天漲倍後跌四成（對做多有事後偏差）</option>
      </select></div>
    <div class=row><input id=sl placeholder="標籤（可空）" style="flex:1;min-width:120px">
      <button class=go onclick="sw()">開始掃描</button><button onclick="swload()">更新</button>
      <button onclick="swtoggle()">收合</button><button onclick="swclear()">清除</button></div>
    <div id=swout class=meta>尚未執行</div>
  </div>
  <div class=card><h2>參數調整</h2>
    <div class=meta>表單的值<b>只用於這次掃描</b>。要長期保留請「存成組合」；要讓實盤照這些值跑，按「套用到實盤」（寫入 Volume，重佈後仍有效）。</div>
    <div class=row style=margin-top:8px>
      <select id=psel style="flex:1;min-width:130px"><option value="">— 載入組合 —</option></select>
      <button onclick="pload()">載入</button><button onclick="psave()">存成組合</button><button onclick="pdel()">刪除</button></div>
    <div class=row>
      <button onclick="ptoggle()">顯示/隱藏參數</button><button onclick="pclear()">清空表單</button>
      <button class=go onclick="plive()">套用到實盤</button><button onclick="plivereset()">實盤回預設</button></div>
    <div id=plivebox class=meta></div>
    <div id=pform style=display:none></div>
  </div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
const num=v=>typeof v==='number'?v:null;
function T(rows,cols,cls){if(!rows||!rows.length)return '<div class=empty>（無）</div>';
 return '<div class=scroll><table><tr>'+cols.map(c=>'<th>'+c).join('')+'</tr>'+
 rows.map(r=>'<tr>'+cols.map(c=>{let v=r[c];if(v===undefined||v===null)v='';
   let k='';if(typeof v==='number'){if(c==='r'||c[0]==='R'&&c.length<4||c==='total')k=v>0?'pos':v<0?'neg':''}
   if(c==='label'||c==='reason'||c==='skipped')k+=' lbl';
   return '<td class="'+k+'" title="'+String(v).replace(/"/g,'')+'">'+v}).join('')+'</tr>').join('')+'</table></div>'}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
  document.querySelectorAll('.pane').forEach(p=>p.style.display=p.id===t.dataset.t?'':'none')});

async function load(){let s;try{s=await (await fetch('/api/state')).json()}catch(e){$('hmeta').innerHTML='<span style=color:var(--down)>連線失敗：'+e.message+'</span>';return}
 if(s.error){$('hmeta').innerHTML='<span style=color:var(--down)>後端錯誤：'+s.error+'</span>';return}
 const e=s.equity||{},L=s.loop;
 const lo=Object.keys(s.live_overrides||{}).length;
 $('hmeta').innerHTML=`啟動 ${s.started||'—'} · 掃描 ${s.last_scan||'—'} · 迴圈 ${L?L.took+'s / '+L.symbols+' 檔':'—'}`+
   (lo?` · <span style=color:var(--gold)>實盤覆蓋 ${lo} 項</span>`:'');
 $('hstats').innerHTML=[
   ['持倉',Object.keys(s.open||{}).length],['擁擠',s.watch.length],['觀察',s.observe.length],
   ['訊號',s.signals.length],['已下單',s.trades.length],
   ['可用',e.tier?Math.round(e.tier*(1-e.reserve_pct))+' U':'—']
 ].map(([k,v])=>`<span class=stat>${k}<b>${v}</b></span>`).join('');
 const hits=r=>r.hits||'';
 const wcols=['symbol','score','hits','chg24','gain48','ma20_dev','oi_growth','funding','vol24'];
 $('watch').innerHTML=`
 <div class=card><h2>擁擠名單 · 引擎正在盯</h2>${T(s.watch,wcols,1)}</div>
 <div class=card><h2>觀察名單 · 24h 漲幅前 ${s.observe.length}</h2>
   <div class=meta style=margin-bottom:8px>score = g/d/o/f 四項各 1 分 · g=48h漲幅 d=偏離MA20 o=OI增幅 f=資金費率 · 💥=24h跌超30% 🔥=24h漲超門檻（都直接進引擎）· 單位 %</div>
   ${T(s.observe,wcols)}</div>
 <div class=card><h2>錯誤與警告</h2><div class=meta>${(s.errors||[]).slice(-8).reverse().join('<br>')||'（無）'}</div></div>`;
 const sig=s.signals.slice().reverse();
 $('trade').innerHTML=`
 <div class=card><h2>本策略持倉</h2>${T(Object.entries(s.open||{}).map(([k,v])=>({symbol:k,...v})),['symbol','engine','side','time','entry','fill','stop','qty','adopted'])}</div>
 <div class=card><h2>帳號全部持倉 · 對帳用</h2>
   <div class=meta style=margin-bottom:8px>owner=其他 表示不是這支機器人開的（crypto-screener／黃金等）</div>
   ${T(s.exchange||[],['symbol','owner','amt','entry','upnl'])}</div>
 <div class=card><h2>已平倉 · 最新在上</h2>${T((s.closed||[]).slice().reverse().slice(0,30),['closed','symbol','engine','side','entry','fill','stop','qty'])}</div>
 <div class=card><h2>訊號 · 最新在上 <button onclick="sigclear()" style="font-size:11px;padding:3px 8px">清除</button></h2>
   ${T(sig,['time','symbol','engine','side','entry','fill','slip_pct','stop','stop_pct','margin','notional','executed','skipped','stop_error','reason'])}</div>`}

async function bt(){const o=$('btout');o.innerHTML='跑中…';
 const r=await (await fetch('/api/backtest?s='+$('bs').value+'&d='+$('bd').value)).json();
 if(r.error){o.innerHTML='錯誤: '+r.error;return}
 o.innerHTML=`<b>${r.symbol}</b> ${r.bars} 根<br>`+T(r.summary,['engine','n','win','exp','pf','best','worst'])+'<br>'+
  T(r.trades.slice().reverse(),['time','engine','side','entry','exit','r','reason','bars'])}
async function dg(){const o=$('btout');o.innerHTML='診斷中…';
 const r=await (await fetch('/api/diag?s='+$('bs').value+'&d='+$('bd').value)).json();
 if(r.error){o.innerHTML='錯誤: '+r.error;return}const c=r.counts;
 o.innerHTML=`<b>${r.symbol}</b> ${c.bars} 根 · hot ${c.hot} · pivot ${c.pivot} · top ${c.top} · vol ${c.vol} · first ${c.first} · (div ${c.div}) · 跌破中樞 ${c.brk} · 全部成立 ${c.all}<br>`+
  T(r.breaks.slice().reverse(),['time','close','zd','zg','width','hot','pivot','top','vol','first','fire'])}

let swOpen=true;function swtoggle(){swOpen=!swOpen;swload()}
async function swclear(){if(!confirm('清除掃描結果？（K 線快取保留）'))return;await fetch('/api/sweep/clear');swload()}
async function sw(){const o=pcollect();
 const lbl=$('sl').value||Object.entries(o).map(([k,v])=>k.split('.').slice(-2).join('.')+'='+v).join(' ')||'預設';
 const r=await (await fetch('/api/sweep/start?d='+$('sd').value+'&m='+$('sm').value+'&l='+encodeURIComponent(lbl)+'&o='+encodeURIComponent(Object.keys(o).length?JSON.stringify(o):''))).json();
 if(r.error){alert(r.error);return}if(!r.started){alert('已有掃描在跑');return}setTimeout(swload,1500)}
async function swload(){const o=$('swout');const r=await (await fetch('/api/sweep')).json();
 const runs=r.runs&&r.runs.length?'<h2 style=margin-top:10px>歷次比較</h2>'+T(r.runs,['label','days','time','n','total','A','B','C','D','E','F','G']):'';
 if(!r.status){o.innerHTML='尚未執行'+runs;return}
 let h='<b>'+r.status+'</b>'+runs;
 if(swOpen){
  if(r.summary&&r.summary.length)h+='<h2 style=margin-top:10px>各引擎彙整</h2>'+T(r.summary,['engine','n','win','exp','pf','best','worst']);
  if(r.results&&r.results.length)h+='<h2 style=margin-top:10px>每檔事件</h2>'+T(r.results,['symbol','peak_day','pump','dump','trades','R_A','R_B','R_C','R_D','R_E','R_F','R_G']);
  if(r.trades&&r.trades.length)h+='<h2 style=margin-top:10px>最大單筆</h2>'+T(r.trades,['symbol','time','engine','side','entry','exit','r','reason']);
 }else h+='<div class=meta>（明細已收合）</div>';
 o.innerHTML=h;
 if(!r.status.startsWith('完成')&&!r.status.startsWith('失敗'))setTimeout(swload,5000)}

let PS=[],PRE={},LIVE={};
function ptoggle(){const p=$('pform');p.style.display=p.style.display=='none'?'':'none';if(!PS.length)pform()}
function pclear(){document.querySelectorAll('.pv').forEach(i=>i.value='')}
function pfill(form){pclear();for(const [k,v] of Object.entries(form||{})){const el=document.querySelector('.pv[data-k="'+k+'"]');if(el)el.value=v}}
async function pmeta(q){const r=await (await fetch('/api/presets'+(q||''))).json();PRE=r.presets||{};LIVE=r.live||{};
 $('psel').innerHTML='<option value="">— 載入組合 —</option>'+Object.keys(PRE).map(n=>`<option>${n}</option>`).join('');
 const n=Object.keys(LIVE).length;
 $('plivebox').innerHTML=n?`實盤目前覆蓋 <b>${n}</b> 項：`+Object.entries(LIVE).map(([k,v])=>k.split('.').slice(-1)+'='+v).join('、'):'實盤目前使用程式預設值';}
async function pload(){if(!PS.length)await pform();const n=$('psel').value;if(!n)return;pfill(PRE[n]);$('pform').style.display=''}
async function psave(){const n=prompt('組合名稱：');if(!n)return;
 await pmeta('?act=save&name='+encodeURIComponent(n)+'&form='+encodeURIComponent(JSON.stringify(pcollect())));alert('已存：'+n)}
async function pdel(){const n=$('psel').value;if(!n||!confirm('刪除組合 '+n+'？'))return;await pmeta('?act=del&name='+encodeURIComponent(n))}
async function plive(){const f=pcollect();if(!Object.keys(f).length){alert('表單是空的');return}
 if(!confirm('把這 '+Object.keys(f).length+' 項套用到實盤？立即生效，重佈後仍有效。'))return;
 await pmeta('?act=live&form='+encodeURIComponent(JSON.stringify(f)));alert('已套用到實盤');load()}
async function plivereset(){if(!confirm('實盤參數回到程式預設？'))return;await pmeta('?act=live&form=%7B%7D');alert('已回預設');load()}
async function pform(){if(!PS.length)PS=await (await fetch('/api/params')).json();
 await pmeta();
 let g='',h='';for(const s of PS){if(s.g!=g){g=s.g;h+=(h?'</div>':'')+'<div class=pgrp><h3>'+g+'</h3>'}
  h+=`<div class=p><label>${s.label} <span class=meta>(${s.default}${s.unit?' '+s.unit:''})</span></label>
   <input class=pv data-k="${s.k}" type=number step="${s.step}" placeholder="${s.default}"><div class=d>${s.help}</div></div>`}
 $('pform').innerHTML=h+'</div>'}
function pcollect(){const o={};document.querySelectorAll('.pv').forEach(i=>{if(i.value!=='')o[i.dataset.k]=Number(i.value)});return o}
async function sigclear(){if(!confirm('清除訊號紀錄？（已下單紀錄保留）'))return;await fetch('/api/signals/clear');load()}
load();swload().catch(()=>{});pmeta().catch(()=>{});setInterval(load,60000);</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        try: body, ct = self._route()
        except Exception as e:
            traceback.print_exc()
            body, ct = json.dumps(dict(error=f"{type(e).__name__}: {e}"), ensure_ascii=False).encode(), "application/json; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(body)
    def _route(self):
        if self.path == "/api/state":
            s = store.get()
            s["live_overrides"] = presets.live()
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
            store.update(signals=[]); body, ct = b'{"ok":true}', "application/json; charset=utf-8"
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

if __name__ == "__main__":
    try:
        _p = presets.boot()
        if _p.get("live"): print("套用實盤參數覆蓋:", _p.get("live_name") or "自訂", _p["live"])
    except Exception as e: print("preset boot:", e)
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8080")); print("listening", port)
    HTTPServer(("0.0.0.0", port), H).serve_forever()
