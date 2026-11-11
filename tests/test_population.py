# 母體偏差檢查（app/population.py）：標籤手算、無偏差母體的挑時段不漏單、實盤對照與整段跑、跟其他研究互斥。
# 在專案根目錄執行：python -m tests.test_population
import json, os, time
from tests.harness import (B, TG, check, fresh, finish, store)   # 共用案例框架
from app import backtest, labels, main, population, sweep

DAY, HOUR = 86400000, 3600000

def series(t0):
    """t0 起：平盤 2 天 → 15 根各 +4% 爆量拉升 → 頂部 30 根 → 15 根各 −2.5% 爆量崩 → 平盤到滿 4 天（1m）。"""
    k, t, p = [], t0, 100.0
    def add(o, c, v):
        nonlocal t
        k.append(dict(t=t, o=o, h=max(o, c) * 1.001, l=min(o, c) * 0.999, c=c, v=v)); t += 60000
    for j in range(2880): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    for _ in range(15): add(p, p * 1.04, 10.0); p *= 1.04
    for j in range(30): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    for _ in range(15): add(p, p * 0.975, 20.0); p *= 0.975
    while len(k) < 4 * 1440: add(p, p * (1.001 if len(k) % 2 else 0.999), 1.0)
    return k

def hourly(k, span=HOUR):
    out = {}
    for x in k:
        h = x["t"] - x["t"] % span
        if h not in out: out[h] = dict(t=h, o=x["o"], h=x["h"], l=x["l"], c=x["c"], v=x["v"])
        else:
            o = out[h]; o["h"] = max(o["h"], x["h"]); o["l"] = min(o["l"], x["l"]); o["c"] = x["c"]; o["v"] += x["v"]
    return [out[h] for h in sorted(out)]

print("進場標籤（手算）")
fx = fresh()
kk = [dict(t=i * 60000, o=100.0, h=100.0, l=100.0, c=100.0, v=1.0) for i in range(1440)] + [dict(t=1440 * 60000, o=100.0, h=150.0, l=100.0, c=150.0, v=1.0)]
g = population.entry_tags("G", kk, 1440, 160.0, 90.0, 100.0)
check("P1", "（前提）1441 根：24h 區間 150/100 ＝ 50%、24h 漲跌（收盤比 1440 根前）＝ +50%", g["gain24"] == 50.0 and g["chg24"] == 50.0, f"{g}")
check("P1", "G：當天最高 160／前收 100 ＝ +60% → 在暴漲日母體；24h 漲 50% ≥ 40% → 實盤名單會盯；區間 ≥ 40%",
      g["pop"] == "在" and g["watch"] == "會盯" and g["band"] == "≥40%", f"{g}")
f = population.entry_tags("F", kk, 1440, 160.0, 90.0, 100.0)
check("P1", "F：當天最低 90／前收 100 只跌 10% → 不在崩盤日母體；區間 50% < 80%", f["pop"] == "不在" and f["band"] == "<80%", f"{f}")
short = population.entry_tags("G", kk[-100:], 99, None, None, None)
check("P1", "不滿 24h 的 K、沒有日 K → 對應標籤都是 None（不硬算）", all(short[x] is None for x in ("pop", "watch", "band", "chg24")), f"{short}")

print("當天高低與前日收盤（從小時 K）")
fx = fresh()
h1 = [dict(t=DAY - HOUR, h=10, l=9, c=9.5), dict(t=DAY, h=12, l=8, c=11), dict(t=DAY + 5 * HOUR, h=20, l=10, c=15), dict(t=2 * DAY, h=99, l=1, c=50)]
hi, lo, pc = population.day_stats(h1, DAY + 3 * HOUR)
check("P2", "（前提）進場在第二天 03:00：當天最高 20（含進場之後的 05:00）、最低 8、前日收 9.5；隔天的不算", (hi, lo, pc) == (20, 8, 9.5), f"{hi} {lo} {pc}")
check("P2", "沒有前一天的小時 K → 三個都 None", population.day_stats(h1[1:], DAY + 3 * HOUR) == (None, None, None))

