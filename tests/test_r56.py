# 清單 r54 → r56 差異的行為測試：「均價不同」要加上「查到這筆的平倉成交」才判定重開。
# 在專案根目錄執行：python -m tests.test_r56
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, preflight, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def stops(fx): return [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
def closes_after(fx, mark): return [c for c in fx.calls[mark:] if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true"]

def mismatched(fx):
    """開一筆真的部位，再把帳上的成交價改成 0.98——舊版程式記法不同（例如回應沒均價時用標記價）留下的持倉。交易所上均價是 1.001。"""
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    if own(): store.update(open={"XUSDT": dict(own(), fill=0.98)})   # 開倉沒成功（例如突變下）時不能憑空寫一筆假的紀錄
    return dict(own())

# =====================================================================
print("均價有出入、但沒有平倉成交：是同一筆，照常管理（不能被當成重開結帳、撤停損）")
fx = fresh(); p = mismatched(fx); sid = p.get("stop_id")
e = fx.pos.get(("XUSDT", "LONG"), [0, 0])[1]
check("Z2", "（前提）帳上成交價 0.98、交易所均價約 1.001，差超過容許誤差；交易所上部位還在、停損掛著",
      p.get("fill") == 0.98 and abs(e - 1.001) < 1e-6 and fx.qty("XUSDT", "LONG") == 100 and sid in fx.algo_orders)
main._rc["t"] = 0; main.reconcile(force=True)
check("Z2", "對帳：沒有平倉成交 → 部位照常在帳上、沒結帳", bool(own()) and not store.get().get("closed"), f"closed={len(store.get().get('closed') or [])}")
check("Z2", "對帳：停損沒被撤（撤了就是裸倉）", sid in fx.algo_orders)

fx = fresh(); p = mismatched(fx)
before = fx.qty("XUSDT", "LONG")
check("Z2", "（前提）手動平倉之前交易所上部位還在（100）", before == 100)
mark = len(fx.calls)
r, e2 = run(lambda: main.trade_action("close", "XUSDT"))
check("Z2", "手動平倉：均價有出入但沒被平過 → 照送平倉單、真的平掉", closes_after(fx, mark) and fx.qty("XUSDT", "LONG") == 0,
      f"平倉單 {len(closes_after(fx, mark))} 張、交易所剩 {fx.qty('XUSDT', 'LONG')} 回應={r}")

fx = fresh(); p = mismatched(fx)
fx.algo_orders.clear(); manager._missing["XUSDT"] = 2; B.klines = lambda *a, **k: []
check("Z2", "（前提）守衛執行前：停損不見、已經連續缺兩輪（這一輪就要補掛）", not stops(fx) and manager._missing.get("XUSDT") == 2)
manager.run()
check("Z2", "守衛：均價有出入但沒被平過 → 照樣補掛停損（不能讓它裸著）", len(stops(fx)) == 1, f"停損 {len(stops(fx))} 張")

# =====================================================================
print("均價不同、成交明細查不到：判斷不了 → 這輪不送任何單、不結帳")
fx = fresh(); p = mismatched(fx); sid = p.get("stop_id")
fx.inject.append(dict(path="/fapi/v1/userTrades", times=50, kind="http", code=503, body="busy"))
mark = len(fx.calls)
main._rc["t"] = 0; main.reconcile(force=True)
r, e2 = run(lambda: main.trade_action("close", "XUSDT"))
check("Z2", "（前提）成交明細真的查不到（注入在查成交明細時觸發）", any(f["path"] == "/fapi/v1/userTrades" for f in fx.fired))
check("Z2", "判斷不了：對帳不結帳、停損不撤", bool(own()) and not store.get().get("closed") and sid in fx.algo_orders)
check("Z2", "判斷不了：手動平倉這次不送單、回錯誤（不是當成沒了靜靜結帳）", not closes_after(fx, mark) and isinstance(r, dict) and bool(r.get("error")),
      f"平倉單 {len(closes_after(fx, mark))} 張 回應={r}")

# =====================================================================
print("真的重開（均價不同、而且查到原本那筆的平倉成交）：照樣判定，不動別人的部位")
fx = fresh(); p = mismatched(fx)
if own(): store.update(open={"XUSDT": dict(own(), fill=1.001)})     # 帳上成交價正確
for o in list(fx.algo_orders.values()): fx.algo_orders.pop(o["algoId"])
fx.price = 0.9
if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)  # 前提不成立時不能讓測試崩掉（r35；上一輪 test_r53 同一個錯）
fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)
check("Z2", "（前提）原本那筆的平倉成交在明細裡、交易所上是別人的 100（均價 1.2）", any(t["side"] == "SELL" for t in fx.trades)
      and fx.qty("XUSDT", "LONG") == 100)
main._rc["t"] = 0; main.reconcile(force=True)
c = (store.get().get("closed") or [{}])[-1]
check("Z2", "對帳：我們那筆照停損成交結帳（約 0.9）、別人的部位不進帳", c.get("symbol") == "XUSDT" and abs((c.get("exit") or 0) - 0.9) < 0.01 and not own(),
      f"closed={ {k: c.get(k) for k in ('symbol', 'exit')} } 帳上={bool(own())}")

# =====================================================================
print("部署前檢查：列出帳上成交價跟交易所均價對不上的持倉")
fx = fresh(); p = mismatched(fx)
res = preflight.check()
item = next((x for x in res if x["item"] == "帳上成交價與交易所均價"), {})
check("Z3", "（前提）自檢跑完、有這一項", bool(item), f"{[x['item'] for x in res]}")
check("Z3", "自檢列出對不上的那一筆（幣、帳上成交價、交易所均價）、有沒有平倉成交", item.get("status") == "warn" and "XUSDT" in item.get("msg", "")
      and "0.98" in item.get("msg", "") and "沒有平倉成交" in item.get("msg", ""), f"{item}")

finish()
