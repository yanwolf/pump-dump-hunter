# 籌碼標籤分析（app/labels.py）與 backtest.simulate_each 的套件匯入。預期值手算。
# 在專案根目錄執行：python -m tests.test_labels
import io, json, os, zipfile
from tests.harness import (TG, check, fresh, finish)   # 共用案例框架
from app import backtest, labels, sweep

DAY = 86400000

def bars():
    """平盤 300 根 → 15 根各 +4% 爆量拉升 → 頂部 30 根 → 15 根各 -2.5% 爆量崩跌 → 平盤 60 根（1m）。"""
    k, t, p = [], 1_780_000_000_000, 100.0
    def add(o, c, v):
        nonlocal t
        k.append(dict(t=t, o=o, h=max(o, c) * 1.001, l=min(o, c) * 0.999, c=c, v=v)); t += 60000
    for j in range(300): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    for _ in range(15): add(p, p * 1.04, 10.0); p *= 1.04
    for j in range(30): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    for _ in range(15): add(p, p * 0.975, 20.0); p *= 0.975
    for j in range(60): add(p, p * (1.001 if j % 2 else 0.999), 1.0)
    return k

print("backtest.simulate_each 在套件匯入下能跑（以前寫 from signals import，app/ 搬家後歷史掃描每個事件都 ModuleNotFoundError）")
fx = fresh()
k = bars()
check("L1", "（前提）合成資料：平盤 300 ＋拉升 15 ＋頂部 30 ＋崩跌 15 ＋平盤 60 ＝ 420 根", len(k) == 420, f"{len(k)}")
err = None
try: tr = backtest.simulate_each(k, k)
except Exception as e: tr, err = [], e
check("L1", "simulate_each 沒有拋錯", err is None, repr(err))
check("L1", "合成的拉升 → 崩跌：G 進一筆多、F 進一筆空", {t["engine"] for t in tr} >= {"F", "G"}, f"{[(t['engine'], t['side']) for t in tr]}")

print("成交量分布（手算：三根 K，量平均攤在高低區間）")
fx = fresh()
k3 = [dict(t=0, o=100, h=110, l=90, c=100, v=100), dict(t=60000, o=100, h=100, l=100, c=100, v=50),
      dict(t=120000, o=100, h=101, l=99, c=100, v=10)]
vp = labels.volume_profile(k3, 2)
check("L2", "（對照組）三根都有量 → 算得出值", vp is not None and len(vp) == 4, f"{vp}")
check("L2", "獲利比例＝低於 100 的量 (50+0+5)/160 ＝ 34.4%（剛好在 100 的那根不算低於）", vp["vp_profit"] == 34.4, f"{vp}")
check("L2", "上方 2%＝(10+0+5)/160＝9.4%；下方 2%＝9.4%", vp["vp_up2"] == 9.4 and vp["vp_dn2"] == 9.4, f"{vp}")
check("L2", "主峰在 [100,100.5) 那格（點狀 50 + 2.5 + 2.5）→ 中點 100.25，價格相對主峰 -0.25%", vp["vp_poc"] == -0.25, f"{vp}")
check("L2", "只看進場那根以前：把第三根之後加一根大量 K，第 2 根的結果不變",
      labels.volume_profile(k3 + [dict(t=180000, o=50, h=50, l=40, c=45, v=10**6)], 2) == vp)
check("L2", "全部沒量 → None（不給 0）", labels.volume_profile([dict(t=0, o=1, h=1, l=1, c=1, v=0)], 0) is None)

