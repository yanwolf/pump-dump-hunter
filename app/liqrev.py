"""清算反轉＋熬鷹（研究用，不動實盤）。複製一個外部訊號頁（「加密貨幣掛單訊號」，Binance 永續）公布的兩套規則來驗證。
背景子程序執行，結果寫 DATA_DIR/liqrev.json。跟歷史事件掃描、籌碼標籤分析、母體偏差檢查互斥（都在抓 K 線）。

LQ 清算反轉（5m）——它的說明：「以 5 分鐘未平倉量下降推估清算；4 小時內單邊清算爆量（多頭被清就接多、空頭被軋就接空）、
掃單後價格已離開極值，才給進場價、止損（插針極值外）與目標（1.5 倍／3 倍風險）」。這裡的寫法：
- 清算推估：每 5 分鐘 OI（合約張數，data.binance.vision 每日 metrics）比前 5 分鐘少的部分，占 OI 的 %。
  同一段價格下跌 → 算多頭被清；價格上漲 → 算空頭被軋。OI 增加的段不算。
- 不偷看：快照時間 s 那筆，要到 s + 5 分鐘（labels.OI_LAG）才算已知；第 j 根 5m（開盤 t）收盤時，
  已知的最後一段是「t−5m → t」，價格變化用那兩個時間點的收盤價。
- 單邊爆量：最近 4 小時（48 段）多頭被清合計 ≥ liq_pct% 的 OI，而且 ≥ dom 倍空頭那邊（反之亦然）；這 48 段要有 80% 以上有資料。
- 離開極值：多頭被清 → 4 小時內最低點是極值，最低點至少是 settle_bars 根以前（不是還在破底），收盤已離開最低點 leave_pct% 以上。
- 掛單：限價（預設收盤價，entry_pull 可往極值方向退）；止損＝極值外 stop_buf%；目標一 1.5R 減碼一半、止損移到入場價，目標二 3R。
  4 小時沒成交就撤單；最多持有 72 小時（它沒寫，跟熬鷹一樣）。

AY 熬鷹（1h，只放空）——它的說明：「ETH 1 小時 RSI≥75、24 小時漲 4% 以上、站上 240 小時均線且貼近 24 小時高點時放空；
止損 +1.5%、目標 −2.5%／−4%，4 小時未成交過期、最多持有 72 小時」。貼近高點＝收盤在 24h 最高的 near_hi% 以內。

兩邊共用的撮合（保守）：限價單要價格「穿過」掛單價才算成交（做多要最低 < 掛單價）；成交那根只看止損、不看目標；
同一根同時碰到止損和目標，算止損；碰到目標一但沒到目標二、那根又回到入場價，算剩下一半保本出場。
成本：掛單進場、目標出場＝maker；止損、時間出場＝taker＋滑價。毛（不含成本，跟它的戰績同一個口徑）和淨兩種都算。

預先登記（2026-11-19，看結果之前定死）：參數照它公布的說明，沒調過。判讀只看淨值：筆數 ≥ 30、淨平均 R > 0、
而且前後兩段淨平均 R 都 > 0，才算「值得前瞻記錄」。看完結果再調參數，就要換一段新資料重驗。
限制：OI 下降不全是清算（也有主動平倉）；宇宙是現存的幣；K 線內的先後順序不知道，所以用上面那套保守規則。"""
import json, os, subprocess, sys, time, traceback

DATA_DIR = os.environ.get("DATA_DIR", "./data")
STATE = os.path.join(DATA_DIR, "liqrev.json")
DAY, HOUR, M5 = 86400000, 3600000, 300000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "PAXGUSDT", "SOXLUSDT", "TQQQUSDT")
LQ = dict(window_bars=48, liq_pct=2.0, dom=3.0, cover=0.8, leave_pct=0.5, settle_bars=2, stop_buf=0.3, entry_pull=0.0,
          tp1_r=1.5, tp2_r=3.0, expire_bars=48, max_hold_bars=864, cooldown_bars=48, min_stop_pct=0.3, max_stop_pct=6.0)