print("無偏差母體：用必要條件挑時段，跟整段跑的 F、G 交易完全一樣（不漏單、不多單）")
fx = fresh()
T0 = 1_780_000_000_000 - 1_780_000_000_000 % DAY
k = series(T0)
check("P3", "（前提）合成 4 天 1m、拉升與崩跌在第 3 天", len(k) == 5760)
full = [(e, t["t"], t["r"]) for e in ("F", "G") for t in backtest.simulate(k, (e,)) if t["t"] >= T0 + DAY + HOUR]
check("P3", "（前提）整段跑：F、G 各至少一筆", {x[0] for x in full} == {"F", "G"}, f"{full}")
segs = population.trigger_segments(hourly(k))
got = []
for a, b, keep in segs:
    part = [x for x in k if a <= x["t"] < b]
    got += [(e, t["t"], t["r"]) for e in ("F", "G") for t in backtest.simulate(part, (e,)) if t["t"] >= keep]
check("P3", "挑出來的時段跑出同一組交易（引擎、進場時間、R 都一樣）", sorted(got) == sorted(full), f"{sorted(got)} vs {sorted(full)}")
check("P3", "只挑了拉升崩跌那一段（1 段），平盤的時段都沒抓", len(segs) == 1 and segs[0][2] <= min(x[1] for x in full), f"{segs}")
calm = [dict(t=T0 + i * 60000, o=100.0, h=100.1, l=99.9, c=100.0, v=1.0) for i in range(4 * 1440)]
segs0 = population.trigger_segments(hourly(calm))
check("P3", "（對照組）同樣長度的平盤：一段都不挑", segs0 == [])
cut = k[2899]["c"] if len(k) > 2900 else 100.0
segs2 = population.trigger_segments(hourly(k[:2900] + [dict(x, o=cut, h=cut, l=cut, c=cut) for x in k[2900:]]))
check("P3", "挑時段只看該小時為止：把拉升之後的 K 全換成平盤，挑出的第一段起點不變",
      len(segs2) >= 1 and len(segs) >= 1 and segs2[0][0] == segs[0][0], f"{segs2} {segs}")

print("無偏差母體（C，5m）：2h 高低差 ≥15% 挑時段，跟整段跑的 C 交易完全一樣")
fx = fresh()
def series5(t0):
    """5m：平盤 1 天 → 一根 −20% 爆量崩盤棒 → 一根小反彈 → 一根破低 → 平盤到滿 2 天。"""
    k, t, p = [], t0, 100.0
    def add(o, c, v, h=None, l=None):
        nonlocal t
        k.append(dict(t=t, o=o, h=h or max(o, c) * 1.001, l=l or min(o, c) * 0.999, c=c, v=v)); t += 300000
    for j in range(288): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    add(p, p * 0.8, 10.0); p *= 0.8
    add(p, p * 1.01, 3.0, l=p); p *= 1.01                 # 反彈棒不破崩盤棒低點
    add(p, p * 0.95, 4.0); p *= 0.95
    while len(k) < 576: add(p, p * (1.001 if len(k) % 2 else 0.999), 1.0)
    return k
k5 = series5(T0)
fullc = [(t["t"], t["r"]) for t in backtest.simulate(k5, ("C",)) if t["t"] >= T0 + 3 * HOUR]
check("P7", "（前提）合成 2 天 5m：C 在崩盤棒後破低進場一筆", len(fullc) == 1, f"{fullc}")
sp = population.SPECS["c5"]
segc = population.trigger_segments(hourly(k5), **sp)
gotc = []
for a, b, keep in segc:
    gotc += [(t["t"], t["r"]) for t in backtest.simulate([x for x in k5 if a <= x["t"] < b], ("C",)) if t["t"] >= keep]
check("P7", "挑出來的時段跑出同一筆（進場時間、R 一樣），只挑了 1 段", sorted(gotc) == sorted(fullc) and len(segc) == 1, f"{gotc} {segc}")
check("P7", "（對照組）平盤 2 天：C 一段都不挑", population.trigger_segments(hourly(calm[:576]), **sp) == [])

print("分組報表")
fx = fresh()
tr = [dict(engine="G", r=2.0, pop="在", watch="w", band="b"), dict(engine="G", r=-1.0, pop="不在", watch="w", band="b"),
      dict(engine="G", r=-1.0, pop="不在", watch=None, band="b"), dict(engine="F", r=1.0, pop="在", watch="w", band="b"),
      dict(engine="G", r=None, pop="在")]
