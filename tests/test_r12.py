# 清單 r10 → r12 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安（tests/fake_exchange.py）。
# 在專案根目錄執行：python -m tests.test_r12
import os, re, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["TRADE"] = "1"
from app import config as C
C.API_KEY = "k"; C.API_SECRET = "s"; C.USE_TESTNET = True
from app import binance as B, store, telegram
from tests.fake_exchange import FakeBinance

TG = []
telegram.send = lambda m: TG.append(m)
time.sleep = lambda s: None
from app import manager, main
manager.telegram.send = telegram.send; main.telegram.send = telegram.send

fails = []
def check(tag, name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} [{tag}] {name}" + (f"　{detail}" if detail else ""))
    if not cond: fails.append(tag)

def fresh(hedge=False, algo="ok"):
    """每個情境都從乾淨狀態開始。"""
    fx = FakeBinance(hedge=hedge, algo=algo).install()
    B._mode.update(hedge=None, t=0); B._F.clear()
    if hasattr(B, "_algo_ok"): B._algo_ok[0] = None
    if hasattr(B, "_algo"): B._algo.update(legacy_until=0)
    main._rc["t"] = 0
    manager._missing.clear()
    store.update(open={}, pending={}, closed=[], leftover={}, trades=[], signals=[], errors=[], exchange=[])
    TG.clear()
    return fx

def run(fn):
    try: return fn(), None
    except Exception as e: return None, e

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

# =====================================================================
print("第 1 條：Algo 端點退回條件")
fx = fresh()
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=1, kind="http", code=400,
                      body='{"code":-1013,"msg":"Filter failure: PRICE_FILTER"}'))
run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
r, e = run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
check("A1", "-1013（參數錯）不能被當成端點不存在；下一張照樣走 Algo 並成功", e is None and r.get("via") == "algo", f"err={e}")

fx = fresh()
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=1, kind="http", code=400,
                      body='{"code":-1000,"msg":"An unknown error occured while processing the request."}'))
run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
r, e = run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
check("A1", "-1000（暫時性）不能切到舊端點", e is None and r.get("via") == "algo", f"err={e}")

fx = fresh()
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=1, kind="http", code=404, body="Not Found"))
r, e = run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
check("A2", "一次 404 後舊端點回 -4120 → 要改回 Algo 重送，這張停損仍掛得上", e is None and r and r.get("via") == "algo", f"err={e}")
r, e = run(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
check("A2", "之後的條件單不能被永久鎖在舊端點", e is None and r and r.get("via") == "algo", f"err={e}")

# =====================================================================
print("第 3 條：進場單結果不明時保留 pending")
fx = fresh()
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=1, kind="timeout", then_execute=True))
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=0))
check("B1", "逾時（但其實成交）→ 不能當失敗清掉 pending", bool(store.get().get("pending")) or "XUSDT" in store.get().get("open", {}),
      f"pending={store.get().get('pending')} skipped={rec.get('skipped')}")
check("B1", "逾時 → 通知不能寫「下單失敗」", not any("下單失敗" in m for m in TG), f"{TG}")
main._rc["t"] = 0; main.reconcile(force=True)
own = store.get().get("open", {}).get("XUSDT")
check("B1", "下一輪對帳認領回來", bool(own), f"open={store.get().get('open')}")

fx = fresh()
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=1, kind="http", code=503, body="Service Unavailable"))
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=0))
main._rc["t"] = 0; main.reconcile(force=True)
check("B1", "5xx（沒成交）→ 第一輪對帳沒看到部位時，pending 仍保留（交易所可能還沒反映）",
      bool(store.get().get("pending")), f"pending={store.get().get('pending')}")

fx = fresh()
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=1, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=0))
check("B2", "4xx 明確拒絕 → 可以清掉 pending、記下單失敗", not store.get().get("pending") and "下單失敗" in (rec.get("skipped") or ""))

