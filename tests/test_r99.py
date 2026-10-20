# 清單 r98 → r99 第 16 條：交易所一直拒絕平倉（-2022）——重試要會退讓，也要有「停止追蹤」的出口。
# 時間用測試控制的時鐘一輪一輪往前推（每輪 60 秒，跟背景迴圈一樣）。
# 在專案根目錄執行：python -m tests.test_r99
import importlib, json
from tests.harness import (one, B, TG, BACKFILL_JOBS, check, fresh, finish, main, manager, os, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
CLOCK = dict(t=2_000_000_000_000)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closes(fx): return [c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true"]
def stop_posts(fx): return [c for c in fx.calls if c[0] == "POST" and c[1] == "/fapi/v1/algoOrder"]
def start(fx, mode):
    """開倉、平倉單從此被拒（mode），先手動平倉一次讓它進待平倉；之後每輪背景出場管理重試。"""
    CLOCK["t"] = 2_000_000_000_000; manager._now = lambda: CLOCK["t"]
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    fx.close_reject = mode; B.klines = lambda *a, **k: []
    run(lambda: main.trade_action("close", "XUSDT"))
    return bool(own()) and own().get("want_close")
def rounds(n, fx):
    for _ in range(n):
        CLOCK["t"] += 60_000; run(manager.run)
def stuck_msgs(): return [m for m in TG if "需要人工處理" in m]

print("模擬交易所能回 -2022（第 3 項）")
fx = fresh(); fx.open("XUSDT", "LONG", 100); fx.close_reject = "reject"
r, e = run(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True))
check("K3", "平倉單回 HTTP 400、-2022 ReduceOnly Order is rejected、部位沒動", e is not None and "-2022" in str(e) and fx.qty("XUSDT", "LONG") == 100, f"{e}")
check("K3", "程式認得這是「交易所明確拒絕」", hasattr(B, "reduce_only_rejected") and B.reduce_only_rejected(e) is True)
r, e2 = run(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True) if not setattr(fx, "close_reject", "error") else None)
check("K3", "對照組：503 不是明確拒絕", e2 is not None and hasattr(B, "reduce_only_rejected") and B.reduce_only_rejected(e2) is False, f"{e2}")

print("明確拒絕：連續 10 分鐘後標成卡住、只推一則要人工處理，之後每 10 分鐘才重試（第 1 項）")
fx = fresh()
check("K1", "（前提）開倉成交、手動平倉被 -2022 拒絕、進了待平倉", start(fx, "reject"))
n0 = len(closes(fx)); rounds(9, fx)
check("K1", "前 9 分鐘：每輪照樣重送（還沒到退讓的時間）", len(closes(fx)) - n0 >= 9 and not own().get("stuck_at"), f"重送 {len(closes(fx)) - n0} 次")
rounds(2, fx)
check("K1", "連續被拒滿 10 分鐘：標成卡住", bool(own().get("stuck_at")), f"{ {k: own().get(k) for k in ('reject_n', 'stuck_at', 'next_retry')} }")
check("K1", "標成卡住時推一則「需要人工處理」", len(stuck_msgs()) == 1, f"{len(stuck_msgs())} 則")
n1, s1, t1 = len(closes(fx)), len(stop_posts(fx)), len(TG)
nxt = own().get("next_retry") or 0                                   # 以實際的「下次重試」時間為準，一輪一輪推到它之前
k = 0
while CLOCK["t"] + 60_000 < nxt and k < 20: rounds(1, fx); k += 1
check("K1", "卡住之後、下次重試之前：不重送平倉、不重掛停損（一樣會被拒）、不再推播", k >= 8 and len(closes(fx)) == n1 and len(stop_posts(fx)) == s1 and len(TG) == t1,
      f"推了 {k} 輪；平倉 {len(closes(fx)) - n1} 次、停損 {len(stop_posts(fx)) - s1} 次、推播 {len(TG) - t1} 則")
rounds(1, fx)
check("K1", "到了下次重試的時間：重試一次", len(closes(fx)) == n1 + 1, f"重送 {len(closes(fx)) - n1} 次")
check("K1", "卡住期間「需要人工處理」只推過一則（不洗版）", len(stuck_msgs()) == 1)
fx.close_reject = None; rounds(10, fx)
c = (store.get().get("closed") or [{}])[-1]
check("K1", "交易所恢復後，下一次重試平掉了、照常結帳", not own() and c.get("symbol") == "XUSDT" and fx.qty("XUSDT", "LONG") == 0, f"交易所剩 {fx.qty('XUSDT', 'LONG')}")

print("對照組：暫時性錯誤（503）照舊每輪重試，不退讓")
fx = fresh()
check("K1", "（前提）開倉成交、手動平倉遇 503、進了待平倉", start(fx, "error"))
n0 = len(closes(fx)); rounds(20, fx)
check("K1", "20 分鐘裡每輪都重送、從來沒標成卡住、沒推「需要人工處理」", len(closes(fx)) - n0 >= 20 and not own().get("stuck_at") and not stuck_msgs(),
      f"重送 {len(closes(fx)) - n0} 次 stuck={own().get('stuck_at')}")

