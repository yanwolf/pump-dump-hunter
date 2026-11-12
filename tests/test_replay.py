# 主網成交重算（app/replay.py＋population 的 slip 工作）。預期值手算。
# 在專案根目錄執行：python -m tests.test_replay
from tests.harness import (B, TG, check, fresh, finish)   # 共用案例框架
import os, time
from app import backtest, config as C, labels, population, replay

R0 = dict(tp1_r=1.0, be_r=None, trail_after_r=2.0, trail_bars=3, max_hold_bars=72, fee=0.0)
def bar(i, o, h, l, c): return dict(t=i * 300000, o=o, h=h, l=l, c=c, v=1.0)

print("實盤機制重走（參考價成交）：1R 在收盤減碼一半、停損移到成本")
fx = fresh()
k = [bar(0, 101, 101, 100, 100), bar(1, 100, 104, 95, 95), bar(2, 95, 101, 95, 100.5)]
res = replay.replay(k, 0, 100.0, 105.0, R0, replay.ref_fill)
check("R1", "（前提）第 1 根最低 95＝1R：收盤 95 減碼一半 +0.5R；第 2 根最高 101 碰成本 100 停損，另一半 0", res is not None and res["reason"] == "stop" and res["r"] == 0.5, f"{res}")
check("R1", "成交紀錄：開倉 100、減碼 95（一半）、停損 100（一半）", [(x[0], x[2], x[3]) for x in res["fills"]] == [("open", 100.0, 1.0), ("tp1", 95, 0.5), ("stop", 100.0, 0.5)], f"{res['fills']}")
k2 = [bar(0, 101, 101, 100, 100), bar(1, 100, 104, 95, 97)]
res2 = replay.replay(k2, 0, 100.0, 105.0, R0, replay.ref_fill)
check("R1", "減碼那根收盤已經反彈到 97：實盤只拿到 (100−97)/5×0.5＝0.3R（回測假設剛好 1R 會記 0.5），剩下一半資料結束按收盤 97 再 +0.3", res2["r"] == 0.6, f"{res2}")
k3 = [bar(0, 101, 101, 100, 100)] + [bar(i, 100, 101, 99, 100) for i in range(1, 75)]
res3 = replay.replay(k3, 0, 100.0, 105.0, R0, replay.ref_fill)
kinds3 = [x[0] for x in ((res3 or {}).get("fills") or [])]
check("R1", "沒碰停損也沒到 1R：第 72 根收盤時間出場 0R", res3 is not None and res3["reason"] == "time" and res3["r"] == 0.0 and kinds3 == ["open", "time"], f"{res3}")
res4 = replay.replay(k, 0, 100.0, 105.0, dict(R0, fee=0.0005), replay.ref_fill)
check("R1", "手續費：0.05% ×（100×1 + 95×0.5 + 100×0.5）／5 ＝ 0.01975R → 0.5 − 0.02 ＝ 0.48", res4["r"] == 0.48, f"{res4['r']}")

print("跟回測同一條路（沒有減碼時）：時間出場、滑價手續費設 0 → R 完全一樣")
fx = fresh()
snap = C.apply_overrides({"RISK": {"slippage": 0.0, "fee": 0.0}})
k5 = []
t, p = 0, 100.0
for j in range(288): k5.append(bar(j, p, p * 1.001, p * 0.999, p)); t += 1
k5.append(bar(288, p, p * 1.001, p * 0.8 * 0.999, p * 0.8)); p *= 0.8
k5.append(bar(289, p, p * 1.01, p, p * 1.01)); p *= 1.01
k5.append(bar(290, p, p * 1.001, p * 0.95 * 0.999, p * 0.95)); p *= 0.95
for j in range(291, 400): k5.append(bar(j, p, p * 1.004, p * 0.996, p))
for x in k5: x["v"] = 10.0 if x["t"] == 288 * 300000 else (4.0 if x["t"] == 290 * 300000 else 1.0)
bt = backtest.simulate(k5, ("C",))
check("R2", "（前提）合成資料回測出一筆 C、時間出場", len(bt) == 1 and bt[0]["reason"] == "time", f"{bt}")
from app.signals import ENGINES as ENG
i0 = next((j for j, x in enumerate(k5) if bt and x["t"] == bt[0]["t"]), None)
sig = ENG["C"](k5, i0) if i0 is not None else None
rr = replay.replay(k5, i0, sig.entry, sig.stop, {**C.RISK, **C.EXIT.get("C", {})}, replay.ref_fill) if sig else None
check("R2", "實盤機制重走（參考價）跟回測同一個 R", rr is not None and bt and abs(rr["r"] - bt[0]["r"]) < 0.01 and rr["reason"] == "time", f"{rr and rr['r']} vs {bt and bt[0]['r']}")
C.restore(snap)

