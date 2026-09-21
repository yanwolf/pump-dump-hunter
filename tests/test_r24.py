# 清單 r22 → r24 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安。
# 在專案根目錄執行：python -m tests.test_r24
from tests.harness import (one, ALL_ERR, B, C, FakeBinance, TG, check, fresh, finish, main, manager, os, preflight,
                           re, run, since, store, sys, telegram, time)   # 共用案例框架（清單用法第 5 點 r27）；明列名稱，pyflakes 才查得到未定義名稱





EMPTY_ONE = lambda times=50: dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=times, kind="empty")

def own_pos(fx, sym="XUSDT", qty=100, base=0, side="LONG", stop=0.9, stop_on_exchange=True, **extra):
    sid = via = None
    if stop_on_exchange:
        o = B.stop_order(sym, "SELL" if side == "LONG" else "BUY", qty, stop); sid, via = o["orderId"], o["via"]
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=1.0, stop=stop, r_unit=0.1,
             stop_id=sid, stop_via=via, ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000, base_qty=base)
    p.update(extra)
    store.update(open={**store.get().get("open", {}), sym: p}); return p

# =====================================================================
print("第 2 條 r24：守衛略過時要回傳原因（測試才分得出「查不到」和「沒了」）")
fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx, stop_on_exchange=False))
manager._missing["XUSDT"] = 2; fx.inject.append(EMPTY_ONE())
r, e = run(lambda: manager.ensure_stop("XUSDT", p))
check("N2", "停損不見、補掛前確認查不到 → 回傳「unknown」", r == "unknown", f"回傳={r} err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx, stop_on_exchange=False)); fx.pos.clear()
manager._missing["XUSDT"] = 2
r, e = run(lambda: manager.ensure_stop("XUSDT", p))
check("N2", "停損不見、部位已沒了 → 回傳「gone」", r == "gone", f"回傳={r} err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx, stop_on_exchange=False))
r, e = run(lambda: manager.ensure_stop("XUSDT", p))
check("N2", "停損不見、還沒到連續 3 輪 → 回傳「waiting」", r == "waiting", f"回傳={r} err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx))
r, e = run(lambda: manager.ensure_stop("XUSDT", p))
check("N2", "停損在 → 回傳「present」", r == "present", f"回傳={r} err={e}")

# =====================================================================
print("第 8 條 r24：結帳要排在最前面，撤單、通知、算損益出錯都不能讓這筆結不了帳")
fx = fresh(); so = own_pos(fx, want_stop="abc", want_fail=2)            # 資料壞掉：想要的停損價不是數字 → 收尾通知格式化會出錯
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))       # 交易所上已經沒有這個部位
check("N5", "（前提）對帳真的走到結帳（交易所上沒有這個部位）", not any(p["symbol"] == "XUSDT" for p in fx.rows()), infra=True)
check("N5", "收尾通知格式化出錯 → 平倉紀錄照樣寫進去", len(store.get().get("closed") or []) == 1, f"closed={len(store.get().get('closed') or [])} err={e}")
check("N5", "收尾通知格式化出錯 → 部位移出帳上", "XUSDT" not in store.get().get("open", {}))
check("N5", "撤停損照樣做（排在結帳之後，各自 try）", so["stop_id"] not in fx.algo_orders)
check("N5", "通知出錯要推播", *one("🐞 XUSDT 收尾通知出錯"))
main._rc["t"] = 0; run(lambda: main.reconcile(force=True))
check("N5", "下一輪對帳不會再結一次帳", len(store.get().get("closed") or []) == 1, f"closed={len(store.get().get('closed') or [])}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
p = dict(store.get()["open"]["XUSDT"]); p.pop("entry")                  # 缺欄位：沒有進場價
mark = len(fx.calls)
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))
check("N5", "（前提）平倉單真的送出、交易所上確實平掉", fx.qty("XUSDT", "LONG") == 0 and
      any(c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true" for c in since(fx, mark)))
check("N5", "缺進場價 → 算損益不拋例外，照樣結帳（損益記為未知）", e is None and len(store.get().get("closed") or []) == 1, f"err={e}")
check("N5", "缺進場價 → 部位移出帳上", "XUSDT" not in store.get().get("open", {}))

# =====================================================================
print("第 8 條 r23：背景執行緒的例外要有人接")
fx = fresh()
def bad_tick(state): raise RuntimeError("tick 本身壞了")
main.tick = bad_tick
r, e = run(lambda: main.run_tick({}))
check("N4", "（前提）背景迴圈有「包一層」的入口 run_tick", hasattr(main, "run_tick"), f"err={e}")
check("N4", "tick 本身丟例外 → 不往外拋（執行緒不會結束）", e is None, f"err={e}")
check("N4", "tick 本身丟例外 → 推播", *one("🐞 背景迴圈「整輪」出錯", "tick 本身壞了"))

# =====================================================================
print("第 8 條 r24：網頁觸發的交易操作要包一層")
fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
def bad_close(sym, pos, by):
    pos["side"]                                              # 讀得到
    raise RuntimeError("平倉流程在結帳前壞了")
manager.close_now = bad_close
r, e = run(lambda: main.trade_action("close", "XUSDT", "C", None))
check("N6", "（前提）有網頁交易入口 trade_action", hasattr(main, "trade_action"), f"err={e}")
check("N6", "手動平倉在結帳前出錯 → 回錯誤給網頁（不往外拋）", e is None and isinstance(r, dict) and r.get("error"), f"回應={r} err={e}")
check("N6", "手動平倉在結帳前出錯 → 推播", *one("🐞 XUSDT 手動close出錯", "平倉流程在結帳前壞了"))
own = store.get().get("open", {}).get("XUSDT") or {}
check("N6", "手動平倉在結帳前出錯 → 記待平倉（使用者要平倉的意圖不能丟）", bool(own.get("want_close")), f"own={own.get('want_close')}")
check("N6", "停損沒被撤", so["stop_id"] in fx.algo_orders)

# =====================================================================

finish(allowed=('abc', 'entry'))   # 本檔刻意注入的錯誤字串；其餘程式錯誤一律算失敗
