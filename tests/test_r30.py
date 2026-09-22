# 清單 r28 → r30 差異的行為測試。模擬交易所的成交價 ≠ 標記價（清單用法第 5 點 r30），出場價用錯哪一個都分得出來。
# 在專案根目錄執行：python -m tests.test_r30
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, since, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）
from scripts.check_returns import TARGETS, check_file, count_returns

def own_pos(fx, sym="XUSDT", qty=100, side="LONG", stop=0.9, **extra):
    """在模擬交易所用市價單真的開倉（有成交明細），再把帳記上。"""
    o = B.market_order(sym, "BUY" if side == "LONG" else "SELL", qty)
    so = B.stop_order(sym, "SELL" if side == "LONG" else "BUY", qty, stop)
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=float(o["avgPrice"]), stop=stop, r_unit=0.1, base_qty=0,
             stop_id=so["orderId"], stop_via=so["via"], ts=int(time.time() * 1000) - 1000, bar_t=300_000, last_t=300_000)
    p.update(extra)
    store.update(open={**store.get().get("open", {}), sym: p}); return p

def app_reduce(fx, sym, qty, px):
    """模擬在 App 手動減碼：交易所上真的成交一筆（在指定價格），程式不知道。"""
    if fx.qty(sym, "LONG") >= qty: fx._reduce(sym, "LONG", qty, px)      # 前提不成立時不讓測試崩掉（r35）
    return fx.trades[-1]

def seg_pnl(t): return float(t["realizedPnl"]) - float(t["commission"])

# =====================================================================
print("第 2 條 r29、r30：回傳原因檢查——每個函式至少 2 個 return（不是看總數），範圍含手動平倉、認領")
fx = fresh()
counts = {f"{p}:{n}": c for p, names in TARGETS.items() for n, c in count_returns(p, names).items()}
check("Q1", "（前提）每個目標函式都真的解析到至少 2 個 return", counts and all(c >= 2 for c in counts.values()), f"{counts}")
check("Q1", "範圍含手動平倉、認領、網頁交易入口", all(any(k.endswith(n) for k in counts) for n in (":manage", ":_protect", ":trade_action")), f"{list(counts)}")
res = {f"{p}:{n}": v for p, names in TARGETS.items() for n, v in check_file(p, names).items()}
check("Q1", "每個出口都回傳原因、不會掉出函式", all(not v for v in res.values()), "；".join(f"{k}：{v}" for k, v in res.items() if v))

# =====================================================================
print("第 8 條 r30：出場價只用實際成交價（不用標記價、不退回進場價）")
fx = fresh(); p = own_pos(fx); fx.price = 1.10                      # 標記價 1.10
t = app_reduce(fx, "XUSDT", 40, 1.05)                               # 但實際在 1.05 成交
main._rc["t"] = 0; main.reconcile(force=True)
part = ((store.get().get("open", {}).get("XUSDT") or {}).get("partials") or [{}])[-1]
check("Q3", "（前提）對帳偵測到數量減少、記了一筆部分出場", bool(part.get("qty")), f"{part}")
check("Q3", "部分出場的損益用實際成交（1.05）算，不是標記價（1.10）", part.get("pnl") is not None and abs(part["pnl"] - round(seg_pnl(t), 2)) < 0.011,
      f"部分出場 pnl={part.get('pnl')} 實際成交算出={round(seg_pnl(t), 2)}")

fx = fresh(); p = own_pos(fx); fx.price = 1.10
app_reduce(fx, "XUSDT", 40, 1.05)
fx.inject.append(dict(path="/fapi/v1/userTrades", times=5, kind="empty"))
mark = len(fx.calls)
main._rc["t"] = 0; main.reconcile(force=True)
part = ((store.get().get("open", {}).get("XUSDT") or {}).get("partials") or [{}])[-1]
check("Q3", "（前提）部分出場有記下來，而且程式真的去查了成交明細", bool(part.get("qty")) and any(c[1] == "/fapi/v1/userTrades" for c in since(fx, mark)))
check("Q3", "成交明細查不到 → 部分出場損益記未知（不拿標記價估一個數字）", part.get("pnl", "缺") is None, f"{part}")

fx = fresh(); fx.open("XUSDT", "LONG", 40, entry=0.8); p = own_pos(fx, base_qty=40)   # 同側有別人的 40
fx.price = 1.10; app_reduce(fx, "XUSDT", 30, 1.05)
main._rc["t"] = 0; main.reconcile(force=True)
part = ((store.get().get("open", {}).get("XUSDT") or {}).get("partials") or [{}])[-1]
check("Q3", "（前提）有基準部位時也偵測到數量減少", bool(part.get("qty")), f"{part}")
check("Q3", "有基準部位 → 成交明細分不出哪幾筆是自己的，損益記未知", part.get("pnl", "缺") is None, f"{part}")

