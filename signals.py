"""三個進場引擎。輸入 5m K 線 list（dict t,o,h,l,c,v），輸出 Signal 或 None。
全部只做空；每個引擎在同一根 K 線上判斷，方便回測與實盤共用。"""
from dataclasses import dataclass
import config as C

@dataclass
class Signal:
    engine: str
    side: str        # 'SHORT'
    entry: float
    stop: float
    reason: str
    @property
    def risk(self): return abs(self.stop - self.entry) / self.entry

def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None

def ema(xs, n):
    if len(xs) < n: return None
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    for x in xs[n:]: e = x * k + e * (1 - k)
    return e

def ema_series(xs, n):
    out = [None] * len(xs)
    if len(xs) < n: return out
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    out[n - 1] = e
    for i in range(n, len(xs)):
        e = xs[i] * k + e * (1 - k); out[i] = e
    return out

def macd_hist_series(closes):
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    line = [None if (a is None or b is None) else a - b for a, b in zip(e12, e26)]
    valid = [x for x in line if x is not None]
    sig = ema_series(valid, 9)
    out = [None] * len(closes); off = len(closes) - len(valid)
    for i, sv in enumerate(sig):
        if sv is not None: out[off + i] = valid[i] - sv
    return out

def rolling_max(xs, win, gap):
    """out[j] = max(xs[max(0, j-win) : j-gap])，單調佇列 O(n)；視窗為空時 None。"""
    from collections import deque
    out, dq = [None] * len(xs), deque()
    for j in range(len(xs)):
        i = j - gap - 1                      # 本輪新進視窗的索引
        if i >= 0:
            while dq and xs[dq[-1]] <= xs[i]: dq.pop()
            dq.append(i)
        while dq and dq[0] < j - win: dq.popleft()
        if dq: out[j] = xs[dq[0]]
    return out

_IND = {}
def ind(k):
    """整段 K 線的指標快取：同一份資料只算一次。"""
    key = (id(k), len(k), k[0]["t"], k[-1]["t"])
    if key not in _IND:
        if len(_IND) > 8: _IND.clear()
        P = C.ENGINE_E
        _IND[key] = dict(hist=macd_hist_series([x["c"] for x in k]),
                         hi_lvl=rolling_max([x["h"] for x in k], P["range_bars"], 12))
    return _IND[key]

def macd_hist(closes):
    if len(closes) < 35: return None
    return macd_hist_series(closes)[-1]

# ---------- A：崩前，頂背馳 + 中樞跌破 ----------
def engine_a_flags(k, i):
    """回傳各條件旗標，診斷用；engine_a 只在全部成立時給 Signal。"""
    P = C.ENGINE_A
    f = dict(hot=False, div=False, pivot=False, top=False, vol=False, first=False, brk=False, zd=None, zg=None, width=None)
    if i < 60: return f
    win = k[:i + 1]
    closes = [x["c"] for x in win]
    # hot：過去 hot_bars 內從最低點到最高點漲了 hot_gain 以上（資料不足就用現有的）
    hb = win[-min(P["hot_bars"], i):]
    lo = min(x["l"] for x in hb); hi = max(x["h"] for x in hb)
    f["hot"] = lo > 0 and hi / lo - 1 >= P["hot_gain"]
    # div：最近 div_bars 的高點 >= 前一個 div_bars 的高點，但 MACD 柱較弱
    n = P["div_bars"]
    if i >= 2 * n + 35:
        hi_idx = max(range(i - n, i + 1), key=lambda j: win[j]["h"])
        prev_hi_idx = max(range(i - 2 * n, i - n), key=lambda j: win[j]["h"])
        hist = ind(k)["hist"]
        h_now, h_prev = hist[hi_idx], hist[prev_hi_idx]
        if h_now is not None and h_prev is not None:
            f["div"] = win[hi_idx]["h"] >= win[prev_hi_idx]["h"] * 0.98 and h_now < h_prev
    # pivot：最近 pivot_bars 根（不含本根）的高低區間
    seg = win[-P["pivot_bars"] - 1:-1]
    zg, zd = max(x["h"] for x in seg), min(x["l"] for x in seg)
    w = (zg - zd) / zd
    f["zd"], f["zg"], f["width"] = zd, zg, round(w * 100, 2)
    f["pivot"] = P["min_pivot_width"] <= w <= P["max_pivot_width"]
    f["top"] = zg >= hi * P["near_top"]
    if f["top"] and P.get("top_age_bars", 0):
        hi_pos = max(range(len(seg)), key=lambda j: seg[j]["h"])       # 區間高點在 seg 內的位置
        f["top"] = (len(seg) - 1 - hi_pos) >= P["top_age_bars"]
    mavol = sum(x["v"] for x in win[-21:-1]) / 20
    f["vol"] = mavol > 0 and win[-1]["v"] >= P["brk_vol_mult"] * mavol
    f["brk"] = win[-1]["c"] < zd and win[-2]["c"] >= zd
    f["first"] = win[-1]["c"] >= hi * P.get("entry_min_of_high", 0)   # 還在頂部附近，不是第 N 段
    return f

