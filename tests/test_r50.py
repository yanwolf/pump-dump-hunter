# 清單 r48 → r50 差異的行為測試。
# 在專案根目錄執行：python -m tests.test_r50
import contextlib, threading
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def entries(fx): return [c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" and c[2].get("reduceOnly") != "true"]
def stops(fx): return [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]

class NoLock:
    """對照組用的假鎖：什麼都不擋（清單用法第 5 點 r50：要驗證交錯測試真的是鎖擋下的）。"""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def acquire(self, *a, **k): return True
    def release(self): pass

@contextlib.contextmanager
def lock_removed(on):
    real = getattr(manager, "ENGINE_LOCK", None)                   # 舊版沒有引擎鎖：什麼都不換（r38：先加存在性前提，r50 又漏一次）
    if on and real is not None: manager.ENGINE_LOCK = main.ENGINE_LOCK = NoLock()
    try: yield
    finally:
        if real is not None: manager.ENGINE_LOCK = main.ENGINE_LOCK = real

def paused_at(path_match):
    go, stopped = threading.Event(), threading.Event()
    orig = B._get
    def hook(path, params=None, base=None, signed=False):
        if path_match(path, params) and not stopped.is_set(): stopped.set(); go.wait(5)
        return orig(path, params, base, signed)
    B._get = hook
    return go, stopped

# =====================================================================
print("第 8 條 r50：同一檔再開一次倉——開倉函式本身要擋（不只呼叫端）")
fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
first = dict(own()); first_stop = first.get("stop_id")
check("X2", "（前提）第一筆開倉成交、帳上有這筆、停損掛上", bool(first) and first_stop in fx.algo_orders)
rec, e = run(lambda: main.place("XUSDT", "F", Sig(), dict(SZ), dict(time="t", bar_t=360_000)))
check("X2", "同一檔已有部位 → 第二次開倉不送單", len(entries(fx)) == 1, f"進場單 {len(entries(fx))} 張 err={e}")
check("X2", "不送單的原因講明", rec is not None and "已有" in (rec.get("skipped") or ""), f"skipped={rec and rec.get('skipped')}")
check("X2", "原本那筆沒被蓋掉（引擎、停損都還是原本的）", own().get("engine") == "C" and own().get("stop_id") == first_stop, f"{own().get('engine')} {own().get('stop_id')}")

fx = fresh()
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=int(time.time() * 1000), base_qty=0)})
check("X2", "（前提）呼叫之前，同一檔有一筆正在等確認的 pending", "XUSDT" in store.get().get("pending", {}))   # 呼叫之後 pending 會被處理掉，前提要在之前檢查
rec, e = run(lambda: main.place("XUSDT", "F", Sig(), dict(SZ), dict(time="t", bar_t=360_000)))
check("X2", "同一檔有 pending → 開倉函式本身不送單", not entries(fx), f"進場單 {len(entries(fx))} 張")

# =====================================================================
print("第 8 條 r50：鎖只讓兩張單排隊——拿到鎖之後要再檢查一次（兩條執行緒同時開同一檔）")
for no_lock in (False, True):
    tag = "對照組：鎖拿掉" if no_lock else "有鎖"
    fx = fresh()
    with lock_removed(no_lock):
        go, stopped = paused_at(lambda p, q: p == "/fapi/v2/positionRisk" and q and "symbol" in q)   # 第一張停在記基準那一步
        a = threading.Thread(target=lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)), daemon=True); a.start()
        stopped.wait(5)
        b = threading.Thread(target=lambda: main.place("XUSDT", "F", Sig(), dict(SZ), dict(time="t", bar_t=300_000)), daemon=True); b.start()
        b.join(1.0); go.set(); a.join(5); b.join(5)
    check("X2", f"（前提・{tag}）第一條真的停在送單前、兩條都跑完", stopped.is_set() and not a.is_alive() and not b.is_alive())
    if no_lock:
        check("X2", "（對照組）鎖拿掉時真的會送出兩張進場單——證明上面那項是鎖擋下的", len(entries(fx)) == 2, f"進場單 {len(entries(fx))} 張")
    else:
        check("X2", "有鎖：第二條等到第一條做完、拿到鎖後再檢查，不送第二張", len(entries(fx)) == 1, f"進場單 {len(entries(fx))} 張")
        check("X2", "有鎖：交易所上的部位與帳上一致、停損只有一張", fx.qty("XUSDT", "LONG") == own().get("qty") == 100 and len(stops(fx)) == 1,
              f"交易所 {fx.qty('XUSDT', 'LONG')} 帳上 {own().get('qty')} 停損 {len(stops(fx))} 張")

# =====================================================================
print("用法第 5 點 r50：r47 的交錯測試也要有對照組，另外確認動帳本的函式都套了鎖")
for no_lock in (False, True):
    fx = fresh()
    with lock_removed(no_lock):
        main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
        go, stopped = paused_at(lambda p, q: p == "/fapi/v2/positionRisk" and (not q or "symbol" not in q))
        main._rc["t"] = 0
        a = threading.Thread(target=lambda: main.reconcile(force=True), daemon=True); a.start(); stopped.wait(5)
        b = threading.Thread(target=lambda: main.trade_action("close", "XUSDT"), daemon=True); b.start()
        b.join(1.0); go.set(); a.join(5); b.join(5)
    n = len([c for c in store.get().get("closed") or [] if c.get("symbol") == "XUSDT"])
    if no_lock: check("X5", "（對照組）鎖拿掉時，對帳與手動平倉交錯會讓同一筆結兩次帳", n == 2, f"結帳 {n} 次")
    else: check("X5", "有鎖：同一筆只結一次帳", n == 1, f"結帳 {n} 次")
fx = fresh()
wrapped = {n: hasattr(f, "__wrapped__") for n, f in (("main.reconcile", main.reconcile), ("main.place", main.place), ("manager.run", manager.run))}
check("X5", "對帳、下單、出場管理三個函式本身都套了引擎鎖（只看標記證明不了鎖有效，要跟上面的對照組一起看）", all(wrapped.values()), f"{wrapped}")

finish()
