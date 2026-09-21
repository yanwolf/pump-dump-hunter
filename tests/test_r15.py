# 清單 r13 → r15 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安。
# 在專案根目錄執行：python -m tests.test_r15
import os, sys, tempfile, time
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

ALL_ERR = []                                          # 每個情境的錯誤區與推播都收起來，最後檢查有沒有程式錯誤
def fresh(hedge=False, algo="ok"):
    ALL_ERR.extend(store.get().get("errors", [])); ALL_ERR.extend(TG)
    fx = FakeBinance(hedge=hedge, algo=algo).install()
    B._mode.update(hedge=None, t=0); B._F.clear(); B._algo.update(legacy_until=0.0)
    main._rc["t"] = 0; manager._missing.clear()
    store.update(open={}, pending={}, closed=[], leftover={}, trades=[], signals=[], errors=[], exchange=[])
    TG.clear()
    return fx

def run(fn):
    try: return fn(), None
    except Exception as e: return None, e

def since(fx, mark):
    """第 7 種測錯方式：只算測試動作開始之後的呼叫。"""
    return fx.calls[mark:]

def market_closes(calls):
    return [c for c in calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"
            and (c[2].get("reduceOnly") == "true" or c[2].get("positionSide"))]

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

def own_pos(fx, qty=100, base=0, side="LONG", stop_on_exchange=True):
    sid = via = None
    if stop_on_exchange:
        o = B.stop_order("XUSDT", "SELL" if side == "LONG" else "BUY", qty, 0.9 if side == "LONG" else 1.1)
        sid, via = o["orderId"], o["via"]
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=1.0, stop=0.9 if side == "LONG" else 1.1, r_unit=0.1,
             stop_id=sid, stop_via=via, ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000, base_qty=base)
    store.update(open={"XUSDT": p}); return p

# =====================================================================
print("第 1 條 r15：退回期間 Algo 查詢一直失敗，守衛要改用舊端點結果")
fx = fresh(algo="404")                              # 沒有 Algo 服務的環境；服務剛重啟，退回旗標是空的
fx.open("XUSDT", "LONG", 100)
own_pos(fx, stop_on_exchange=False)                 # 舊端點上的停損不見了
p = dict(store.get()["open"]["XUSDT"]); p.update(stop_via="legacy"); store.update(open={"XUSDT": p})
manager.B.klines = lambda *a, **k: []
mark = len(fx.calls)
for _ in range(3): manager.run()
checked_legacy = any(c[1] == "/fapi/v1/openOrders" for c in since(fx, mark))
check("H1", "守衛真的查了舊端點（第 8 種：不能靠「整個跳過」過關）", checked_legacy)
check("H1", "3 輪內在舊端點補掛停損", len(fx.legacy_orders) == 1, f"舊端點掛單={list(fx.legacy_orders.values())}")
r, e = run(lambda: B.open_stops("XUSDT"))
check("H1", "Algo 一直 404 → open_stops 回 ok=True（用舊端點結果）", r and r[1] is True, f"回傳={r} err={e}")

# =====================================================================
print("第 2 條 r15：全量部位表回空清單")
fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" not in p, times=1, kind="empty"))
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("H2", "全量表空清單 → 部位不能被判平倉", "XUSDT" in store.get().get("open", {}) and not store.get().get("closed"), f"err={e}")
check("H2", "全量表空清單 → 停損不能被撤", so["stop_id"] in fx.algo_orders)
check("H2", "全量表空清單 → 有逐幣再查一次（第 8 種：確認真的做了檢查）",
      any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") == "XUSDT" for c in fx.calls))

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=5, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" not in p, times=5, kind="empty"))
mark = len(fx.calls)
p = dict(store.get()["open"]["XUSDT"])
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))   # 直接測平倉確認那段（第 8 種：manage 會在更早就因空清單放棄）
check("H2", "（前提）平倉單確實送出、被拒", len(market_closes(since(fx, mark))) >= 1)
check("H2", "平倉被拒 + 確認用的全量表回空 → 不能當成已平倉", not store.get().get("closed") and so["stop_id"] in fx.algo_orders,
      f"err={e} closed={store.get().get('closed')}")

