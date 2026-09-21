# 清單 r37 → r39 差異的行為測試。
# 在專案根目錄執行：python -m tests.test_r39
import importlib, json
from tests.harness import (one, B, C, TG, check, fresh, finish, main, manager, os, run, store,
                           telegram, time)   # 共用案例框架（清單用法第 5 點 r27）

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
PATH = lambda: os.path.join(os.environ["DATA_DIR"], "state.json")
CORRUPT = '{"open": {"XUSDT": {"engine": "C", "side": "LONG", "qty": 100'   # 寫到一半當機留下的壞檔
GOOD = json.dumps(dict(open={"XUSDT": dict(engine="C", side="LONG", qty=100, entry=1.0, fill=1.0, stop=0.9, base_qty=0,
                                           ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000)}))

def restart():
    importlib.reload(store); main._rc["t"] = 0

def broken_start():
    """狀態檔壞掉的那次重啟。回傳重新載入前那個檔是不是真的解析不了（前提用）。"""
    open(PATH(), "w").write(CORRUPT)
    _, e0 = run(lambda: json.load(open(PATH())))
    restart()
    return e0 is not None

# =====================================================================
print("第 8 條 r38：讀取失敗 ≠ 沒有資料；讀不到不開新倉")
fx = fresh()
check("U1", "（前提）重新載入前，狀態檔真的解析不了", broken_start())
check("U1", "（前提）程式有「已載入」這個狀態（舊版沒有 → 斷言失敗，不是測試崩掉）", hasattr(store, "LOADED"))
check("U1", "讀取失敗 → 標記成「還沒載入」（不是當成沒有持倉）", getattr(store, "LOADED", True) is False)
mark = len(fx.calls)
rec, e = run(lambda: main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000)))
check("U1", "還沒載入 → 開倉函式本身就不送單（自動、手動都經過它）",
      not any(c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET" for c in fx.calls[mark:]), f"err={e}")
check("U1", "不送單的原因講明是持倉紀錄還沒載入", rec is not None and "載入" in (rec.get("skipped") or ""), f"skipped={rec and rec.get('skipped')}")
fx.open("XUSDT", "LONG", 100)
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("U1", "還沒載入 → 對帳不動帳（不認領、不結帳）", not store.get().get("closed") and not store.get().get("open"), f"err={e}")
check("U1", "還沒載入 → 對帳講明無法對帳", *one("⚠️ 持倉紀錄還沒載入"))
r, e = run(lambda: main.manage("adopt", "XUSDT", "C", "0.9"))
check("U1", "還沒載入 → 手動認領也擋（會把部位寫進帳）", isinstance(r, dict) and "載入" in (r.get("error") or ""), f"{r} err={e}")

# =====================================================================
print("第 8 條 r39：讀取失敗期間，存檔不能覆寫原檔（假恢復）")
fx = fresh()
check("U2", "（前提）狀態檔真的壞了", broken_start())
store.update(loop="讀取失敗期間的一次存檔")
check("U2", "讀取失敗期間存檔 → 原檔內容沒被覆寫", open(PATH()).read() == CORRUPT, f"原檔現在是 {open(PATH()).read()[:60]!r}")
restart()
check("U2", "再重啟一次 → 仍然是讀取失敗（不會因為被空白蓋掉而「假恢復」）", getattr(store, "LOADED", True) is False and not store.get().get("errors") == [])
check("U2", "壞檔另外留一份", any(f.startswith("state.json.bad") for f in os.listdir(os.environ["DATA_DIR"])))

fx = fresh()
check("U1", "（前提）狀態檔真的壞了", broken_start())
check("U1", "（前提）程式有重試載入的函式", hasattr(store, "retry_load"))
for _ in range(5): run(lambda: store.retry_load())
check("U1", "重試一直失敗 → 照節奏推播（5 次發第 1、5 次）", len([m for m in TG if m.startswith("🐞 狀態檔讀取失敗")]) == 2,
      f"{[m[:30] for m in TG]}")
open(PATH(), "w").write(GOOD)                                       # 有人修好了檔案
run(lambda: store.retry_load())
check("U1", "修好後重試 → 載入成功、恢復開倉", getattr(store, "LOADED", False) is True and "XUSDT" in store.get().get("open", {}))
check("U1", "載入恢復要通知", *one("✅ 狀態檔已載入"))

# =====================================================================
print("第 8 條 r39：讀到一半出錯不能留下半套（參數覆蓋一邊解析一邊套用）")
fx = fresh()
before = C.SCAN.get("watch_chg24")
r, e = run(lambda: C.apply_overrides({"SCAN": {"watch_chg24": 99}, "EXIT": {"C": 5}}))   # 第二個欄位格式壞掉
check("U3", "（前提）套用真的在第二個欄位出錯", e is not None, f"err={e}")
check("U3", "出錯 → 第一個欄位也沒被換掉（全部解析成功才一次換上）", C.SCAN.get("watch_chg24") == before, f"watch_chg24={C.SCAN.get('watch_chg24')} 原本 {before}")
C.SCAN["watch_chg24"] = before

# =====================================================================
print("第 8 條 r39：推播設定錯也不能被吞掉")
fx = fresh()
real = importlib.reload(telegram)                                   # 拿真的 send（框架把它換成收集器了）
real.TOKEN, real.CHAT = "", ""
real_send = real.send
telegram.send = lambda m: TG.append(m)                              # 還原框架的收集器（其他模組用的）
r, e = run(lambda: real_send("測試"))
check("U5", "（前提）真的呼叫了推播函式、沒拋錯", e is None, f"err={e}")
check("U5", "沒設 TG_TOKEN／TG_CHAT → 寫錯誤區（不是靜靜不送）", any("Telegram 未設定" in x for x in store.get().get("errors", [])),
      f"{store.get().get('errors', [])[-1:]}")
run(lambda: real_send("再一次"))
check("U5", "設定錯的紀錄不會每則都寫（只記一次）", sum("Telegram 未設定" in x for x in store.get().get("errors", [])) == 1)
manager.telegram.send = telegram.send; main.telegram.send = telegram.send

finish()
