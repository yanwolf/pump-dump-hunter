"""歷史事件掃描：找過去 N 天內「短期拉升一倍、隨後崩四成」的幣，全部回測，彙整五引擎統計。
背景執行，進度與結果放 store 的 sweep 欄位。"""
import threading, time, traceback
import binance as B, config as C, backtest, store

EVENT = dict(
    pump_days=3,     # 3 天內
    pump_gain=1.0,   # 從低到高漲 >= 100%
    dump_drop=0.40,  # 高點後跌 >= 40%
    min_quote_vol=1e6,
)
_running = False
_kcache = {}      # symbol -> (days, k)  K 線快取，換參數重跑不用再抓
_events = {}      # days -> events

def find_events(days):
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
                lo = min(x["l"] for x in d[max(0, i - EVENT["pump_days"]):i + 1])
                hi = d[i]["h"]
                if lo <= 0 or hi / lo - 1 < EVENT["pump_gain"]: continue
                after = d[i + 1:]
                if not after: continue
                low_after = min(x["l"] for x in after)
                if 1 - low_after / hi >= EVENT["dump_drop"]:
                    ev = dict(symbol=s, peak_day=time.strftime("%m-%d", time.gmtime(d[i]["t"] / 1000)),
                              pump=round((hi / lo - 1) * 100), dump=round((1 - low_after / hi) * 100))
                    if not best or ev["pump"] > best["pump"]: best = ev
            if best: events.append(best)
        except Exception as e:
            pass
        if n % 25 == 0:
            store.update(sweep=dict(status=f"掃描日線 {n} 檔，找到 {len(events)} 個事件", events=events))
        time.sleep(0.08)
    return events

def run(days=30, overrides=None, label=""):
    global _running
    if _running: return
    _running = True
    snap = None
    try:
        prev = store.get().get("sweep", {}) or {}
        runs = prev.get("runs", [])
        store.update(sweep=dict(status="掃描中…", events=[], results=[], summary=[], runs=runs))
        if days in _events: events = _events[days]
        else: events = find_events(days); _events[days] = events
        snap = C.apply_overrides(overrides)
        results, allt = [], []
        end = int(time.time() * 1000)
        for i, ev in enumerate(events):
            store.update(sweep=dict(status=f"回測 {i + 1}/{len(events)} {ev['symbol']}", events=events, results=results, runs=runs))
            try:
                cached = _kcache.get(ev["symbol"])
                if cached and cached[0] == days: k = cached[1]
                else:
                    k = B.klines_range(ev["symbol"], "5m", end - days * 86400000, end); _kcache[ev["symbol"]] = (days, k)
                tr = backtest.simulate_each(k)
                for t in tr: t["symbol"] = ev["symbol"]; t["time"] = time.strftime("%m-%d %H:%M", time.gmtime(t["t"] / 1000))
                allt += tr
                by = {}
                for t in tr: by[t["engine"]] = round(by.get(t["engine"], 0) + t["r"], 2)
                results.append(dict(**ev, bars=len(k), trades=len(tr), **{f"R_{e}": by.get(e) for e in "ABCDE"}))
            except Exception as e:
                results.append(dict(**ev, error=str(e)))
        summ = backtest.summary(allt)
        runs = (runs + [dict(label=label or (json_short(overrides) if overrides else "預設"), time=time.strftime("%m-%d %H:%M"),
                             n=len(allt), total=round(sum(t["r"] for t in allt), 1),
                             **{f"{x['engine']}": f"{x['n']}筆 PF{x['pf']} {x['exp']:+}" for x in summ})])[-12:]
        store.update(sweep=dict(status=f"完成：{len(events)} 個事件，{len(allt)} 筆交易，{time.strftime('%m-%d %H:%M')}" + (f"（{label}）" if label else ""),
                                events=events, results=results, summary=summ, runs=runs,
                                trades=sorted(allt, key=lambda t: -abs(t["r"]))[:60]))
    except Exception as e:
        store.update(sweep=dict(status=f"失敗: {e}")); traceback.print_exc()
    finally:
        if snap: C.restore(snap)
        _running = False

def json_short(o):
    import json
    s = json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) < 60 else s[:57] + "…"

def clear():
    store.update(sweep={})
_kcache = {}      # symbol -> (days, k)  K 線快取，換參數重跑不用再抓
_events = {}      # days -> events

def start(days=30, overrides=None, label=""):
    if _running: return False
    threading.Thread(target=run, args=(days, overrides, label), daemon=True).start()
    return True