AY = dict(rsi_n=14, rsi_min=75.0, gain24=4.0, ma_n=240, near_hi=1.0, stop_pct=1.5, tp1_pct=2.5, tp2_pct=4.0,
          expire_bars=4, max_hold_bars=72, cooldown_bars=4)
COST = dict(maker=0.0002, taker=0.0005, slip=0.0005)
MIN_N = 30
CHUNK = dict(m5=10 * DAY, h1=100 * DAY)
_proc = None

# ---------- 主程序用 ----------
def state():
    try: return json.load(open(STATE))
    except Exception: return {}

def running():
    return _proc is not None and _proc.poll() is None

def busy():
    """其他會大量抓 K 線的研究工作在跑嗎（互斥）。"""
    from . import labels, population, sweep
    return running() or sweep.running() or labels.running() or population.running()

def parse_symbols(text):
    """「btc, ETH/USDT  paxg」→ ("BTCUSDT", "ETHUSDT", "PAXGUSDT")；空字串用預設宇宙。去重、保留順序。"""
    out = []
    for s in (text or "").replace("，", ",").replace(" ", ",").split(","):
        s = s.strip().upper().replace("/", "").replace("-", "")
        if not s: continue
        s = s if s.endswith("USDT") else s + "USDT"
        if s not in out: out.append(s)
    return tuple(out) or SYMBOLS

