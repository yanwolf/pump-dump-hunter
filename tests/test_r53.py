# 清單 r51 → r53 差異的行為測試：拿鎖前讀出的部位物件。
# 每一條「先讀出部位、中間有 I/O、最後傳進會送單的函式」的路徑，都跑「平掉後同檔重開」（使用者指定）。
# 兩種重開：(甲) 我們自己的程式重開——引擎鎖要擋住（附拿掉鎖的對照組）；(乙) 交易所端重開（別的專案、App）——鎖擋不到，
# 要靠「確認交易所上那一筆就是帳上那一筆」（均價一致）。
# 在專案根目錄執行：python -m tests.test_r53
import contextlib, threading
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def orders_after(fx, mark):
    """動作之後送出的所有單（市價單、條件單），不含查詢。"""
    return [c for c in fx.calls[mark:] if c[0] == "POST" and c[1] in ("/fapi/v1/order", "/fapi/v1/algoOrder")]
def cancels_after(fx, mark): return [c for c in fx.calls[mark:] if c[0] == "DELETE"]

class NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def acquire(self, *a, **k): return True
    def release(self): pass

@contextlib.contextmanager
def lock_removed(on):
    real = getattr(manager, "ENGINE_LOCK", None)
    if on and real is not None: manager.ENGINE_LOCK = main.ENGINE_LOCK = NoLock()
    try: yield
    finally:
        if real is not None: manager.ENGINE_LOCK = main.ENGINE_LOCK = real

def pause_at(match):
    """第一次符合 match 的請求送出前停住；回傳 (繼續, 停住了)。"""
    go, stopped = threading.Event(), threading.Event()
    orig = B._get
    def hook(path, params=None, base=None, signed=False):
        if match(path, params or {}) and not stopped.is_set(): stopped.set(); go.wait(5)
        return orig(path, params, base, signed)
    B._get = hook
    return go, stopped

def open_ours(fx):
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return dict(own())

def exchange_reopen(fx):
    """交易所端：我們的停損觸發（真的成交一筆），然後別人在同一檔同方向開了新部位（均價不同）。
    前提不成立（我們的部位根本沒開成，例如突變下）時不能讓測試崩掉（r35）：沒有部位就跳過觸發。"""
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = 0.9
    if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)
    fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)

