"""籌碼標籤分析（研究用，不動實盤）：F／G 每筆歷史交易在進場當下貼上「成交量分布」與「合約籌碼（OI）」標籤，
按標籤三等分，看各組的 PF、平均 R、總 R，以及前後兩段方向是否一致。

- 交易：跟歷史事件掃描同一個母體（F 用崩盤日、G 用暴漲日）、同一份 1m K 線快取、同一個 backtest.simulate，
  參數用目前的實盤設定（有套用實盤覆蓋就照覆蓋）。
- 成交量分布：進場那根 1m 收盤為止的 24 小時，每根量平均攤在它的高低區間。
- OI：data.binance.vision 的每日 metrics 檔（5 分鐘一筆：持倉量、持倉價值、大戶持倉多空比）。
  /futures/data/openInterestHist 只有最近 30 天，90 天回測用不了，所以走這個公開檔案庫（不算 API 權重）。
  只用「時間戳 + 5 分鐘 <= 進場時間」的列（不確定時間戳標的是區間頭還是尾，保守取前一筆，不偷看）。
以獨立子程序執行，結果寫 DATA_DIR/labels.json；OI 檔快取在 kcache/。"""
import calendar, csv, io, json, os, random, subprocess, sys, time, traceback, urllib.error, urllib.request, zipfile

DATA_DIR = os.environ.get("DATA_DIR", "./data")
STATE = os.path.join(DATA_DIR, "labels.json")
VISION = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"
DAY = 86400000
OI_LAG = 300000              # metrics 列的時間戳 + 5 分鐘才算「已知」
TARGETS = (("F", "crash"), ("G", "pumpday"))   # 引擎 → 自己方向的無偏差母體（README 方法論教訓）
PERM = 2000                  # 置換檢定次數

# (鍵, 名稱, 說明)；值都是「進場當下」就算得出來的
LABELS = (
    ("vp_poc", "價格相對主峰 %", "收盤相對 24h 成交量最大價位（0.5% 一格）。負＝已跌破主峰"),
    ("vp_profit", "獲利比例 %", "24h 成交量中，成交價低於現價的比例（多方持倉在獲利的比例）"),
    ("vp_up2", "上方 2% 量占比 %", "現價到 +2% 之間的成交量占 24h 的比例（套牢賣壓／空方成本）"),
    ("vp_dn2", "下方 2% 量占比 %", "-2% 到現價之間的成交量占 24h 的比例（下方承接）"),
    ("oi_1h", "OI 1h 變化 %", "持倉量現在相對 1 小時前"),
    ("oi_24h", "OI 24h 變化 %", "持倉量現在相對 24 小時前（拉盤時暴增＝槓桿多單堆積）"),
    ("oi_off_hi", "OI 距 24h 高點 %", "持倉量相對 24h 最高（越負＝已經清掉越多）"),
    ("ls_top", "大戶持倉多空比", "前 20% 大戶的多單持倉／空單持倉"),
)

_proc = None

# ---------- 主程序用 ----------
def state():
    try: return json.load(open(STATE))
    except Exception: return {}

def running():
    return _proc is not None and _proc.poll() is None

