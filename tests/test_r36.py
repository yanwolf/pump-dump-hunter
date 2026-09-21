# 清單 r34 → r36 差異的行為測試。所有出場都用 fx.trigger() 留下真的成交。
# 在專案根目錄執行：python -m tests.test_r36
import importlib, json
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, os, run, store,
                           time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

def closed(): return (store.get().get("closed") or [{}])[-1]
def seg(ts):
    q = sum(float(t["qty"]) for t in ts)
    return float(f"{sum(float(t['price']) * float(t['qty']) for t in ts) / q:.6g}"), \
           round(sum(float(t["realizedPnl"]) - float(t["commission"]) for t in ts), 2)

def restart():
    """模擬服務重啟：狀態檔留著，模組重新載入（清單第 8 條 r35：用真的存檔、重新載入）。"""
    importlib.reload(store)
    main._rc["t"] = 0

# =====================================================================
print("第 8 條 r35：重啟後，「不明」的 pending 照樣被確認（期限不能只在記憶體）")
fx = fresh()
B.market_order("XUSDT", "BUY", 100)                                  # 送出去的單其實成交了
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=int(time.time() * 1000), base_qty=0)})
path = os.path.join(os.environ["DATA_DIR"], "state.json")
check("S1", "（前提）pending 真的寫進狀態檔", "XUSDT" in (json.load(open(path)).get("pending") or {}))
restart()
check("S1", "（前提）重啟後從狀態檔還原了 pending", "XUSDT" in (store.get().get("pending") or {}))
main.reconcile(force=True)
check("S1", "重啟後照樣逐幣確認、認領回來（不是「沒有期限就不確認」）", "XUSDT" in store.get().get("open", {}) and not store.get().get("pending"))

fx = fresh()
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
m0 = (store.get().get("open", {}).get("XUSDT") or {}).get("trade_mark")
restart()
m1 = (store.get().get("open", {}).get("XUSDT") or {}).get("trade_mark")
check("S2", "（前提）開倉成交了、帳上有這筆", "XUSDT" in store.get().get("open", {}), f"skipped={rec.get('skipped')}")
# 要測的行為，不寫成前提（r36）
check("S2", "開倉當下就記下了起始界線（成交 id，不是平倉時才找）", isinstance(m0, int) and m0 > 0, f"trade_mark={m0}")
check("S2", "起始界線跟著部位存進狀態檔，重啟後還在", m0 is not None and m1 == m0, f"重啟前 {m0} 重啟後 {m1}")   # 兩邊都 None 也會「相等」

# =====================================================================
print("第 8 條 r36：交易所時鐘比本機慢——有成交 id 界線時不能再用時間篩")
fx = fresh(); fx.clock_offset_ms = -10_000                          # 交易所慢 10 秒
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.price = 0.95
t, e = run(lambda: fx.trigger("XUSDT", "LONG", 100))
main._rc["t"] = 0; main.reconcile(force=True)
c = closed(); pos_ts = c.get("ts")
check("S3", "（前提）平倉成交的交易所時間戳，早於帳上記的開倉時間（本機時鐘）", t and pos_ts and t["time"] < pos_ts, f"成交 {t and t['time']} 開倉 {pos_ts}")
check("S3", "出場價照樣查得到（用成交 id 界線，不被時間篩掉）", t and c.get("exit") == float(f"{float(t['price']):.6g}"), f"exit={c.get('exit')}")

# =====================================================================
print("第 8 條 r35：成交明細有筆數上限——界線之後的平倉成交不能漏")
fx = fresh()
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.price = 0.95
fills = [fx.trigger("XUSDT", "LONG", 50), fx.trigger("XUSDT", "LONG", 50)] if fx.qty("XUSDT", "LONG") >= 100 else []
for i in range(600): fx._trade("XUSDT", "BUY", 1, 1.0, 0.0, "LONG")   # 平倉之後，同幣別的專案又成交了 600 筆（買單，不是我的平倉）
recent = {t["id"] for t in fx.trades[-500:]}
check("S2", "（前提）兩筆平倉成交真的在明細裡，而且已經被擠出「最近 500 筆」",
      len(fills) == 2 and all(f["id"] not in recent for f in fills), f"fills={[f['id'] for f in fills]}")
main._rc["t"] = 0; main.reconcile(force=True)
want_px, want_pnl = seg(fills) if fills else (None, None)
c = closed()
check("S2", "出場價與損益含界線之後的每一筆平倉成交（帶 fromId 往後查，不是最近 N 筆）",
      want_px is not None and c.get("exit") == want_px and c.get("pnl") == want_pnl, f"exit={c.get('exit')} pnl={c.get('pnl')} 應為 {want_px} {want_pnl}")

fx = fresh()
fx.open("XUSDT", "LONG", 30, entry=1.5); fx.price = 2.0; old = fx.trigger("XUSDT", "LONG", 30)   # 這個幣很久以前的平倉
fx.price = 1.0; B.market_order("XUSDT", "BUY", 100)
store.update(open={"XUSDT": dict(engine="C", side="LONG", qty=100, entry=1.0, fill=1.001, stop=0.9, r_unit=0.1, base_qty=0,
                                 ts=None, trade_mark=None, bar_t=300_000, last_t=300_000)})   # 界線與時間戳都無效