BARS_1R = [dict(t=600_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]           # 會觸發 1R 減碼＋移到成本的 K 棒

# =====================================================================
print("(甲) 我們自己的程式平掉後同檔重開：出場管理停在查 K 線，另一條對帳＋重新開倉（引擎鎖要擋住）")
for no_lock in (False, True):
    tag = "對照組：鎖拿掉" if no_lock else "有鎖"
    fx = fresh(); old = open_ours(fx)
    B.klines = lambda *a, **k: list(BARS_1R); manager._now = lambda: 10**15
    stopped_evt = threading.Event(); go = threading.Event(); real_k = B.klines
    def slow_klines(*a, **k):
        if not stopped_evt.is_set(): stopped_evt.set(); go.wait(5)
        return real_k(*a, **k)
    B.klines = slow_klines
    with lock_removed(no_lock):
        a = threading.Thread(target=manager.run, daemon=True); a.start(); stopped_evt.wait(5)
        # 停損在交易所端觸發，接著我們的程式對帳（結掉舊的）、同一檔重新開倉
        for o in list(fx.algo_orders.values()):
            if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
        # 在「同一個價格」重開：均價比對（交易所端那道防線）分不出新舊，只剩引擎鎖——對照組才證明得了鎖（r52：對照組沒出事先懷疑情境）
        fx.price = 0.9
        if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)       # 前提不成立時不能讓測試崩掉（r35）
        fx.price = 1.0
        b = threading.Thread(target=lambda: (main._rc.update(t=0), main.reconcile(force=True),
                                             main.place("XUSDT", "C", Sig(entry=1.0, stop=0.95), dict(SZ), dict(time="t", bar_t=900_000))), daemon=True)
        b.start(); b.join(1.0); mark = len(fx.calls); go.set(); a.join(5); b.join(5)
    new = own()
    n_entry = len([c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly") != "true"])
    check("Y3", f"（前提・{tag}）出場管理真的停在查 K 線、兩條都跑完", stopped_evt.is_set() and not a.is_alive() and not b.is_alive())
    # 前提看「重新開倉的進場單送出了」，不看帳上——帳上會不會被舊的蓋掉正是要測的東西（前提不能量被測的結果）
    check("Y3", f"（前提・{tag}）同一檔真的重新送出了第二張進場單", n_entry == 2, f"進場單 {n_entry} 張")
    stops = [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
    ok = fx.qty("XUSDT", "LONG") == 100 and len(stops) == 1 and float(stops[0]["triggerPrice"]) == 0.95 and new.get("stop_id") == stops[0]["algoId"]
    if no_lock:
        check("Y3", "（對照組）鎖拿掉時，舊部位的出場管理會動到新部位（減碼、停損被換掉、或帳上紀錄被舊的蓋掉）", not ok,
              f"交易所 {fx.qty('XUSDT', 'LONG')} 停損 {[(o['algoId'], o['triggerPrice']) for o in stops]} 帳上 stop_id={new.get('stop_id')}")
    else:
        check("Y3", "有鎖：新部位完整（數量 100、只有它自己那張停損、帳上記的也是它）", ok,
              f"交易所 {fx.qty('XUSDT', 'LONG')} 停損 {[(o['algoId'], o['triggerPrice']) for o in stops]} 帳上 stop_id={new.get('stop_id')}")

# =====================================================================
print("(乙) 交易所端平掉後同檔重開（別的專案或 App）：鎖擋不到，每條會送單的路徑都不能動到新部位")

def scenario(name, setup, act, pause):
    fx = fresh(); old = open_ours(fx)
    check("Y4", f"（前提・{name}）我們的部位開好了、均價約 1.0", abs((old.get("fill") or 0) - 1.0) < 0.01, f"fill={old.get('fill')}")
    setup(fx)
    go, stopped = pause_at(pause)
    t = threading.Thread(target=lambda: run(act), daemon=True); t.start(); stopped.wait(5)
    exchange_reopen(fx)                                                # 讀出部位之後、送單之前，交易所上換成別人的部位
    before = (fx.qty("XUSDT", "LONG"), fx.pos.get(("XUSDT", "LONG"), [0, 0])[1])   # 放行之前記下（前提不能量被測的結果）
    mark = len(fx.calls); go.set(); t.join(5)
    check("Y4", f"（前提・{name}）真的停在讀出部位之後的 I/O；放行前交易所上是別人的 100（均價 1.2）", stopped.is_set() and not t.is_alive()
          and before[0] == 100 and abs(before[1] - 1.2) < 1e-9, f"放行前 {before}")
    sent = orders_after(fx, mark)
    check("Y4", f"{name}：不能對別人的新部位送任何單（減碼、平倉、掛停損）", not sent and fx.qty("XUSDT", "LONG") == 100,
          f"送出 {[(c[1][-10:], c[2].get('type'), c[2].get('quantity')) for c in sent]} 交易所剩 {fx.qty('XUSDT', 'LONG')}")
    return fx

B_1R = lambda: setattr(B, "klines", lambda *a, **k: list(BARS_1R))
fx = scenario("出場判斷（1R 減碼＋移到成本）",
              lambda fx: (B_1R(), setattr(manager, "_now", lambda: 10**15)),
              manager.run, lambda p, q: p == "/fapi/v1/openAlgoOrders")       # 守衛查條件單時停住，接著才是出場判斷
fx = scenario("停損守衛補掛",
              lambda fx: (setattr(B, "klines", lambda *a, **k: []), [fx.algo_orders.pop(k) for k in list(fx.algo_orders)], manager._missing.update(XUSDT=2)),
              manager.run, lambda p, q: p == "/fapi/v1/openAlgoOrders")
fx = scenario("待平倉重試",
              lambda fx: (setattr(B, "klines", lambda *a, **k: []), store.update(open={"XUSDT": dict(own(), want_close="時間")})),
              manager.run, lambda p, q: p == "/fapi/v1/openAlgoOrders" or (p == "/fapi/v2/positionRisk" and "symbol" in q))
fx = scenario("網頁手動平倉",
              lambda fx: None,
              lambda: main.trade_action("close", "XUSDT"), lambda p, q: p == "/fapi/v2/positionRisk")
c = (store.get().get("closed") or [{}])[-1]
check("Y4", "網頁手動平倉：我們那筆照交易所的停損成交結帳（出場約 0.9），不是去平別人的", c.get("symbol") == "XUSDT" and c.get("exit") is not None
      and abs(c["exit"] - 0.9) < 0.01, f"closed={ {k: c.get(k) for k in ('symbol', 'exit', 'by')} }")

print("(乙) 之後的對帳：帳上那筆要結掉，別人的部位不能被當成我們的")
fx2 = fresh(); open_ours(fx2); exchange_reopen(fx2)
check("Y4", "（前提）對帳之前：帳上是我們那筆、交易所上是別人的 100（均價 1.2）", bool(own()) and fx2.qty("XUSDT", "LONG") == 100
      and abs(fx2.pos.get(("XUSDT", "LONG"), [0, 0])[1] - 1.2) < 1e-9)
main._rc["t"] = 0; main.reconcile(force=True)
c = (store.get().get("closed") or [{}])[-1]
check("Y4", "對帳：交易所上換成別人的部位（均價不同）→ 我們那筆結帳（出場用停損成交 0.9）", c.get("symbol") == "XUSDT" and c.get("exit") is not None
      and abs(c["exit"] - 0.9) < 0.01, f"closed={ {k: c.get(k) for k in ('symbol', 'exit', 'by')} }")
check("Y4", "對帳：別人的部位不會變成我們帳上的（沒有被認領、沒有掛停損）", not own() and not [o for o in fx2.algo_orders.values() if o["symbol"] == "XUSDT"],
      f"帳上 {own()}")

finish()
