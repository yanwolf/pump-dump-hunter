# 清算反轉＋熬鷹（app/liqrev.py，研究用）。預期值手算。
# 在專案根目錄執行：python -m tests.test_liqrev
from tests.harness import (B, TG, check, fresh, finish)   # 共用案例框架
import json, os, tempfile, time
from app import labels, liqrev as LR, sweep

M5, H1 = 300000, 3600000
def bar(t, o, h, l, c): return dict(t=t, o=o, h=h, l=l, c=c, v=1.0)
P0 = dict(expire_bars=3, max_hold_bars=10, cooldown_bars=2)
NOCOST = dict(maker=0.0, taker=0.0, slip=0.0)
def one_sig(at, s):
    """只在第 at 根給一次訊號。"""
    return lambda i: dict(s, stop0=s["stop"]) if i == at else None
LONG = dict(side="LONG", limit=100.0, stop=98.0, tp1=103.0, tp2=106.0, why="")   # R＝2，目標 1.5R／3R

print("指標：Wilder RSI、簡單均線")
fx = fresh()
r = LR.rsi_series([float(x) for x in range(1, 20)], 14)
check("I1", "（前提）一路上漲：前 14 根沒有值、第 14 根起 RSI＝100", len(r) == 19 and r[13] is None and r[14] == 100.0 and r[-1] == 100.0, f"{r[12:16]}")
r2 = LR.rsi_series([10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10], 14)
check("I1", "漲跌各 7 次、幅度一樣：RSI＝50", r2[14] is not None and abs(r2[14] - 50.0) < 1e-9, f"{r2[14]}")
m = LR.sma_series([1, 2, 3, 4, 5], 3)
check("I1", "3 期均線：[—, —, 2, 3, 4]", m == [None, None, 2.0, 3.0, 4.0], f"{m}")

print("清算推估：第 j 根收盤才知道 t−5m → t 那段，價跌算多頭被清")
fx = fresh()
k = [bar(j * M5, 100, 100, 100, 100) for j in range(6)]
k[2]["c"] = 99.0                                  # 第 2 根收盤（時間 15 分）跌
oi = [(j * M5, 1000.0, None, None) for j in range(6)]
oi[3] = (3 * M5, 980.0, None, None)               # 10→15 分這段 OI 掉 2%
lb = LR.liq_bars(k, oi)
check("L1", "（前提）每根都有一筆", len(lb) == 6, f"{len(lb)}")
check("L1", "15 分的快照要到 20 分（第 3 根收盤）才知道：第 3 根＝多頭被清 2%", lb[3] == (2.0, 0.0, True), f"{lb}")
check("L1", "不偷看：第 2 根（15 分收盤）還不知道這段", len(lb) == 6 and lb[2][0] == 0.0 and lb[2][1] == 0.0, f"{lb[2]}")
k[2]["c"] = 101.0
lb2 = LR.liq_bars(k, oi)
check("L1", "同一段價格上漲：算空頭被軋", lb2[3] == (0.0, 2.0, True), f"{lb2[3]}")
oi2 = [x for x in oi if x[0] != 2 * M5]
lb3 = LR.liq_bars(k, oi2)
check("L1", "前一筆快照缺：那段標成沒資料", lb3[3] == (0.0, 0.0, False), f"{lb3[3]}")

print("清算反轉訊號：4h 單邊爆量、離開極值、極值不是剛剛")
fx = fresh()
P = dict(LR.LQ)
k = [bar(j * M5, 100, 100.2, 99.8, 100) for j in range(60)]
for j in range(40, 50): k[j] = bar(j * M5, 100 - (j - 40), 100 - (j - 40), 99 - (j - 40), 99 - (j - 40))   # 一路跌到 90
k[50] = bar(50 * M5, 90, 90.5, 89.0, 90.2)        # 最低 89
for j in range(51, 60): k[j] = bar(j * M5, 90.2, 91.0, 90.0, 90.9)
lb = [(0.0, 0.0, True)] * 60
for j in range(42, 52): lb[j] = (0.3, 0.0, True)  # 10 段各 0.3% ＝ 3%
s = LR.lq_signal(k, 55, lb, P)
check("S1", "（前提）多頭被清 3% ≥ 2%、最低 89 在 5 根前、收盤 90.9 離開 >0.5% → 做多", s is not None and s["side"] == "LONG", f"{s}")
if s:
    R = 90.9 - 89 * (1 - 0.003)
    check("S1", "掛單＝收盤 90.9、止損＝89×(1−0.3%)、目標＝+1.5R／+3R", abs(s["limit"] - 90.9) < 1e-9 and abs(s["stop"] - 88.733) < 1e-9
          and abs(s["tp1"] - (90.9 + 1.5 * R)) < 1e-9 and abs(s["tp2"] - (90.9 + 3 * R)) < 1e-9, f"{s}")
