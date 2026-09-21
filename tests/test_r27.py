# 清單 r25 → r27 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安。
# 通知只攔最底層的 telegram.send（它只收已經組好的字串），所以每則通知的格式化都真的會執行（清單第 8 條 r26）。
# 在專案根目錄執行：python -m tests.test_r27
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, since, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）
from scripts.check_returns import TARGETS, check_file

def own_pos(fx, sym="XUSDT", qty=100, side="LONG", stop=0.9, **extra):
    o = B.stop_order(sym, "SELL" if side == "LONG" else "BUY", qty, stop)
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=1.0, stop=stop, r_unit=0.1, base_qty=0,
             stop_id=o["orderId"], stop_via=o["via"], ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000)
    p.update(extra)
    store.update(open={**store.get().get("open", {}), sym: p}); return p

def notify_errors(): return [m for m in TG if "通知" in m and "出錯" in m]

# =====================================================================
print("第 2 條 r26、r27：回傳原因——每個 return 帶值、最後不會掉出函式")
fx = fresh()
res = {n: p for path, names in TARGETS.items() for n, p in check_file(path, names).items()}
check("P1", "（前提）語法樹檢查真的掃到了目標函式", len(res) == sum(len(v) for v in TARGETS.values()) and len(res) >= 5, f"{list(res)}")
check("P1", "守衛、移損、重試移損、送停損、重試平倉：每個出口都回傳原因", all(not v for v in res.values()),
      "；".join(f"{k}：{v}" for k, v in res.items() if v))

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx))
r, e = run(lambda: manager.move_stop("XUSDT", p, 1.0))
check("P1", "移損成功 → 回傳「moved」（不是掉出函式回 None）", r == "moved", f"回傳={r} err={e}")

fx = fresh(); p = dict(engine="C", side="LONG", qty=100, stop=0.9)   # 這兩項不查部位，自成一個情境（不繼承上面移損的命中次數）
r, e = run(lambda: manager.retry_stop("XUSDT", dict(p, want_stop=None)))
check("P1", "沒有待移的停損 → 重試移損回傳原因", isinstance(r, str) and r, f"回傳={r!r}")
r, e = run(lambda: manager.retry_close("XUSDT", dict(p)))
check("P1", "沒有待平倉 → 重試平倉回傳原因", isinstance(r, str) and r, f"回傳={r!r}")

# =====================================================================
print("第 8 條 r26、r27：缺欄位的資料，通知要組得出來、計算不能拋")
fx = fresh(); fx.open("XUSDT", "LONG", 100)
p = dict(own_pos(fx, fill=None)); p.pop("entry")                        # 成交價是 None、沒有進場價
fx.inject.append(dict(path="/fapi/v1/userTrades", times=5, kind="empty"))   # 成交明細也查不到 → 只能靠估算，而估算缺資料
mark = len(fx.calls)
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))
check("P2", "（前提）程式真的去查了成交明細、而且查不到", any(c[1] == "/fapi/v1/userTrades" for c in since(fx, mark)))
check("P2", "（前提）平倉單真的送出、交易所上確實平掉", fx.qty("XUSDT", "LONG") == 0 and
      any(c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true" for c in since(fx, mark)))
check("P2", "缺成交價與進場價 → 照樣結帳", e is None and len(store.get().get("closed") or []) == 1, f"err={e}")
check("P2", "缺成交價與進場價 → 出場通知真的組出來了，而且講明損益未知", one("🏁 XUSDT 引擎C 時間出場", "損益未知")[0] and not notify_errors(), f"{one('🏁 XUSDT 引擎C 時間出場', '損益未知')[1]}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx, fill=None)          # 成交價 None（交易所回應沒帶成交均價）
fx.trigger("XUSDT", "LONG", 40)                                            # 在 App 手動減碼：交易所上真的成交一筆（清單用法第 5 點 r33）
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
own = store.get().get("open", {}).get("XUSDT") or {}
check("P3", "（前提）對帳真的偵測到數量減少", own.get("qty") == 60, f"qty={own.get('qty')} err={e}")
part3 = (own.get("partials") or [{}])[-1]
check("P3", "（前提）部分出場的損益真的算出來了（有成交可以算，不是沒進計算就記未知）", part3.get("pnl") is not None, f"{part3}")
check("P3", "成交價是 None 時算部分出場損益不拋（.get(鍵, 預設) 擋不住值是 None）",
      not [m for m in store.get().get("errors", []) + TG if "TypeError" in m], f"{store.get().get('errors', [])[-1:]}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx, last_t=None)       # last_t 是 None（鍵存在、值是 None）
bars = [dict(t=i * 300_000, o=1, h=1.0, l=0.95, c=1, v=1) for i in range(1, 5)]
B.klines = lambda *a, **k: list(bars)
manager._now = lambda: 10**15
manager.run()
check("P3", "（前提）出場判斷真的跑了（K 棒有處理）", bars and (store.get().get("open", {}).get("XUSDT") or {}).get("last_t") == bars[-1]["t"],
      f"last_t={(store.get().get('open', {}).get('XUSDT') or {}).get('last_t')}")
check("P3", "last_t 是 None → 出場判斷不拋錯", not any("出場判斷出錯" in m for m in TG), f"{TG}")

# =====================================================================
print("第 8 條 r27：損益未知不能被當成 0 或虧損")
fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx, fill=None)
fx.trigger("XUSDT", "LONG", 40)                                            # 部分出場：交易所上真的成交一筆
fx.inject.append(dict(path="/fapi/v1/userTrades", times=1, kind="empty"))  # 但成交明細查不到 → 這一段損益未知（原因在測試裡明寫，第 22 種）
mark = len(fx.calls)
main._rc["t"] = 0; main.reconcile(force=True)
part = ((store.get().get("open", {}).get("XUSDT") or {}).get("partials") or [{}])[-1]
check("P4", "（前提）部分出場有記下來", bool(part))
check("P4", "（前提）未知的原因是成交明細查不到：程式查了、注入真的觸發了，而且交易所上確實有那筆成交（第 16 種）",
      any(c[1] == "/fapi/v1/userTrades" for c in since(fx, mark)) and any(f["path"] == "/fapi/v1/userTrades" for f in fx.fired)
      and not fx.untraced("XUSDT"))
check("P4", "部分出場的損益未知 → 記成 None，不是 0", part.get("pnl", "缺") is None, f"partial={part}")
p = dict(store.get()["open"]["XUSDT"]); p["fill"] = 1.0
# 成交明細查不到 → 只能靠自己估算（有成交明細時以交易所為準，那是對的；r27 要防的是估算時把未知那段當 0）
fx.inject.append(dict(path="/fapi/v1/userTrades", times=5, kind="empty"))
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))
rec = (store.get().get("closed") or [{}])[-1]
check("P4", "（前提）剩下的部位平掉、結帳了", fx.qty("XUSDT", "LONG") == 0 and bool(rec), f"err={e}")
check("P4", "有一段損益未知 → 整筆的損益也是未知（不能把未知那段當 0 加總）", rec.get("pnl") is None, f"pnl={rec.get('pnl')}")
check("P4", "平倉通知（🏁）講明損益未知", *one("🏁 XUSDT 引擎C 時間出場", "損益未知"))

finish()
