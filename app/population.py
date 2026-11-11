"""母體偏差檢查（研究用，不動實盤）。兩個工作，背景子程序執行，結果寫 DATA_DIR/population.json：

1. live（實盤對照）：已平倉的 F／G 逐筆貼標籤——
   - 當天（UTC）有沒有達到回測母體的標準（G：最高價比前日收漲 ≥30%；F：最低價比前日收跌 ≥30%）。
     這個標準用到進場之後的價格，就是懷疑的事後偏差。
   - 進場時 24h 漲跌（收盤比 24h 前）：實盤名單只盯 24h 漲 ≥40% 或跌 ≤−30% 的幣（約略，另有擁擠分數），回測沒有這道門。
   - 進場時 24h 區間（高／低 − 1，就是 G／F 自己的 24h 條件）分段。
2. trigger（無偏差母體）：不看日 K 結果，改用引擎自己的必要條件挑時段——
   某小時「含前一小時的 2 小時高低差 ≥10%」而且「含前 24 小時的 25 小時高低差 ≥15%」。
   任何一筆 F／G 進場都一定落在這種小時裡（15 分鐘內漲／跌 10%、24h 區間 ≥15%／40%），而條件只用到該小時為止的價格，
   所以挑出來的時段不帶「事後知道會大漲／大崩」的資訊。這些時段（前面補 24h＋1h 暖機，後面留 3h 讓單子出場）抓 1m 跑 F、G，
   只收暖機之後的進場。交易一樣貼上面三種標籤，跟實盤並排比。
   限制：宇宙是目前還在交易的永續合約（下市的幣不在，跟歷史事件掃描一樣有生存者偏差）。"""
import json, os, subprocess, sys, time, traceback

DATA_DIR = os.environ.get("DATA_DIR", "./data")
STATE = os.path.join(DATA_DIR, "population.json")
INPUT = os.path.join(DATA_DIR, "population_input.json")
DAY, HOUR = 86400000, 3600000
POP = dict(G=("rise", 0.30), F=("drop", 0.30), C=("drop", 0.30))   # 回測母體：暴漲日（G）、崩盤日（F、C）
WATCH = (40.0, -30.0)                                   # 實盤名單門檻：24h 漲 >= 40% 或跌 <= -30%（config.SCAN 預設）
BANDS = dict(G=(0.25, 0.40), F=(0.80, 1.50), C=(0.40, 0.80))   # 進場時 24h 區間分段的界線
TRIG = dict(r2h=0.10, r25h=0.15, pre=DAY + HOUR, post=3 * HOUR)
# 每種引擎挑時段的必要條件與 K 線週期：
#  F／G（1m）：15 分鐘漲／跌 10% → 2h 高低差 ≥10%；24h 區間 ≥15%（F 要 40%，更嚴）→ 25h 高低差 ≥15%。暖機 25h、出場留 3h（最多持有 60 根）。
#  C（5m）：單根 5m 跌 ≥15%（開→收）→ 那根所在小時的高／低 ≥ 1/0.85，進場在其後 3 根內 → 2h 高低差 ≥15%；不看 24h。
#           暖機 3h（引擎要 25 根、均量 20 根），出場留 7h（最多持有 72 根 5m＝6h）。
SPECS = dict(
    m1=dict(engines=("F", "G"), iv="1m", bar=60000, r2h=0.10, r25h=0.15, pre=DAY + HOUR, post=3 * HOUR),
    c5=dict(engines=("C",), iv="5m", bar=300000, r2h=0.15, r25h=0.0, pre=3 * HOUR, post=7 * HOUR),
)
ENGINES = ("C", "F", "G")
_proc = None

# ---------- 主程序用 ----------
def state():
    try: return json.load(open(STATE))
    except Exception: return {}

def running():
    return _proc is not None and _proc.poll() is None