rep = population.report(tr)
G = {(x["tag"], x["group"]): x for x in rep["G"]}
check("P4", "（前提）G 全部 3 筆（R 未知的不算）：(2−1−1)/3 ＝ 0、PF 1.0", G[("全部", "")]["n"] == 3 and G[("全部", "")]["avg"] == 0.0 and G[("全部", "")]["pf"] == 1.0, f"{G[('全部', '')]}")
check("P4", "依回測母體：在 1 筆 +2R、不在 2 筆 −2R", G[("母體", "在")]["tot"] == 2.0 and G[("母體", "不在")]["n"] == 2)
Fr = {(x["tag"], x["group"]): x for x in rep["F"]}
check("P4", "標籤是 None 的歸「缺」，不丟掉；F 自己一組", G[("名單", "缺")]["n"] == 1 and Fr[("全部", "")]["n"] == 1)
tr2 = [dict(engine="F", r=1.0, bar_t=10, band="≥150%"), dict(engine="F", r=3.0, bar_t=20, band="≥150%"), dict(engine="F", r=-1.0, bar_t=30, band="≥150%")]
h2 = {(x["tag"], x["group"]): x for x in population.report(tr2, split_ts=25)["F"]}
check("P4", "有分界時每組附前後段：前段 (1+3)/2＝+2R 2 筆、後段 −1R 1 筆", h2[("24h區間", "≥150%")]["first"] == 2.0 and h2[("24h區間", "≥150%")]["first_n"] == 2
      and h2[("24h區間", "≥150%")]["second"] == -1.0 and h2[("24h區間", "≥150%")]["second_n"] == 1, f"{h2}")
check("P4", "沒有交易的引擎不出現（不給空表）", "C" not in population.report(tr2) and "G" not in population.report(tr2))

print("啟動：只帶 C／F／G、排除停止追蹤的；跟掃描、籌碼分析互斥")
fx = fresh()
class FakePopen:
    made = []
    def __init__(self, cmd, env=None, cwd=None): FakePopen.made.append((cmd, env))
    def poll(self): return None
class FakeSub: Popen = FakePopen
o_pop, o_sw, o_lb = population.subprocess, sweep.subprocess, labels.subprocess
population.subprocess = sweep.subprocess = labels.subprocess = FakeSub
population._proc = sweep._proc = labels._proc = None
closed = [dict(symbol="AUSDT", engine="G", r=1.0, bar_t=5), dict(symbol="BUSDT", engine="C", r=1.0, bar_t=6),
          dict(symbol="CUSDT", engine="F", r=-1.0, ts=7), dict(symbol="DUSDT", engine="G", r=1.0, bar_t=8, excluded=True),
          dict(symbol="EUSDT", engine="A", r=1.0, bar_t=9)]
ok = population.start("live", 90, closed)
inp = json.load(open(population.INPUT))
check("P5", "（前提）沒有別的在跑 → 啟動子程序 app.population，工作是 live",
      ok is True and len(FakePopen.made) == 1 and FakePopen.made[0][0][-1] == "app.population" and FakePopen.made[0][1]["POP_JOB"] == "live")
check("P5", "交給子程序的只有 C／F／G、沒有停止追蹤的；沒有 bar_t 的用 ts", [(x["symbol"], x["bar_t"]) for x in inp] == [("AUSDT", 5), ("BUSDT", 6), ("CUSDT", 7)], f"{inp}")
check("P5", "母體檢查在跑 → 掃描、籌碼分析都不啟動", sweep.start(30) is False and labels.start(90) is False and len(FakePopen.made) == 1)
population._proc = None
check("P5", "不認得的工作名稱 → 不啟動", population.start("xx") is False and len(FakePopen.made) == 1)
population.subprocess, sweep.subprocess, labels.subprocess = o_pop, o_sw, o_lb
population._proc = sweep._proc = labels._proc = None

print("整段跑：實盤對照、無偏差母體（假的交易所資料）")
fx = fresh()
now = int(time.time() * 1000)
T1 = (now - 3 * DAY) - (now - 3 * DAY) % DAY            # 合成資料：拉升在昨天附近，四天都在 90 天內
kx = series(T1 - DAY)
h1x = hourly(kx); m5x = hourly(kx, 300000)
def fake_range(sym, iv, a, b):
    src = {"1m": kx, "5m": m5x, "1h": h1x}[iv]
    return [dict(x) for x in src if a <= x["t"] <= b]