check("S1", "最低點就在 1 根前（還在破底）：不給", LR.lq_signal(k, 51, lb, P) is None, "settle_bars=2")
lb_weak = [(x[0] / 2, x[1], x[2]) for x in lb]
check("S1", "爆量只有 1.5%：不給", LR.lq_signal(k, 55, lb_weak, P) is None, "")
lb_two = [(x[0], x[0] / 2, x[2]) for x in lb]
check("S1", "反向也有一半（不到 3 倍）：不是單邊，不給", LR.lq_signal(k, 55, lb_two, P) is None, "")
lb_gap = [(x[0], x[1], j < 20) for j, x in enumerate(lb)]
check("S1", "最近 4h 的 OI 資料不到八成：不給", LR.lq_signal(k, 55, lb_gap, P) is None, "")

print("撮合：限價要穿過、目標一減半保本、目標二")
fx = fresh()
k = [bar(0, 100.5, 101, 100.2, 100.5),
     bar(M5, 100.5, 100.6, 99.9, 100.2),          # 穿過 100 → 成交（這根沒碰 98）
     bar(2 * M5, 100.2, 103.5, 100.1, 103.2),     # 到目標一 103，沒回到 100
     bar(3 * M5, 103.2, 106.5, 102, 106)]         # 到目標二 106
tr, cnt = LR.simulate(k, one_sig(0, LONG), P0, NOCOST)
check("M1", "（前提）一筆、發出 1、成交 1", len(tr) == 1 and cnt == dict(issued=1, filled=1, expired=0), f"{tr} {cnt}")
if tr:
    check("M1", "目標一一半 +1.5R、目標二一半 +3R ＝ +2.25R；% ＝ (0.5×3＋0.5×6)／100 ＝ 4.5%",
          tr[0]["reason"] == "tp2" and tr[0]["r_gross"] == 2.25 and tr[0]["pct_gross"] == 4.5, f"{tr[0]}")
tr2, _ = LR.simulate(k, one_sig(0, LONG), P0)
if tr2:
    net = (0.5 * (103 - 100 - 103 * 0.0002) + 0.5 * (106 - 100 - 106 * 0.0002) - 100 * 0.0002) / 2
    check("M1", "淨值：進場與兩段目標都是 maker 0.02%", abs(tr2[0]["r_net"] - round(net, 3)) < 1e-9, f"{tr2[0]['r_net']} vs {net:.4f}")
kb = [bar(0, 100.5, 101, 100.2, 100.5), bar(M5, 100.5, 100.6, 99.9, 100.2), bar(2 * M5, 100.2, 103.2, 99.5, 101)]   # 同一根到目標一又回到 100
tr3, _ = LR.simulate(kb, one_sig(0, LONG), P0, NOCOST)
check("M1", "同一根到目標一又碰到入場價：保守算剩下一半保本，+0.75R", len(tr3) == 1 and tr3[0]["reason"] == "be" and tr3[0]["r_gross"] == 0.75, f"{tr3}")

