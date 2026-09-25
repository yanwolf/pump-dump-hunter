# 清單 r70 → r71 差異的行為測試：FILLED 但沒均價、成交明細稍後才出現或只先出現一部分。
# 背景補登的執行緒不真的開：換掉 manager._start_backfill，收下來由測試在「背景等待過後」（把成交明細調成看得到）自己跑。
# 在專案根目錄執行：python -m tests.test_r71
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
JOBS = []
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def closed(): return (store.get().get("closed") or [{}])[-1]
def setup(fx, strip=True, strip_query=True, hide=True):
    JOBS.clear()
    if hasattr(manager, "_start_backfill"): manager._start_backfill = JOBS.append
    fx.strip_avg = (lambda p: True) if strip else None
    fx.strip_query = strip_query; fx.hide_fills = hide
def wait_then_run(fx, frac=1.0):
    """背景等待過後：成交明細看得到了（frac 比例），再跑收下來的補登工作。"""
    for k in list(fx.trades_visible): fx.trades_visible[k] = frac
    for j in list(JOBS): j()
def entry_fill_true(fx): return next((float(x["avgPrice"]) for x in fx.orders.values() if x["_p"].get("reduceOnly") != "true"), None)

fx = fresh()
check("C0", "（前提）程式有背景補登的排程函式（舊版沒有 → 斷言失敗，不是測試崩掉）", hasattr(manager, "_start_backfill"))

print("開倉：回應 FILLED、沒有均價與成交額")
fx = fresh(); setup(fx, strip=True, strip_query=False, hide=True)
fx.strip_avg = lambda p: p.get("reduceOnly") != "true"
real = {}
orig = fx._strip
def strip_but_keep_cumquote(o, d):                                    # 只拿掉 avgPrice，留 cumQuote
    return {k: v for k, v in d.items() if k != "avgPrice"} if o["_p"].get("reduceOnly") != "true" else d
fx._strip = strip_but_keep_cumquote
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("C1", "（前提）開倉成交、回應沒有 avgPrice、成交明細也還看不到", bool(own()) and fx.trades_visible and all(v == 0 for v in fx.trades_visible.values()))
check("C1", "有 cumQuote → 成交額 ÷ 成交量就是實際均價（不用等成交明細）", entry_fill_true(fx) is not None and abs((own().get("fill") or 0) - entry_fill_true(fx)) < 1e-9,
      f"帳上 fill={own().get('fill')} 實際 {entry_fill_true(fx)}")
fx._strip = orig

fx = fresh(); setup(fx)
logs = []; B.LOG = logs.append if hasattr(B, "LOG") else None
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
gets = [c for c in fx.calls if c[0] == "GET" and c[1] == "/fapi/v1/order"]
check("C2", "（前提）開倉成交、回應與查單都沒有均價、成交明細看不到", bool(own()) and fx.qty("XUSDT", "LONG") == 100)
check("C2", "FILLED 但沒均價 → 用單號查訂單 3 次（有上限，不卡住流程）", len(gets) == 3, f"查單 {len(gets)} 次")
check("C2", "每次查的結果寫日誌", len([l for l in logs if "查訂單" in l]) >= 3, f"{logs[:3]}")
check("C4", "當下查不到 → 帳上成交價先記未知，排背景補登", own().get("fill") is None and len(JOBS) == 1, f"fill={own().get('fill')} 排程 {len(JOBS)}")
wait_then_run(fx, 1.0)
check("C4", "背景等待過後查到 → 還開著的部位補上實際成交價", entry_fill_true(fx) is not None and abs((own().get("fill") or 0) - entry_fill_true(fx)) < 1e-9, f"fill={own().get('fill')}")
check("C4", "補登通知（附原本是未知）", *one("🧾 XUSDT 引擎C 進場成交價補登", "原本記未知"))

fx = fresh(); setup(fx)
orig_get = B.user_trades
def partial_trades(sym, *a, **kw):                                  # 開倉那一刻，成交明細只出現四成
    rows = orig_get(sym, *a, **kw)
    return [dict(t, qty=str(float(t["qty"]) * 0.4)) for t in rows]
fx.hide_fills = False; B.user_trades = partial_trades
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
B.user_trades = orig_get
check("C3", "（前提）開倉成交、回應與查單都沒有均價", bool(own()))
check("C3", "成交明細沒湊滿成交量 → 不能拿前四成的價格當實際成交價，記未知、排補登", own().get("fill") is None and len(JOBS) >= 1,
      f"fill={own().get('fill')} 排程 {len(JOBS)}")

