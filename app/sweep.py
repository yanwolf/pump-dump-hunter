"""歷史事件掃描：找過去 N 天內「短期拉升一倍、隨後崩四成」的幣，全部回測，彙整各引擎統計。
以獨立子程序執行（不搶主程序 CPU/GIL），進度與結果寫在 DATA_DIR/sweep.json；K 線快取落地 DATA_DIR/kcache/。"""
import json, os, subprocess, sys, time, traceback

EVENT = dict(pump_days=3, pump_gain=1.0, dump_drop=0.40, min_quote_vol=1e6,
             before_days=10, after_days=5)     # 拉高崩盤模式：每個事件回測高點前 10 天～後 5 天
CRASH = dict(day_drop=0.30, before_days=2, after_days=2)   # 崩盤日模式：單日從前收跌 >= 30%，不管有沒有拉升（去掉「事後知道會崩」的偏差）
PUMP = dict(day_rise=0.30, before_days=2, after_days=2)    # 暴漲日模式：單日從前收漲 >= 30%，不管後來崩不崩（做多引擎 G/E 的無偏差母體）

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
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 專案根目錄，子程序要以套件方式啟動
    _proc = subprocess.Popen([sys.executable, "-m", "app.sweep"], env=env, cwd=root)
    return True

def clear():
    s = state(); json.dump(dict(runs=s.get("runs", [])), open(STATE, "w"), ensure_ascii=False)

# ---------- 子程序用 ----------
def _write(**kw):
    s = state(); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False)

def _cached(key, fetch):
    """有快取就用；沒有就抓、抓到才存。fetch 拋例外（含 RateLimited）時什麼都不存——限流不能變成「這段沒資料」寫進快取。"""
    p = os.path.join(KDIR, key + ".json")
    if os.path.exists(p):
        try: return json.load(open(p))
        except Exception: pass
    k = fetch(); json.dump(k, open(p, "w")); return k

def _load(key):
    p = os.path.join(KDIR, key + ".json")
    try: return json.load(open(p)) if os.path.exists(p) else None
    except Exception: return None

def _save(key, v):
    json.dump(v, open(os.path.join(KDIR, key + ".json"), "w"))

def _day(B, s, n, failed):
    """掃描日線用：被限流 → 往上拋（整個掃描中止，清單第 6 條 r97）；其他錯誤 → 記進 failed、回 None。
    以前一律 except: pass——被 429 擋掉的幣直接從事件母體消失，那份不完整的母體還被當天快取起來。"""
    try: return B.klines(s, "1d", n, batch=True)
    except B.RateLimited: raise
    except Exception as e:
        failed.append(f"{s}: {str(e)[:80]}"); return None

def find_events(days, B):
    """回傳 (事件, 抓取失敗的幣)。"""
    syms = B.perp_symbols(); tick = B.ticker_24h()
    events, n, failed = [], 0, []
    for s in syms:
        t = tick.get(s)
        if not t or float(t["quoteVolume"]) < EVENT["min_quote_vol"]: continue
        n += 1
        d = _day(B, s, days + 5, failed)
        if d is None: continue
        try:
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
        except Exception as e: failed.append(f"{s}: {str(e)[:80]}")
        if n % 25 == 0: _write(status=f"掃描日線 {n} 檔，找到 {len(events)} 個事件")
        time.sleep(0.08)
    return events, failed

def find_crashes(days, B):
    """崩盤日：任一天最低價相對前一日收盤跌 >= day_drop。每檔可有多個。"""
    syms = B.perp_symbols(); tick = B.ticker_24h()
    events, n, failed = [], 0, []
    for s in syms:
        t = tick.get(s)
        if not t or float(t["quoteVolume"]) < EVENT["min_quote_vol"]: continue
        n += 1
        d = _day(B, s, days + 2, failed)
        if d is None: continue
        try:
            for i in range(1, len(d)):
                prev = d[i - 1]["c"]
                if prev <= 0: continue
                drop = 1 - d[i]["l"] / prev
                if drop >= CRASH["day_drop"]:
                    events.append(dict(symbol=s, peak_day=time.strftime("%m-%d", time.gmtime(d[i]["t"] / 1000)), peak_ts=d[i]["t"],
                                       pump=round((max(x["h"] for x in d[max(0, i - 3):i]) / min(x["l"] for x in d[max(0, i - 3):i]) - 1) * 100),
                                       dump=round(drop * 100)))
        except Exception as e: failed.append(f"{s}: {str(e)[:80]}")
        if n % 25 == 0: _write(status=f"掃描日線 {n} 檔，找到 {len(events)} 個崩盤日")
        time.sleep(0.08)
    return events, failed

