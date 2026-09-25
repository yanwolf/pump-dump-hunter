"""已平倉績效統計——pump-dump-hunter 自己的口徑（多引擎 × 多幣種）。

- 主軸依引擎分開（C 崩盤追空、F 清算追空、G 軋空追多）：三套邏輯完全不同，混算的勝率沒有意義。
- **以 R 為核心**（照 crypto-screener）：C 每筆風險 2%、F／G 1%，同樣賺 10 U 在不同引擎意義不同；R 才能跨引擎比較。U 只當參考。
  勝＝損益 > 0；期望值＝平均 R；獲利因子＝總賺 R ÷ 總賠 R；最大回撤＝累積 R 曲線從高點回落最多的一段（照 gold-scalper 的定義）。
- 依幣種：哪幾檔反覆虧、哪幾檔貢獻獲利。
- 實際滑價（依引擎）：進場成交價對訊號價、停損單出場價對停損價，正數＝不利。回測假設 0.3%，這裡看實盤到底多少。
- 出場原因分布。
- 依參數版本分開（照 crypto-screener）：每筆開倉時記下當下的實盤參數版本，改參數前後可以並排比。沒有版本資訊的歸「舊版（未記錄）」。
- 損益未知的交易（成交明細查不到，清單第 8 條 r27、r29）不算輸也不算贏、不當 0，只另列筆數。
帳上的已平倉紀錄只保留最近 300 筆（store），統計範圍就是這些；紀錄依結帳先後排列，回撤照這個順序算。"""
from collections import Counter, OrderedDict

LEGACY = "舊版（未記錄）"

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def _r(t):
    """R 倍數：紀錄裡的 r；沒有但有損益與風險金額時自己算。"""
    r = _num(t.get("r"))
    if r is not None: return r
    p, risk = _num(t.get("pnl")), _num(t.get("risk_usdt"))
    return round(p / risk, 4) if p is not None and risk else None

def _reason(t):
    by = t.get("by") or "?"
    if "別的部位" in by: return "交易所出場（被重開）"
    return by.split("；")[0].split("（")[0]

def _slip(t):
    """(進場滑價%, 停損出場滑價%)——正數＝對我們不利。算不出來的是 None。"""
    d = 1 if t.get("side") == "LONG" else -1
    e, f = _num(t.get("entry")), _num(t.get("fill"))
    ent = round((f - e) / e * d * 100, 3) if e and f else None               # 多單買貴了、空單賣便宜了 = 不利
    s, x = _num(t.get("stop")), _num(t.get("exit"))
    ext = round((s - x) / s * d * 100, 3) if s and x and _reason(t) == "停損單" else None   # 多單停損成交在停損價下方 = 不利
    # 「停損單」其實是所有交易所端出場（停損觸發，也包括在 App 手動平倉——對帳分不出兩者）。成交價離停損價超過 2% 的不像停損成交，
    # 拿來算停損滑價只會得到沒有意義的數字；這種另外計數（回傳字串 "far"）
    if ext is not None and abs(ext) > 2.0: ext = "far"
    return ent, ext

def compute(trades):
    known = [t for t in trades if _num(t.get("pnl")) is not None]
    rk = [(t, _r(t)) for t in known]
    rk = [(t, r) for t, r in rk if r is not None]
    rs = [r for _, r in rk]
    win_r, loss_r = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    gp, gl = sum(win_r), abs(sum(loss_r))
    cum = peak = dd = 0.0; peak_at = dd_from = dd_to = None
    for t, r in rk:
        cum += r
        if cum > peak: peak, peak_at = cum, t.get("closed")
        if peak - cum > dd: dd, dd_from, dd_to = peak - cum, peak_at, t.get("closed")
    streak = worst = 0
    for t in known:
        streak = streak + 1 if t["pnl"] <= 0 else 0; worst = max(worst, streak)
    ent = [x for x in (_slip(t)[0] for t in trades) if x is not None]
    ext_all = [_slip(t)[1] for t in trades]
    ext = [x for x in ext_all if isinstance(x, (int, float))]
    far = sum(1 for x in ext_all if x == "far")
    avg = lambda xs, n=2: round(sum(xs) / len(xs), n) if xs else None
    return OrderedDict(
        total=len(trades), known=len(known), unknown=len(trades) - len(known),
        wins=sum(1 for t in known if t["pnl"] > 0),
        win_rate=round(sum(1 for t in known if t["pnl"] > 0) / len(known) * 100, 1) if known else None,
        expectancy_r=avg(rs), avg_win_r=avg(win_r), avg_loss_r=round(-gl / len(loss_r), 2) if loss_r else None,
        profit_factor=round(gp / gl, 2) if gl > 0 else None,
        total_r=round(sum(rs), 2) if rs else None, max_dd_r=round(dd, 2) if rs else None, dd_from=dd_from, dd_to=dd_to,
        max_losing_streak=worst, pnl=round(sum(t["pnl"] for t in known), 2) if known else None,   # 沒有已知損益不能寫成 0
        slip_entry_avg=avg(ent, 3), slip_entry_max=round(max(ent), 3) if ent else None, slip_entry_n=len(ent),
        slip_stop_avg=avg(ext, 3), slip_stop_max=round(max(ext), 3) if ext else None, slip_stop_n=len(ext), slip_stop_far=far,
        reasons=dict(Counter(_reason(t) for t in trades)),
    )

def report(closed):
    closed = list(closed or [])
    engines = sorted({t.get("engine") for t in closed if t.get("engine")})
    out = OrderedDict(scope=dict(n=len(closed), first=closed[0].get("closed") if closed else None,
                                 last=closed[-1].get("closed") if closed else None, kept=300))
    out["engines"] = OrderedDict([("全部", compute(closed))] + [(e, compute([t for t in closed if t.get("engine") == e])) for e in engines])
    vers = []
    for t in closed:
        v = t.get("ver_label") or LEGACY
        if v not in vers: vers.append(v)
    out["versions"] = [dict(version=v, engine=e, **{k: s[k] for k in ("total", "unknown", "win_rate", "expectancy_r", "total_r", "pnl")})
                       for v in vers for e in engines
                       for s in [compute([t for t in closed if (t.get("ver_label") or LEGACY) == v and t.get("engine") == e])] if s["total"]]
    syms = {}
    for t in closed: syms.setdefault(t.get("symbol") or "?", []).append(t)
    rows = []
    for sym, ts in syms.items():
        s = compute(ts)
        rows.append(dict(symbol=sym, engines="".join(sorted({t.get("engine") or "?" for t in ts})), total=s["total"],
                         win_rate=s["win_rate"], total_r=s["total_r"], pnl=s["pnl"], unknown=s["unknown"]))
    rows.sort(key=lambda x: x["total_r"] if x["total_r"] is not None else 0)
    out["symbols"] = rows
    return out