print("主網成交：延遲之後第一筆同方向主動成交；停損觸發後 0.3 秒")
fx = fresh()
# (時間, 價格, 買方是掛單方)：買方掛單＝主動賣
ticks = [(300000 + 500, 99.9, True), (300000 + 1200, 100.2, False), (300000 + 1500, 99.0, True),
         (600000 + 1500, 95.5, False), (600000 + 2000, 96.0, False),
         (600000 + 10000, 100.0, False), (600000 + 10100, 100.4, True), (600000 + 10400, 101.0, False)]
get = lambda a, b: [x for x in ticks if a <= x[0] <= b]
f1 = replay.trade_fill(get, 1000)
check("R3", "（前提）開倉延遲 1 秒：收盤 300000 之後 1 秒起第一筆主動賣是 99.0（中間那筆 100.2 是主動買，不算）", f1("open", 300000, 100, None) == 99.0)
check("R3", "1R 減碼（買回）：600000 之後 1 秒起第一筆主動買 95.5", f1("close", 600000, 95, None) == 95.5)
check("R3", "停損 100：這根第一筆 ≥100 的成交在 +10000 觸發，0.3 秒後第一筆主動買 101.0（0.1 秒那筆是主動賣不算）", f1("stop", 600000, 100.0, 900000) == 101.0)
res = replay.replay(k, 0, 100.0, 105.0, R0, f1)
check("R3", "整筆：開 99、減碼 95.5 一半、停損 101 一半 → 0.5×(99−95.5)/5 + 0.5×(99−101)/5 ＝ 0.35−0.2 ＝ 0.15R", res is not None and res["r"] == 0.15, f"{res}")
check("R3", "這根沒有任何成交越過停損價 → None（不拿停損價充數）", f1("stop", 600000, 102.0, 900000) is None)
check("R3", "（對照組）延遲 3 秒：開倉找不到 1 分鐘內的主動賣 → None，整筆不算", replay.replay(k, 0, 100.0, 105.0, R0, replay.trade_fill(lambda a, b: [x for x in ticks if a <= x[0] <= b], 3000)) is None)
check("R3", "滑價方向：空單開倉 100→99 不利 +1%；平倉 95→95.5 不利 +0.526%", replay.slip_pct("open", 100, 99) == 1.0 and replay.slip_pct("close", 95, 95.5) == 0.526)

print("逐筆成交抓取：滿 1000 筆往後翻頁、每次停一下、同一窗口只抓一次")
fx = fresh()
calls = []
def fake_get(path, params=None, **kw):
    calls.append((path, params["startTime"], params["endTime"]))
    st = params["startTime"]
    if st == 0: return [dict(T=i, p="1", m=True) for i in range(1000)]
    return [dict(T=1000 + i, p="2", m=False) for i in range(5)]
sleeps = []
B._get = fake_get; B._sleep = lambda s: sleeps.append(s)
g = population.agg_getter(B, "XUSDT", pause=1.5)
rows = g(0, 5000)
check("R4", "（前提）第一頁 1000 筆 → 從最後一筆 +1 再抓；合計 1005 筆", len(rows) == 1005 and [c[1] for c in calls] == [0, 1000], f"{len(rows)} {calls}")
check("R4", "走主網 /fapi/v1/aggTrades、每次請求後停 1.5 秒", all(c[0] == "/fapi/v1/aggTrades" for c in calls) and sleeps == [1.5, 1.5], f"{sleeps}")
g(0, 5000)
check("R4", "同一窗口第二次不再打 API", len(calls) == 2)

