# 清單 r84 → r86 差異的行為測試：執行時才長出來的狀態，重啟後讀不讀得回來。
# 用真的存檔、重新載入模組模擬重啟（清單第 8 條 r35），不手寫假的帳本。
# 在專案根目錄執行：python -m tests.test_r86
import importlib, json
from tests.harness import (one, B, TG, BACKFILL_JOBS, check, fresh, finish, main, manager, os, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def on_disk(): return json.load(open(os.path.join(os.environ["DATA_DIR"], "state.json"), encoding="utf-8"))
def restart():
    importlib.reload(store); main._rc["t"] = 0; BACKFILL_JOBS.clear()
    if isinstance(getattr(main, "_resumed", None), dict): main._resumed.clear()

print("讀寫兩邊都沒有過濾：從來沒出現過的鍵，重啟後照樣在（以後誰加了「只收預設值的鍵」，這項會失敗）")
fx = fresh()
store.update(brand_new_top_key={"x": 1})
check("I1", "（前提）新的最上層鍵寫進狀態檔了（寫的一方沒有白名單）", on_disk().get("brand_new_top_key") == {"x": 1})
restart()
check("I1", "重啟後：新的最上層鍵讀回來了（讀的一方沒有只收預設值的鍵）", store.get().get("brand_new_top_key") == {"x": 1}, f"{store.get().get('brand_new_top_key')}")
check("I1", "冷卻（cool）不在預設值裡——是執行時才長出來的鍵，crypto-screener 踩到的就是這種；下一個情境確認它重啟後讀得回來",
      "cool" not in getattr(store, "_DEFAULT", {"cool": None}))

print("冷卻：存在狀態的 cool 鍵（不在預設值裡），重啟後照樣生效")
fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("I1", "（前提）開倉成交", bool(own()))
for o in list(fx.algo_orders.values()):
    if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
fx.price = 0.9
if fx.qty("XUSDT", "LONG") > 0: fx.trigger("XUSDT", "LONG", fx.qty("XUSDT", "LONG"))
main._rc["t"] = 0; main.reconcile(force=True)
check("I1", "（前提）出場了、冷卻開始（重啟前）", not own() and manager.cooling("XUSDT", "C"), f"cool={store.get().get('cool')}")
restart()
check("I1", "重啟後：同引擎同幣照樣在冷卻中（不會一重啟就放行）", manager.cooling("XUSDT", "C"), f"cool={store.get().get('cool')}")

print("「已經決定出場、還沒平完」：待平倉、分段平倉、部位上執行時長出來的鍵，重啟後都在")
fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("I3", "（前提）開倉成交", bool(own()))
fx.fill_mode = lambda p: "partial" if p.get("reduceOnly") == "true" else "filled"
r, e = run(lambda: main.trade_action("close", "XUSDT"))
p = own()
check("I3", "（前提）手動平倉只成交一部分 → 記成待平倉、標記分段平倉（重啟前）", p.get("want_close") and p.get("close_partial") is True and 0 < fx.qty("XUSDT", "LONG") < 100,
      f"{ {k: p.get(k) for k in ('want_close', 'close_partial', 'qty')} }")
store.update(open={"XUSDT": dict(own(), brand_new_pos_key=7)})   # 部位上一個從來沒出現過的鍵
restart()
p = own()
check("I3", "重啟後：待平倉、分段平倉的標記、部位上的新鍵都在（寫的一方沒有白名單）",
      p.get("want_close") and p.get("close_partial") is True and p.get("brand_new_pos_key") == 7, f"{ {k: p.get(k) for k in ('want_close', 'close_partial', 'brand_new_pos_key')} }")
fx.fill_mode = None; B.klines = lambda *a, **k: []
left0 = fx.qty("XUSDT", "LONG")
manager.run()
check("I3", "重啟後的出場管理照樣重試平倉、把剩下的平掉（出場決定沒被忘掉）", left0 > 0 and fx.qty("XUSDT", "LONG") == 0 and not own(), f"重試前剩 {left0}、之後剩 {fx.qty('XUSDT', 'LONG')}")

finish()