def start(symbols=SYMBOLS, days=180, ay_days=1095):
    """回 (有沒有啟動, 原因)。"""
    global _proc
    if busy(): return False, "已有母體檢查、籌碼分析、歷史事件掃描或清算反轉在跑，等它跑完"
    env = dict(os.environ, LQ_SYMBOLS=",".join(symbols), LQ_DAYS=str(int(days)), AY_DAYS=str(int(ay_days)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _proc = subprocess.Popen([sys.executable, "-m", "app.liqrev"], env=env, cwd=root)
    return True, None

# ---------- 指標（純函式）----------
def rsi_series(closes, n=14):
    """Wilder RSI；前 n 根是 None。"""
    out = [None] * len(closes)
    if len(closes) <= n: return out
    g = l = 0.0
    for j in range(1, n + 1):
        d = closes[j] - closes[j - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for j in range(n + 1, len(closes)):
        d = closes[j] - closes[j - 1]
        g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
        out[j] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out

def sma_series(xs, n):
    out, s = [None] * len(xs), 0.0
    for j, x in enumerate(xs):
        s += x
        if j >= n: s -= xs[j - n]
        if j >= n - 1: out[j] = s / n
    return out

def liq_bars(k, oi_rows):
    """5m K（依時間）＋ metrics 列 [(ts, oi, oi_val, ls)] → 每根收盤時「剛知道」的那一段：
    [(多頭被清 %, 空頭被軋 %, 有沒有資料)]。第 j 根（開盤 t）對應 t−5m → t 這段，價格用第 j−2、j−1 根的收盤。"""
    oi = {(r[0] + M5 // 2) // M5 * M5: r[1] for r in oi_rows if r[1]}   # 快照時間對齊到 5 分鐘
    out = []
    for j, x in enumerate(k):
        s = x["t"]; a, b = oi.get(s - M5), oi.get(s)
        if j < 2 or not a or not b or a <= 0 or k[j - 1]["t"] != s - M5 or k[j - 2]["t"] != s - 2 * M5:
            out.append((0.0, 0.0, False)); continue
        drop = (a - b) / a * 100
        dp = k[j - 1]["c"] - k[j - 2]["c"]
        if drop <= 0 or dp == 0: out.append((0.0, 0.0, True)); continue
        out.append((drop, 0.0, True) if dp < 0 else (0.0, drop, True))
    return out

# ---------- 訊號（純函式；只看 k[:i+1]）----------
def lq_signal(k, i, lb, P=None):
    """回 dict(side, limit, stop, tp1, tp2, why) 或 None。lb＝liq_bars(k, …)。"""
    P = P or LQ
    w = P["window_bars"]
    if i < w + 2: return None
    seg = lb[i - w + 1:i + 1]
    if sum(1 for x in seg if x[2]) < P["cover"] * w: return None
    L, S = sum(x[0] for x in seg), sum(x[1] for x in seg)
    if L >= P["liq_pct"] and L >= P["dom"] * S: side = "LONG"
    elif S >= P["liq_pct"] and S >= P["dom"] * L: side = "SHORT"
    else: return None
    bars = k[i - w + 1:i + 1]; c = k[i]["c"]
    if side == "LONG":
        j = min(range(len(bars)), key=lambda m: bars[m]["l"]); ext = bars[j]["l"]
        if len(bars) - 1 - j < P["settle_bars"] or ext <= 0 or c < ext * (1 + P["leave_pct"] / 100): return None
        limit = c - P["entry_pull"] * (c - ext); stop = ext * (1 - P["stop_buf"] / 100); d = 1
    else:
        j = max(range(len(bars)), key=lambda m: bars[m]["h"]); ext = bars[j]["h"]
        if len(bars) - 1 - j < P["settle_bars"] or c > ext * (1 - P["leave_pct"] / 100): return None
        limit = c + P["entry_pull"] * (ext - c); stop = ext * (1 + P["stop_buf"] / 100); d = -1
    R = abs(limit - stop)
    if not (P["min_stop_pct"] <= R / limit * 100 <= P["max_stop_pct"]): return None
    return dict(side=side, limit=limit, stop=stop, tp1=limit + d * P["tp1_r"] * R, tp2=limit + d * P["tp2_r"] * R,
                why=f"4h {'多頭被清' if d > 0 else '空頭被軋'} {max(L, S):.1f}% OI（反向 {min(L, S):.1f}%）")

def ay_signal(k, i, ind, P=None):
    """熬鷹（1h）。ind＝dict(rsi=…, ma=…) 整段序列。只放空。"""
    P = P or AY
    if i < max(P["ma_n"], 24): return None
    r, ma, c = ind["rsi"][i], ind["ma"][i], k[i]["c"]
    if r is None or ma is None or r < P["rsi_min"] or c <= ma: return None
    if k[i - 24]["c"] <= 0 or (c / k[i - 24]["c"] - 1) * 100 < P["gain24"]: return None
    hi = max(x["h"] for x in k[i - 23:i + 1])
    if c < hi * (1 - P["near_hi"] / 100): return None
    return dict(side="SHORT", limit=c, stop=c * (1 + P["stop_pct"] / 100), tp1=c * (1 - P["tp1_pct"] / 100),
                tp2=c * (1 - P["tp2_pct"] / 100), why=f"RSI {r:.0f}、24h +{(c / k[i - 24]['c'] - 1) * 100:.1f}%")

def ay_ind(k, P=None):
    P = P or AY
    cl = [x["c"] for x in k]
    return dict(rsi=rsi_series(cl, P["rsi_n"]), ma=sma_series(cl, P["ma_n"]))

# ---------- 撮合（純函式）----------
def simulate(k, sig_at, P, cost=None, keep_from=None):
    """k：K 線；sig_at(i) → 訊號或 None（只看到 i 為止）；P 要有 expire_bars、max_hold_bars、cooldown_bars。
    回 (trades, counts)。trades 每筆：side, t_sig, t_fill, entry, stop, reason, bars, r_gross, r_net, pct_gross, pct_net。
    keep_from：訊號時間早於這個的不算（暖機區）。"""
    cost = cost or COST
    trades, cnt = [], dict(issued=0, filled=0, expired=0)
    order = pos = None; free_at = 0
    for i in range(len(k)):
        bar = k[i]
        if pos:
            d = pos["d"]
            adv = bar["l"] if d > 0 else bar["h"]; fav = bar["h"] if d > 0 else bar["l"]
            hit = lambda px, x: (x <= px) if d > 0 else (x >= px)      # 不利方向碰到
            reach = lambda px, x: (x >= px) if d > 0 else (x <= px)    # 有利方向碰到
            done = None
            if hit(pos["stop"], adv):
                gap = bar["o"] if hit(pos["stop"], bar["o"]) else pos["stop"]    # 開盤就跳過止損：用開盤價
                kind = "be" if pos["half"] else "stop"
                done = (kind, gap, True)
            elif pos["fill_i"] < i and not pos["half"] and reach(pos["tp1"], fav):
                pos["half"] = True; pos["legs"].append((0.5, pos["tp1"], False))
                if reach(pos["tp2"], fav): done = ("tp2", pos["tp2"], False)
                elif hit(pos["entry"], adv): done = ("be", pos["entry"], True)   # 同一根回到入場價：保守算保本（止損單＝taker）
                else: pos["stop"] = pos["entry"]
            elif pos["fill_i"] < i and pos["half"] and reach(pos["tp2"], fav):
                done = ("tp2", pos["tp2"], False)
            elif i - pos["fill_i"] >= P["max_hold_bars"]:
                done = ("time", bar["c"], True)
            if done:
                kind, px, taker = done
                pos["legs"].append((0.5 if pos["half"] and kind != "stop" else 1.0, px, taker))
                trades.append(_close(pos, kind, i, cost)); pos = None; free_at = i + P["cooldown_bars"]
            continue
        if order:
            if i - order["i"] > P["expire_bars"]:                      # 過期撤單：同一波爆量不再重複掛（冷卻）
                cnt["expired"] += 1; order = None; free_at = i + P["cooldown_bars"]
                continue
            d = order["d"]
            if (bar["l"] < order["limit"]) if d > 0 else (bar["h"] > order["limit"]):
                cnt["filled"] += 1
                pos = dict(order, entry=order["limit"], fill_i=i, t_fill=bar["t"], half=False, legs=[])
                order = None
                adv = bar["l"] if d > 0 else bar["h"]
                if (adv <= pos["stop"]) if d > 0 else (adv >= pos["stop"]):   # 成交那根就打到止損
                    pos["legs"].append((1.0, pos["stop"], True))
                    trades.append(_close(pos, "stop", i, cost)); pos = None; free_at = i + P["cooldown_bars"]
            continue                                                     # 掛單中（不論成交與否）這根都不產生新訊號
        if i < free_at: continue
        s = sig_at(i)
        if not s or (keep_from is not None and bar["t"] < keep_from): continue
        cnt["issued"] += 1
        order = dict(s, d=1 if s["side"] == "LONG" else -1, i=i, t_sig=bar["t"])
    return trades, cnt

def _close(pos, kind, i, cost):
    d, e, R = pos["d"], pos["entry"], abs(pos["entry"] - pos["stop0"])
    gross = net = 0.0
    for frac, px, taker in pos["legs"]:
        fill = px * (1 - d * cost["slip"]) if taker else px                  # 市價出場滑價對我們不利
        gross += frac * d * (px - e)
        net += frac * (d * (fill - e) - fill * (cost["taker"] if taker else cost["maker"]))
    net -= e * cost["maker"]
    return dict(side=pos["side"], t_sig=pos["t_sig"], t_fill=pos["t_fill"], entry=round(e, 8), stop=round(pos["stop0"], 8),
                reason=kind, bars=i - pos["fill_i"], why=pos.get("why", ""),
                r_gross=round(gross / R, 3), r_net=round(net / R, 3),
                pct_gross=round(gross / e * 100, 3), pct_net=round(net / e * 100, 3))

def run_lq(k, oi_rows, P=None, cost=None, keep_from=None):
    P = P or LQ
    lb = liq_bars(k, oi_rows)
    def sig(i):
        s = lq_signal(k, i, lb, P)
        if s: s["stop0"] = s["stop"]
        return s
    return simulate(k, sig, P, cost, keep_from)

def run_ay(k, P=None, cost=None, keep_from=None):
    P = P or AY
    ind = ay_ind(k, P)
    def sig(i):
        s = ay_signal(k, i, ind, P)
        if s: s["stop0"] = s["stop"]
        return s
    return simulate(k, sig, P, cost, keep_from)

# ---------- 報表（純函式）----------
def stats(ts):
    if not ts: return dict(n=0)
    rn = [t["r_net"] for t in ts]
    w = [r for r in rn if r > 0]; l = [r for r in rn if r <= 0]
    avg = lambda key: round(sum(t[key] for t in ts) / len(ts), 3)
    return dict(n=len(ts), win=round(len(w) / len(ts) * 100, 1), pf=round(sum(w) / abs(sum(l)), 2) if l and sum(l) else None,
                r_gross=avg("r_gross"), r_net=avg("r_net"), pct_gross=avg("pct_gross"), pct_net=avg("pct_net"),
                tot=round(sum(rn), 1))

def verdict(ts, split):
    a = [t for t in ts if t["t_sig"] < split]; b = [t for t in ts if t["t_sig"] >= split]
    s, sa, sb = stats(ts), stats(a), stats(b)
    if s["n"] < MIN_N: return f"樣本不足（{s['n']} 筆，要 {MIN_N} 筆以上）"
    ok = s["r_net"] > 0 and sa.get("r_net", -1) > 0 and sb.get("r_net", -1) > 0
    return "成立：值得前瞻記錄" if ok else "不成立"

def report(ts, start, end, now_ms=None, cnt=None):
    """一個策略的報表：全部（含判讀）、前後兩段、最近 30 天（對照它公布的戰績）、多空、出場原因、各幣。"""
    split = start + (end - start) // 2
    now_ms = now_ms or end
    rows = [dict(group="全部", **stats(ts))]
    rows += [dict(group="前段", **stats([t for t in ts if t["t_sig"] < split])),
             dict(group="後段", **stats([t for t in ts if t["t_sig"] >= split])),
             dict(group="最近 30 天", **stats([t for t in ts if t["t_sig"] >= now_ms - 30 * DAY]))]
    for side, nm in (("LONG", "做多"), ("SHORT", "做空")):
        g = [t for t in ts if t["side"] == side]
        if g: rows.append(dict(group=nm, **stats(g)))
    reasons = {}
    for t in ts: reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    syms = {}
    for t in ts: syms.setdefault(t.get("symbol", "?"), []).append(t)
    return dict(rows=rows, verdict=verdict(ts, split), reasons=reasons, counts=cnt or {},
                symbols=[dict(symbol=s, **stats(g)) for s, g in sorted(syms.items())],
                split=time.strftime("%Y-%m-%d", time.localtime(split / 1000)))

# ---------- 子程序 ----------
def _write(**kw):
    s = state(); s.update(kw); json.dump(s, open(STATE, "w"), ensure_ascii=False)

def _end(msg):
    from . import telegram
    _write(status=msg)
    try: telegram.send("🔬 清算反轉／熬鷹" + msg)
    except Exception as e: print("liqrev telegram:", e)

def klines_chunked(B, kdir, s, iv, start, end, now_ms=None):
    """依固定區塊抓 K 線：整塊都在一小時前以前的才快取（重跑不用再抓）。限流照樣往上拋。"""
    size = CHUNK["m5" if iv == "5m" else "h1"]; now_ms = now_ms or int(time.time() * 1000)
    out, c = [], start - start % size
    while c < end:
        a, b = max(c, start), min(c + size, end)
        p = os.path.join(kdir, f"lq_{s}_{iv}_{c}.json")
        rows = None
        if c + size <= now_ms - HOUR and os.path.exists(p):
            try: rows = [dict(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in json.load(open(p))]
            except Exception: rows = None
        if rows is None:
            rows = B.klines_range(s, iv, c, c + size - 1)
            if c + size <= now_ms - HOUR:
                json.dump([[x["t"], x["o"], x["h"], x["l"], x["c"], x["v"]] for x in rows], open(p, "w"))
        out += [x for x in rows if a <= x["t"] < b]
        c += size
    seen, uniq = set(), []
    for x in out:
        if x["t"] not in seen: seen.add(x["t"]); uniq.append(x)
    return uniq

def main():
    from . import binance as B, labels, sweep
    syms = parse_symbols(os.environ.get("LQ_SYMBOLS", ""))
    days = int(os.environ.get("LQ_DAYS", "180")); ay_days = int(os.environ.get("AY_DAYS", "1095"))
    now = int(time.time() * 1000); end = now - now % HOUR
    _write(status="準備中…", started=time.strftime("%m-%d %H:%M"), lq=None, ay=None)
    try:
        listed = set(B.perp_symbols())
        use = [s for s in syms if s in listed]; missing = [s for s in syms if s not in listed]
        if not use: _end(f"中止：{'、'.join(syms)} 都不是現在交易中的 U 本位永續"); return
        lq_all, ay_all, oi_miss, lq_cnt, ay_cnt = [], [], 0, {}, {}
        # 熬鷹：1h，多年
        a0 = end - ay_days * DAY
        for n, s in enumerate(use):
            _write(status=f"熬鷹 {ay_days} 天：{n}/{len(use)} 檔（{s}），{len(ay_all)} 筆")
            k = klines_chunked(B, sweep.KDIR, s, "1h", a0 - 11 * DAY, end, now)
            tr, cnt = run_ay(k, keep_from=a0)
            for t in tr: t["symbol"] = s
            ay_all += tr
            for kk, v in cnt.items(): ay_cnt[kk] = ay_cnt.get(kk, 0) + v
        _write(ay=dict(time=time.strftime("%m-%d %H:%M"), days=ay_days, params=AY, symbols=use, missing=missing,
                       report=report(ay_all, a0, end, now, ay_cnt), trades=ay_all[-60:]))
        # 清算反轉：5m＋每日 OI
        l0 = end - days * DAY
        for n, s in enumerate(use):
            _write(status=f"清算反轉 {days} 天：{n}/{len(use)} 檔（{s}）K 線，{len(lq_all)} 筆")
            k = klines_chunked(B, sweep.KDIR, s, "5m", l0 - DAY, end, now)
            rows = []
            d = l0 - DAY - (l0 - DAY) % DAY
            while d < end:
                if d % (30 * DAY) == 0: _write(status=f"清算反轉 {days} 天：{n}/{len(use)} 檔（{s}）OI {labels._ymd(d)}，{len(lq_all)} 筆")
                try: rows += labels.metrics_day(s, labels._ymd(d), sweep.KDIR, now_ms=now)
                except labels.Missing: oi_miss += 1
                d += DAY
            tr, cnt = run_lq(k, sorted(rows), keep_from=l0)
            for t in tr: t["symbol"] = s
            lq_all += tr
            for kk, v in cnt.items(): lq_cnt[kk] = lq_cnt.get(kk, 0) + v
        _write(lq=dict(time=time.strftime("%m-%d %H:%M"), days=days, params=LQ, symbols=use, missing=missing, oi_missing=oi_miss,
                       report=report(lq_all, l0, end, now, lq_cnt), trades=lq_all[-60:]))
        st = lambda x: x["report"]["rows"][0]
        S = state(); a, b = st(S["ay"]), st(S["lq"])
        avg = lambda x: f"淨 {x['r_net']:+.3f}R／筆、勝率 {x['win']}%" if x["n"] else "沒有交易"
        _end(f"完成：熬鷹 {ay_days} 天 {a['n']} 筆 {avg(a)} → {S['ay']['report']['verdict']}；"
             f"清算反轉 {days} 天 {b['n']} 筆 {avg(b)} → {S['lq']['report']['verdict']}"
             + (f"；不在幣安永續：{'、'.join(missing)}" if missing else ""))
    except B.RateLimited as e:
        _end(f"中止：被幣安限流（{str(e)[:100]}）。已抓的 K 線有快取，過幾分鐘重跑會接著抓")
    except Exception as e:
        _end(f"失敗：{e}"); traceback.print_exc()

if __name__ == "__main__":
    main()