print("訂單簿 1% 深度")
fx = fresh()
CSVB = "timestamp,percentage,depth,notional\n1970-01-01 00:04:00,-1.00,10,500\n1970-01-01 00:04:00,1.00,10,700\n1970-01-01 00:05:30,-1.00,10,900\n1970-01-01 00:06:00,-1.00,10,99999\n"
d1 = population.book_depth_1pct("XUSDT", 330000, -1.0, fetch=lambda u: CSVB)
check("R5", "（前提）進場 00:05:30 → 之前最近一筆買方 1% 快照 900（00:06 那筆是之後的，不能用）", d1 == 900.0, f"{d1}")
check("R5", "沒有檔案 → None", population.book_depth_1pct("XUSDT", 330000, -1.0, fetch=lambda u: None) is None)

print("報表")
fx = fresh()
tr = [dict(bar_t=10, r=0.5, r_ref=0.4, r_1000=0.2, r_3000=0.1, slips_1000=[("open", 1.0), ("stop", 2.0)], slips_3000=[("open", 1.5)], depth_ratio=0.2),
      dict(bar_t=30, r=-1.0, r_ref=-1.0, r_1000=-1.2, r_3000=None, slips_1000=[("open", 0.0), ("tp1", 0.5)], depth_ratio=0.8)]
sr = population.slip_report(tr, 20)
rows = {x["name"]: x for x in sr["rows"]}
check("R6", "（前提）四個情境；主網 1 秒：2 筆 (0.2−1.2)/2 ＝ −0.5R，樣本外 +0.2（1 筆）、最近 −1.2（1 筆）",
      len(sr["rows"]) == 4 and rows["主網成交、延遲 1 秒"]["avg"] == -0.5 and rows["主網成交、延遲 1 秒"]["first"] == 0.2 and rows["主網成交、延遲 1 秒"]["second_n"] == 1, f"{sr['rows']}")
check("R6", "延遲 3 秒有一筆查不到 → 只算 1 筆；查不到的筆數另列", rows["主網成交、延遲 3 秒"]["n"] == 1 and sr["missing"] == 0, f"{sr['missing']}")
sl = {(x["latency"], x["kind"]): x for x in sr["slips"]}
check("R6", "滑價按延遲、動作分：1 秒進場 2 筆平均 0.5%、停損 1 筆 2%", sl[("1 秒", "進場")]["avg"] == 0.5 and sl[("1 秒", "停損")]["n"] == 1, f"{sr['slips']}")
check("R6", "深度：2 筆、超過一半 1 筆、超過全部 0 筆", sr["depth"]["n"] == 2 and sr["depth"]["over_half"] == 1 and sr["depth"]["over_one"] == 0, f"{sr['depth']}")

print("整段跑（假的交易所）：C 一筆，主網成交＝每秒一筆、價格是當根收盤 → 跟參考價幾乎一樣")
fx = fresh()
now = int(time.time() * 1000); T0 = (now - 3 * 86400000) - (now - 3 * 86400000) % 86400000
k6 = [dict(x, t=T0 + x["t"]) for x in k5]
h6 = {}
for x in k6:
    hh = x["t"] - x["t"] % 3600000
    if hh not in h6: h6[hh] = dict(x, t=hh)
    else: h6[hh].update(h=max(h6[hh]["h"], x["h"]), l=min(h6[hh]["l"], x["l"]), c=x["c"])
