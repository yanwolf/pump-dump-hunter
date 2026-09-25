# 清單 r72 → r73 差異的行為測試：部分出場查不到成交明細時，界線沒推進 → 最後出場把那段混進去。
# 在專案根目錄執行：python -m tests.test_r73
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
JOBS = []
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def opened(fx):
    JOBS.clear()
    if hasattr(manager, "_start_backfill"): manager._start_backfill = JOBS.append
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return bool(own()) and fx.qty("XUSDT", "LONG") == 100
def app_reduce_hidden(fx, qty=40, px=1.2):
    """在 App 減碼：交易所上真的成交，但成交明細這時還看不到（非同步寫入）。"""
    fx.trades_visible[None] = 0.0; fx.price = px
    if fx.qty("XUSDT", "LONG") >= qty: fx.trigger("XUSDT", "LONG", qty)
    main._rc["t"] = 0; main.reconcile(force=True)
def stop_out(fx, px=0.9):
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = px
    if fx.qty("XUSDT", "LONG") > 0: fx.trigger("XUSDT", "LONG", fx.qty("XUSDT", "LONG"))
    main._rc["t"] = 0; main.reconcile(force=True)
def last_close_trade(fx): return next((t for t in reversed(fx.trades) if t["side"] == "SELL"), {})

print("App 減碼那段稍後才出現，接著交易所端平掉剩下的")
fx = fresh()
check("D0", "（前提）開倉成交", opened(fx))
mark0 = own().get("trade_mark")
app_reduce_hidden(fx)
part = (own().get("partials") or [{}])[-1]
check("D1", "（前提）對帳偵測到減少 40、那段成交明細還看不到 → 這段損益記未知", own().get("qty") == 60 and part.get("pnl", "缺") is None, f"{part}")
check("D1", "界線沒推進（那段還沒被認領），而且記下「還沒認領的減少量 40」", own().get("trade_mark") == mark0 and sum(x.get("qty", 0) for x in (own().get("unclaimed") or [])) == 40,
      f"trade_mark={own().get('trade_mark')} unclaimed={own().get('unclaimed')}")
fx.trades_visible[None] = 1.0                                         # 那段出現了（背景還沒補登）
stop_out(fx, 0.9)
c = closed(); t = last_close_trade(fx)
check("D2", "（前提）交易所端平掉剩下 60、兩段成交都在明細裡（1.2 那段 40、0.9 那段 60）", c.get("symbol") == "XUSDT"
      and sum(float(x["qty"]) for x in fx.trades if x["side"] == "SELL") == 100)
check("D2", "最後出場價只算平倉那 60（約 0.9），先跳過還沒認領的那 40（不被 1.2 拉偏）", c.get("exit") is not None and abs(c["exit"] - float(t.get("price", 0))) < 1e-9,
      f"exit={c.get('exit')} 平倉那筆 {t.get('price')}")

print("App 減碼那段的補登先完成（界線推進過去），再平倉")
fx = fresh()
check("D0", "（前提）開倉成交", opened(fx))
app_reduce_hidden(fx)
check("D4", "（前提）那段排了背景補登", len(JOBS) >= 1, f"排程 {len(JOBS)}")
fx.trades_visible[None] = 1.0
for j in list(JOBS): j()
part = (own().get("partials") or [{}])[-1]
check("D4", "補登：那段換成實際損益（成交在 1.2 附近）、界線推進過去、清掉「還沒認領」", part.get("pnl") is not None and abs((part.get("px") or 0) - 1.2) < 0.01
      and not own().get("unclaimed"), f"{part} unclaimed={own().get('unclaimed')}")
check("D4", "補登通知", *one("🧾 XUSDT 引擎C 部分出場成交價補登"))
stop_out(fx, 0.9)
c = closed()
check("D4", "之後平倉：出場價只算最後那 60、整筆損益＝兩段相加（已知）", c.get("exit") is not None and abs(c["exit"] - 0.9) < 0.01 and c.get("pnl") is not None,
      f"exit={c.get('exit')} pnl={c.get('pnl')}")

print("1R 減碼（程式自己送的單）那段稍後才出現，接著平倉")
fx = fresh()
check("D0", "（前提）開倉成交", opened(fx))
B.klines = lambda *a, **k: [dict(t=600_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]; manager._now = lambda: 10**15
fx.price = 1.1; fx.hide_fills = True                                  # 減碼單的成交明細先看不到
pp = dict(own()); run(lambda: manager.step("XUSDT", pp))
store.update(open={"XUSDT": pp}); fx.hide_fills = False
check("D1", "（前提）1R 減碼成交 50、那段成交明細還看不到", pp.get("tp1") and fx.qty("XUSDT", "LONG") == 50)
check("D1", "1R 減碼：記下「還沒認領的減少量 50」", sum(x.get("qty", 0) for x in (pp.get("unclaimed") or [])) == 50, f"unclaimed={pp.get('unclaimed')}")
for k in list(fx.trades_visible): fx.trades_visible[k] = 1.0
stop_out(fx, 0.95)
c = closed(); t = last_close_trade(fx)
check("D2", "1R 減碼後平倉：最後出場價只算最後那 50，不混進減碼那 50", c.get("exit") is not None and abs(c["exit"] - float(t.get("price", 0))) < 1e-9,
      f"exit={c.get('exit')} 最後那筆 {t.get('price')}")

print("最後一筆跨過帳上數量：分不出，記未知")
fx = fresh()
check("D0", "（前提）開倉成交", opened(fx))
fx.price = 1.05; fx._trade("XUSDT", "SELL", 30, 1.05, 0.0, "LONG")   # 界線之後多一筆 30（別的來源，數量跟帳上對不齊）
stop_out(fx, 0.9)
c = closed()
check("D3", "（前提）結帳了、界線之後的平倉成交是 30＋100（超過帳上 100）", c.get("symbol") == "XUSDT"
      and sum(float(x["qty"]) for x in fx.trades if x["side"] == "SELL") == 130)
check("D3", "跨過帳上數量 → 分不出哪幾張是這筆的，出場價記未知（不把 130 張平均當成出場價）", c.get("symbol") == "XUSDT" and c.get("exit") is None, f"exit={c.get('exit')}")

finish()