def start(days=90):
    """跟歷史事件掃描互斥：兩個子程序各自只看自己用掉的 K 線權重，同時跑會一起打滿，擠到實盤。"""
    global _proc
    from . import population, sweep
    if running() or sweep.running() or population.running(): return False
    env = dict(os.environ, LABEL_DAYS=str(int(days)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _proc = subprocess.Popen([sys.executable, "-m", "app.labels"], env=env, cwd=root)
    return True

# ---------- 標籤計算（純函式，測試直接呼叫）----------
def volume_profile(k, i, hours=24, bin_pct=0.005, band=0.02):
    """k[:i+1] 最後 hours 小時的 1m K。回傳 dict(vp_poc, vp_profit, vp_up2, vp_dn2)，單位 %；沒量回 None。"""
    w = k[max(0, i - hours * 60 + 1):i + 1]
    c = k[i]["c"]
    if c <= 0: return None
    tot = below = up = dn = 0.0
    bins = {}
    bw = c * bin_pct
    lo_b, hi_b = c, c * (1 + band)
    lo_d, hi_d = c * (1 - band), c

    def part(l, h, a, b):                              # [l,h] 與 [a,b] 重疊的比例
        if h <= l: return 1.0 if a <= l <= b else 0.0
        return max(0.0, min(h, b) - max(l, a)) / (h - l)

    for x in w:
        v, l, h = x["v"], x["l"], x["h"]
        if v <= 0 or l <= 0: continue
        tot += v
        below += v * (part(l, h, 0, c) if h > l else (1.0 if l < c else 0.0))
        up += v * (part(l, h, lo_b, hi_b) if h > l else (1.0 if lo_b < l <= hi_b else 0.0))
        dn += v * (part(l, h, lo_d, hi_d) if h > l else (1.0 if lo_d <= l < hi_d else 0.0))
        a, b = int(l // bw), int(h // bw)
        for j in range(a, b + 1):                       # 量攤到 0.5% 的格子，找主峰
            bins[j] = bins.get(j, 0.0) + v * (part(l, h, j * bw, (j + 1) * bw) if h > l else 1.0)
    if tot <= 0 or not bins: return None
    poc = (max(bins, key=bins.get) + 0.5) * bw
    return dict(vp_poc=round((c / poc - 1) * 100, 2), vp_profit=round(below / tot * 100, 1),
                vp_up2=round(up / tot * 100, 1), vp_dn2=round(dn / tot * 100, 1))

def oi_labels(rows, t_entry):
    """rows：[(ts_ms, oi, oi_val, ls_top)] 依時間排序。t_entry：進場時間（那根 1m 收盤）。
    只用 ts + OI_LAG <= t_entry 的列。缺資料（現在那筆、或 1h／24h 前那筆找不到）的項目回 None。"""
    known = [r for r in rows if r[0] + OI_LAG <= t_entry]
    out = dict(oi_1h=None, oi_24h=None, oi_off_hi=None, ls_top=None)
    if not known: return out
    now = known[-1]
    if t_entry - now[0] > 30 * 60000: return out           # 最近一筆已經是半小時前：這段沒資料，不拿舊值充數
    out["ls_top"] = round(now[3], 3) if now[3] is not None else None

    def at(ts):                                            # ts 之前最近一筆，誤差 15 分鐘內
        cand = [r for r in known if r[0] <= ts]
        return cand[-1] if cand and ts - cand[-1][0] <= 15 * 60000 else None
    if now[1] and now[1] > 0:
        a = at(now[0] - 3600000)
        if a and a[1] > 0: out["oi_1h"] = round((now[1] / a[1] - 1) * 100, 1)
        b = at(now[0] - DAY)
        if b and b[1] > 0: out["oi_24h"] = round((now[1] / b[1] - 1) * 100, 1)
        day = [r[1] for r in known if r[0] > now[0] - DAY and r[1]]
        if b and day: out["oi_off_hi"] = round((now[1] / max(day) - 1) * 100, 1)   # 24h 資料不完整就不算高點
    return out

def _stats(rs):
    if not rs: return dict(n=0)
    w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
    return dict(n=len(rs), win=round(len(w) / len(rs) * 100), avg=round(sum(rs) / len(rs), 2), tot=round(sum(rs), 1),
                pf=round(sum(w) / abs(sum(l)), 2) if l and sum(l) else None)

def analyse(trades, key, split_ts, perm=PERM, seed=7):
    """trades：[{r, t, <key>: 值或 None}]。三等分（低／中／高）＋ 無資料一組。
    回傳每組統計、前後段平均 R、前 10 大獲利單落在哪組、高減低平均 R 的置換檢定 p 值與判讀。"""
    have = sorted((t for t in trades if t.get(key) is not None), key=lambda t: t[key])
    miss = [t for t in trades if t.get(key) is None]
    out = dict(key=key, n=len(have), missing=len(miss), groups=[])
    if len(have) < 30:
        out["verdict"] = f"有值的只有 {len(have)} 筆，不分組"; return out
    n = len(have); cuts = (n // 3, 2 * n // 3)
    parts = (have[:cuts[0]], have[cuts[0]:cuts[1]], have[cuts[1]:])
    top = set(id(t) for t in sorted(trades, key=lambda t: -t["r"])[:10])
    for name, g in zip(("低", "中", "高"), parts):
        st = _stats([t["r"] for t in g])
        a = [t["r"] for t in g if t["t"] < split_ts]; b = [t["r"] for t in g if t["t"] >= split_ts]
        st.update(name=name, rng=f"{g[0][key]:g} ~ {g[-1][key]:g}",
                  first=_stats(a).get("avg"), first_n=len(a), second=_stats(b).get("avg"), second_n=len(b),
                  top10=sum(1 for t in g if id(t) in top))
        out["groups"].append(st)
    if miss:
        st = _stats([t["r"] for t in miss]); st.update(name="無資料", rng="", top10=sum(1 for t in miss if id(t) in top))
        out["groups"].append(st)
    lo, hi = parts[0], parts[2]
    diff = sum(t["r"] for t in hi) / len(hi) - sum(t["r"] for t in lo) / len(lo)
    rs = [t["r"] for t in have]; rnd = random.Random(seed); ext = 0
    for _ in range(perm):                                   # 打亂標籤：高、低兩組的平均差有多常這麼大
        rnd.shuffle(rs)
        d = sum(rs[cuts[1]:]) / len(hi) - sum(rs[:cuts[0]]) / len(lo)
        if abs(d) >= abs(diff) - 1e-12: ext += 1
    p = (ext + 1) / (perm + 1)
    g0, g2 = out["groups"][0], out["groups"][2]
    same = (None not in (g0["first"], g2["first"], g0["second"], g2["second"])
            and (g2["first"] - g0["first"]) * (g2["second"] - g0["second"]) > 0
            and (g2["first"] - g0["first"]) * diff > 0)
    small = min(g0["first_n"], g0["second_n"], g2["first_n"], g2["second_n"]) < 15
    out.update(diff=round(diff, 2), p=round(p, 3), same=same)
    if p < 0.05 and same and not small:
        better = "高" if diff > 0 else "低"
        out["verdict"] = f"候選：{better}組明顯較好、前後兩段同向（還要看排除另一組會砍掉幾筆前 10 大單）"
    elif p < 0.05 and same:
        out["verdict"] = "p < 0.05、兩段同向，但有半段樣本不到 15 筆 → 還不能判斷，看前瞻驗證"
    elif p < 0.05 and not same:
        out["verdict"] = "全期有差，但前後兩段方向不一致 → 不採用"
    elif same and not small:
        out["verdict"] = "兩段同向但差距可能是運氣（p ≥ 0.05）→ 先記著，累積更多樣本"
    else:
        out["verdict"] = "看不出差異"
    return out

# ---------- 實盤前瞻驗證 ----------
OI_KEYS = ("oi_1h", "oi_24h", "oi_off_hi")          # 實盤訊號當下記的 OI 標籤（只記、不過濾）
FROZEN = os.path.join(DATA_DIR, "forward_cuts.json")
HYPO = {("F", "oi_1h"): "低", ("F", "oi_off_hi"): "低", ("G", "oi_1h"): "中"}   # 回測看到、等前瞻驗證的假設：哪組較好

def live_oi(symbol, bar_t, hist=None):
    """實盤訊號當下的 OI 標籤，算法跟回測同一個 oi_labels（同樣只用時間戳 + 5 分鐘 <= 進場時間的列）。
    資料走 /futures/data/openInterestHist（5m、最近 25 小時）。抓不到就拋例外，呼叫端記原因，不影響下單。"""
    if hist is None:
        from . import binance as B
        hist = B.oi_hist(symbol, "5m", 300)
    if not isinstance(hist, list) or not hist: raise RuntimeError("openInterestHist 回空")
    rows = sorted((int(x["timestamp"]), float(x["sumOpenInterest"]), float(x.get("sumOpenInterestValue") or 0), None) for x in hist)
    o = oi_labels(rows, bar_t + 60000)
    return {k: o[k] for k in OI_KEYS}

def _cuts_from(st):
    """labels.json 的三等分界線：低組上界、高組下界（範圍字串 'a ~ b'）。"""
    out = {}
    for eid, R in (st.get("engines") or {}).items():
        for L in R.get("labels") or []:
            g = {x.get("name"): x for x in L.get("groups") or []}
            if L.get("key") not in OI_KEYS or "低" not in g or "高" not in g: continue
            try: out[f"{eid}.{L['key']}"] = [float(g["低"]["rng"].split("~")[1]), float(g["高"]["rng"].split("~")[0])]
            except Exception: continue
    return out

def frozen_cuts(path=None):
    """前瞻驗證的分組界線：第一次用時從回測結果凍結下來，之後固定（重跑回測不會改到，避免看了新資料再挪界線）。"""
    path = path or FROZEN
    try: return json.load(open(path))
    except Exception: pass
    st = state()
    if not str(st.get("status", "")).startswith("完成"): return None
    cuts = _cuts_from(st)
    if not cuts: return None
    d = dict(cuts=cuts, at=time.strftime("%m-%d %H:%M"), source=st.get("started"))
    json.dump(d, open(path, "w"), ensure_ascii=False); return d

def forward(closed, frozen):
    """已平倉的 F／G 依開倉當下的 OI 標籤分組（凍結界線），看回測的假設在新資料上成不成立。"""
    if not frozen: return dict(error="還沒有完成的籌碼標籤分析可以凍結分組界線")
    closed = [t for t in (closed or []) if not t.get("excluded") and t.get("engine") in ("F", "G")]
    rows = []
    for key_, (lo, hi) in sorted(frozen["cuts"].items()):
        eid, key = key_.split(".", 1)
        ts = [t for t in closed if t.get("engine") == eid]
        grp = {"低": [], "中": [], "高": [], "無資料": []}
        for t in ts:
            v = t.get(key)
            if not isinstance(t.get("r"), (int, float)): continue        # R 未知的不算（跟績效統計一致）
            name = "無資料" if not isinstance(v, (int, float)) else "低" if v <= lo else "高" if v >= hi else "中"
            grp[name].append(t["r"])
        for name, rs in grp.items():
            st = _stats(rs)
            rows.append(dict(engine=eid, label=key, cut=f"{lo:g} / {hi:g}", group=name, n=st["n"], win=st.get("win"),
                             pf=st.get("pf"), avg=st.get("avg"), tot=st.get("tot"), hypo="✓ 假設較好" if HYPO.get((eid, key)) == name else ""))
    return dict(frozen_at=frozen.get("at"), rows=rows, n=len(closed))

# ---------- OI 每日檔 ----------
def _ymd(ms): return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))

def _parse_ts(s):
    s = s.strip()
    if s.isdigit(): return int(s) if len(s) > 10 else int(s) * 1000
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S")) * 1000

def parse_metrics(raw):
    """zip（或已解開的 csv 文字）→ [(ts, oi, oi_val, ls_top)]。欄位照表頭名稱取，不靠位置。"""
    if isinstance(raw, bytes):
        z = zipfile.ZipFile(io.BytesIO(raw)); raw = z.read(z.namelist()[0]).decode()
    rd = csv.DictReader(io.StringIO(raw)); out = []
    f = lambda v: float(v) if v not in (None, "") else None
    for r in rd:
        try: out.append((_parse_ts(r["create_time"]), f(r.get("sum_open_interest")), f(r.get("sum_open_interest_value")),
                         f(r.get("sum_toptrader_long_short_ratio"))))
        except Exception: continue
    return sorted(out)

class Missing(RuntimeError):
    """這一天的檔案暫時抓不到（網路錯、5xx、還沒產生）。不能當成「這天沒有 OI」寫進快取。"""

class NotYet(Missing):
    """最近兩天的 404：幣安每日檔隔天才產生，不是抓取失敗。"""

def _http(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "pdh-research"}), timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise Missing(f"HTTP {e.code}") from None
    except Exception as e:
        raise Missing(str(e)[:80]) from None

def metrics_day(sym, ymd, kdir, fetch=None, now_ms=None):
    """一天的 metrics。404 而且日期是兩天前以前 → 幣安確定沒有這天的檔（還沒上市之類），快取成空的；
    最近兩天的 404（檔案可能還沒產生）和其他錯誤 → 拋 Missing、不寫快取。"""
    p = os.path.join(kdir, f"{sym}_metrics_{ymd.replace('-', '')}.json")
    if os.path.exists(p):
        try: return [tuple(x) for x in json.load(open(p))]
        except Exception: pass
    raw = (fetch or _http)(VISION.format(s=sym, d=ymd))
    if raw is None:
        now_ms = now_ms or int(time.time() * 1000)
        if calendar.timegm(time.strptime(ymd, "%Y-%m-%d")) * 1000 < now_ms - 2 * DAY:
            json.dump([], open(p, "w")); return []
        raise NotYet(f"{ymd} 的檔案還沒產生")
    rows = parse_metrics(raw); json.dump(rows, open(p, "w")); return rows

# ---------- 子程序 ----------
def _write(**kw):
    s = state(); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False)