def engine_a(k, i):
    f = engine_a_flags(k, i)
    if f["hot"] and f["pivot"] and f["top"] and f["vol"] and f["first"] and f["brk"]:
        return Signal("A", "SHORT", k[i]["c"], f["zg"], f"頂部中樞放量跌破 [{f['zd']:.5g},{f['zg']:.5g}]" + (" 背馳" if f["div"] else ""))
    return None

# ---------- B：崩後，死貓反彈力竭做空 ----------
_state_b = {}  # symbol-agnostic 狀態放在 caller；這裡用純函數 + 回看

def engine_b(k, i):
    P = C.ENGINE_B
    n = P["crash_bars"]
    if i < n + 20: return None
    win = k[:i + 1]
    # 往回找最近一次「崩盤」：n 根內從高到低跌 >= crash_drop，且量爆
    for start in range(i - P["wait_bars"], i - 2):
        if start < 20: continue
        seg = win[start:start + n]
        top = max(x["h"] for x in seg); low_idx = min(range(len(seg)), key=lambda j: seg[j]["l"])
        low = seg[low_idx]["l"]
        if top <= 0 or (top - low) / top < P["crash_drop"]: continue
        mavol = sum(x["v"] for x in win[start - 20:start]) / 20
        if max(x["v"] for x in seg) < P["vol_mult"] * mavol: continue
        # 找到崩盤；崩後反彈區間
        after = win[start + low_idx + 1:]
        if len(after) < 3: return None
        hi_rel = max(range(len(after)), key=lambda j: after[j]["h"])
        bounce_hi = after[hi_rel]["h"]
        if len(after) - 1 - hi_rel > P["after_hi_bars"]: return None   # 反彈高點太久以前，反彈已結束
        ratio = (bounce_hi - low) / (top - low)
        if not (P["bounce_min"] <= ratio <= P["bounce_max"]): continue
        bar, prev = win[-1], win[-2]
        # 力竭：本根收盤跌破前一根低點，且本根不是反彈新高那根
        if bar["c"] < prev["l"] and bar["h"] < bounce_hi:
            return Signal("B", "SHORT", bar["c"], bounce_hi * 1.005,
                          f"崩盤 {top:.5g}->{low:.5g} 反彈{ratio:.0%} 力竭")
        return None
    return None

# ---------- C：崩盤延續，破崩盤棒低點追空 ----------
def engine_c(k, i):
    P = C.ENGINE_C
    if i < 25: return None
    win = k[:i + 1]
    for back in range(1, P["confirm_bars"] + 1):
        j = i - back
        cb = win[j]
        drop = (cb["o"] - cb["c"]) / cb["o"]
        mavol = sum(x["v"] for x in win[j - 20:j]) / 20
        if drop >= P["bar_drop"] and cb["v"] >= P["vol_mult"] * mavol:
            bar = win[-1]
            # 中間幾根都沒破低、本根破低 → 進
            if all(win[m]["l"] >= cb["l"] for m in range(j + 1, i)) and bar["c"] < cb["l"]:
                pull_hi = max(x["h"] for x in win[j + 1:i + 1])
                stop = max(pull_hi * 1.02, bar["c"] * 1.03)   # 回抽高點上方，最少 3%
                return Signal("C", "SHORT", bar["c"], stop, f"崩盤棒 -{drop:.0%} 破低延續")
    return None

# ---------- D：崩後 V 反抄底（多）----------
def _find_crash(win, P):
    """回傳 (top, low, low_abs_idx) 或 None。"""
    n = P["crash_bars"]; i = len(win) - 1
    for start in range(i - P["wait_bars"] - n, i - 2):
        if start < 20: continue
        seg = win[start:start + n]
        top = max(x["h"] for x in seg); li = min(range(len(seg)), key=lambda j: seg[j]["l"])
        low = seg[li]["l"]
        if (top - low) / top < P["crash_drop"]: continue
        mavol = sum(x["v"] for x in win[start - 20:start]) / 20
        if max(x["v"] for x in seg) < P["vol_mult"] * mavol: continue
        return top, low, start + li
    return None

