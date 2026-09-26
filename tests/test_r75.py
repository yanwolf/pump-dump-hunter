# 清單 r74 → r75 差異的行為測試：重啟前後。用真的存檔、重新載入模組模擬重啟（清單第 8 條 r35），不手寫假的帳本。
# 在專案根目錄執行：python -m tests.test_r75
import importlib, threading
from tests.harness import (one, B, TG, BACKFILL_JOBS, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def restart():
    """服務重啟：狀態檔留著，模組重新載入，記憶體裡排好的背景工作全部消失；接著跑一輪背景迴圈的開頭。"""
    importlib.reload(store); main._rc["t"] = 0
    BACKFILL_JOBS.clear()
    if isinstance(getattr(main, "_resumed", None), dict): main._resumed.clear()
    B.klines = lambda *a, **k: []
    run(lambda: main.tick(dict(watch={}, last={}, last_scan=time.time())))   # 跟背景迴圈一樣形狀；last_scan＝現在，這一輪不掃描
def opened(fx):
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return bool(own()) and fx.qty("XUSDT", "LONG") == 100
def kinds(): return [getattr(j, "__qualname__", "") for j in BACKFILL_JOBS]

print("部分出場：損益與「還沒認領」存進狀態檔；重啟後重新排補登")
fx = fresh()
check("E0", "（前提）開倉成交", opened(fx))
fx.trades_visible[None] = 0.0; fx.price = 1.2
if fx.qty("XUSDT", "LONG") >= 40: fx.trigger("XUSDT", "LONG", 40)
main._rc["t"] = 0; main.reconcile(force=True)
check("E2", "（前提）App 減碼 40、成交明細還看不到 → 這段記未知、記下還沒認領、排了補登",
      own().get("qty") == 60 and own().get("unclaimed") and len(BACKFILL_JOBS) >= 1)
restart()
p = own()
check("E2", "重啟後：部分出場那段（數量、損益未知）還在", (p.get("partials") or [{}])[-1].get("qty") == 40 and (p.get("partials") or [{}])[-1].get("pnl", "缺") is None,
      f"partials={p.get('partials')}")
check("E2", "重啟後：「還沒認領 40」還在", sum(x.get("qty", 0) for x in p.get("unclaimed") or []) == 40, f"unclaimed={p.get('unclaimed')}")
check("E1", "重啟後：部分出場補登重新排了", any("schedule_partial_backfill" in k for k in kinds()), f"排程 {kinds()}")
fx.trades_visible[None] = 1.0
for j in list(BACKFILL_JOBS): j()
check("E1", "重新排的補登跑完：那段換成實際損益、清掉還沒認領", (own().get("partials") or [{}])[-1].get("pnl") is not None and not own().get("unclaimed"),
      f"{own().get('partials')} unclaimed={own().get('unclaimed')}")

print("進場成交價：還原時讀回來；查不到的重啟後重新排補登（要存開倉單號）")
fx = fresh()
check("E0", "（前提）開倉成交", opened(fx))
f0 = own().get("fill")
restart()
check("E3", "重啟後：進場成交價讀回來了（均價比對、損益都靠它）", f0 is not None and own().get("fill") == f0, f"重啟前 {f0} 重啟後 {own().get('fill')}")
fx = fresh(); fx.strip_avg = lambda p: p.get("reduceOnly") != "true"; fx.strip_query = True; fx.hide_fills = True
check("E0", "（前提）開倉成交、回應與查單都沒有均價、成交明細看不到", opened(fx))
check("E1", "（前提）進場成交價記未知、排了進場補登", own().get("fill") is None and len(BACKFILL_JOBS) >= 1)
check("E1", "開倉單號存進部位（重啟後重新排補登要用）", own().get("entry_oid") is not None, f"{ {k: own().get(k) for k in ('entry_oid', 'fill')} }")
restart()
check("E1", "重啟後：進場補登重新排了", any("schedule_entry_backfill" in k for k in kinds()), f"排程 {kinds()}")
for k in list(fx.trades_visible): fx.trades_visible[k] = 1.0
for j in list(BACKFILL_JOBS): j()
check("E1", "重新排的進場補登跑完：部位有進場成交價", own().get("fill") is not None, f"fill={own().get('fill')}")

print("出場成交價：查不到的重啟後重新排補登")
fx = fresh()
check("E0", "（前提）開倉成交", opened(fx))
fx.strip_avg = lambda p: p.get("reduceOnly") == "true"; fx.strip_query = True; fx.hide_fills = True
r, e = run(lambda: main.trade_action("close", "XUSDT"))
check("E1", "（前提）平倉了、出場價未知、排了出場補登", closed().get("symbol") == "XUSDT" and closed().get("exit") is None and len(BACKFILL_JOBS) >= 1)
check("E1", "已平倉紀錄上記著「補登還沒完成」（重啟後重新排要用）", closed().get("backfill_pending") is True, f"{ {k: closed().get(k) for k in ('exit', 'backfill_pending')} }")
restart()
check("E1", "重啟後：出場補登重新排了", any("schedule_exit_backfill" in k for k in kinds()), f"排程 {kinds()}")
for k in list(fx.trades_visible): fx.trades_visible[k] = 1.0
for j in list(BACKFILL_JOBS): j()
check("E1", "重新排的出場補登跑完：紀錄補上出場價、清掉「補登還沒完成」", closed().get("exit") is not None and not closed().get("backfill_pending"),
      f"{ {k: closed().get(k) for k in ('exit', 'backfill_pending')} }")
restart()
check("E1", "補登完成後再重啟：不會再排（不重複補）", closed().get("exit") is not None and not kinds(), f"排程 {kinds()}")   # 先要求真的補登完成了

print("正式路徑真的開 daemon 執行緒（框架預設不開，這一項用框架另存的真的那個）")
fx = fresh()
from tests import harness
real = getattr(harness, "REAL_START_BACKFILL", None)
check("E4", "（前提）框架另存了真的排程函式", callable(real))
done = threading.Event()
if callable(real): real(done.set)
done.wait(3)
check("E4", "正式路徑：真的開了一條 daemon 執行緒去跑", done.is_set() and harness.REAL_THREADS["n"] >= 1, f"{harness.REAL_THREADS if hasattr(harness, 'REAL_THREADS') else '沒有計數'}")
if hasattr(harness, "REAL_THREADS"): harness.REAL_THREADS["allowed"] += 1   # 這一項是刻意開的，不算進全套檢查

finish()