# =====================================================================
print("第 3 條 r14：送單前記基準數量")
fx = fresh(); fx.open("XUSDT", "LONG", 40, entry=0.8)          # 送單前同側已有 40（共用帳號／之前的孤兒倉）
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=1, kind="timeout", then_execute=True))
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
main._rc["t"] = 0; main.reconcile(force=True)
own = store.get().get("open", {}).get("XUSDT") or {}
check("H3", "認領數量 = 現在 140 − 基準 40 = 100", own.get("qty") == 100, f"own.qty={own.get('qty')}")
check("H3", "認領時記下基準數量，之後平倉／對帳要扣", own.get("base_qty") == 40, f"base_qty={own.get('base_qty')}")
check("H3", "有基準部位時，通知講明均價是合併過的", any("合併" in m for m in TG), f"{TG}")
stops = [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
check("H3", "認領後掛的停損數量是 100（不是 140）", stops and float(stops[-1]["quantity"]) == 100, f"{stops}")

# =====================================================================
print("第 3 條 r15：認領要在判斷「還在場」之前（走完整對帳流程）")
fx = fresh(); fx.open("XUSDT", "SHORT", 250, entry=1.2)
store.update(pending={"XUSDT": dict(engine="C", side="SHORT", time="t", entry=1.25, stop=1.35, ts=int(time.time()*1000), base_qty=0)})
main._rc["t"] = 0; main.reconcile(force=True)
stops = [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
check("H4", "完整對帳：認領後同一輪部位仍在帳上", "XUSDT" in store.get().get("open", {}))
check("H4", "完整對帳：同一輪沒有被記成平倉", not store.get().get("closed"))
check("H4", "完整對帳：剛掛的停損沒有被撤", len(stops) == 1, f"{stops}")

# =====================================================================
print("第 7 條 r15：確認自己那一側要扣掉基準數量")
fx = fresh(); fx.open("XUSDT", "LONG", 140); own_pos(fx, qty=100, base=40)
mark = len(fx.calls)
r, e = run(lambda: main.manage("close", "XUSDT"))
check("H5", "單向：交易所 140（自己 100＋別人 40）→ 手動平倉只平 100，別人的 40 留著",
      fx.qty("XUSDT", "LONG") == 40, f"剩 {fx.qty('XUSDT','LONG')} 回應={r} err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 40); own_pos(fx, qty=100, base=40)    # 自己的 100 已被停損，只剩別人的 40
mark = len(fx.calls)
r, e = run(lambda: main.manage("close", "XUSDT"))
check("H5", "單向：自己那一側扣掉基準後沒有了 → 根本不送平倉單", not market_closes(since(fx, mark)) and fx.qty("XUSDT", "LONG") == 40,
      f"送出={len(market_closes(since(fx, mark)))} 剩 {fx.qty('XUSDT','LONG')} 回應={r}")
check("H5", "單向：沒送單 → 回錯誤（不是已平倉）", isinstance(r, dict) and r.get("error"), f"{r}")

fx = fresh(); fx.open("XUSDT", "LONG", 40); own_pos(fx, qty=100, base=40)
main._rc["t"] = 0; main.reconcile(force=True)
check("H5", "對帳：扣掉基準後自己的 0 → 判成已平倉（不是「數量減少 100→40」）",
      "XUSDT" not in store.get().get("open", {}) and not any("減少" in m for m in TG), f"{TG}")

fx = fresh(); fx.open("XUSDT", "LONG", 140); p = own_pos(fx, qty=100, base=40)
r, e = run(lambda: manager.close_now("XUSDT", dict(p), "時間"))
check("H5", "時間出場（close_now）也要扣基準：只平自己的 100", fx.qty("XUSDT", "LONG") == 40 and e is None,
      f"剩 {fx.qty('XUSDT','LONG')} err={e}")

# =====================================================================
print("第 8 條 r14／r15：送平倉單那一刻交易所上有沒有停損")
fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=1, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
mark = len(fx.calls)
r, e = run(lambda: main.manage("close", "XUSDT"))
check("H6", "（前提）平倉單真的送出、被拒", len(market_closes(since(fx, mark))) >= 1)
check("H6", "平倉被拒 → 停損仍在（撤停損在確認平掉之後）", so["stop_id"] in fx.algo_orders)

seen = []
def spy(fx):
    """記錄每張平倉單送出那一刻，交易所上這個幣有幾張停損。每個情境各包各的，不能沿用上一個模擬交易所。"""
    orig = fx._market
    def wrapped(url, p):
        if p.get("reduceOnly") == "true" or p.get("positionSide"):
            seen.append(len([o for o in list(fx.algo_orders.values()) + list(fx.legacy_orders.values()) if o["symbol"] == p["symbol"]]))
        return orig(url, p)
    fx._market = wrapped

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx); spy(fx); seen.clear()
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=1, kind="http", code=400,
                      body='{"code":-2021,"msg":"Order would immediately trigger."}'))
p = dict(store.get()["open"]["XUSDT"])
r, e = run(lambda: manager.retry_stop("XUSDT", dict(p, want_stop=1.0)))
check("H8", "移損遇 -2021 → 送平倉單那一刻，交易所上仍有停損", seen and all(n >= 1 for n in seen), f"送單時停損張數={seen} err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx, stop_on_exchange=False); spy(fx); seen.clear()
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=5, kind="http", code=400,
                      body='{"code":-2021,"msg":"Order would immediately trigger."}'))
for _ in range(3): manager.run()
check("H8", "守衛補掛遇 -2021（價格已穿過停損）→ 直接平倉，不是只告警", fx.qty("XUSDT", "LONG") == 0,
      f"剩 {fx.qty('XUSDT','LONG')} 推播={TG[-2:]}")

# =====================================================================
print("第 8 條 r14：待平倉期間只有「每輪重試」那條路送單")
fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx)
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("type") == "MARKET",
                      times=20, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
bars = [dict(t=i * 300_000, o=1, h=1.0, l=0.95, c=1, v=1) for i in range(1, 80)]
manager.B.klines = lambda *a, **k: list(bars)
manager._now = lambda: 10**15
manager.run()                                        # 第 1 輪：時間出場被拒 → 待平倉
mark = len(fx.calls)
manager.run()                                        # 第 2 輪：只能由 retry_close 送
n2 = len(market_closes(since(fx, mark)))
check("H7", "待平倉期間每輪只送一張平倉單（出場判斷不再觸發）", n2 == 1, f"第 2 輪送了 {n2} 張")

# =====================================================================
print("用法第 5 點 r15：通知在 try 裡的錯誤不能被吞")
ALL_ERR.extend(store.get().get("errors", [])); ALL_ERR.extend(TG)
bugs = [x for x in ALL_ERR if any(k in x for k in ("NameError", "AttributeError", "TypeError", "KeyError", "is not defined"))]
check("H10", "所有情境的錯誤區與推播裡，沒有程式錯誤", not bugs, f"{bugs[:3]}")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{sorted(set(fails))}")
sys.exit(1 if fails else 0)