print("撮合：保守的先後順序")
fx = fresh()
k = [bar(0, 100.5, 101, 100.2, 100.5), bar(M5, 100.5, 100.6, 99.9, 100.2), bar(2 * M5, 100.2, 104, 97.5, 101)]
tr, _ = LR.simulate(k, one_sig(0, LONG), P0, NOCOST)
check("M2", "（前提）同一根碰到止損 98 也碰到目標一：算止損 −1R", len(tr) == 1 and tr[0]["reason"] == "stop" and tr[0]["r_gross"] == -1.0, f"{tr}")
k2 = [bar(0, 100.5, 101, 100.2, 100.5), bar(M5, 100.5, 100.6, 97.0, 99)]
tr2, _ = LR.simulate(k2, one_sig(0, LONG), P0, NOCOST)
check("M2", "成交那根就跌破止損：−1R", len(tr2) == 1 and tr2[0]["reason"] == "stop" and tr2[0]["r_gross"] == -1.0, f"{tr2}")
k3 = [bar(0, 100.5, 101, 100.2, 100.5), bar(M5, 100.5, 100.6, 99.9, 100.2), bar(2 * M5, 97, 97.5, 96, 97)]
tr3, _ = LR.simulate(k3, one_sig(0, LONG), P0, NOCOST)
check("M2", "開盤就跳過止損：用開盤價 97 出，−1.5R", len(tr3) == 1 and tr3[0]["r_gross"] == -1.5, f"{tr3}")
k4 = [bar(0, 100.5, 101, 100.2, 100.5)] + [bar(j * M5, 100.5, 101, 100.0, 100.5) for j in range(1, 6)]
tr4, cnt4 = LR.simulate(k4, one_sig(0, LONG), P0, NOCOST)
check("M2", "最低剛好 100（沒穿過）：不算成交，3 根後過期", cnt4 == dict(issued=1, filled=0, expired=1), f"{cnt4}")
check("M2", "（對照組前提已在上一項）過期的不是交易", len(tr4) == 0, f"{tr4}")
k5 = [bar(0, 100.5, 101, 100.2, 100.5), bar(M5, 100.5, 100.6, 99.9, 100.2)] + [bar(j * M5, 100.2, 101, 99.5, 100.6) for j in range(2, 15)]
tr5, _ = LR.simulate(k5, one_sig(0, LONG), P0, NOCOST)
check("M2", "沒碰止損、沒到目標：持有 10 根後收盤 100.6 出，+0.3R", len(tr5) == 1 and tr5[0]["reason"] == "time" and tr5[0]["bars"] == 10 and tr5[0]["r_gross"] == 0.3, f"{tr5}")

print("撮合：掛單中不換新訊號、出場後冷卻")
fx = fresh()
k = [bar(j * M5, 100.5, 101, 100.2, 100.5) for j in range(12)]
tr, cnt = LR.simulate(k, lambda i: dict(LONG, stop0=98.0), P0, NOCOST)
check("M3", "每根都有訊號、都沒成交：第 0 根掛、第 4 根過期、冷卻 2 根、第 6 根再掛、第 10 根過期 → 發出 2、過期 2（掛單中沒有換新單）", cnt == dict(issued=2, filled=0, expired=2), f"{cnt}")

print("熬鷹：RSI≥75、24h ≥4%、站上 240h 均線、貼近 24h 高點")
fx = fresh()
k = [bar(j * H1, 100, 100.5, 99.5, 100) for j in range(260)]
for j in range(240, 260):
    p = 100 * (1.003 ** (j - 239)); k[j] = bar(j * H1, p / 1.003, p, p / 1.003, p)
ind = LR.ay_ind(k)
s = LR.ay_signal(k, 259, ind)
check("A1", "（前提）連漲 20 根：RSI 高、24h 漲 6%、在均線上、收在最高 → 放空", s is not None and s["side"] == "SHORT", f"{s} rsi={ind['rsi'][259]}")
if s:
    c = k[259]["c"]
    check("A1", "止損 +1.5%、目標 −2.5%／−4%", abs(s["stop"] - c * 1.015) < 1e-9 and abs(s["tp1"] - c * 0.975) < 1e-9 and abs(s["tp2"] - c * 0.96) < 1e-9, f"{s}")
check("A1", "第 239 根（還沒漲）：不給", LR.ay_signal(k, 239, ind) is None, "")
k2 = [dict(x) for x in k]; k2[259] = dict(k2[259], h=k2[259]["c"] * 1.05)
check("A1", "收盤離 24h 最高 5%：不貼近高點，不給", LR.ay_signal(k2, 259, LR.ay_ind(k2)) is None, "")

print("報表與預先登記的判讀")
fx = fresh()
mk = lambda t, r: dict(side="LONG", t_sig=t, r_gross=r + 0.05, r_net=r, pct_gross=0.1, pct_net=0.05, reason="tp2", symbol="BTCUSDT")
ts = [mk(j, 0.2 if j % 2 else -0.1) for j in range(40)]
rep = LR.report(ts, 0, 40, 40)
check("V1", "（前提）40 筆、一半 +0.2 一半 −0.1：淨平均 +0.05R、PF 2、前後段都正 → 成立", len(rep["rows"]) >= 1 and rep["rows"][0]["n"] == 40 and rep["rows"][0]["r_net"] == 0.05
      and rep["rows"][0]["pf"] == 2.0 and rep["verdict"].startswith("成立"), f"{(rep['rows'] or [None])[0]} {rep['verdict']}")
