# 實盤出場管理 vs 回測 一致性測試：python test_parity.py（不連網，全部用假交易所）
import sys, types, os, random
import tempfile; os.environ["DATA_DIR"]=tempfile.mkdtemp()
import config as C; C.API_KEY="x"
fake=types.ModuleType("binance"); sys.modules["binance"]=fake
S=dict(bars=[], now=0, pos_qty=0, orders=[], stops={}, sid=0)
fake.round_qty=lambda s,q: f"{q:.0f}"
def klines(sym, tf, limit): return [b for b in S["bars"] if b["t"] <= S["now"]][-limit:]
fake.klines=klines
def mo(sym, side, qty, reduce_only=False):
    S["orders"].append(("MKT",side,qty)); S["pos_qty"]-=float(qty); return dict(avgPrice=str(S["bars_cur"]["c"]))
fake.market_order=mo
def so(sym, side, qty, px):
    S["sid"]+=1; S["stops"][S["sid"]]=px; return dict(orderId=S["sid"])
fake.stop_order=so
fake.cancel_order=lambda s,i: S["stops"].pop(i,None)
fake.user_trades=lambda s: []
import store, manager, backtest, signals
manager._now = lambda: S["now"]
from signals import Signal

def run_case(name, eid, side, bars, sig_i, entry, stop):
    # --- 回測 ---
    tf_ms = manager.TF_MS[manager.tf_of(eid)]
    def eng(k,i): return Signal(eid, side, entry, stop, "t") if i==sig_i else None
    backtest.ENGINES[eid]=eng
    bt = backtest.simulate(bars, (eid,))[0]
    # --- 實盤管理：逐根推進時間，每根收盤後跑一次 ---
    S.update(bars=bars, orders=[], stops={}, pos_qty=100)
    pos=dict(engine=eid, side=side, entry=entry, stop=stop, qty=100, bar_t=bars[sig_i]["t"], last_t=bars[sig_i]["t"],
             r_unit=abs(entry-stop), ts=bars[sig_i]["t"], stop_id=None)
    live=None
    for i in range(sig_i+1, len(bars)):
        S["now"]=bars[i]["t"]+tf_ms; S["bars_cur"]=bars[i]
        d=1 if side=="LONG" else -1
        adv=bars[i]["l"] if d>0 else bars[i]["h"]
        if (adv<=pos["stop"] if d>0 else adv>=pos["stop"]):   # 交易所停損單觸發
            live=("stop", i-sig_i); break
        if manager.step("X", pos)=="closed": live=("time", i-sig_i); break
    print(f"{name:26s} 回測: {bt['reason']:5s} 第{bt['bars']:3d}根 | 實盤管理: {live[0]:5s} 第{live[1]:3d}根 | 減碼={pos.get('tp1',False)} 保本={pos.get('be',False)} 最後停損={pos['stop']:.5f}",
          "✅" if (bt['reason'],bt['bars'])==live else "❌")

def mk(n, ms, path):
    bars=[]; p=path[0]
    for i in range(n):
        o=p; p=path[min(i,len(path)-1)]
        bars.append(dict(t=1_700_000_000_000+i*ms, o=o, h=max(o,p)*1.001, l=min(o,p)*0.999, c=p, v=1))
    return bars
# NIL 實況：G 做多進場後一路陰跌但沒碰停損 → 60 根時間出場
nil=[0.06508 - 0.00005*i for i in range(40)] + [0.06308]*60
run_case("G 陰跌不觸停損（NIL 情境）","G","LONG", mk(100,60000,[0.065]*5+nil), 4, 0.06508, 0.0610)
# G 衝到 1R 保本、1.5R 後追蹤，回落打到追蹤停損
up=[0.1+0.0008*i for i in range(15)]+[0.112-0.001*i for i in range(30)]
run_case("G 1R 保本→追蹤停損","G","LONG", mk(60,60000,[0.1]*3+up), 2, 0.1, 0.096)
# C 做空到 1R 減碼一半，之後 72 根時間出場
dn=[1.0-0.012*i for i in range(10)]+[0.88]*90
run_case("C 1R 減碼→時間出場","C","SHORT", mk(100,300000,[1.0]*3+dn), 2, 1.0, 1.1)
# 直接打停損
run_case("C 直接停損","C","SHORT", mk(20,300000,[1.0]*3+[1.0+0.02*i for i in range(15)]), 2, 1.0, 1.1)
# 隨機路徑 200 組
random.seed(7); ok=tot=0
import io, contextlib
for t in range(200):
    eid=random.choice("CFG"); side="SHORT" if eid in "CF" else "LONG"; ms=manager.TF_MS[manager.tf_of(eid)]
    p=[1.0]; 
    for i in range(160): p.append(p[-1]*(1+random.gauss(0,0.012)))
    st=1.0*(1.06 if side=="SHORT" else 0.94)
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): run_case("r",eid,side,mk(160,ms,p),2,1.0,st)
    tot+=1; ok+= "✅" in buf.getvalue()
print(f"隨機路徑 {ok}/{tot} 筆出場原因與出場根數完全一致")