# =====================================================================
print("第 7 條：認領／確認成交要用「幣＋方向」並拿交易所的數量均價")
fx = fresh(hedge=False)
fx.open("XUSDT", "SHORT", 250, entry=1.2)                     # 單向空單：positionAmt = -250
store.update(pending={"XUSDT": dict(engine="C", side="SHORT", time="t", entry=1.25, stop=1.35, ts=int(time.time()*1000))})   # 訊號價 1.25 ≠ 成交均價 1.2
main._rc["t"] = 0; main.reconcile(force=True)
own = store.get().get("open", {}).get("XUSDT") or {}
check("C2", "單向空單（負數）認領 → 數量取絕對值 250", own.get("qty") == 250, f"own={own}")
check("C2", "認領 → 成交均價用交易所的 1.2（不是訊號價 1.25）", own.get("fill") == 1.2, f"own={own}")
check("C2", "認領 → 立刻補掛停損（不等守衛 3 輪）", any(o["symbol"] == "XUSDT" for o in fx.algo_orders.values()),
      f"掛單={list(fx.algo_orders.values())}")
for _ in range(3): manager.run()                              # 守衛要連 3 輪才補掛，跑滿 3 輪才會碰到 qty
errs = [x for x in store.get().get("errors", []) + TG if "'qty'" in x or "KeyError" in x or "程式錯誤" in x]
check("C2", "認領後跑滿 3 輪出場管理與守衛，不因缺欄位出錯（錯誤區與推播都要看）", not errs, f"{errs[-2:]}")
check("C2", "認領後 3 輪內停損一定在交易所上", any(o["symbol"] == "XUSDT" for o in fx.algo_orders.values()))

fx = fresh(hedge=True)
fx.open("XUSDT", "SHORT", 80)                                  # 別的專案的反向倉
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=int(time.time()*1000))})
main._rc["t"] = 0; main.reconcile(force=True)
check("C2", "雙向：只有別人的 SHORT → 不能認領成自己的 LONG", "XUSDT" not in store.get().get("open", {}))

fx = fresh(hedge=True)
fx.open("XUSDT", "SHORT", 80)
store.update(open={"XUSDT": dict(engine="C", side="LONG", qty=100, entry=1.0, stop=0.9)})
r, e = run(lambda: main.manage("close", "XUSDT"))
mk = [c for c in fx.calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
check("C1", "手動平倉：自己的 LONG 已不在、只剩別人的 SHORT → 根本不送平倉單、回錯誤",
      not mk and isinstance(r, dict) and r.get("error"), f"回應={r} 送出的市價單={len(mk)}")
check("C1", "手動平倉：自己的 LONG 已不在 → 帳上紀錄不能被移除（交給對帳處理）", "XUSDT" in store.get().get("open", {}))
fx = fresh(hedge=True)
fx.open("XUSDT", "SHORT", 80); fx.open("XUSDT", "LONG", 50)
r, e = run(lambda: main.manage("close", "XUSDT"))
mk = [c for c in fx.calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
check("C1", "手動平倉孤兒倉：雙向同幣兩側都有、帳上沒紀錄 → 分不出是哪一側，不能自己挑", not mk, f"回應={r}")

# =====================================================================
print("第 8 條：成交後的例外不能改寫已成交")
fx = fresh()
orig_push = store.push
def bad_push(k, v):
    if k == "trades": raise RuntimeError("disk full")
    return orig_push(k, v)
store.push = bad_push
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=0)))
store.push = orig_push
check("D1", "成交後記錄步驟丟例外 → place 不往外拋、回傳已成交", e is None and rec and rec.get("executed") is True, f"err={e}")
check("D1", "成交後例外 → 部位仍在帳上、停損照掛",
      "XUSDT" in store.get().get("open", {}) and any(o["symbol"] == "XUSDT" for o in fx.algo_orders.values()))

# =====================================================================
print("第 8 條：平倉單送出後一定要看結果")
def setup_open(fx, side="LONG", qty=100):
    fx.open("XUSDT", side, qty)
    o = B.stop_order("XUSDT", "SELL" if side == "LONG" else "BUY", qty, 0.9 if side == "LONG" else 1.1)
    store.update(open={"XUSDT": dict(engine="C", side=side, qty=qty, entry=1.0, fill=1.0, stop=0.9, r_unit=0.1,
                                     stop_id=o["orderId"], stop_via=o["via"], ts=int(time.time()*1000), bar_t=0, last_t=0)})
    return o

