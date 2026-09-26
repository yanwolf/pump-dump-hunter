# 清單 r76 → r77 差異的行為測試：進場補登跑完之前部位就平掉、接著重啟；補登記號的生命週期。
# 用真的存檔、重新載入模組模擬重啟（清單第 8 條 r35）。
# 在專案根目錄執行：python -m tests.test_r77
import importlib
from tests.harness import (one, B, TG, BACKFILL_JOBS, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def kinds(): return [getattr(j, "__qualname__", "") for j in BACKFILL_JOBS]
def restart():
    importlib.reload(store); main._rc["t"] = 0; BACKFILL_JOBS.clear()
    if isinstance(getattr(main, "_resumed", None), dict): main._resumed.clear()
    B.klines = lambda *a, **k: []
    run(lambda: main.tick(dict(watch={}, last={}, last_scan=time.time())))
def open_unknown_fill(fx):
    """開倉成交，但回應與查單都沒有均價、成交明細也還看不到 → 進場成交價記未知、排進場補登。"""
    fx.strip_avg = lambda p: p.get("reduceOnly") != "true"; fx.strip_query = True; fx.hide_fills = True
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    fx.strip_avg = None; fx.strip_query = False; fx.hide_fills = False
    return bool(own()) and own().get("fill") is None
def stop_out(fx):
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = 0.9
    if fx.qty("XUSDT", "LONG") > 0: fx.trigger("XUSDT", "LONG", fx.qty("XUSDT", "LONG"))
    main._rc["t"] = 0; main.reconcile(force=True)

print("進場補登跑完之前部位就平掉，接著重啟")
fx = fresh()
check("F1", "（前提）開倉成交、進場成交價未知、排了進場補登", open_unknown_fill(fx) and any("schedule_entry_backfill" in k for k in kinds()))
check("F1", "部位上記著「進場補登還沒完成」", own().get("entry_backfill_pending") is True, f"{ {k: own().get(k) for k in ('fill', 'entry_oid', 'entry_backfill_pending')} }")
stop_out(fx)
c = closed()
check("F1", "（前提）部位平掉、結帳了；已平倉紀錄上進場成交價仍未知、記號跟著帶過來", c.get("symbol") == "XUSDT" and c.get("fill") is None
      and c.get("entry_backfill_pending") is True, f"{ {k: c.get(k) for k in ('fill', 'entry_backfill_pending')} }")
restart()
check("F1", "重啟後：已平倉那筆的進場補登重新排了", any("schedule_entry_backfill" in k for k in kinds()), f"排程 {kinds()}")
for k in list(fx.trades_visible): fx.trades_visible[k] = 1.0
for j in list(BACKFILL_JOBS): j()
c = closed()
check("F1", "重新排的補登跑完：已平倉紀錄補上進場成交價、清掉記號", c.get("fill") is not None and not c.get("entry_backfill_pending"),
      f"{ {k: c.get(k) for k in ('fill', 'entry_backfill_pending')} }")
restart()
check("F1", "補登完成後再重啟：不會再排", c.get("fill") is not None and not kinds(), f"排程 {kinds()}")

print("進場補登放棄（一直查不到）：清掉記號，重啟後不再排")
fx = fresh()
check("F1", "（前提）開倉成交、進場成交價未知", open_unknown_fill(fx))
check("F1", "（前提）放棄之前記號是在的（不然「清掉了」在舊程式上會空的通過）", own().get("entry_backfill_pending") is True)
for j in list(BACKFILL_JOBS): j()                                     # 成交明細一直看不到 → 放棄
check("F1", "放棄 → 清掉記號、推一則補登失敗", not own().get("entry_backfill_pending") and one("⚠️ XUSDT 引擎C 進場成交價補登失敗")[0],
      f"記號={own().get('entry_backfill_pending')}")
restart()
check("F1", "放棄後重啟：不再排（記號清掉了，不會每次重啟都重查一輪）", bool(own()) and own().get("fill") is None and not kinds(), f"排程 {kinds()}")   # 先要求部位真的在

print("出場補登放棄：清掉記號（放棄之前記號是在的）")
fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.strip_avg = lambda p: p.get("reduceOnly") == "true"; fx.strip_query = True; fx.hide_fills = True
r, e = run(lambda: main.trade_action("close", "XUSDT"))
check("F2", "（前提）平倉了、出場價未知、放棄之前記號是在的", closed().get("symbol") == "XUSDT" and closed().get("exit") is None
      and closed().get("backfill_pending") is True)
for j in list(BACKFILL_JOBS): j()
check("F2", "出場補登放棄 → 清掉記號", closed().get("symbol") == "XUSDT" and not closed().get("backfill_pending"), f"記號={closed().get('backfill_pending')}")

print("「每個行程只重排一次」的旗標，框架每個情境都重設")
fx = fresh()
if isinstance(getattr(main, "_resumed", None), dict): main._resumed["done"] = True   # 舊版沒有這個旗標（r38：先加存在性前提）
check("F3", "（前提）旗標設上了（模擬前一支測試跑過「重排補登」）", getattr(main, "_resumed", {}).get("done") is True)
fx = fresh()
check("F3", "（前提）框架的還原清單裡有這個旗標", any(a == "_resumed" for m, a in __import__("tests.harness", fromlist=["RESET_STATE"]).RESET_STATE))
check("F3", "前一個情境留下「已經重排過」，下一個情境開始時清掉了", isinstance(getattr(main, "_resumed", None), dict) and not main._resumed.get("done"),
      f"{getattr(main, '_resumed', '舊版沒有')}")

finish()
