# 績效統計（依引擎、R 為核心、依幣種、實際滑價、參數版本）。預期值都是手算的，不拿統計模組自己的結果當答案（第 24 種）。
# 在專案根目錄執行：python -m tests.test_stats
from tests.harness import (one, B, TG, check, fresh, finish, main, manager, run, store,
                           time)   # 共用案例框架
from app import presets, stats

def T(engine, sym, pnl, r, side="SHORT", by="停損單", entry=1.0, fill=1.0, stop=1.1, exit=None, ver=None, closed="09-23 01:00"):
    d = dict(engine=engine, symbol=sym, pnl=pnl, r=r, side=side, by=by, entry=entry, fill=fill, stop=stop, exit=exit, closed=closed)
    if ver: d["ver_label"] = ver
    return d

# 手算用的資料：C 三筆（+2R、-1R、損益未知）、G 兩筆（-0.5R、+1R）
DATA = [T("C", "AUSDT", 20.0, 2.0, closed="09-21 10:00"),
        T("G", "BUSDT", -5.0, -0.5, side="LONG", by="時間", entry=1.0, fill=1.002, stop=0.9, closed="09-21 11:00"),
        T("C", "BUSDT", -10.0, -1.0, entry=1.0, fill=0.997, stop=1.1, exit=1.102, closed="09-21 12:00", ver="自訂 abc123"),
        T("C", "CUSDT", None, None, by="停損單", closed="09-21 13:00", ver="自訂 abc123"),
        T("G", "AUSDT", 10.0, 1.0, side="LONG", by="手動", closed="09-21 14:00", ver="自訂 abc123")]

print("依引擎、R 為核心（預期值手算）")
fx = fresh()
rep = stats.report(DATA); E = rep["engines"]
a = E["全部"]
check("S1", "全部：5 筆、損益已知 4、未知 1（不算輸贏、不當 0）", (a["total"], a["known"], a["unknown"]) == (5, 4, 1), f"{a}")
check("S1", "全部：勝 2／已知 4 → 勝率 50%", a["wins"] == 2 and a["win_rate"] == 50.0)
check("S1", "全部：期望值＝(2 −0.5 −1 +1)/4 ＝ 0.375 → 0.38R；累計 1.5R", a["expectancy_r"] == 0.38 and a["total_r"] == 1.5, f"{a['expectancy_r']} {a['total_r']}")
check("S1", "全部：獲利因子＝(2+1)/(0.5+1)＝2.0；平均賺 1.5R、平均賠 −0.75R", a["profit_factor"] == 2.0 and a["avg_win_r"] == 1.5 and a["avg_loss_r"] == -0.75)
check("S1", "全部：最大回撤＝累計 R 2 → 1.5 → 0.5 → 1.5，從 2 回落到 0.5 ＝ 1.5R（09-21 10:00 → 12:00）",
      a["max_dd_r"] == 1.5 and a["dd_from"] == "09-21 10:00" and a["dd_to"] == "09-21 12:00", f"{a['max_dd_r']} {a['dd_from']} {a['dd_to']}")
check("S1", "全部：最長連敗 2（−0.5R、−1R 連續）；損益 U 合計 15", a["max_losing_streak"] == 2 and a["pnl"] == 15.0)
c = E["C"]
check("S1", "C：3 筆、未知 1、勝率 50%、期望值 0.5R；沒有 F 就不列 F", c["total"] == 3 and c["unknown"] == 1 and c["win_rate"] == 50.0
      and c["expectancy_r"] == 0.5 and "F" not in E, f"{c}")

print("實際滑價（正數＝不利）")
check("S2", "C 空單：訊號 1.0、成交 0.997（賣便宜了）→ 進場滑價 +0.3%", c["slip_entry_max"] == 0.3, f"{c['slip_entry_avg']} {c['slip_entry_max']}")
check("S2", "C 空單停損：停損 1.1、成交 1.102（買貴了）→ 停損滑價 +0.182%，只算停損單出場", c["slip_stop_avg"] == 0.182 and c["slip_stop_n"] == 1,
      f"{c['slip_stop_avg']} n={c['slip_stop_n']}")
g = E["G"]
check("S2", "G 多單：訊號 1.0、成交 1.002（買貴了）→ 進場滑價 +0.2%；時間／手動出場不算停損滑價", g["slip_entry_max"] == 0.2 and g["slip_stop_n"] == 0)