fx = fresh(); fx.slip = 0.0; p = own_pos(fx)                          # 打平：開倉、出場都在 1.0，realizedPnl 剛好是 0
pp = dict(store.get()["open"]["XUSDT"])
r, e = run(lambda: manager.close_now("XUSDT", pp, "停損"))
rec = (store.get().get("closed") or [{}])[-1]
last = fx.trades[-1] if fx.trades else {"realizedPnl": "nan", "side": "", "commission": "0"}   # 先確認有東西（r35、r36）
check("Q3", "（前提）打平出場：帳上成交價與出場價都是 1.0，最後那筆成交的 realizedPnl 真的是 0",
      pp["fill"] == 1.0 and float(last["realizedPnl"]) == 0 and last["side"] == "SELL", f"fill={pp['fill']} {last}")
check("Q3", "打平出場的成交也要算（不能用 realizedPnl ≠ 0 篩平倉成交）：損益 = 0 − 手續費，來自成交明細",
      rec.get("pnl") is not None and abs(rec["pnl"] - round(seg_pnl(last), 2)) < 1e-9,
      f"pnl={rec.get('pnl')} 成交明細算出={round(seg_pnl(last), 2)}（估算會是 0.0）")

# 1R 減碼：程式自己送的部分出場，要記成部分出場
fx = fresh(); p = own_pos(fx, qty=100, r_unit=0.1, entry=1.0)
bars = [dict(t=600_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]
B.klines = lambda *a, **k: list(bars); manager._now = lambda: 10**15
fx.price = 1.10
pp = dict(store.get()["open"]["XUSDT"]); manager.step("XUSDT", pp)
check("Q3", "（前提）1R 減碼真的送出了一半", pp.get("tp1") and fx.qty("XUSDT", "LONG") == 50, f"剩 {fx.qty('XUSDT', 'LONG')}")
check("Q3", "（前提）這一步完整走完：停損也移到成本、新停損真的掛上", pp.get("stop") == pp.get("entry") and not pp.get("want_stop"),
      f"stop={pp.get('stop')} want={pp.get('want_stop')}")
parts = pp.get("partials") or []
check("Q3", "1R 減碼記成一筆部分出場，損益用實際成交算", len(parts) == 1 and parts[0].get("pnl") is not None and
      len(fx.trades) >= 1 and abs(parts[0]["pnl"] - round(seg_pnl(fx.trades[-1]), 2)) < 0.011, f"{parts}")

# =====================================================================
print("第 8 條 r30：成交明細的時間界線用「已採用的最後一筆」")
fx = fresh(); p = own_pos(fx)
t1 = app_reduce(fx, "XUSDT", 40, 1.20)                              # 前一段：在 1.20 部分出場
main._rc["t"] = 0; main.reconcile(force=True)
fx.price = 0.90
pp = dict(store.get()["open"]["XUSDT"])
r, e = run(lambda: manager.close_now("XUSDT", pp, "時間"))           # 最後一段：在 0.90 附近平掉剩下的 60
rec = (store.get().get("closed") or [{}])[-1]; t2 = fx.trades[-1] if fx.trades else {"id": None, "price": "nan"}
check("Q4", "（前提）兩段真的都成交了，而且時間戳落在同一毫秒內也分得開（id 不同）", t1["id"] != t2["id"] and fx.qty("XUSDT", "LONG") == 0,
      f"t1={t1['time']} t2={t2['time']}")
check("Q4", "出場價只看最後一段（0.899），不被前一段（1.20）拉偏", abs((rec.get("exit") or 0) - float(t2["price"])) < 1e-9,
      f"exit={rec.get('exit')} 最後一段={t2['price']}")
check("Q4", "整筆損益 = 前一段 + 最後一段（各自扣手續費）", rec.get("pnl") is not None and abs(rec["pnl"] - round(seg_pnl(t1) + seg_pnl(t2), 2)) < 0.011,
      f"pnl={rec.get('pnl')} 應為 {round(seg_pnl(t1) + seg_pnl(t2), 2)}")

# =====================================================================
print("第 8 條 r30（crypto 那點）：pending 的時間戳不是數字")
fx = fresh()
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=None, base_qty=0)})
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("Q6", "（前提）對帳跑完", e is None, f"err={e}")
check("Q6", "時間戳是 None → 不拋錯、不卡住（判定逾時並講明原因）", not store.get().get("pending") and one("ℹ️ XUSDT 引擎C 的 pending 沒有有效的時間戳")[0], f"pending={store.get().get('pending')} {one('ℹ️ XUSDT 引擎C 的 pending 沒有有效的時間戳')[1]}")

# =====================================================================
print("用法第 5 點 r29：錯誤掃描要攔到所有模組（程式裡只 print 的錯誤也要進得來）")
fx = fresh()
from app import presets
orig_apply = presets.C.apply_overrides
def boom(ov): raise RuntimeError("套用參數時出錯（模擬）")
presets.C.apply_overrides = boom
presets.set_live({"SCAN.watch_chg24": 15})
r, e = run(presets.apply_live)
presets.C.apply_overrides = orig_apply; presets.set_live({})
check("Q8", "（前提）套用參數真的走到出錯那一步", e is None)
check("Q8", "套用實盤參數失敗 → 進錯誤區並推播（原本只 print）", any("套用參數時出錯" in x for x in store.get().get("errors", [])) and one("🐞 套用實盤參數覆蓋失敗", "套用參數時出錯")[0], f"{one('🐞 套用實盤參數覆蓋失敗', '套用參數時出錯')[1]}")

finish(allowed=("套用參數時出錯",))