fx.price = 0.95; t, e = run(lambda: fx.trigger("XUSDT", "LONG", 100))
main._rc["t"] = 0; main.reconcile(force=True)
c = closed()
check("S2", "（前提）結帳了", bool(c.get("symbol")), f"err={e}")
check("S2", "界線與時間戳都無效 → 出場價記未知（不能退成 0、把歷史上的平倉算進來）", c.get("exit") is None, f"exit={c.get('exit')}（舊的那筆 {old['price']}）")

# =====================================================================
print("第 8 條 r36：存檔失敗不能被吞掉")
fx = fresh()
real_dump = store.json.dump; calls = []
def full_disk(*a, **k):
    calls.append(1)
    if len(calls) <= 3: raise OSError(28, "No space left on device")
    return real_dump(*a, **k)
store.json.dump = full_disk
r, e = run(lambda: store.update(open={"XUSDT": dict(engine="C", side="LONG", qty=1)}))
check("S4", "（前提）存檔真的失敗了", len(calls) >= 1)
check("S4", "存檔失敗不往外拋進交易流程（記憶體裡的狀態照樣是對的）", e is None and "XUSDT" in store.get().get("open", {}), f"err={e}")
check("S4", "存檔失敗要推播", *one("🐞 狀態檔存檔失敗", "No space left"))
for _ in range(4): run(lambda: store.update(loop="x"))               # 之後磁碟恢復（包進 run：修改前的程式在這裡會往外拋，不能讓測試崩掉）
store.json.dump = real_dump
check("S4", "存檔恢復時要通知", *one("✅ 狀態檔存檔已恢復"))
check("S4", "存檔失敗的訊息也進錯誤區", any("存檔失敗" in x for x in store.get().get("errors", [])))

import threading
fx = fresh()
calls.clear(); store.json.dump = full_disk                           # 再讓存檔失敗一次
real_send = store.telegram.send if hasattr(store, "telegram") else None
from app import telegram as tg_mod
saved_send = tg_mod.send
def send_that_fails_and_logs(m):                                     # 模擬 Telegram 送出失敗：失敗路徑會回頭寫錯誤區（store.push）
    TG.append(m); store.push("errors", "Telegram 送出失敗（模擬）")
tg_mod.send = send_that_fails_and_logs
done = []
th = threading.Thread(target=lambda: (store.update(loop="y"), done.append(1)), daemon=True); th.start(); th.join(5)
tg_mod.send = saved_send; store.json.dump = real_dump
check("S4", "（前提）存檔真的失敗、而且真的去推播了", len(calls) >= 1 and any("存檔失敗" in m for m in TG), f"calls={len(calls)}")
check("S4", "存檔失敗 → 推播 → 推播失敗寫錯誤區：不能死鎖（5 秒內完成）", bool(done), "" if done else "卡住了：鎖不可重入")

fx = fresh()
open(path, "w").write('{"open": {"XUSDT": ')                        # 寫到一半當機留下的壞檔
broken, e0 = run(lambda: json.load(open(path)))
restart()
bad = [f for f in os.listdir(os.environ["DATA_DIR"]) if f.startswith("state.json.bad")]
check("S4", "（前提）重新載入之前，狀態檔真的解析不了", broken is None and e0 is not None, f"err={e0}")
check("S4", "狀態檔讀取失敗不能靜靜回到空白：進錯誤區", any("狀態檔讀取失敗" in x for x in store.get().get("errors", [])), f"{store.get().get('errors', [])[-1:]}")
check("S4", "壞掉的檔另外留一份（不被下一次存檔蓋掉）", len(bad) >= 1, f"{os.listdir(os.environ['DATA_DIR'])}")
check("S4", "開機時會推播（store 載入時記下，由主程式開機時送出）", bool(getattr(store, "LOAD_ERROR", None)))
store.update(errors=[])

fx = fresh()
store.update(open={"XUSDT": dict(engine="C", side="LONG", qty=100, entry=1.0, fill=1.0, stop=0.9, stop_id=1, stop_via="algo",
                                 stale_ids=[[2, "algo"]], ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000, base_qty=0)})
fx.open("XUSDT", "LONG", 100)
fx.algo_orders[1] = dict(algoId=1, symbol="XUSDT", side="SELL", orderType="STOP_MARKET", quantity="100")
fx.algo_orders[2] = dict(algoId=2, symbol="XUSDT", side="SELL", orderType="STOP_MARKET", quantity="100")
fx.inject.append(dict(path="/fapi/v1/algoOrder", method="DELETE", times=5, kind="http", code=503, body="busy"))
B.klines = lambda *a, **k: []
manager.run()
check("S4", "（前提）守衛真的去撤了殘留的舊停損、而且撤不掉", any(c[0] == "DELETE" and c[2].get("algoId") == "2" for c in fx.calls))
check("S4", "守衛撤自己的殘留停損失敗 → 進待撤清單、推播（原本靜靜略過）", "2" in store.get().get("leftover", {}) and
      one("⚠️ XUSDT 移損後舊停損單 2", "撤不掉")[0], f"leftover={list(store.get().get('leftover', {}))} {TG}")

finish(allowed=("No space left",))