print("出場原因、依幣種、參數版本")
check("S3", "出場原因：停損單 3、時間 1、手動 1", a["reasons"] == {"停損單": 3, "時間": 1, "手動": 1}, f"{a['reasons']}")
sy = {x["symbol"]: x for x in rep["symbols"]}
check("S3", "依幣種：BUSDT 兩筆（C、G）累計 −1.5R，排在最前面（虧最多）", rep["symbols"][0]["symbol"] == "BUSDT" and sy["BUSDT"]["total_r"] == -1.5
      and sy["BUSDT"]["engines"] == "CG", f"{rep['symbols'][0]}")
vers = {(v["version"], v["engine"]): v for v in rep["versions"]}
check("S3", "參數版本：沒記錄的歸「舊版（未記錄）」、同一版本依引擎分開", ("舊版（未記錄）", "C") in vers and ("自訂 abc123", "C") in vers
      and vers[("自訂 abc123", "C")]["total"] == 2 and vers[("自訂 abc123", "C")]["unknown"] == 1, f"{list(vers)}")
u = stats.compute([T("F", "X", None, None)])
check("S3", "沒有已知損益時期望值、損益 U、累計都是 —（None），不是 0", u["expectancy_r"] is None and u["pnl"] is None and u["total_r"] is None, f"{u}")
far = stats.compute([T("C", "Y", -3.0, -0.3, side="SHORT", by="停損單", stop=1.1, exit=1.25)])
check("S2", "「停損單」但成交價離停損價超過 2%（像是在 App 手動平倉）→ 不算停損滑價、另外計數", far["slip_stop_n"] == 0 and far["slip_stop_far"] == 1,
      f"n={far['slip_stop_n']} far={far['slip_stop_far']}")

print("開倉時記下當下的參數版本")
class Sig:
    def __init__(s): s.side, s.entry, s.stop, s.engine, s.reason = "LONG", 1.0, 0.9, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)
fx = fresh(); presets.set_live({}); presets.apply_live()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
o = store.get().get("open", {}).get("XUSDT") or {}
check("S4", "（前提）開倉成交", bool(o))
check("S4", "沒有實盤覆蓋 → 記成「預設參數」", o.get("ver_label") == "預設參數", f"{o.get('ver_label')}")
fx = fresh(); presets.set_live({"SCAN.watch_chg24": 15}); presets.apply_live()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
o1 = (store.get().get("open", {}).get("XUSDT") or {}).get("ver")
fx = fresh(); presets.set_live({"SCAN.watch_chg24": 15}); presets.apply_live()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
o2 = (store.get().get("open", {}).get("XUSDT") or {}).get("ver")
fx = fresh(); presets.set_live({"SCAN.watch_chg24": 20}); presets.apply_live()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
o3 = (store.get().get("open", {}).get("XUSDT") or {}).get("ver")
check("S4", "同一組實盤參數 → 同一個版本；改了參數 → 不同版本", o1 and o1 == o2 and o3 and o3 != o1, f"{o1} {o2} {o3}")
fx = fresh(); presets.set_live({}); presets.apply_live()
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
fx.price = 0.9
if fx.qty("XUSDT", "LONG") >= 100: fx.trigger("XUSDT", "LONG", 100)
main._rc["t"] = 0; main.reconcile(force=True)
cl = (store.get().get("closed") or [{}])[-1]
check("S4", "版本跟著部位進到已平倉紀錄", cl.get("ver_label") == "預設參數", f"{cl.get('ver_label')}")

print("網頁的狀態 API 帶著統計")
fx = fresh(); store.update(closed=list(DATA))                        # 自成一個情境：帳上就是上面那 5 筆手算資料
h = main.H.__new__(main.H); h.headers = {}; h.path = "/api/state"
import json
body, ct = h._route(); s = json.loads(body)
st = s.get("stats") or {}
check("S5", "狀態 API 的統計算的就是帳上那 5 筆：全部期望值 0.38R、C 勝率 50%", (st.get("scope") or {}).get("n") == 5
      and st["engines"]["全部"]["expectancy_r"] == 0.38 and st["engines"]["C"]["win_rate"] == 50.0, f"{str(st)[:120]}")
presets.set_live({}); presets.apply_live()

finish()