h6 = [h6[t_] for t_ in sorted(h6)]
B.klines_range = lambda sym, iv, a, b: [dict(x) for x in (k6 if iv == "5m" else h6 if iv == "1h" else []) if a <= x["t"] <= b]
B.perp_symbols = lambda: ["XUSDT"]
B.ticker_24h = lambda: {"XUSDT": {"quoteVolume": "5000000"}}
def agg(path, params=None, **kw):
    a, b = params["startTime"], min(params["endTime"], params["startTime"] + 999 * 1000)
    out = []
    for tt in range(a - a % 1000, b + 1, 1000):
        if tt < a: continue
        bb = next((x for x in k6 if x["t"] <= tt < x["t"] + 300000), k6[-1])
        out.append(dict(T=tt, p=str(bb["o"]), m=(tt // 1000) % 2 == 0))
    return out
B._get = agg; B._sleep = lambda s_: None
labels._http = lambda u: "timestamp,percentage,depth,notional\n" + "\n".join(
    f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime((T0 + n * 60000) / 1000))},-1.00,1,100000" for n in range(4 * 1440))
os.environ.update(POP_JOB="slip", POP_DAYS="10", POP_ENGINES="C")
population.main()
st = population.state(); sl = st.get("slip") or {}
rows = {x["name"]: x for x in sl.get("rows", [])}
check("R7", "（前提）完成、C 一筆", str(st.get("status", "")).startswith("完成（C 主網成交重算") and sl.get("n") == 1, f"{st.get('status')}")
check("R7", "四個情境都算出來；主網兩種延遲都有那一筆", all(rows.get(n, {}).get("n") == 1 for n in ("回測（假設滑價 0.3%）", "實盤機制、無滑價", "主網成交、延遲 1 秒", "主網成交、延遲 3 秒")), f"{sl.get('rows')}")
check("R7", "訂單簿深度有資料（部位／深度很小）", sl.get("depth", {}).get("n") == 1 and sl["depth"]["over_half"] == 0, f"{sl.get('depth')}")
check("R7", "結束推播一次", len([m for m in TG if m.startswith("🔬 母體偏差檢查完成（C 主網成交重算")]) == 1, f"{TG}")
for v in ("POP_JOB", "POP_DAYS", "POP_ENGINES"): os.environ.pop(v, None)

print("API 查不到 → 改用 data.binance.vision 每日檔；原因記下來")
fx = fresh()
import io, zipfile
assert hasattr(population, "vision_getter"); assert hasattr(population, "slip_hook")
csv_rows = ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"]
for x in k6:
    for sec in range(0, 300, 1):
        tt = x["t"] + sec * 1000
        csv_rows.append(f"{tt},{x['o']},1,1,1,{tt},{'true' if sec % 2 == 0 else 'false'}")
buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("X.csv", "\n".join(csv_rows))
urls = []
vf = lambda u: (urls.append(u), buf.getvalue())[1]
w0 = T0 + 600000
gv, nv = population.vision_getter("XUSDT", w0, w0 + 300000, fetch=vf)
check("R8", "（前提）每日檔：有表頭也吃，只留窗口內的成交（5 分鐘含兩端 ＝ 301 筆）", nv == 301 and all(w0 <= x[0] <= w0 + 300000 for x in gv(0, 10**15)), f"{nv}")
check("R8", "走 data.binance.vision 的 USD-M aggTrades 每日檔", urls and "data.binance.vision/data/futures/um/daily/aggTrades/XUSDT/XUSDT-aggTrades-" in urls[0], f"{urls[:1]}")
B._get = lambda path, params=None, **kw: []                    # 這次失敗的樣子：API 回空
B._sleep = lambda s_: None
labels._http = lambda u: None
hook = population.slip_hook(B, 1125.0, vision_fetch=vf)
i6 = next((j for j, x in enumerate(k6) if bt and x["t"] == T0 + bt[0]["t"]), None)
out = hook("XUSDT", "C", k6, i6, {}) if i6 is not None else {}
check("R8", "API 回空 → 兩種延遲都改用每日檔算出來", out.get("src_1000") == "每日檔" and out.get("src_3000") == "每日檔"
      and isinstance(out.get("r_1000"), float), f"{ {k_: v for k_, v in out.items() if k_.startswith(('src', 'r_', 'err'))} }")
check("R8", "原因記下來：API 那段時間沒有任何成交資料", "API：那段時間沒有任何成交資料" in out.get("err_1000", ""), f"{out.get('err_1000')}")
def boom(path, params=None, **kw): raise RuntimeError("HTTP 400 {\"code\":-1130}")
B._get = boom
out2 = hook("XUSDT", "C", k6, i6, {}) if i6 is not None else {}
check("R8", "API 出錯也改用每日檔，錯誤訊息照記", out2.get("src_1000") == "每日檔" and "HTTP 400" in out2.get("err_1000", ""), f"{out2.get('err_1000')}")
sr = population.slip_report([dict(out, bar_t=0, r=0.1), dict(out2, bar_t=0, r=0.1)], 1)
check("R8", "報表列出來源與失敗原因", sr["sources"].get("1 秒：每日檔") == 2 and any("HTTP 400" in e["msg"] for e in sr["errors"]), f"{sr['sources']} {sr['errors']}")

finish()
