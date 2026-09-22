# 交易所市價單數量上限（-4005 Quantity greater than max quantity，2026-09-22 13:06 SKRUSDT 實單發生）。
# 在專案根目錄執行：python -m tests.test_maxqty
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def entries(fx): return [c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly") != "true"]

print("開倉數量超過交易所市價單上限")
fx = fresh(); fx.market_max["XUSDT"] = 60
check("MQ", "（前提）交易所規格表裡有市價單上限 60", B.filters("XUSDT").get("mmax") == 60, f"{B.filters('XUSDT')}")
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
check("MQ", "（前提）開倉真的送出了一張進場單", len(entries(fx)) == 1, f"err={e} skipped={rec and rec.get('skipped')}")
check("MQ", "送出的數量壓到上限 60（不是 100 被 -4005 拒絕、訊號丟掉）", entries(fx) and float(entries(fx)[0][2]["quantity"]) == 60,
      f"{[c[2].get('quantity') for c in entries(fx)]}")
stops = [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
check("MQ", "帳上與停損都是 60", own().get("qty") == 60 and len(stops) == 1 and float(stops[0]["quantity"]) == 60, f"帳上 {own().get('qty')} 停損 {stops}")
check("MQ", "通知講明數量被壓到上限", *one("ℹ️ XUSDT 引擎C 數量 100 超過交易所市價單上限 60"))

print("平倉數量超過交易所市價單上限（例如認領回來的大部位）：分批平")
fx = fresh(); fx.open("XUSDT", "LONG", 100); fx.market_max["XUSDT"] = 60
store.update(open={"XUSDT": dict(engine="C", side="LONG", qty=100, entry=1.0, fill=1.0, stop=0.9, base_qty=0, ts=int(time.time() * 1000),
                                 bar_t=300_000, last_t=300_000)})
r, e = run(lambda: main.manage("close", "XUSDT"))
closes = [c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true"]
check("MQ", "（前提）平倉單真的送出了", len(closes) >= 1, f"err={e}")
check("MQ", "每一張平倉單都不超過上限", closes and all(float(c[2]["quantity"]) <= 60 for c in closes), f"{[c[2]['quantity'] for c in closes]}")
check("MQ", "分兩次平完、結帳", fx.qty("XUSDT", "LONG") == 0 and bool(store.get().get("closed")), f"交易所剩 {fx.qty('XUSDT', 'LONG')} 回應={r}")

finish()