def find_pumps(days, B):
    """暴漲日：任一天最高價相對前一日收盤漲 >= day_rise。"""
    syms = B.perp_symbols(); tick = B.ticker_24h()
    events, n, failed = [], 0, []
    for s in syms:
        t = tick.get(s)
        if not t or float(t["quoteVolume"]) < EVENT["min_quote_vol"]: continue
        n += 1
        d = _day(B, s, days + 2, failed)
        if d is None: continue
        try:
            for i in range(1, len(d)):
                prev = d[i - 1]["c"]
                if prev <= 0: continue
                rise = d[i]["h"] / prev - 1
                if rise >= PUMP["day_rise"]:
                    after = d[i + 1:i + 3]
                    dump = round((1 - min(x["l"] for x in after) / d[i]["h"]) * 100) if after else 0
                    events.append(dict(symbol=s, peak_day=time.strftime("%m-%d", time.gmtime(d[i]["t"] / 1000)), peak_ts=d[i]["t"],
                                       pump=round(rise * 100), dump=dump))
        except Exception as e: failed.append(f"{s}: {str(e)[:80]}")
        if n % 25 == 0: _write(status=f"掃描日線 {n} 檔，找到 {len(events)} 個暴漲日")
        time.sleep(0.08)
    return events, failed

def main():
    from . import binance as B, config as C, backtest
    days = int(os.environ.get("SWEEP_DAYS", "30")); label = os.environ.get("SWEEP_LABEL", "")
    mode = os.environ.get("SWEEP_MODE", "pump")
    W = {"crash": CRASH, "pumpday": PUMP}.get(mode, EVENT)
    FIND = {"crash": find_crashes, "pumpday": find_pumps}.get(mode, find_events)
    overrides = json.loads(os.environ.get("SWEEP_OVERRIDES") or "{}")
    try:
        _write(status="掃描中…", events=[], results=[], summary=[], trades=[], started=time.strftime("%m-%d %H:%M"),
               scan_failed=[], limited=None)
        ek = f"events_{mode}_{days}_{time.strftime('%Y%m%d')}"
        events, scan_failed = _load(ek), []
        if events is None:
            try: events, scan_failed = FIND(days, B)
            except B.RateLimited as e:
                # 母體掃到一半被限流：不完整的母體不能拿來算（也不存快取），整個中止
                _write(status=f"中止：掃描日線時被幣安限流（{str(e)[:120]}）。母體不完整，沒有回測、沒有存快取——過幾分鐘再跑",
                       limited=str(e)[:200]); return
            if not scan_failed: _save(ek, events)        # 有幣抓不到的母體不快取：下次重跑會補齊，不會整天沿用缺的那份
        C.apply_overrides(overrides)
        results, allt = [], []
        for i, ev in enumerate(events):
            _write(status=f"回測 {i + 1}/{len(events)} {ev['symbol']}", events=events, results=results, scan_failed=scan_failed[:50])
            try:
                a, b = ev["peak_ts"] - W["before_days"] * 86400000, ev["peak_ts"] + W["after_days"] * 86400000
                k = _cached(f"{ev['symbol']}_5m_{ev['peak_ts']}_{mode}", lambda: B.klines_range(ev["symbol"], "5m", a, b))
                k1m = _cached(f"{ev['symbol']}_1m_{ev['peak_ts']}", lambda: B.klines_range(ev["symbol"], "1m", ev["peak_ts"] - 86400000, ev["peak_ts"] + 2 * 86400000))
                tr = backtest.simulate_each(k, k1m)
                for t in tr: t["symbol"] = ev["symbol"]; t["time"] = time.strftime("%m-%d %H:%M", time.localtime(t["t"] / 1000))
                allt += tr
                by = {}
                for t in tr: by[t["engine"]] = round(by.get(t["engine"], 0) + t["r"], 2)
                results.append(dict(**ev, bars=len(k), trades=len(tr), **{f"R_{e}": by.get(e) for e in "ABCDEFG"}))
            except B.RateLimited as e:
                # 限流（清單第 6 條 r97）：不是「這個事件沒資料」。只彙整一部分的事件會偏（剩下的不見了），整個中止
                _write(status=f"中止：回測到第 {i + 1}/{len(events)} 個事件被幣安限流（{str(e)[:120]}）。沒有彙整、沒有記進比較表；"
                              f"已抓到的 K 線有快取，過幾分鐘重跑會接著抓", events=events, results=results, limited=str(e)[:200])
                return
            except Exception as e:
                results.append(dict(**ev, error=str(e)))
        summ = backtest.summary(allt)
        n_err = sum(1 for r in results if r.get("error"))
        miss = (f"；{n_err} 個事件抓取／回測失敗，沒算進統計" if n_err else "") + (f"；掃描時 {len(scan_failed)} 檔抓不到日線，母體可能不完整" if scan_failed else "")
        runs = (state().get("runs", []) + [dict(label=(label or "預設") + ({"crash": "｜崩盤日", "pumpday": "｜暴漲日"}.get(mode, "")), days=days, time=time.strftime("%m-%d %H:%M"),
                 n=len(allt), total=round(sum(t["r"] for t in allt), 1), err=n_err, scan_failed=len(scan_failed),
                 **{x["engine"]: f"{x['n']}筆 PF{x['pf']} {x['exp']:+}" for x in summ})])[-12:]
        _write(status=f"完成：{len(events)} 個事件，{len(allt)} 筆交易，{time.strftime('%m-%d %H:%M')}" + (f"（{label}）" if label else "") + miss,
               events=events, results=results, summary=summ, runs=runs,
               trades=sorted(allt, key=lambda t: -abs(t["r"]))[:60])
    except Exception as e:
        _write(status=f"失敗: {e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
