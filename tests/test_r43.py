# 清單 r40 → r43 差異的行為測試。模擬交易所的市價單可以回「還沒成交」（清單第 15 條 r43：一律回 FILLED 的話這些全部空跑）。
# 在專案根目錄執行：python -m tests.test_r43
import importlib, json
from tests.harness import (one, B, C, TG, check, fresh, finish, main, manager, os, run, since, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）
from app import params, presets

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

def mode_for(entry=None, close=None):
    """開倉單（不帶 reduceOnly）與平倉單（帶 reduceOnly）各自的成交情境"""
    return lambda p: (close if p.get("reduceOnly") == "true" or p.get("positionSide") and p.get("side") == "SELL" else entry) or "filled"
def markets(fx, mark=0): return [c for c in fx.calls[mark:] if c[0] == "POST" and c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
def own(): return store.get().get("open", {}).get("XUSDT") or {}
def open_pos(fx, qty=100):
    fx.fill_mode = None
    main.place("XUSDT", "C", Sig(), dict(SZ, qty=qty), dict(time="t", bar_t=300_000))
    return dict(own())

# =====================================================================
print("第 15 條：市價單要帶 RESULT，「成功」看 executedQty")
fx = fresh()
open_pos(fx)
check("V1", "（前提）開倉真的送出市價單", len(markets(fx)) == 1)
check("V1", "所有市價單都帶 newOrderRespType=RESULT", bool(markets(fx)) and all(c[2].get("newOrderRespType") == "RESULT" for c in markets(fx)))

fx = fresh(); fx.fill_mode = mode_for(entry="later")               # 回 NEW，查單時才成交
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
o = own(); real = [x for x in fx.orders.values() if x["type"] == "MARKET"]
first = [c for c in markets(fx)]
check("V2", "（前提）開倉單送出了，交易所一開始回的是 NEW、成交 0（查單時才會成交）", len(real) == 1 and real[0]["_mode"] == "later")
check("V2", "回 NEW → 用單號查單，拿到最終成交量與均價", o.get("qty") == 100 and o.get("fill") == float(real[0]["avgPrice"]) if real else False,
      f"帳上 qty={o.get('qty')} fill={o.get('fill')}；交易所 {real and real[0]['avgPrice']}")

fx = fresh(); fx.fill_mode = mode_for(entry="never")               # 一直卡在 NEW
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
real = [x for x in fx.orders.values() if x["type"] == "MARKET"]
check("V2", "（前提）開倉單一直卡在 NEW", len(real) == 1 and fx.qty("XUSDT", "LONG") == 0)
check("V2", "卡在 NEW → 撤掉那張單（之後才成交的數量才不會沒人知道）", real and real[0]["status"] == "CANCELED", f"status={real and real[0]['status']}")
check("V2", "撤掉後成交 0 → 不記帳、不留 pending", bool(real) and not own() and not store.get().get("pending"), f"own={own()} pending={store.get().get('pending')}")

fx = fresh(); fx.fill_mode = mode_for(entry="partial")             # 成交 40、其餘卡住
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
o = own(); stops = [x for x in fx.algo_orders.values() if x["symbol"] == "XUSDT"]
check("V2", "（前提）交易所上實際成交 40", fx.qty("XUSDT", "LONG") == 40)
check("V2", "部分成交 → 帳上數量用交易所確認的 40（不是送出的 100）", o.get("qty") == 40, f"qty={o.get('qty')}")
check("V2", "部分成交 → 停損數量也是 40", len(stops) == 1 and float(stops[0]["quantity"]) == 40, f"{stops}")

fx = fresh(); fx.fill_mode = mode_for(entry="expired")             # 交易所明確說沒成交
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
exp = [x for x in fx.orders.values() if x["type"] == "MARKET"]
check("V2", "（前提）開倉單真的送出、交易所回 EXPIRED 成交 0", len(exp) == 1 and exp[0]["status"] == "EXPIRED" and fx.qty("XUSDT", "LONG") == 0)
check("V2", "EXPIRED 成交 0 → 不記帳、pending 直接丟掉（不用等 3 分鐘）", bool(exp) and not own() and not store.get().get("pending"),
      f"own={bool(own())} pending={store.get().get('pending')}")

# =====================================================================
print("第 15 條：程式自己送的部分出場（1R 減碼）也要看 executedQty")
fx = fresh(); p = open_pos(fx)
fx.fill_mode = mode_for(close="never"); fx.price = 1.10
B.klines = lambda *a, **k: [dict(t=600_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]; manager._now = lambda: 10**15
pp = dict(own()); run(lambda: manager.step("XUSDT", pp))
check("V1", "（前提）1R 減碼單真的送出、卡在 NEW", any(x["status"] in ("NEW", "CANCELED") and x["_p"].get("reduceOnly") == "true" for x in fx.orders.values())
      and fx.qty("XUSDT", "LONG") == 100)
check("V1", "減碼單沒成交 → 帳上數量不能減半（交易所上還是 100）", pp.get("qty") == 100, f"帳上 qty={pp.get('qty')}")

# =====================================================================
print("第 15 條：平倉不能只看回應——三條平倉路徑（清單用法第 5 點 r43：呼叫端多，至少跑三條）")
for path, act in (("手動平倉", lambda: main.manage("close", "XUSDT")),
                  ("待平倉重試", lambda: manager.retry_close("XUSDT", dict(own(), want_close="時間"))),
                  ("守衛補掛遇 -2021", None)):
    fx = fresh(); p = open_pos(fx)
    sid = own().get("stop_id")
    if act is None:                                                  # 守衛：停損不見、補掛時價格已穿過
        fx.algo_orders.clear(); manager._missing["XUSDT"] = 2
        fx.inject.append(dict(path="/fapi/v1/algoOrder", method="POST", times=5, kind="http", code=400, body='{"code":-2021,"msg":"x"}'))
        act = lambda: manager.ensure_stop("XUSDT", own())
    fx.fill_mode = mode_for(close="never")
    mark = len(fx.calls)
    r, e = run(act)
    closes = [x for x in fx.orders.values() if x["_p"].get("reduceOnly") == "true"]
    check("V3", f"（前提）{path}：平倉單真的送出、一直卡在 NEW，交易所上部位還在", closes and fx.qty("XUSDT", "LONG") == 100, f"err={e}")
    check("V3", f"{path}：沒成交 → 不結帳、帳上部位還在", not store.get().get("closed") and bool(own()), f"closed={len(store.get().get('closed') or [])}")
    if path != "守衛補掛遇 -2021":
        check("V3", f"{path}：沒成交 → 交易所停損不能撤", sid in fx.algo_orders)
    check("V3", f"{path}：卡住的平倉單要撤掉（不跟下一次重試的單重疊）", bool(closes) and all(x["status"] == "CANCELED" for x in closes),
          f"{[x['status'] for x in closes]}")   # 空清單的 all() 是 True——先要求真的有平倉單

# =====================================================================
print("第 15 條補充：部分成交的平倉，出場價要兩段加權")
fx = fresh(); p = open_pos(fx)
fx.fill_mode = mode_for(close="partial"); fx.price = 1.20
r, e = run(lambda: main.manage("close", "XUSDT"))
check("V4", "（前提）第一次沒平完（只成交一部分）、進了待平倉", 0 < fx.qty("XUSDT", "LONG") < 100 and own().get("want_close"), f"交易所剩 {fx.qty('XUSDT', 'LONG')}")
fx.fill_mode = None; fx.price = 0.90
fx.inject.append(dict(path="/fapi/v1/userTrades", times=20, kind="empty"))   # 成交明細查不到：只能靠自己記的
r, e = run(lambda: manager.retry_close("XUSDT", dict(own())))
c = (store.get().get("closed") or [{}])[-1]
check("V4", "（前提）重試把剩下的平掉了", fx.qty("XUSDT", "LONG") == 0 and bool(c.get("symbol")), f"err={e}")
check("V4", "成交明細查不到時，出場價不能只用最後一張單的均價（那只算到後半段）",
      bool(c.get("symbol")) and (c.get("exit") is None or abs(c["exit"] - 0.899) > 0.01),
      f"exit={c.get('exit')}（最後一張單約 0.899、前一段約 1.199）")

# =====================================================================
print("第 8 條 r41：不合法的值不能跳過、其他照套、回報成功")
fx = fresh()
before = C.SCAN.get("watch_chg24")
for form, why in (({"SCAN.watch_chg24": 15, "SCAN.typo": 5}, "欄位名稱打錯"), ({"SCAN.watch_chg24": 15, "RISK.max_pos": "abc"}, "值打錯")):
    r, e = run(lambda: params.validate(form)) if hasattr(params, "validate") else (None, "舊版沒有 validate")
    check("V7", f"（前提）程式有整批驗證參數的函式（{why}）", hasattr(params, "validate"))
    check("V7", f"{why} → 整批不合法，而且講明是哪個欄位", isinstance(r, tuple) and r[1] and any(k in " ".join(r[1]) for k in form if k != "SCAN.watch_chg24"),
          f"回傳={r} err={e}")
    # 網頁「套用到實盤」那條路。舊版沒有 set_live_form 時，照網頁原本的做法走（存進參數檔、再套用），才測得到舊行為
    run(lambda: main.set_live_form(form)) if hasattr(main, "set_live_form") else run(lambda: (presets.set_live(form), presets.apply_live()))
    check("V7", f"{why} → 其他欄位也不能套上（watch_chg24 仍是 {before}）", C.SCAN.get("watch_chg24") == before and "typo" not in C.SCAN,
          f"watch_chg24={C.SCAN.get('watch_chg24')}")
    check("V7", f"{why} → 不合法的表單不能存進參數檔", form != (presets.live() or {}), f"live={presets.live()}")
presets.set_live({}); presets.apply_live()

# =====================================================================
print("第 8 條 r43：讀取失敗期間原檔「不見了」不是全新開始")
fx = fresh()
path = os.path.join(os.environ["DATA_DIR"], "state.json")
open(path, "w").write('{"open": {"XUSDT": ')
importlib.reload(store); main._rc["t"] = 0
check("V8", "（前提）讀取失敗、還沒載入", getattr(store, "LOADED", True) is False)
if os.path.exists(path): os.remove(path)                             # 讀取失敗期間，有人把原檔搬走了（舊版讀取失敗時自己就改名了，r35：不能讓測試崩掉）
run(lambda: store.retry_load())
check("V8", "原檔不見了 → 仍是讀取失敗（不能恢復開新倉、帳是空的）", getattr(store, "LOADED", True) is False)
open(path, "w").write("{}")                                          # 確定要全新開始：放一份內容為 {} 的檔
run(lambda: store.retry_load())
check("V8", "放一份 {} 的檔 → 才算全新開始", getattr(store, "LOADED", False) is True)

# =====================================================================
print("第 8 條 r43：設定檔（參數集）也一樣")
fx = fresh()
presets.save_preset("我的參數", {"SCAN.watch_chg24": 20})
pp_path = presets.PATH
good = open(pp_path).read()
open(pp_path, "w").write(good[:len(good) // 2])                     # 寫到一半的壞檔
check("V9", "（前提）存檔之前，參數集檔真的解析不了", run(lambda: json.load(open(pp_path)))[1] is not None)
r, e = run(lambda: presets.save_preset("新的", {"SCAN.watch_chg24": 25}))
check("V9", "讀取失敗 → 存檔不能把壞檔蓋掉（使用者的參數集會全部消失）", open(pp_path).read() == good[:len(good) // 2], f"檔案現在 {open(pp_path).read()[:60]!r}")
check("V9", "讀取失敗 → 存檔回報失敗（不是靜靜成功）", e is not None or (isinstance(r, dict) and r.get("error")), f"回傳={r} err={e}")
check("V9", "讀取失敗 → 壞檔複製一份", any(f.startswith("presets.json.bad") for f in os.listdir(os.environ["DATA_DIR"])))
check("V9", "讀取失敗 → 推播", *one("🐞 參數集檔讀取失敗"))
if os.path.exists(pp_path): os.remove(pp_path)
for f in os.listdir(os.environ["DATA_DIR"]):
    if f.startswith("presets.json"): os.remove(os.path.join(os.environ["DATA_DIR"], f))

finish()
