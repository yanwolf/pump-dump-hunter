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
POP = dict(G=("rise", 0.30), F=("drop", 0.30))          # 回測母體：暴漲日（G）、崩盤日（F）
WATCH = (40.0, -30.0)                                   # 實盤名單門檻：24h 漲 >= 40% 或跌 <= -30%（config.SCAN 預設）
BANDS = dict(G=(0.25, 0.40), F=(0.80, 1.50))            # 進場時 24h 區間分段的界線
TRIG = dict(r2h=0.10, r25h=0.15, pre=DAY + HOUR, post=3 * HOUR)
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
                if t.get("engine") in ("F", "G") and not t.get("excluded")]
        json.dump(rows, open(INPUT, "w"), ensure_ascii=False)
    env = dict(os.environ, POP_JOB=job, POP_DAYS=str(int(days)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _proc = subprocess.Popen([sys.executable, "-m", "app.population"], env=env, cwd=root)
    return True

# ---------- 純函式（測試直接呼叫）----------
def entry_tags(eid, k, i, day_hi, day_lo, prev_close):
    """k[:i+1] 是到進場那根為止的 1m K（至少 1441 根才算 24h 漲跌）。日 K 數字是進場那天（UTC）的。
    回傳 dict(pop, watch, band, gain24, chg24)；算不出來的是 None。"""
    out = dict(pop=None, watch=None, band=None, gain24=None, chg24=None)
    if prev_close and prev_close > 0 and day_hi and day_lo:
        kind, th = POP[eid]
        hit = day_hi / prev_close - 1 >= th if kind == "rise" else 1 - day_lo / prev_close >= th
        out["pop"] = "在" if hit else "不在"                  # 手機表格要窄：組名只寫「在／不在」，說明在網頁圖例
    w = k[max(0, i - 1439):i + 1]
    lo, hi = min(x["l"] for x in w), max(x["h"] for x in w)
    if lo > 0 and len(w) >= 1440:
        g = hi / lo - 1; out["gain24"] = round(g * 100, 1)
        a, b = BANDS[eid]
        out["band"] = f"<{a:.0%}" if g < a else f"≥{b:.0%}" if g >= b else f"{a:.0%}～{b:.0%}"
    if i >= 1440 and k[i - 1440]["c"] > 0:
        c = (k[i]["c"] / k[i - 1440]["c"] - 1) * 100; out["chg24"] = round(c, 1)
        out["watch"] = "會盯" if (c >= WATCH[0] or c <= WATCH[1]) else "不會盯"
    return out

def trigger_segments(h1, r2h=TRIG["r2h"], r25h=TRIG["r25h"], pre=TRIG["pre"], post=TRIG["post"]):
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

def report(trades):
    """trades: [{engine, r, pop, watch, band}] → {engine: [{tag, group, n, win, pf, avg, tot}]}，第一列是全部。"""
    out = {}
    for eid in ("F", "G"):
        ts = [t for t in trades if t.get("engine") == eid and isinstance(t.get("r"), (int, float))]
        rows = [dict(tag="全部", group="", **_stats([t["r"] for t in ts]))]
        for tag, name in (("pop", "母體"), ("watch", "名單"), ("band", "24h區間")):
            groups = {}
            for t in ts: groups.setdefault(t.get(tag) or "缺", []).append(t["r"])
            for g in sorted(groups): rows.append(dict(tag=name, group=g, **_stats(groups[g])))
        out[eid] = rows
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
        k = _klines(B, kdir, f"pop_{s}_1m_pre_{bt}", lambda: B.klines_range(s, "1m", bt - DAY, bt + 1))
        idx = {x["t"]: j for j, x in enumerate(k)}
        d0 = bt - bt % DAY
        h1 = _klines(B, kdir, f"pop_{s}_1h_{d0}", lambda: B.klines_range(s, "1h", d0 - DAY, d0 + DAY - 1)) \
            if d0 + DAY <= time.time() * 1000 else B.klines_range(s, "1h", d0 - DAY, d0 + DAY - 1)   # 今天還沒過完的不快取
        if bt not in idx: skipped += 1; continue
        hi, lo, pc = day_stats(h1, bt)
        out.append(dict(t, **entry_tags(t["engine"], k, idx[bt], hi, lo, pc)))
    return out, skipped

def run_trigger(B, kdir, days):
    from . import backtest
    end = int(time.time() * 1000) // HOUR * HOUR; start = end - days * DAY
    syms = B.perp_symbols(); tick = B.ticker_24h()
    syms = [s for s in syms if tick.get(s) and float(tick[s]["quoteVolume"]) >= 1e6]
    out, nseg = [], 0
    for n, s in enumerate(syms):
        _write(status=f"無偏差母體：{n}/{len(syms)} 檔，{nseg} 段，F {sum(1 for x in out if x['engine'] == 'F')} 筆、G {sum(1 for x in out if x['engine'] == 'G')} 筆")
        h1 = B.klines_range(s, "1h", start - DAY, end - 1)        # 每次的區間都不同，不快取（一檔兩個請求）
        for a, b, keep in trigger_segments(h1):
            if keep >= end or b <= start: continue
            keep = max(keep, start)
            nseg += 1
            k = _klines(B, kdir, f"pop_{s}_1m_{a}_{b}", lambda: B.klines_range(s, "1m", a, min(b, end) - 1))
            if len(k) < 1500: continue
            idx = {x["t"]: j for j, x in enumerate(k)}
            for eid in ("F", "G"):
                for tr in backtest.simulate(k, (eid,)):
                    if tr["t"] < keep or tr["t"] not in idx: continue
                    hi, lo, pc = day_stats(h1, tr["t"])
                    out.append(dict(symbol=s, engine=eid, r=tr["r"], bar_t=tr["t"], reason=tr["reason"],
                                    **entry_tags(eid, k, idx[tr["t"]], hi, lo, pc)))
    return out, nseg, len(syms)

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
            tr, nseg, nsym = run_trigger(B, sweep.KDIR, days)
            res = dict(time=time.strftime("%m-%d %H:%M"), days=days, n=len(tr), segments=nseg, symbols=nsym, report=report(tr))
            g = res["report"]["G"][0]; f = res["report"]["F"][0]
            msg = (f"完成（無偏差母體 {days} 天）：{nsym} 檔 {nseg} 段；G {g['n']} 筆 PF {g.get('pf')} 平均 {g.get('avg')}R；"
                   f"F {f['n']} 筆 PF {f.get('pf')} 平均 {f.get('avg')}R")
        _write(**{key: res}); _end(msg)
    except B.RateLimited as e:
        _end(f"中止：被幣安限流（{str(e)[:100]}）。已抓的 K 線有快取，過幾分鐘重跑會接著抓")
    except Exception as e:
        _end(f"失敗：{e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