def _end(msg):
    """結束（完成／中止／失敗）：寫狀態並推播一次——背景跑二三十分鐘，不用一直開著網頁等。"""
    from . import telegram
    _write(status=msg)
    try: telegram.send("🔬 籌碼標籤分析" + msg)
    except Exception as e: print("labels telegram:", e)

def main():
    from . import backtest, binance as B, presets, sweep
    days = int(os.environ.get("LABEL_DAYS", "90"))
    try:
        live = presets.live() or {}
        presets.apply_live()
    except Exception as e:
        live = {}; print("labels apply_live:", e)
    run_ms = int(time.time() * 1000); split_ts = run_ms - days * DAY // 2
    _write(status="準備中…", started=time.strftime("%m-%d %H:%M"), days=days, engines={}, rows=[],
           params=f"實盤覆蓋 {len(live)} 項" if live else "程式預設（實盤沒有覆蓋）",
           split=time.strftime("%m-%d", time.localtime(split_ts / 1000)))
    report, rows_all = {}, []
    try:
        for eid, mode in TARGETS:
            ek = f"events_{mode}_{days}_{time.strftime('%Y%m%d')}"
            events = sweep._load(ek)
            if events is None:
                _write(status=f"{eid}：掃描{'崩盤日' if mode == 'crash' else '暴漲日'}母體…")
                try: events, failed = {"crash": sweep.find_crashes, "pumpday": sweep.find_pumps}[mode](days, B)
                except B.RateLimited as e:
                    _end(f"中止：掃描日線時被幣安限流（{str(e)[:100]}）。母體不完整，沒有分析——過幾分鐘再跑"); return
                if failed:
                    _end(f"中止：{len(failed)} 檔日線抓不到，母體不完整（{failed[0]}）——過幾分鐘再跑"); return
                sweep._save(ek, events)
            trades, oi_miss, oi_new = [], 0, 0
            for n, ev in enumerate(events):
                if n % 10 == 0: _write(status=f"{eid}：{n}/{len(events)} 個事件，{len(trades)} 筆交易，OI 抓不到 {oi_miss} 天")
                pk = ev["peak_ts"]
                try:
                    k1m = sweep._cached(f"{ev['symbol']}_1m_{pk}", lambda: B.klines_range(ev["symbol"], "1m", pk - DAY, pk + 2 * DAY))
                except B.RateLimited as e:
                    _end(f"中止：抓 {ev['symbol']} 1m K 線被幣安限流（{str(e)[:100]}）。已抓的有快取，過幾分鐘重跑會接著抓"); return
                except Exception as e:
                    print("labels kline", ev["symbol"], e); continue
                if not k1m: continue
                tr = backtest.simulate(k1m, (eid,))
                if not tr: continue
                idx = {x["t"]: j for j, x in enumerate(k1m)}
                oi = []
                for d in range(-2, 3):                      # 交易在 peak-1d～peak+2d，OI 24h 要再往前一天
                    if pk + d * DAY > run_ms: continue      # 還沒到的日子：本來就不會有交易，不抓也不算缺
                    try: oi += metrics_day(ev["symbol"], _ymd(pk + d * DAY), sweep.KDIR)
                    except NotYet: oi_new += 1
                    except Missing: oi_miss += 1
                    time.sleep(0.03)
                oi.sort()
                for t in tr:
                    j = idx.get(t["t"])
                    if j is None: continue
                    row = dict(engine=eid, symbol=ev["symbol"], t=t["t"], r=t["r"], reason=t["reason"],
                               time=time.strftime("%m-%d %H:%M", time.localtime(t["t"] / 1000)))
                    row.update(volume_profile(k1m, j) or {})
                    row.update(oi_labels(oi, t["t"] + 60000))
                    trades.append(row)
            base = _stats([t["r"] for t in trades])
            report[eid] = dict(mode=mode, events=len(events), base=base, oi_missing_days=oi_miss, oi_notyet_days=oi_new,
                               labels=[dict(analyse(trades, k, split_ts), name=nm, help=hp) for k, nm, hp in LABELS])
            rows_all += trades
            _write(engines=report)
        _write(engines=report, rows=sorted(rows_all, key=lambda t: t["t"]))
        cand = [f"{e} {L['name']}" for e, R in report.items() for L in R["labels"] if L.get("verdict", "").startswith("候選")]
        _end(f"完成：{time.strftime('%m-%d %H:%M')}，F {report['F']['base'].get('n', 0)} 筆、G {report['G']['base'].get('n', 0)} 筆；"
             + (f"濾網候選 {len(cand)} 個：{'、'.join(cand)}" if cand else "沒有濾網候選"))
    except Exception as e:
        _end(f"失敗：{e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
