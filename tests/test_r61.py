# 清單 r59 → r61 差異的行為測試。
# 在專案根目錄執行：python -m tests.test_r61
import threading
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, preflight, run, since, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def stops(fx): return [o for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
def reduce_orders(fx, mark): return [c for c in fx.calls[mark:] if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true"]
def opened(fx):
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    return bool(own()) and fx.qty("XUSDT", "LONG") == 100
def stop_hit(fx):
    for o in list(fx.algo_orders.values()):
        if o["symbol"] == "XUSDT": fx.algo_orders.pop(o["algoId"])
    fx.price = 0.9
    if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)
TRADES_DOWN = lambda: dict(path="/fapi/v1/userTrades", times=50, kind="http", code=503, body="busy")

def manual_close_race(fx, trades_down):
    """手動平倉：讀部位表時部位還在；平倉送單前確認的那一刻，停損剛好在交易所觸發。"""
    go, stopped = threading.Event(), threading.Event(); orig = B._get
    def hook(path, params=None, base=None, signed=False):
        if path == "/fapi/v2/positionRisk" and params and "symbol" in params and not stopped.is_set():
            stopped.set(); go.wait(5)
        return orig(path, params, base, signed)
    B._get = hook
    out = {}
    t = threading.Thread(target=lambda: out.update(r=main.trade_action("close", "XUSDT")), daemon=True); t.start(); stopped.wait(5)
    stop_hit(fx)
    if trades_down: fx.inject.append(TRADES_DOWN())
    mark = len(fx.calls); go.set(); t.join(5)
    return (out.get("r") or {}), mark, stopped.is_set()

# =====================================================================
print("第 8 條 r60：手動平倉回應說「查到平倉成交」時，要真的查到")
fx = fresh()
check("A1", "（前提）開倉成交", opened(fx))
r, mark, st = manual_close_race(fx, trades_down=True)
msg = r.get("msg") or r.get("error") or ""
check("A1", "（前提）停在平倉送單前的確認、那一刻停損觸發了；成交明細查不到（注入觸發）", st and fx.qty("XUSDT", "LONG") == 0
      and any(f["path"] == "/fapi/v1/userTrades" for f in fx.fired))
check("A1", "沒送平倉單", not reduce_orders(fx, mark))
check("A1", "成交明細查不到 → 回應不能寫「查到平倉成交」，要寫查不到、出場價記未知", "查到平倉成交" not in msg and "查不到" in msg and "記未知" in msg,
      f"回應={msg}")

fx = fresh()
check("A1", "（前提）開倉成交", opened(fx))
r, mark, st = manual_close_race(fx, trades_down=False)
msg = r.get("msg") or ""
check("A1", "（前提）停在平倉送單前的確認、那一刻停損觸發了", st and fx.qty("XUSDT", "LONG") == 0)
check("A1", "成交明細查得到 → 回應寫照成交明細結帳、出場價、可能是停損觸發、這次沒有送平倉單（沒有重開，不提別的部位）",
      "照成交明細結帳" in msg and "0.9" in msg and "停損觸發" in msg and "沒有送平倉單" in msg and "別的部位" not in msg, f"回應={msg}")

# =====================================================================
print("第 8 條 r61：對帳替被重開的那筆結帳時，結帳原因與通知也要寫明")
fx = fresh()
check("A3", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.price = 1.2; fx.open("XUSDT", "LONG", 100, entry=1.2)
check("A3", "（前提）交易所上是別人的 100（均價 1.2）、帳上還是我們那筆", fx.qty("XUSDT", "LONG") == 100 and bool(own()))
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("A3", "結帳原因寫明交易所上那張是別的部位、沒有動它（不是寫死「停損單」）", "別的部位" in (c.get("by") or "") and "沒有動它" in (c.get("by") or ""),
      f"by={c.get('by')}")
check("A3", "平倉通知（🏁）也寫明", *one("🏁 XUSDT 引擎C", "別的部位", "沒有動它"))

fx = fresh()
check("A3", "（前提）開倉成交", opened(fx))
stop_hit(fx); fx.inject.append(TRADES_DOWN())
main._rc["t"] = 0; main.reconcile(force=True)
check("A3", "（前提）對帳結帳了、成交明細查不到（注入觸發）", closed().get("symbol") == "XUSDT" and any(f["path"] == "/fapi/v1/userTrades" for f in fx.fired))
check("A3", "出場價查不到 → 通知寫「出場價成交明細查不到，記未知」", *one("🏁 XUSDT 引擎C", "成交明細查不到", "記未知"))

# =====================================================================
print("用法第 5 點 r60：每個「某一步查不到」都要有直接測試（部位正常在帳上、只在那一步查不到）——補上沒有的 6 個")

# 平倉送出後的確認
fx = fresh()
check("A2", "（前提）開倉成交", opened(fx))
sid = own().get("stop_id"); orig = B._get
def after_send_empty(path, params=None, base=None, signed=False):
    sent = any(c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true" for c in fx.calls)
    if sent and path == "/fapi/v2/positionRisk" and params and "symbol" in params: return []
    return orig(path, params, base, signed)
B._get = after_send_empty
mark = len(fx.calls)
r, e = run(lambda: manager.close_now("XUSDT", dict(own()), "時間"))
check("A2", "（前提・平倉送出後確認）平倉單真的送出了、之後的部位確認查不到", len(reduce_orders(fx, mark)) >= 1)
check("A2", "平倉送出後確認查不到 → 不結帳、不撤停損（下一輪重試再確認）", not store.get().get("closed") and sid in fx.algo_orders,
      f"err={type(e).__name__ if e else None}")

# 守衛查掛單
fx = fresh()
check("A2", "（前提）開倉成交", opened(fx))
fx.algo_orders.clear(); manager._missing["XUSDT"] = 2
fx.inject.append(dict(path="/fapi/v1/openAlgoOrders", times=5, kind="http", code=503, body="busy"))
fx.inject.append(dict(path="/fapi/v1/openOrders", times=5, kind="http", code=503, body="busy"))
mark = len(fx.calls)
r, e = run(lambda: manager.ensure_stop("XUSDT", dict(own())))
check("A2", "（前提・守衛查掛單）查掛單真的失敗（注入觸發）", any(f["path"] == "/fapi/v1/openAlgoOrders" for f in fx.fired))
check("A2", "守衛查掛單查不到 → 這輪不補掛、回傳 query_failed、不往上計數", r == "query_failed" and not stops(fx) and manager._missing.get("XUSDT") == 2,
      f"回傳={r} 計數={manager._missing.get('XUSDT')}")

# 1R 減碼前確認
fx = fresh()
check("A2", "（前提）開倉成交", opened(fx))
B.klines = lambda *a, **k: [dict(t=600_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]; manager._now = lambda: 10**15; fx.price = 1.1
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=5, kind="empty"))
mark = len(fx.calls); pp = dict(own())
r, e = run(lambda: manager.step("XUSDT", pp))
check("A2", "（前提・1R 減碼前）K 棒真的到了 1R、減碼前的部位確認查不到（注入觸發）", any(f["path"] == "/fapi/v2/positionRisk" for f in fx.fired))
check("A2", "1R 減碼前確認查不到 → 不送減碼單、帳上數量不變", not reduce_orders(fx, mark) and pp.get("qty") == 100 and not pp.get("tp1"),
      f"減碼單 {len(reduce_orders(fx, mark))} 張 qty={pp.get('qty')}")

# 開倉後查單
fx = fresh(); fx.fill_mode = lambda p: "never" if p.get("reduceOnly") != "true" else "filled"
fx.inject.append(dict(path="/fapi/v1/order", method="GET", times=20, kind="http", code=503, body="busy"))
fx.inject.append(dict(path="/fapi/v1/order", method="DELETE", times=5, kind="http", code=503, body="busy"))
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
check("A2", "（前提・開倉後查單）開倉單送出了、回 NEW，查單與撤單都失敗（注入觸發）",
      any(f["method"] == "GET" and f["path"] == "/fapi/v1/order" for f in fx.fired) and any(x["type"] == "MARKET" for x in fx.orders.values()))
check("A2", "開倉後查單查不到 → 不記帳、保留 pending 交給對帳（結果不明）", not own() and "XUSDT" in store.get().get("pending", {}),
      f"own={bool(own())} pending={list(store.get().get('pending', {}))}")

# 手動平倉讀部位表
fx = fresh()
check("A2", "（前提）開倉成交", opened(fx))
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" not in p, times=5, kind="http", code=503, body="busy"))
mark = len(fx.calls)
r, e = run(lambda: main.trade_action("close", "XUSDT"))
check("A2", "（前提・手動平倉讀部位表）全量部位表真的查不到（注入觸發）", any(f["path"] == "/fapi/v2/positionRisk" for f in fx.fired))
check("A2", "手動平倉讀部位表查不到 → 不送單、回錯誤、部位與停損都在", not reduce_orders(fx, mark) and isinstance(r, dict) and bool(r.get("error"))
      and fx.qty("XUSDT", "LONG") == 100 and bool(stops(fx)), f"回應={r}")

# 自檢孤兒單：查得到就列（正常路徑）、逐幣查不到就列「無法確認」
fx = fresh()
fx.algo_orders[77] = dict(algoId=77, symbol="XUSDT", side="SELL", orderType="STOP_MARKET", quantity="100", positionSide="BOTH")
res = preflight.check(); item = next((x for x in res if x["item"] == "孤兒條件單"), {})
check("A2", "自檢孤兒單（正常路徑）：沒有部位的停損 → 列成孤兒", "77" in item.get("msg", "") and "無法確認" not in item.get("msg", ""), f"{item}")
fx = fresh()
check("A2", "（前提）開倉成交", opened(fx))
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" not in p, times=5, kind="empty"))
fx.inject.append(dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=5, kind="http", code=503, body="busy"))
preflight._last.clear() if hasattr(preflight, "_last") else None
res = preflight.check(); item = next((x for x in res if x["item"] == "孤兒條件單"), {})
check("A2", "（前提・自檢孤兒單逐幣確認）逐幣查詢真的失敗（注入觸發）", sum(f["path"] == "/fapi/v2/positionRisk" for f in fx.fired) >= 2)
check("A2", "自檢孤兒單逐幣查不到 → 列「無法確認」，不列成孤兒（不會叫人去撤還在用的停損）", "無法確認" in item.get("msg", "") and item.get("status") == "warn",
      f"{item}")

finish()