fx = fresh(); so = setup_open(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("reduceOnly") == "true",
                      times=5, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
r, e = run(lambda: main.manage("close", "XUSDT"))
check("F1", "手動平倉被拒 → 部位仍在帳上", "XUSDT" in store.get().get("open", {}), f"回應={r}")
check("F1", "手動平倉被拒 → 停損沒被撤", so["orderId"] in fx.algo_orders)
check("F1", "手動平倉被拒 → 不記成已平倉", not store.get().get("closed"))
check("F1", "手動平倉被拒 → 回應是錯誤、不是「已平倉」", isinstance(r, dict) and r.get("error"), f"{r}")

fx = fresh(); so = setup_open(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("reduceOnly") == "true",
                      times=1, kind="timeout", then_execute=True))
r, e = run(lambda: main.manage("close", "XUSDT"))
check("F1", "平倉逾時但其實成交 → 查部位確認沒了才記帳，並撤停損",
      bool(store.get().get("closed")) and so["orderId"] not in fx.algo_orders and "XUSDT" not in store.get().get("open", {}),
      f"回應={r} closed={len(store.get().get('closed') or [])}")

fx = fresh(hedge=True); so = setup_open(fx, qty=100)
fx.pos[("XUSDT", "LONG")][0] = 60                            # 帳上 100，交易所實際 60（例如在 App 手動減過）
pos = dict(store.get()["open"]["XUSDT"])
r, e = run(lambda: manager.close_now("XUSDT", pos, "時間"))
check("F2", "雙向超量被拒 → 用交易所實際數量 60 重送、平掉", fx.qty("XUSDT", "LONG") == 0 and e is None, f"err={e}")

fx = fresh(); setup_open(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("reduceOnly") == "true",
                      times=10, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
p = dict(store.get()["open"]["XUSDT"]); p.update(bar_t=300_000, last_t=300_000); store.update(open={"XUSDT": p})   # bar_t 不能用 0（會被當成沒填）
bars = [dict(t=i * 300_000, o=1, h=1.0, l=0.95, c=1, v=1) for i in range(1, 80)]   # 超過 C 的 72 根 → 時間出場
manager.B.klines = lambda *a, **k: list(bars)
manager._now = lambda: 10**15
for i in range(6): manager.run()                 # 第 1 輪時間出場被拒；之後每輪由 retry_close 重試
n_alert = len([m for m in TG if "XUSDT" in m])
check("F1", "時間出場平倉一直被拒 → 依節奏告警（6 輪只發第 1、5 次，共 2 則）", n_alert == 2, f"告警 {n_alert} 則")
check("F1", "時間出場平倉被拒 → 部位留在帳上", "XUSDT" in store.get().get("open", {}))

# =====================================================================
print("第 8 條：失敗狀態的收尾")
fx = fresh(); so = setup_open(fx)
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="DELETE", times=1, kind="http", code=503, body="busy"))
pos = dict(store.get()["open"]["XUSDT"])
run(lambda: manager.move_stop("XUSDT", pos, 1.0))
check("E2", "移損時撤舊單失敗 → 當下就告警（第 1 次），不等平倉；原因要是注入的 503",
      any("撤不掉" in m and "第 1 次" in m and "busy" in m for m in TG), f"{TG}")

fx = fresh(); setup_open(fx)
pos = dict(store.get()["open"]["XUSDT"]); pos.update(want_stop=1.0, want_fail=3, guard_fail=2)
manager._missing["XUSDT"] = 2
manager.record_close("XUSDT", pos, "停損單", info={})
check("E3", "失敗中部位被平掉 → 發收尾通知（移損／補掛失敗狀態隨平倉結束）",
      any("隨平倉結束" in m or "收尾" in m for m in TG), f"{TG}")
check("E4", "平倉後補掛連續次數歸零，同幣下次進場不會接著數", not manager._missing.get("XUSDT"))

# =====================================================================
print("第 8 條：交易所端數量減少（本專案沒有交易所停利單；手動在 App 減碼也會造成）")
fx = fresh(); setup_open(fx, qty=100)
fx.pos[("XUSDT", "LONG")][0] = 60
main._rc["t"] = 0; main.reconcile(force=True)
own = store.get().get("open", {}).get("XUSDT") or {}
check("G1", "對帳偵測數量從 100 變 60 → 更新帳上數量並通知", own.get("qty") == 60 and any("減少" in m for m in TG), f"qty={own.get('qty')} {TG}")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{sorted(set(fails))}")
sys.exit(1 if fails else 0)