ts2 = [mk(j, 0.3 if j < 20 else -0.2) for j in range(40)]
check("V1", "全部是正的但後段是負的：不成立", LR.report(ts2, 0, 40, 40)["verdict"] == "不成立", "")
check("V1", "只有 10 筆：樣本不足", LR.report(ts[:10], 0, 40, 40)["verdict"].startswith("樣本不足"), "")
check("V1", "各幣分組", [x["symbol"] for x in rep["symbols"]] == ["BTCUSDT"], f"{rep['symbols']}")

print("幣名輸入")
fx = fresh()
check("P1", "btc、ETH/USDT、paxg（全形逗號、空白、重複）→ 三檔", LR.parse_symbols("btc，ETH/USDT  paxg btc") == ("BTCUSDT", "ETHUSDT", "PAXGUSDT"), f"{LR.parse_symbols('btc，ETH/USDT  paxg btc')}")
check("P1", "空白：預設宇宙", LR.parse_symbols("  ") == LR.SYMBOLS, "")

print("K 線分塊快取：整塊在一小時前以前才存，重跑不再抓")
fx = fresh()
class FakeB:
    def __init__(self): self.calls = []
    def klines_range(self, s, iv, a, b):
        self.calls.append((a, b)); return [bar(t, 1, 1, 1, 1) for t in range(a, b + 1, H1)]
d = tempfile.mkdtemp(); fb = FakeB(); size = LR.CHUNK["h1"]
now = 3 * size + 5 * H1
k = LR.klines_chunked(fb, d, "X", "1h", size // 2, now, now)
check("K1", "（前提）三塊半 → 抓 4 次、資料從 start 開始到 now 為止、時間不重複", len(fb.calls) == 4 and k and k[0]["t"] == size // 2 and k[-1]["t"] == now - H1
      and len({x["t"] for x in k}) == len(k), f"{fb.calls} {len(k)}")
check("K1", "存了 3 個完整區塊的快取檔（最後一塊沒過完不存）", len([f for f in os.listdir(d) if f.startswith("lq_X_1h_")]) == 3, f"{os.listdir(d)}")
fb2 = FakeB(); k2 = LR.klines_chunked(fb2, d, "X", "1h", size // 2, now, now)
check("K1", "重跑：只抓最後一塊、結果一樣", len(fb2.calls) == 1 and k2 == k, f"{fb2.calls}")

print("整個工作（模擬交易所）：不在永續的幣列出來、兩個策略都有報表、結束推播一次")
fx = fresh()
def fake_kl(s_, iv, a, b):
    step = M5 if iv == "5m" else H1
    t0 = a + (-a) % step
    return [bar(t, 100, 100.3, 99.7, 100) for t in range(t0, b + 1, step)]
B.perp_symbols = lambda: ["BTCUSDT", "ETHUSDT"]
B.klines_range = fake_kl
now_ms = int(time.time() * 1000)
for dd in range(-6, 2):                                    # OI 每日檔先放進快取：不打網路
    ymd = labels._ymd(now_ms + dd * 86400000)
    json.dump([], open(os.path.join(sweep.KDIR, f"BTCUSDT_metrics_{ymd.replace('-', '')}.json"), "w"))
os.environ.update(LQ_SYMBOLS="BTCUSDT,FOOUSDT", LQ_DAYS="3", AY_DAYS="15")
LR.main()
st = LR.state()
check("X1", "（前提）工作跑完", (st.get("status") or "").startswith("完成"), f"{st.get('status')}")
check("X1", "兩個策略都有報表、都只跑 BTCUSDT、FOOUSDT 列為不在永續", bool(st.get("ay") and st.get("lq")) and st["ay"]["symbols"] == ["BTCUSDT"]
      and st["lq"]["missing"] == ["FOOUSDT"], f"{ {k: (v or {}).get('symbols') for k, v in st.items() if k in ('ay', 'lq')} }")
check("X1", "平盤沒有訊號：判讀是樣本不足", bool(st.get("lq")) and st["lq"]["report"]["verdict"].startswith("樣本不足"), f"{st.get('lq', {}).get('report', {}).get('verdict')}")
check("X1", "結束推播一次", len([m for m in TG if m.startswith("🔬 清算反轉／熬鷹完成")]) == 1, f"{TG}")
fx = fresh()
B.perp_symbols = lambda: ["BTCUSDT"]
os.environ.update(LQ_SYMBOLS="FOOUSDT")
LR.main()
check("X2", "全部都不在永續：中止並推播", (LR.state().get("status") or "").startswith("中止") and any(m.startswith("🔬 清算反轉／熬鷹中止") for m in TG), f"{TG}")
for kk in ("LQ_SYMBOLS", "LQ_DAYS", "AY_DAYS"): os.environ.pop(kk, None)

finish()