B.klines_range = fake_range
B.perp_symbols = lambda: ["XUSDT"]
B.ticker_24h = lambda: {"XUSDT": {"quoteVolume": "5000000"}}
full = [t for e in ("G",) for t in backtest.simulate(kx, (e,))]
check("P6", "（前提）合成資料上 G 有一筆", len(full) == 1, f"{full}")
json.dump([dict(symbol="XUSDT", engine="G", r=1.2, bar_t=full[0]["t"])] if full else [], open(population.INPUT, "w"))
os.environ["POP_JOB"] = "live"; population.main()
st = population.state()
lv = st.get("live", {}).get("report", {}).get("G", [])
check("P6", "實盤對照完成：那筆貼上「在暴漲日母體」（當天漲到 +80%）", st.get("status", "").startswith("完成") and any(x["tag"] == "母體" and x["group"] == "在" and x["n"] == 1 for x in lv), f"{st.get('status')} {lv}")
oi_urls = []
def fake_http(url):
    oi_urls.append(url)
    head = "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"
    return head + "\n" + "\n".join(f"{(kx[0]['t'] - DAY + n * 300000) // 1000},XUSDT,{100 + n},0,0,1,0,0" for n in range(1700)) + "\n"
labels._http = fake_http
os.environ["POP_JOB"] = "trigger"; os.environ["POP_DAYS"] = "10"; population.main()
st = population.state()
tg = st.get("trigger", {})
allrow = {e: next((x for x in (tg.get("report") or {}).get(e, []) if x["tag"] == "全部"), {}) for e in ("F", "G")}
check("P6", "無偏差母體完成：1 檔；1m 一段、5m 一段（崩跌那段 2h 高低差也 ≥15%），F、G 都有交易，而且實盤對照的結果還在",
      st.get("status", "").startswith("完成") and tg.get("symbols") == 1 and tg.get("segments") == 2
      and allrow["G"].get("n", 0) >= 1 and allrow["F"].get("n", 0) >= 1 and st.get("live"), f"{st.get('status')}")
G_oi = {x["key"]: x for x in (tg.get("oi") or {}).get("G", [])}
check("P6", "交易貼上 OI 標籤（從 data.binance.vision 的 metrics 檔），OI 與 24h 區間各有判讀", oi_urls and all("data.binance.vision" in u for u in oi_urls)
      and set(G_oi) == {"oi_1h", "oi_24h", "oi_off_hi", "gain24"} and all(x.get("verdict") for x in G_oi.values()), f"{list(G_oi)} {oi_urls[:1]}")
check("P6", "結束各推播一次", len([m for m in TG if m.startswith("🔬 母體偏差檢查完成")]) == 2, f"{TG}")
for v in ("POP_JOB", "POP_DAYS"): os.environ.pop(v, None)

print("實盤 F／G 只記訊號不下單")
fx = fresh()
assert hasattr(main, "_paper"); assert hasattr(main, "SIGNAL_ONLY")    # 舊版沒有只記模式：這段整個不能跑
check("P8", "（前提）預設只記不下單的是 F、G（C 照常下單）", main.SIGNAL_ONLY == {"F", "G"}, f"{main.SIGNAL_ONLY}")
labels.live_oi = lambda s_, bt: dict(oi_1h=2.0, oi_24h=30.0, oi_off_hi=-1.0)
st_ = dict(watch={}, last={})
r1 = dict(time="t", symbol="XUSDT", bar_t=1_000_000_000_000, executed=False)
main._paper(st_, "XUSDT", "G", r1)
sigs = store.get().get("signals") or []
check("P8", "記一筆：標明只記不下單、帶 OI 標籤、沒有下單也沒有推播", len(sigs) == 1 and sigs[-1].get("skipped", "").startswith("只記訊號") and sigs[-1].get("oi_1h") == 2.0
      and sigs[-1]["executed"] is False and not store.get().get("open") and TG == [], f"{sigs} {TG}")
main._paper(st_, "XUSDT", "G", dict(r1, bar_t=r1["bar_t"] + 89 * 60000))
check("P8", "同一波（回測持有 60＋冷卻 30＝90 根 1m 內）不重複記", len(store.get().get("signals") or []) == 1)
main._paper(st_, "XUSDT", "G", dict(r1, bar_t=r1["bar_t"] + 90 * 60000))
main._paper(st_, "YUSDT", "G", dict(r1, symbol="YUSDT"))
check("P8", "滿 90 根之後、或別的幣 → 照記", len(store.get().get("signals") or []) == 3)

finish()