def engine_d(k, i):
    P = C.ENGINE_D
    if i < 40: return None
    win = k[:i + 1]
    found = _find_crash(win, P)
    if not found: return None
    top, low, li = found
    if i - li > P["wait_bars"]: return None
    bar, prev = win[-1], win[-2]
    rng = bar["h"] - bar["l"]
    if rng <= 0 or bar["c"] <= bar["o"]: return None
    lower_wick = min(bar["o"], bar["c"]) - bar["l"]
    mavol = sum(x["v"] for x in win[-21:-1]) / 20
    if lower_wick / rng < P["wick_ratio"] or bar["v"] < P["vol_mult"] * mavol: return None
    prev_mid = (prev["o"] + prev["c"]) / 2
    close_pos = (bar["c"] - bar["l"]) / rng           # 收在全棒的位置
    if bar["c"] < prev_mid and close_pos < 0.7: return None   # 收回前根一半，或收在頂部 30%
    # 只抓「新低附近」那根，不抓反彈半途
    if bar["l"] > low * 1.05: return None
    return Signal("D", "LONG", bar["c"], bar["l"] * 0.99,
                  f"崩盤 {top:.5g}->{low:.5g} 爆量長下影收回，抄反彈")

# ---------- E：拉升初期突破回踩跟多（多）----------
def engine_e(k, i):
    P = C.ENGINE_E
    if i < P["range_bars"] + 30: return None
    win = k[:i + 1]
    # 往回找最近 pull_bars 內的突破棒
    for back in range(2, P["pull_bars"] + 1):
        j = i - back
        level = ind(k)["hi_lvl"][j]
        if level is None: continue
        bb = win[j]
        mavol = sum(x["v"] for x in win[j - 20:j]) / 20
        if not (bb["c"] > level and win[j - 1]["c"] <= level and bb["v"] >= P["vol_mult"] * mavol):
            continue
        mid = win[j + 1:i]                      # 突破後、本根前
        if not mid: return None
        pull_low = min(x["l"] for x in mid)
        if pull_low > level * (1 + P["pull_tol"]): return None   # 沒回踩
        if min(x["c"] for x in mid) < level * 0.98: return None  # 假突破
        bar = win[-1]
        if bar["c"] > max(x["h"] for x in mid[-3:]):             # 回踩後再轉強
            return Signal("E", "LONG", bar["c"], min(pull_low, level) * 0.98,
                          f"突破3日高 {level:.5g} 回踩不破，跟多")
        return None
    return None

# ---------- F：崩盤進行中順勢追空（1m）----------
def engine_f(k, i):
    P = C.ENGINE_F
    if i < 40: return None
    win = k[:i + 1]
    if P.get("min_gain_24h"):                                  # 只做已經漲瘋的幣
        day = win[-min(1440, i):]
        lo24 = min(x["l"] for x in day); hi24 = max(x["h"] for x in day)
        if lo24 <= 0 or hi24 / lo24 - 1 < P["min_gain_24h"]: return None
    seg = win[-P["window"]:]
    hi = max(x["h"] for x in seg); bar = win[-1]
    if hi <= 0 or 1 - bar["c"] / hi < P["drop"]: return None
    if any(x["c"] >= x["o"] for x in win[-P["red_bars"]:]): return None
    mavol = sum(x["v"] for x in win[-23:-3]) / 20
    if mavol <= 0 or sum(x["v"] for x in win[-3:]) / 3 < P["vol_mult"] * mavol: return None
    stop = max(x["h"] for x in win[-P["stop_bars"]:])
    stop = max(stop, bar["c"] * (1 + P["min_stop"]))
    return Signal("F", "SHORT", bar["c"], stop, f"15m跌{1 - bar['c'] / hi:.0%} 連黑放量，清算連鎖追空")

# ---------- G：暴漲進行中順勢追多（1m，軋空）----------
def engine_g(k, i, funding=None):
    P = C.ENGINE_G
    if i < 40: return None
    win = k[:i + 1]
    day = win[-min(1440, i):]
    lo24, hi24 = min(x["l"] for x in day), max(x["h"] for x in day)
    if lo24 <= 0: return None
    gain24 = hi24 / lo24 - 1
    if not (P["gain24_min"] <= gain24 <= P["gain24_max"]): return None   # 沒動 或 已經瘋掉
    if funding is not None and funding > P["max_funding"]: return None   # 多頭已擠爆，不追
    seg = win[-P["window"]:]
    lo = min(x["l"] for x in seg); bar = win[-1]
    if lo <= 0 or bar["c"] / lo - 1 < P["rise"]: return None
    if any(x["c"] <= x["o"] for x in win[-P["green_bars"]:]): return None
    mavol = sum(x["v"] for x in win[-23:-3]) / 20
    if mavol <= 0 or sum(x["v"] for x in win[-3:]) / 3 < P["vol_mult"] * mavol: return None
    stop = min(x["l"] for x in win[-P["stop_bars"]:])
    stop = min(stop, bar["c"] * (1 - P["min_stop"]))
    return Signal("G", "LONG", bar["c"], stop, f"15m漲{bar['c'] / lo - 1:.0%} 連紅放量，軋空追多")

ENGINES = {"A": engine_a, "B": engine_b, "C": engine_c, "D": engine_d, "E": engine_e, "F": engine_f, "G": engine_g}
LONG_ENGINES = {"D", "E", "G"}
ENGINE_TF = {"F": "1m", "G": "1m"}          # 沒列的都是 5m
