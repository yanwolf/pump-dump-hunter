"""歷史事件掃描：找過去 N 天內「短期拉升一倍、隨後崩四成」的幣，全部回測，彙整各引擎統計。
以獨立子程序執行（不搶主程序 CPU/GIL），進度與結果寫在 DATA_DIR/sweep.json；K 線快取落地 DATA_DIR/kcache/。"""
import json, os, subprocess, sys, time, traceback

EVENT = dict(pump_days=3, pump_gain=1.0, dump_drop=0.40, min_quote_vol=1e6,
             before_days=10, after_days=5)     # 拉高崩盤模式：每個事件回測高點前 10 天～後 5 天
CRASH = dict(day_drop=0.30, before_days=2, after_days=2)   # 崩盤日模式：單日從前收跌 >= 30%，不管有沒有拉升（去掉「事後知道會崩」的偏差）

DATA_DIR = os.environ.get("DATA_DIR", "./data")
STATE = os.path.join(DATA_DIR, "sweep.json")
KDIR = os.path.join(DATA_DIR, "kcache"); os.makedirs(KDIR, exist_ok=True)
_proc = None

# ---------- 主程序用 ----------
def state():
    try: return json.load(open(STATE))
    except Exception: return {}

def running():
    return _proc is not None and _proc.poll() is None

def start(days=30, overrides=None, label="", mode="pump"):
    global _proc
    if running(): return False
    env = dict(os.environ, SWEEP_DAYS=str(days), SWEEP_LABEL=label or "", SWEEP_MODE=mode,
               SWEEP_OVERRIDES=json.dumps(overrides or {}, ensure_ascii=False))
    _proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], env=env, cwd=os.path.dirname(os.path.abspath(__file__)))
    return True

def clear():
    s = state(); json.dump(dict(runs=s.get("runs", [])), open(STATE, "w"), ensure_ascii=False)

# ---------- 子程序用 ----------
def _write(**kw):
    s = state(); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False)

def _cached(key, fetch):
    p = os.path.join(KDIR, key + ".json")
    if os.path.exists(p):
        try: return json.load(open(p))
        except Exception: pass
    k = fetch(); json.dump(k, open(p, "w")); return k

def find_events(days, B):
    syms = B.perp_symbols(); tick = B.ticker_24h()
    events, n = [], 0
    for s in syms:
        t = tick.get(s)
        if not t or float(t["quoteVolume"]) < EVENT["min_quote_vol"]: continue
        n += 1
        try:
            d = B.klines(s, "1d", days + 5)
            best = None
            for i in range(len(d)):
                lo = min(x["l"] for x in d[max(0, i - EVENT["pump_days"]):i + 1]); hi = d[i]["h"]
                if lo <= 0 or hi / lo - 1 < EVENT["pump_gain"]: continue
                after = d[i + 1:]
                if not after: continue
                low_after = min(x["l"] for x in after)
                if 1 - low_after / hi >= EVENT["dump_drop"]:
                    ev = dict(symbol=s, peak_day=time.strftime("%m-%d", time.gmtime(d[i]["t"] / 1000)), peak_ts=d[i]["t"],
                              pump=round((hi / lo - 1) * 100), dump=round((1 - low_after / hi) * 100))
                    if not best or ev["pump"] > best["pump"]: best = ev
            if best: events.append(best)
        except Exception: pass
        if n % 25 == 0: _write(status=f"掃描日線 {n} 檔，找到 {len(events)} 個事件")
        time.sleep(0.08)
    return events

def find_crashes(days, B):
    """崩盤日：任一天最低價相對前一日收盤跌 >= day_drop。每檔可有多個。"""
    syms = B.perp_symbols(); tick = B.ticker_24h()
    events, n = [], 0
    for s in syms:
        t = tick.get(s)
        if not t or float(t["quoteVolume"]) < EVENT["min_quote_vol"]: continue
        n += 1
        try:
            d = B.klines(s, "1d", days + 2)
            for i in range(1, len(d)):
                prev = d[i - 1]["c"]
                if prev <= 0: continue
                drop = 1 - d[i]["l"] / prev
                if drop >= CRASH["day_drop"]:
                    events.append(dict(symbol=s, peak_day=time.strftime("%m-%d", time.gmtime(d[i]["t"] / 1000)), peak_ts=d[i]["t"],
                                       pump=round((max(x["h"] for x in d[max(0, i - 3):i]) / min(x["l"] for x in d[max(0, i - 3):i]) - 1) * 100),
                                       dump=round(drop * 100)))
        except Exception: pass
        if n % 25 == 0: _write(status=f"掃描日線 {n} 檔，找到 {len(events)} 個崩盤日")
        time.sleep(0.08)
    return events

def main():
    import binance as B, config as C, backtest
    days = int(os.environ.get("SWEEP_DAYS", "30")); label = os.environ.get("SWEEP_LABEL", "")
    mode = os.environ.get("SWEEP_MODE", "pump"); W = CRASH if mode == "crash" else EVENT
    overrides = json.loads(os.environ.get("SWEEP_OVERRIDES") or "{}")
    try:
        _write(status="掃描中…", events=[], results=[], summary=[], trades=[], started=time.strftime("%m-%d %H:%M"))
        events = _cached(f"events_{mode}_{days}_{time.strftime('%Y%m%d')}",
                         lambda: (find_crashes if mode == "crash" else find_events)(days, B))
        C.apply_overrides(overrides)
        results, allt = [], []
        for i, ev in enumerate(events):
            _write(status=f"回測 {i + 1}/{len(events)} {ev['symbol']}", events=events, results=results)
            try:
                a, b = ev["peak_ts"] - W["before_days"] * 86400000, ev["peak_ts"] + W["after_days"] * 86400000
                k = _cached(f"{ev['symbol']}_5m_{ev['peak_ts']}_{mode}", lambda: B.klines_range(ev["symbol"], "5m", a, b))
                k1m = _cached(f"{ev['symbol']}_1m_{ev['peak_ts']}", lambda: B.klines_range(ev["symbol"], "1m", ev["peak_ts"] - 86400000, ev["peak_ts"] + 2 * 86400000))
                tr = backtest.simulate_each(k, k1m)
                for t in tr: t["symbol"] = ev["symbol"]; t["time"] = time.strftime("%m-%d %H:%M", time.gmtime(t["t"] / 1000))
                allt += tr
                by = {}
                for t in tr: by[t["engine"]] = round(by.get(t["engine"], 0) + t["r"], 2)
                results.append(dict(**ev, bars=len(k), trades=len(tr), **{f"R_{e}": by.get(e) for e in "ABCDEFG"}))
            except Exception as e:
                results.append(dict(**ev, error=str(e)))
        summ = backtest.summary(allt)
        runs = (state().get("runs", []) + [dict(label=(label or "預設") + ("｜崩盤日" if mode == "crash" else ""), days=days, time=time.strftime("%m-%d %H:%M"),
                 n=len(allt), total=round(sum(t["r"] for t in allt), 1),
                 **{x["engine"]: f"{x['n']}筆 PF{x['pf']} {x['exp']:+}" for x in summ})])[-12:]
        _write(status=f"完成：{len(events)} 個事件，{len(allt)} 筆交易，{time.strftime('%m-%d %H:%M')}" + (f"（{label}）" if label else ""),
               events=events, results=results, summary=summ, runs=runs,
               trades=sorted(allt, key=lambda t: -abs(t["r"]))[:60])
    except Exception as e:
        _write(status=f"失敗: {e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