def start(job, days=90, closed=None):
    """job: live | trigger。跟歷史事件掃描、籌碼標籤分析互斥（同時抓 K 線會一起打滿權重）。"""
    global _proc
    from . import labels, sweep
    if job not in ("live", "trigger") or running() or sweep.running() or labels.running(): return False
    if job == "live":
        rows = [dict(symbol=t.get("symbol"), engine=t.get("engine"), r=t.get("r"), bar_t=t.get("bar_t") or t.get("ts"),
                     closed=t.get("closed")) for t in (closed or [])
                if t.get("engine") in ENGINES and not t.get("excluded")]
        json.dump(rows, open(INPUT, "w"), ensure_ascii=False)
    env = dict(os.environ, POP_JOB=job, POP_DAYS=str(int(days)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _proc = subprocess.Popen([sys.executable, "-m", "app.population"], env=env, cwd=root)
    return True

# ---------- 純函式（測試直接呼叫）----------
def entry_tags(eid, k, i, day_hi, day_lo, prev_close, per_day=1440):
    """k[:i+1] 是到進場那根為止的 K（1m 一天 1440 根、5m 288 根；要多一根才算 24h 漲跌）。日 K 數字是進場那天（UTC）的。
    回傳 dict(pop, watch, band, gain24, chg24)；算不出來的是 None。"""
    out = dict(pop=None, watch=None, band=None, gain24=None, chg24=None)
    if prev_close and prev_close > 0 and day_hi and day_lo:
        kind, th = POP[eid]
        hit = day_hi / prev_close - 1 >= th if kind == "rise" else 1 - day_lo / prev_close >= th
        out["pop"] = "在" if hit else "不在"                  # 手機表格要窄：組名只寫「在／不在」，說明在網頁圖例
    w = k[max(0, i - per_day + 1):i + 1]
    lo, hi = min(x["l"] for x in w), max(x["h"] for x in w)
    if lo > 0 and len(w) >= per_day:
        g = hi / lo - 1; out["gain24"] = round(g * 100, 1)
        a, b = BANDS[eid]
        out["band"] = f"<{a:.0%}" if g < a else f"≥{b:.0%}" if g >= b else f"{a:.0%}～{b:.0%}"
    if i >= per_day and k[i - per_day]["c"] > 0:
        c = (k[i]["c"] / k[i - per_day]["c"] - 1) * 100; out["chg24"] = round(c, 1)
        out["watch"] = "會盯" if (c >= WATCH[0] or c <= WATCH[1]) else "不會盯"
    return out

def trigger_segments(h1, r2h=TRIG["r2h"], r25h=TRIG["r25h"], pre=TRIG["pre"], post=TRIG["post"], **_):
    """小時 K → 要抓 1m 的區段 [(開始, 結束, 最早可收的進場時間)]。只用每個小時為止的價格判斷。"""
    hits = []
    for j in range(1, len(h1)):
        w2 = h1[j - 1:j + 1]; w25 = h1[max(0, j - 24):j + 1]
        lo2, lo25 = min(x["l"] for x in w2), min(x["l"] for x in w25)
        if lo2 <= 0 or lo25 <= 0: continue
        if max(x["h"] for x in w2) / lo2 - 1 >= r2h and max(x["h"] for x in w25) / lo25 - 1 >= r25h:
            hits.append(h1[j]["t"])
    segs = []
    for h in hits:
        a, b = h - pre, h + HOUR + post
        if segs and a <= segs[-1][1]: segs[-1][1] = max(segs[-1][1], b)
        else: segs.append([a, b])
    return [(a, b, a + pre) for a, b in segs]

def day_stats(h1, t):
    """進場那天（UTC）的最高、最低（整天，含進場之後——事後偏差就在這裡）與前日收盤，從小時 K 算。"""
    d0 = t - t % DAY
    day = [x for x in h1 if d0 <= x["t"] < d0 + DAY]
    prev = [x for x in h1 if x["t"] < d0]
    if not day or not prev: return None, None, None
    return max(x["h"] for x in day), min(x["l"] for x in day), prev[-1]["c"]

def _stats(rs):
    if not rs: return dict(n=0)
    w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
    return dict(n=len(rs), win=round(len(w) / len(rs) * 100), avg=round(sum(rs) / len(rs), 2), tot=round(sum(rs), 1),
                pf=round(sum(w) / abs(sum(l)), 2) if l and sum(l) else None)

def report(trades, split_ts=None):
    """trades: [{engine, r, bar_t, pop, watch, band}] → {engine: [{tag, group, n, win, pf, avg, tot, first, second}]}，第一列是全部。
    split_ts 有給就附前段／後段平均 R（看了表才挑出來的分組，至少要兩段同向才值得追）。"""
    out = {}
    def row(tag, group, ts):
        d = dict(tag=tag, group=group, **_stats([t["r"] for t in ts]))
        if split_ts:
            a = [t["r"] for t in ts if (t.get("bar_t") or 0) < split_ts]; b = [t["r"] for t in ts if (t.get("bar_t") or 0) >= split_ts]
            d.update(first=_stats(a).get("avg"), first_n=len(a), second=_stats(b).get("avg"), second_n=len(b))
        return d
    for eid in ENGINES:
        ts = [t for t in trades if t.get("engine") == eid and isinstance(t.get("r"), (int, float))]
        if not ts: continue
        rows = [row("全部", "", ts)]
        for tag, name in (("pop", "母體"), ("watch", "名單"), ("band", "24h區間")):
            groups = {}
            for t in ts: groups.setdefault(t.get(tag) or "缺", []).append(t)
            for g in sorted(groups): rows.append(row(name, g, groups[g]))
        out[eid] = rows
    return out

OI_TESTS = (("oi_1h", "OI 1h 變化 %"), ("oi_24h", "OI 24h 變化 %"), ("oi_off_hi", "OI 距 24h 高點 %"), ("gain24", "24h 區間 %"))

def oi_report(trades, split_ts):
    """無偏差母體上，OI（與 24h 區間）三等分的判讀，跟籌碼標籤分析同一套（置換檢定＋前後兩段同向）。"""
    from . import labels
    out = {}
    for eid in ENGINES:
        ts = [dict(t, t=t.get("bar_t") or 0) for t in trades if t.get("engine") == eid and isinstance(t.get("r"), (int, float))]
        if ts: out[eid] = [dict(labels.analyse(ts, k, split_ts), name=nm) for k, nm in OI_TESTS]
    return out

# ---------- 子程序 ----------
def _write(**kw):
    s = state(); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False)

def _end(msg):
    from . import telegram
    _write(status=msg)
    try: telegram.send("🔬 母體偏差檢查" + msg)
    except Exception as e: print("population telegram:", e)

def _klines(B, kdir, key, fetch):
    """1m／1h K 線快取（緊湊格式）。抓取拋例外（含限流）什麼都不存。"""
    p = os.path.join(kdir, key + ".json")
    if os.path.exists(p):
        try: return [dict(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in json.load(open(p))]
        except Exception: pass
    k = fetch()
    json.dump([[x["t"], x["o"], x["h"], x["l"], x["c"], x["v"]] for x in k], open(p, "w"))
    return k

def run_live(B, kdir):
    rows = json.load(open(INPUT)) if os.path.exists(INPUT) else []
    out, skipped = [], 0
    for n, t in enumerate(rows):
        if n % 10 == 0: _write(status=f"實盤對照：{n}/{len(rows)} 筆")
        bt, s = t.get("bar_t"), t.get("symbol")
        if not bt or not s: skipped += 1; continue
        last = bt + (240000 if t.get("engine") == "C" else 0)   # C 是 5m 棒：進場在那根收盤，1m 取它的最後一根
        k = _klines(B, kdir, f"pop_{s}_1m_pre_{last}", lambda: B.klines_range(s, "1m", last - DAY, last + 1))
        idx = {x["t"]: j for j, x in enumerate(k)}
        d0 = bt - bt % DAY
        h1 = _klines(B, kdir, f"pop_{s}_1h_{d0}", lambda: B.klines_range(s, "1h", d0 - DAY, d0 + DAY - 1)) \
            if d0 + DAY <= time.time() * 1000 else B.klines_range(s, "1h", d0 - DAY, d0 + DAY - 1)   # 今天還沒過完的不快取
        if last not in idx: skipped += 1; continue
        hi, lo, pc = day_stats(h1, bt)
        out.append(dict(t, **entry_tags(t["engine"], k, idx[last], hi, lo, pc)))
    return out, skipped

def run_trigger(B, kdir, days):
    from . import backtest
    end = int(time.time() * 1000) // HOUR * HOUR; start = end - days * DAY
    syms = B.perp_symbols(); tick = B.ticker_24h()
    syms = [s for s in syms if tick.get(s) and float(tick[s]["quoteVolume"]) >= 1e6]
    out, nseg = [], 0
    cnt = lambda e: sum(1 for x in out if x["engine"] == e)
    for n, s in enumerate(syms):
        _write(status=f"無偏差母體：{n}/{len(syms)} 檔，{nseg} 段，C {cnt('C')}、F {cnt('F')}、G {cnt('G')} 筆")
        h1 = B.klines_range(s, "1h", start - DAY, end - 1)        # 每次的區間都不同，不快取（一檔兩個請求）
        for fam, sp in SPECS.items():
            per_day = DAY // sp["bar"]
            for a, b, keep in trigger_segments(h1, **sp):
                if keep >= end or b <= start: continue
                keep = max(keep, start); nseg += 1
                k = _klines(B, kdir, f"pop_{s}_{sp['iv']}_{a}_{b}", lambda: B.klines_range(s, sp["iv"], a, min(b, end) - 1))
                if len(k) < (sp["pre"] // sp["bar"]): continue
                idx = {x["t"]: j for j, x in enumerate(k)}
                for eid in sp["engines"]:
                    for tr in backtest.simulate(k, (eid,)):
                        if tr["t"] < keep or tr["t"] not in idx: continue
                        hi, lo, pc = day_stats(h1, tr["t"])
                        out.append(dict(symbol=s, engine=eid, r=tr["r"], bar_t=tr["t"], bar=sp["bar"], reason=tr["reason"],
                                        **entry_tags(eid, k, idx[tr["t"]], hi, lo, pc, per_day=per_day)))
    return out, nseg, len(syms), start

def add_oi(trades, kdir, fetch=None):
    """無偏差母體的交易貼 OI 標籤（data.binance.vision 每日 metrics，跟籌碼標籤分析同一個快取與同一個不偷看規則）。
    回傳 (抓取失敗天數, 還沒產生的天數)。"""
    from . import labels
    cache, miss, notyet = {}, 0, 0
    now = int(time.time() * 1000)
    for n, t in enumerate(trades):
        if n % 50 == 0: _write(status=f"無偏差母體：貼 OI 標籤 {n}/{len(trades)} 筆")
        rows = []
        d0 = t["bar_t"] - t["bar_t"] % DAY
        for d in (d0 - DAY, d0):
            key = (t["symbol"], d)
            if key not in cache:
                try: cache[key] = labels.metrics_day(t["symbol"], labels._ymd(d), kdir, fetch=fetch, now_ms=now)
                except labels.NotYet: cache[key] = []; notyet += 1
                except labels.Missing: cache[key] = []; miss += 1
            rows += cache[key]
        t.update(labels.oi_labels(sorted(rows), t["bar_t"] + t.get("bar", 60000)))
    return miss, notyet

def main():
    from . import binance as B, presets, sweep
    job = os.environ.get("POP_JOB", "live"); days = int(os.environ.get("POP_DAYS", "90"))
    try: presets.apply_live()
    except Exception as e: print("population apply_live:", e)
    key = "live" if job == "live" else "trigger"
    _write(status="準備中…", job=job, started=time.strftime("%m-%d %H:%M"))
    try:
        if job == "live":
            tr, skipped = run_live(B, sweep.KDIR)
            res = dict(time=time.strftime("%m-%d %H:%M"), n=len(tr), skipped=skipped, report=report(tr))
            msg = f"完成（實盤對照）：{len(tr)} 筆" + (f"，{skipped} 筆缺進場時間或 K 線，沒算" if skipped else "")
        else:
            tr, nseg, nsym, start = run_trigger(B, sweep.KDIR, days)
            miss, notyet = add_oi(tr, sweep.KDIR)
            split = start + days * DAY // 2
            rep_ = report(tr, split)
            res = dict(time=time.strftime("%m-%d %H:%M"), days=days, n=len(tr), segments=nseg, symbols=nsym, report=rep_,
                       oi=oi_report(tr, split), oi_missing=miss, oi_notyet=notyet,
                       split=time.strftime("%m-%d", time.localtime(split / 1000)))
            msg = f"完成（無偏差母體 {days} 天）：{nsym} 檔 {nseg} 段；" + "；".join(
                f"{e} {rep_[e][0]['n']} 筆 PF {rep_[e][0].get('pf')} 平均 {rep_[e][0].get('avg')}R" for e in ENGINES if e in rep_)
        _write(**{key: res}); _end(msg)
    except B.RateLimited as e:
        _end(f"中止：被幣安限流（{str(e)[:100]}）。已抓的 K 線有快取，過幾分鐘重跑會接著抓")
    except Exception as e:
        _end(f"失敗：{e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
