# 清單 r44 → r47 差異的行為測試。
# 在專案根目錄執行：python -m tests.test_r47
import threading
from tests.harness import (one, B, C, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）
from app import presets

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def api(path):
    """直接呼叫網頁的路由（不經過 HTTP 伺服器）。"""
    h = main.H.__new__(main.H); h.headers = {"X-PDH": "1"}; h.path = path
    import json
    body, ct = h._route()
    return json.loads(body)

def paused(target_path, when):
    """讓背景那條在查交易所（target_path）時停住，等另一條做完。回傳 (繼續用的事件, 停住了的事件)。"""
    go, stopped = threading.Event(), threading.Event()
    orig = B._get
    def hook(path, params=None, base=None, signed=False):
        if path == target_path and when(params) and not stopped.is_set():
            stopped.set(); go.wait(5)
        return orig(path, params, base, signed)
    B._get = hook
    return go, stopped

# =====================================================================
print("第 8 條 r45：後寫的一步不能把先寫的清掉——背景對帳與網頁手動平倉同時發生")
fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("W1", "（前提）開倉成交、帳上有這筆", bool(own()) and fx.qty("XUSDT", "LONG") == 100)
go, stopped = paused("/fapi/v2/positionRisk", lambda p: not p or "symbol" not in p)   # 對帳拿全量部位表時停住
main._rc["t"] = 0
bg = threading.Thread(target=lambda: main.reconcile(force=True), daemon=True); bg.start()
stopped.wait(5)
check("W1", "（前提）背景對帳真的停在查交易所那一步", stopped.is_set())
web = threading.Thread(target=lambda: main.trade_action("close", "XUSDT"), daemon=True); web.start()
web.join(1.0)                                                   # 有引擎鎖的話，網頁那條會等背景對帳做完
go.set(); bg.join(5); web.join(5)
closed = [c for c in store.get().get("closed") or [] if c.get("symbol") == "XUSDT"]
check("W1", "（前提）手動平倉真的平掉了", fx.qty("XUSDT", "LONG") == 0 and len(closed) >= 1, f"交易所剩 {fx.qty('XUSDT', 'LONG')}")
check("W1", "對帳結束寫回帳本時，不能把已經結帳的部位放回帳上", bool(closed) and not own(), f"帳上 {own()}")   # 先要求真的結帳了
main._rc["t"] = 0; main.reconcile(force=True)
closed = [c for c in store.get().get("closed") or [] if c.get("symbol") == "XUSDT"]
check("W1", "同一筆只結一次帳（包括被暫停的那次對帳、以及下一輪對帳）", len(closed) == 1, f"結帳 {len(closed)} 次")

fx = fresh()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("W1", "（前提）開倉成交、帳上有這筆", bool(own()))
B.klines = lambda *a, **k: []
go, stopped = paused("/fapi/v1/openAlgoOrders", lambda p: True)   # 出場管理的停損守衛查條件單時停住
bg = threading.Thread(target=manager.run, daemon=True); bg.start()
stopped.wait(5)
check("W1", "（前提）背景出場管理真的停在查交易所那一步", stopped.is_set())
fx.inject.append(dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("reduceOnly") == "true",
                      times=5, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}'))
web = threading.Thread(target=lambda: main.trade_action("close", "XUSDT"), daemon=True); web.start()
web.join(1.0)
go.set(); bg.join(5); web.join(5)
check("W1", "（前提）手動平倉被拒、交易所上部位還在", fx.qty("XUSDT", "LONG") == 100)
check("W1", "出場管理結束寫回帳本時，不能把手動平倉記下的「待平倉」清掉", bool(own().get("want_close")), f"want_close={own().get('want_close')}")

# =====================================================================
print("第 8 條 r47：解析得了但不是物件、或一個要改的欄位都沒有，都要回錯誤")
fx = fresh()
def with_override():
    presets.set_live({"SCAN.watch_chg24": 15}); presets.apply_live()
    return presets.live() == {"SCAN.watch_chg24": 15}
for path, why in (("/api/presets?act=live", "沒帶表單"), ("/api/presets?act=live&form=null", "表單是 null"),
                  ("/api/presets?act=live&form=%5B%5D", "表單是 []"), ("/api/presets?act=live&form=%22x%22", "表單是字串"),
                  ("/api/presets?act=live&form=%7B%7D", "表單是空物件")):
    check("W3", f"（前提）{why}：送出前實盤有一項覆蓋", with_override())   # 每個情境各自設好，不被前一個拖累
    r, e = run(lambda: api(path))
    check("W3", f"套用到實盤、{why} → 回錯誤", isinstance(r, dict) and bool(r.get("error")), f"回應={r} err={e}")
    check("W3", f"套用到實盤、{why} → 實盤覆蓋沒被清掉", presets.live() == {"SCAN.watch_chg24": 15}, f"live={presets.live()}")
r, e = run(lambda: api("/api/presets?act=save&name=%E7%A9%BA%E7%9A%84&form=%7B%7D"))
check("W3", "存一個空的參數集 → 回錯誤", isinstance(r, dict) and bool(r.get("error")), f"回應={r}")
check("W3", "存一個空的參數集 → 沒存進去", "空的" not in (presets.all_presets() or {}))
check("W3", "（前提）回預設之前實盤有一項覆蓋", with_override())
r, e = run(lambda: api("/api/presets?act=reset"))
check("W3", "要回預設用明確的動作（act=reset），而且真的回預設", isinstance(r, dict) and not r.get("error") and presets.live() == {},
      f"回應={r} live={presets.live()}")
page = open("app/static/dashboard.html", encoding="utf-8").read()
check("W3", "網頁「實盤回預設」按鈕用 act=reset，不再送空表單", "act=reset" in page and "act=live&form=%7B%7D" not in page)
pm = page.split("async function pmeta")[1][:200]; pl = page.split("async function plive(")[1][:400]
check("W3", "網頁「套用到實盤」看回應才說成功：pmeta 收到錯誤會丟例外，plive 等它完成才跳「已套用」",
      "if(r.error)" in pm and "throw" in pm and pl.index("await pmeta(") < pl.index("已套用到實盤"))

finish()
