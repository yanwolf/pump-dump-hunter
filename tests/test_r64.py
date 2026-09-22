# 清單 r62 → r64 差異的行為測試：交易所端已平倉的說明有自己的位置，不借用風控欄位（本專案沒有斷路器，通知裡也不能出現那類字樣）。
# 在專案根目錄執行：python -m tests.test_r64
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
RISK_WORDS = ("暫停真實", "模擬單", "風控", "斷路", "當日停損")
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def opened(fx):
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return bool(own()) and fx.qty("XUSDT", "LONG") == 100
def stop_hit(fx):
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = 0.9
    if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)
def flag(): return [m for m in TG if m.startswith("🏁 XUSDT")]

print("第 8 條 r63：三種出場的平倉通知（🏁）——說明都在通知本身，沒有借用任何風控字樣")
fx = fresh()
check("B1", "（前提）開倉成交", opened(fx))
stop_hit(fx); main._rc["t"] = 0; main.reconcile(force=True)
check("B1", "（前提）正常停損出場結帳了", closed().get("by") == "停損單")
check("B1", "正常停損出場：通知寫「停損單觸發出場 @ 價格」，沒有風控字樣", *one("🏁 XUSDT 引擎C 停損單觸發出場 @ 0.9"))
check("B1", "正常停損出場：沒有任何風控字樣", flag() and not any(w in flag()[0] for w in RISK_WORDS), f"{flag()}")

fx = fresh()
check("B1", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)
main._rc["t"] = 0; main.reconcile(force=True)
check("B1", "（前提）被重開那筆結帳了", "別的部位" in (closed().get("by") or ""))
check("B1", "被重開：通知本身寫「別的部位、沒有動它」，沒有風控字樣", flag() and "別的部位" in flag()[0] and "沒有動它" in flag()[0]
      and not any(w in flag()[0] for w in RISK_WORDS), f"{flag()}")

fx = fresh()
check("B1", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.inject.append(dict(path="/fapi/v1/userTrades", times=50, kind="http", code=503, body="busy"))
main._rc["t"] = 0; main.reconcile(force=True)
check("B1", "（前提）結帳了、成交明細查不到（注入觸發）", closed().get("symbol") == "XUSDT" and any(f["path"] == "/fapi/v1/userTrades" for f in fx.fired))
check("B1", "出場價未知：通知本身寫「成交明細查不到，記未知」，沒有風控字樣", flag() and "記未知" in flag()[0]
      and not any(w in flag()[0] for w in RISK_WORDS), f"{flag()}")

print("手動平倉回應：「這次沒有送平倉單」是回應自己的句子（msg），不是錯誤欄位、也沒有風控字樣")
fx = fresh()
check("B1", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)
r, e = run(lambda: main.trade_action("close", "XUSDT"))
check("B1", "（前提）走到「交易所端已平掉、沒送單」", isinstance(r, dict) and "沒有送平倉單" in (r.get("msg") or ""), f"{r}")
check("B1", "回應是 ok＋msg（不是 error），msg 裡沒有風控字樣", r.get("ok") is True and not r.get("error") and not any(w in r["msg"] for w in RISK_WORDS), f"{r}")

finish()