print("平倉（手動）：回應 FILLED、沒有均價與成交額，成交明細稍後才出現／只先出現一部分")
for frac, tag in ((0.0, "稍後才出現"), (0.4, "只先出現四成")):
    fx = fresh(); setup(fx, strip=False, hide=False)
    main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
    check("C4", f"（前提・{tag}）開倉成交、成交價已知", bool(own()) and own().get("fill"))
    fx.strip_avg = lambda p: p.get("reduceOnly") == "true"; fx.strip_query = True; fx.hide_fills = True; fx.hide_frac = frac
    fx.price = 1.1
    r, e = run(lambda: main.trade_action("close", "XUSDT"))                  # 平倉當下，成交明細就只看得到 frac
    c = closed(); true_exit = next((float(x["avgPrice"]) for x in fx.orders.values() if x["_p"].get("reduceOnly") == "true"), None)
    check("C4", f"（前提・{tag}）平倉單成交、交易所上平掉了", fx.qty("XUSDT", "LONG") == 0 and c.get("symbol") == "XUSDT", f"回應={r}")
    check("C3", f"{tag}：出場價記未知（不拿部分成交當實際價、不估算）", c.get("symbol") == "XUSDT" and c.get("exit") is None and c.get("pnl") is None,
          f"exit={c.get('exit')} pnl={c.get('pnl')}")   # 先要求真的結帳了：沒結帳時紀錄是空的，「是 None」也成立
    check("C4", f"{tag}：排了背景補登", len(JOBS) >= 1, f"排程 {len(JOBS)}")
    JOBS[:] = [j for j in JOBS]; wait_then_run(fx, 1.0)
    c = closed()
    check("C4", f"{tag}：背景等待過後查到 → 已平倉紀錄補上實際出場價與損益", true_exit is not None and c.get("exit") is not None and abs(c["exit"] - float(f"{true_exit:.6g}")) < 1e-9
          and c.get("pnl") is not None, f"exit={c.get('exit')} 實際 {true_exit} pnl={c.get('pnl')}")
    check("C4", f"{tag}：補登通知附原本是未知", *one("🧾 XUSDT 引擎C 出場成交價補登", "原本記未知"))

print("平倉（交易所端停損觸發、對帳結帳）：成交明細只先出現一部分")
fx = fresh(); setup(fx, strip=False, hide=False)
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
for o in list(fx.algo_orders.values()): fx.algo_orders.pop(o["algoId"])
fx.price = 0.9
if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)
fx.trades_visible[None] = 0.4                                        # 停損觸發那筆成交（沒有我們的單號）只先出現四成
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("C3", "（前提）對帳結帳了", c.get("symbol") == "XUSDT")
check("C3", "對帳：界線之後的平倉成交沒湊滿帳上數量 → 出場價記未知（不拿四成當整筆）", c.get("symbol") == "XUSDT" and c.get("exit") is None, f"exit={c.get('exit')}")
wait_then_run(fx, 1.0)
check("C4", "對帳：背景等待過後查到 → 補上出場價（約 0.9）", abs((closed().get("exit") or 0) - 0.9) < 0.01, f"exit={closed().get('exit')}")

print("一直查不到：推一則補登失敗")
fx = fresh(); setup(fx, strip=False, hide=False)
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.strip_avg = lambda p: p.get("reduceOnly") == "true"; fx.strip_query = True; fx.hide_fills = True
r, e = run(lambda: main.trade_action("close", "XUSDT"))
check("C4", "（前提）平倉了、出場價未知、排了補登", closed().get("exit") is None and len(JOBS) >= 1)
wait_then_run(fx, 0.0)                                               # 等過了還是看不到
check("C4", "補登一直查不到 → 推一則補登失敗、附單號", *one("⚠️ XUSDT 引擎C 出場成交價補登失敗", "單號"))
check("C4", "補登失敗 → 紀錄維持未知（不估算）", closed().get("symbol") == "XUSDT" and closed().get("exit") is None)

print("通知不貼原始回應")
check("C5", "（前提）有收到通知", len(TG) >= 1)
check("C5", "平倉與補登失敗的通知都發了，沒有任何一則含原始回應（訂單的 JSON）", any(m.startswith("🏁") for m in TG) and any("補登失敗" in m for m in TG)
      and not any('"orderId"' in m or "'orderId'" in m or "cumQty" in m for m in TG), f"{[m[:60] for m in TG]}")

finish()