print("OI 標籤：不偷看、缺資料給 None")
fx = fresh()
T0 = 1_780_000_200_000
rows = [(T0 + n * 300000, 100.0 if n < 288 else 150.0, None, 1.5) for n in range(289)] + [(T0 + 289 * 300000, 999.0, None, 9.9)]
te = T0 + 289 * 300000 + 60000            # 第 289 筆的時間戳 + 1 分鐘：它要再 4 分鐘才算已知
o = labels.oi_labels(rows, te)
check("L3", "（對照組）現在＝第 288 筆（150），第 289 筆（999）還不能用", o["ls_top"] == 1.5 and o["oi_1h"] == 50.0, f"{o}")
check("L3", "24h 前＝第 0 筆（100）→ +50%；24h 高點就是現在 → 0%", o["oi_24h"] == 50.0 and o["oi_off_hi"] == 0.0, f"{o}")
o2 = labels.oi_labels(rows[:100], te)
check("L3", "最近一筆已經是半天前 → 全部 None（不拿舊值充數）", all(v is None for v in o2.values()), f"{o2}")
o3 = labels.oi_labels([r for r in rows if r[0] >= T0 + 6 * 3600000], te)
check("L3", "前面缺 6 小時：1h 照算、24h 前那筆找不到 → 24h 變化與距高點都是 None",
      o3["oi_1h"] == 50.0 and o3["oi_24h"] is None and o3["oi_off_hi"] is None, f"{o3}")

print("分組判讀")
fx = fresh()
SPLIT = 1000
good = [dict(r=(3.0 if i % 4 else -1.0) if i >= 60 else (-1.0 if i < 30 else 0.0), t=500 if i % 2 else 1500, x=float(i)) for i in range(90)]
a = labels.analyse(good, "x", SPLIT)
g = {x["name"]: x for x in a["groups"]}
check("L4", "三等分各 30 筆；高組平均 (22×3−8)/30＝1.93R、低組 −1R", g["低"]["n"] == g["高"]["n"] == 30 and g["高"]["avg"] == 1.93 and g["低"]["avg"] == -1.0, f"{g}")
check("L4", "高組比低組好、p < 0.05、前後兩段同向 → 候選", a["p"] < 0.05 and a["same"] and a["verdict"].startswith("候選"), f"{a['p']} {a['verdict']}")
check("L4", "前 10 大獲利單全部在高組", g["高"]["top10"] == 10, f"{g['高']['top10']}")
flat = [dict(r=1.0 if i % 3 == 0 else -0.5, t=500 if i % 2 else 1500, x=float(i)) for i in range(90)]
b = labels.analyse(flat, "x", SPLIT)
check("L4", "R 跟標籤無關（每組都是 1:2 的 +1／−0.5）→ 看不出差異", b["diff"] == 0 and b["verdict"] == "看不出差異", f"{b.get('diff')} {b['verdict']}")
few = labels.analyse(good[:20] + [dict(r=1.0, t=0, x=None)] * 50, "x", SPLIT)
check("L4", "有值只有 20 筆 → 不分組；沒值的 50 筆照記", few["groups"] == [] and few["missing"] == 50 and "不分組" in few["verdict"], f"{few}")

print("OI 每日檔：404 與抓取失敗分開處理")
fx = fresh()
KD = os.path.join(os.environ["DATA_DIR"], "kc_test"); os.makedirs(KD, exist_ok=True)
CSV = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
       "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
       "2026-09-01 00:05:00,XUSDT,1000,500,1.1,2.5,1.2,0.9\n")
buf = io.BytesIO(); zipfile.ZipFile(buf, "w").writestr("XUSDT-metrics-2026-09-01.csv", CSV)
NOW = 1_790_000_000_000                    # 2026-09-21 前後
r = labels.metrics_day("XUSDT", "2026-09-01", KD, fetch=lambda u: buf.getvalue(), now_ms=NOW)
check("L5", "照表頭名稱取欄位：時間、持倉量、持倉價值、大戶持倉多空比", r == [(1788221100000, 1000.0, 500.0, 2.5)], f"{r}")
calls = []
r2 = labels.metrics_day("XUSDT", "2026-09-01", KD, fetch=lambda u: calls.append(u), now_ms=NOW)
check("L5", "第二次讀快取，不再抓", r2 == r and calls == [])
r3 = labels.metrics_day("YUSDT", "2026-08-01", KD, fetch=lambda u: None, now_ms=NOW)
check("L5", "兩天前以前的 404 → 確定沒有這天的檔，快取成空的", r3 == [] and os.path.exists(os.path.join(KD, "YUSDT_metrics_20260801.json")))
def boom(u): raise labels.Missing("HTTP 503")
for ymd, f, why in (("2026-09-20", lambda u: None, "最近兩天的 404（檔案可能還沒產生）"), ("2026-08-02", boom, "5xx／網路錯")):
    err = None
    try: labels.metrics_day("ZUSDT", ymd, KD, fetch=f, now_ms=NOW)
    except labels.Missing as e: err = e
    check("L5", f"{why} → 拋 Missing、不寫快取（抓不到 ≠ 沒有 OI）",
          err is not None and not os.path.exists(os.path.join(KD, f"ZUSDT_metrics_{ymd.replace('-', '')}.json")), repr(err))