print("被拒中換成別種錯誤：歸零回到每輪重試")
fx = fresh()
check("K1", "（前提）開倉成交、被 -2022 拒絕", start(fx, "reject"))
rounds(5, fx)
check("K1", "（前提）換成 503 之前，明確拒絕的計數確實在累加（不然「歸零」在舊程式上會空的通過）", (own().get("reject_n") or 0) >= 5, f"reject_n={own().get('reject_n')}")
fx.close_reject = "error"; rounds(1, fx)
check("K1", "換成 503：明確拒絕的計數歸零", bool(own()) and own().get("want_close") and not own().get("reject_n"), f"reject_n={own().get('reject_n')}")   # 先要求部位在、還在待平倉
fx.close_reject = "reject"; rounds(8, fx)
check("K1", "再被拒 8 分鐘：從頭算（計數從 1 起累加到 8），還沒標成卡住", (own().get("reject_n") or 0) == 8 and not own().get("stuck_at"), f"reject_n={own().get('reject_n')}")

print("停止追蹤（第 2 項）")
fx = fresh()
check("K2", "（前提）開倉成交、被 -2022 拒絕", start(fx, "reject"))
r, e = run(lambda: main.trade_action("abandon", "XUSDT"))
check("K2", "還沒標成卡住 → 不能停止追蹤（回錯誤）", isinstance(r, dict) and bool(r.get("error")) and bool(own()), f"{r}")
rounds(11, fx)
check("K2", "（前提）標成卡住了", bool(own().get("stuck_at")))
stops_before = [o["algoId"] for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"]
n0 = len(fx.calls)
r, e = run(lambda: main.trade_action("abandon", "XUSDT"))
c = (store.get().get("closed") or [{}])[-1]
check("K2", "停止追蹤：帳上移除", isinstance(r, dict) and r.get("ok") and not own(), f"{r}")
check("K2", "記一筆排除的損益未知（不計入績效）", c.get("symbol") == "XUSDT" and c.get("pnl") is None and c.get("excluded") is True, f"{ {k: c.get(k) for k in ('by', 'pnl', 'excluded')} }")
check("K2", "交易所上的部位與掛單都沒動（停損留著多一層保護）", isinstance(r, dict) and r.get("ok") and fx.qty("XUSDT", "LONG") == 100
      and [o["algoId"] for o in fx.algo_orders.values() if o["symbol"] == "XUSDT"] == stops_before
      and not [c2 for c2 in fx.calls[n0:] if c2[0] in ("POST", "DELETE")], f"{fx.calls[n0:]}")
from app import stats
st = stats.report(store.get().get("closed") or [])
check("K2", "績效統計不算它（排除的筆數另外列）", st["scope"].get("excluded") == 1 and st["engines"]["全部"]["total"] == len(store.get().get("closed") or []) - 1, f"{st['scope']}")
check("K2", "這檔記進「已放棄追蹤」", "XUSDT" in (store.get().get("abandoned") or {}))
importlib.reload(store); BACKFILL_JOBS.clear()
check("K2", "重啟後「已放棄追蹤」還在（存進狀態檔）", "XUSDT" in (store.get().get("abandoned") or {}))
fx.close_reject = None
rec, e = run(lambda: main.place("XUSDT", "F", Sig(), dict(SZ), dict(time="t", bar_t=900_000)))
check("K2", "交易所那一側清空之前，這檔不再進場", rec is not None and "放棄追蹤" in (rec.get("skipped") or ""), f"skipped={rec and rec.get('skipped')}")
fx.inject.append(dict(path="/fapi/v2/positionRisk", times=10, kind="http", code=503, body="busy"))
main._rc["t"] = 0; run(lambda: main.reconcile(force=True))
check("K2", "對帳查不到部位 → 不解除（保守）", "XUSDT" in (store.get().get("abandoned") or {}))
fx.inject.clear(); main._rc["t"] = 0; main.reconcile(force=True)
check("K2", "交易所那一側還在 → 不解除", "XUSDT" in (store.get().get("abandoned") or {}))
fx.pos.pop(("XUSDT", "LONG"), None); fx._traced.pop(("XUSDT", "LONG"), None)
main._rc["t"] = 0; main.reconcile(force=True)
check("K2", "交易所那一側清空 → 自動解除並通知", "XUSDT" not in (store.get().get("abandoned") or {}) and one("✅ XUSDT 交易所那一側清空了")[0],
      f"abandoned={store.get().get('abandoned')}")

print("網頁：持倉卡片看得到待平倉狀態，停止追蹤只在卡住後出現")
fx = fresh()                                                          # 自成一個情境：只看網頁檔，跟交易所無關
page = open("app/static/dashboard.html", encoding="utf-8").read()
check("K2", "持倉卡片顯示待平倉（重試次數、-2022 連續次數、卡住時間、下次重試、錯誤）", all(k in page for k in ("close_fail", "reject_n", "stuck_at", "next_retry", "reject_err")))
import re
check("K2", "「停止追蹤」按鈕只在卡住（stuck_at）時出現，送 act=abandon", "act=abandon" in page and re.search(r"v\.stuck_at\?`[^`]{0,20}<button onclick=\"abandon\(", page) is not None)

finish()
