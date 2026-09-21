# 清單 r31 → r33 差異的行為測試。所有出場都用 fx.trigger() 留下真的成交（清單用法第 5 點 r33），不直接改部位數量。
# 在專案根目錄執行：python -m tests.test_r33
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, since, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

def closed(): return (store.get().get("closed") or [{}])[-1]

# =====================================================================
print("第 8 條 r32：成交明細的起始界線從開倉那張單的成交之後算，不用「開倉時間往前 5 秒」")
fx = fresh()
fx.open("XUSDT", "LONG", 30, entry=1.5); fx.price = 2.0
old = fx.trigger("XUSDT", "LONG", 30)                               # 開倉前幾毫秒：這個幣上一筆部位在 2.0 平掉（別的專案或上一筆）
fx.price = 1.0
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("R2", "（前提）開倉成交了、帳上有這筆", "XUSDT" in store.get().get("open", {}), f"skipped={rec.get('skipped')}")
fx.price = 0.95
t, e = run(lambda: fx.trigger("XUSDT", "LONG", 100))                # 交易所端停損觸發；前提失敗（沒開成倉）時不讓測試崩掉
t = t or {"time": 0, "id": old["id"], "price": "nan"}
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("R2", "（前提）兩筆平倉成交真的都在明細裡、時間相差不到 5 秒", e is None and old["time"] >= t["time"] - 5000 and old["id"] != t["id"], f"{old['time']} {t['time']} err={e}")
check("R2", "出場價只看開倉之後的成交（0.949），不含開倉前那筆（2.0）", c.get("exit") == float(f"{float(t['price']):.6g}"),
      f"exit={c.get('exit')} 應為 {t['price']}")

fx = fresh(hedge=True)
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.price = 1.2; B.market_order("XUSDT", "SELL", 50)                 # 別的專案在同幣開空單：SELL、positionSide=SHORT
fx.price = 0.95
t, e = run(lambda: fx.trigger("XUSDT", "LONG", 100))
t = t or {"price": "nan"}
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("R2", "（前提）雙向模式：我的 LONG 真的開成、也真的被觸發平掉", e is None, f"err={e}")
check("R2", "（前提）雙向模式：交易所上真的有別人的 SHORT 開倉成交（方向同樣是 SELL）",
      any(x["side"] == "SELL" and x.get("positionSide") == "SHORT" for x in fx.trades) and fx.qty("XUSDT", "SHORT") == 50)
check("R2", "雙向模式：別人 SHORT 的 SELL 成交不能算成我 LONG 的平倉（看成交明細的 positionSide）",
      c.get("exit") == float(f"{float(t['price']):.6g}"), f"exit={c.get('exit')} 應為 {t['price']}")

# =====================================================================
print("第 8 條 r33：pending 時間戳缺值——不能拋錯，也不能「當 0 歲」靜靜卡住")
fx = fresh()
fx.open("XUSDT", "LONG", 30, entry=1.5); fx.price = 2.0; old = fx.trigger("XUSDT", "LONG", 30)   # 這個幣很久以前的平倉成交
fx.price = 1.0; B.market_order("XUSDT", "BUY", 100)                 # 送出去的單其實成交了，但 pending 的時間戳是 None
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=None, base_qty=0)})
main._rc["t"] = 0; main.reconcile(force=True)
own = store.get().get("open", {}).get("XUSDT") or {}
# 這是要測的行為，不是前提（清單用法第 5 點 r36：前提是用來排除「沒走到」，不是用來放要測的東西）
check("R3", "時間戳缺值、交易所上有部位 → 認領回來（走逐幣確認，不是當 0 歲卡住）", bool(own) and not store.get().get("pending"), f"own={bool(own)}")
check("R3", "認領後的部位時間戳是數字（`.get(鍵, 預設)` 擋不住值是 None）", isinstance(own.get("ts"), (int, float)) and not isinstance(own.get("ts"), bool), f"ts={own.get('ts')}")   # 測試自己判斷，不借用被測程式的 num()
fx.price = 0.95
t, e = run(lambda: fx.trigger("XUSDT", "LONG", 100))
t = t or {"price": "nan"}
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("R3", "出場價只看認領之後的成交，不把這個幣歷史上的平倉算進來", c.get("exit") == float(f"{float(t['price']):.6g}"),
      f"exit={c.get('exit')} 應為 {t['price']}（舊的那筆是 {old['price']}）")

fx = fresh()
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts="壞掉", base_qty=0)})
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("R3", "（前提）對帳跑完", e is None, f"err={e}")
check("R3", "時間戳是非數字、交易所上沒部位 → 判定逾時並講明，不留著佔名額", not store.get().get("pending"),
      f"pending={store.get().get('pending')}")
check("R3", "講明原因的通知恰好一則", *one("ℹ️ XUSDT 引擎C 的 pending 沒有有效的時間戳"))

finish()