print("跟歷史事件掃描互斥")
fx = fresh()
class FakePopen:
    made = []
    def __init__(self, cmd, env=None, cwd=None): FakePopen.made.append((cmd, env)); self.cmd = cmd
    def poll(self): return None                      # 一直在跑
class FakeSub: Popen = FakePopen
orig_sub_l, orig_sub_s, orig_run = labels.subprocess, sweep.subprocess, sweep.running
labels.subprocess = FakeSub; sweep.subprocess = FakeSub
labels._proc = None; sweep._proc = None
ok = labels.start(45)
check("L6", "（前提）兩邊都沒在跑 → 分析啟動子程序 app.labels，天數 45 帶進去",
      ok is True and len(FakePopen.made) == 1 and FakePopen.made[0][0][-1] == "app.labels" and FakePopen.made[0][1]["LABEL_DAYS"] == "45",
      f"{ok} {[m[0] for m in FakePopen.made]}")
check("L6", "分析在跑 → 掃描不啟動、也沒有多開子程序", sweep.start(30) is False and len(FakePopen.made) == 1)
labels._proc = None; sweep.running = lambda: True
check("L6", "掃描在跑 → 分析不啟動、也沒有多開子程序", labels.start(90) is False and len(FakePopen.made) == 1)
labels.subprocess, sweep.subprocess, sweep.running = orig_sub_l, orig_sub_s, orig_run
labels._proc = None; sweep._proc = None

print("整段跑一次（假的事件、K 線、OI 檔）")
fx = fresh()
k = bars()
check("L7", "（前提）合成 K 線有資料、sweep 有快取讀取介面", len(k) == 420 and hasattr(sweep, "_load") and hasattr(sweep, "_cached"))
assert hasattr(sweep, "_load"); assert hasattr(sweep, "_cached")    # 舊版沒有快取介面：這段整個不能跑
pk = k[0]["t"] - k[0]["t"] % DAY
ev = [dict(symbol="XUSDT", peak_day="x", peak_ts=pk, pump=80, dump=30)]
o_load, o_cached, o_http = sweep._load, sweep._cached, labels._http
sweep._load = lambda key: ev
sweep._cached = lambda key, fetch: k
fetched = []
def fake_http(url):
    fetched.append(url)
    lines = [f"{(k[0]['t'] - DAY + n * 300000) // 1000},XUSDT,{100 + n},0,0,1.2,0,0" for n in range(1500)]
    return CSV.split("\n")[0] + "\n" + "\n".join(lines) + "\n"
labels._http = fake_http
labels.main()
st = labels.state()
sweep._load, sweep._cached, labels._http = o_load, o_cached, o_http
E = st.get("engines", {})
check("L7", "結束推播一次（背景跑很久，不用開著網頁等）", len(TG) == 1 and TG[0].startswith("🔬 籌碼標籤分析完成"), f"{TG}")
check("L7", "（前提）F、G 都有結果，狀態是完成", st.get("status", "").startswith("完成") and set(E) == {"F", "G"}, st.get("status"))
fr = [x for x in st.get("rows", []) if x["engine"] == "F"]
check("L7", "F 那筆有成交量分布與 OI 標籤", fr and fr[0].get("vp_profit") is not None and fr[0].get("oi_1h") is not None, f"{fr[:1]}")
check("L7", "每個引擎 8 個標籤都有判讀", all(len(E[e]["labels"]) == 8 and all(L.get("verdict") for L in E[e]["labels"]) for e in E))
check("L7", "OI 從 data.binance.vision 的 metrics 檔抓", fetched and all("data.binance.vision/data/futures/um/daily/metrics/XUSDT/" in u for u in fetched), f"{fetched[:1]}")
json.dumps(st)

finish()
