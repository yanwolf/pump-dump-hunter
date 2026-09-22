# 清單 r57 → r58 差異的行為測試：手動平倉遇到「交易所端已平掉」時，回應要講明這次沒有送單。
# 在專案根目錄執行：python -m tests.test_r58
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closes_after(fx, mark): return [c for c in fx.calls[mark:] if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true"]

def opened(fx):
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return bool(own()) and fx.qty("XUSDT", "LONG") == 100

def stop_hit(fx):
    """交易所端：停損觸發，真的成交一筆（出場約 0.9）。沒開成時不能讓測試崩掉。"""
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = 0.9
    if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)

print("手動平倉：交易所端停損已觸發（對帳還沒跑到）")
fx = fresh()
check("M58", "（前提）開倉成交", opened(fx))
stop_hit(fx)
check("M58", "（前提）按平倉之前：交易所上已經沒有部位、帳上還有那筆", fx.qty("XUSDT", "LONG") == 0 and bool(own()))
mark = len(fx.calls)
r, e = run(lambda: main.trade_action("close", "XUSDT"))
msg = (r or {}).get("msg") or (r or {}).get("error") or ""
check("M58", "這次沒有送平倉單、帳上那筆留給對帳結帳（r16 起的設計：手動平倉先查交易所，沒有自己的部位就交給對帳）",
      not closes_after(fx, mark) and bool(own()), f"平倉單 {len(closes_after(fx, mark))} 張")
check("M58", "回應講明：交易所上已經沒有這筆、不送單", "已經沒有自己的部位" in msg and "不送單" in msg, f"回應={msg}")

print("手動平倉：交易所端平掉後被別人重開（兩個證據都成立）")
fx = fresh()
check("M58", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)
check("M58", "（前提）按平倉之前：交易所上是別人的 100（均價 1.2）、帳上還是我們那筆", fx.qty("XUSDT", "LONG") == 100 and bool(own()))
mark = len(fx.calls)
r, e = run(lambda: main.trade_action("close", "XUSDT"))
msg = (r or {}).get("msg") or (r or {}).get("error") or ""
check("M58", "別人的部位沒被平掉、這次沒有送平倉單", not closes_after(fx, mark) and fx.qty("XUSDT", "LONG") == 100)
check("M58", "回應講明：沒有送平倉單，而且交易所上現在那張是別的部位、沒有動它", "沒有送平倉單" in msg and "別的部位" in msg, f"回應={msg}")

print("用法第 5 點 r58：確認 pump-dump-hunter 平倉前「部位查不到」是不送單（所以「應該送單」的測試不會因為查不到而通過）")
fx = fresh()
check("M58", "（前提）開倉成交（部位正常開好，只在平倉那一刻查不到）", opened(fx))
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=50, kind="empty"))
mark = len(fx.calls)
r, e = run(lambda: manager.close_now("XUSDT", dict(own()), "時間"))
check("M58", "（前提）平倉前的逐幣部位查詢真的查不到（注入觸發）", any(f["path"] == "/fapi/v2/positionRisk" for f in fx.fired))
check("M58", "部位查不到 → 不送平倉單、不結帳、交易所上的部位與停損都還在", not closes_after(fx, mark) and not store.get().get("closed")
      and fx.qty("XUSDT", "LONG") == 100 and bool([o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]),
      f"平倉單 {len(closes_after(fx, mark))} 張 err={type(e).__name__ if e else None}")

print("對照組：正常手動平倉（真的送單）——回應不能寫「沒有送單」")
fx = fresh()
check("M58", "（前提）開倉成交", opened(fx))
mark = len(fx.calls)
r, e = run(lambda: main.trade_action("close", "XUSDT"))
msg = (r or {}).get("msg") or ""
check("M58", "（前提）真的送出平倉單、交易所上平掉了", len(closes_after(fx, mark)) >= 1 and fx.qty("XUSDT", "LONG") == 0)
check("M58", "回應是「已平倉」，沒有寫「沒有送平倉單」", "已平倉" in msg and "沒有送" not in msg, f"回應={msg}")

finish()
