"""主網成交重算（研究用）：回測的交易照「實盤的出場機制」重走一遍，成交價改用主網公開的逐筆成交（aggTrades）。

實盤機制（跟回測不同的地方）：
- 進場：訊號棒收盤後送市價單 → 成交在收盤 + 延遲之後的第一筆「同方向主動成交」價（做空吃買盤：主動賣的成交）。
- 停損：交易所上的 STOP_MARKET → 價格碰到停損之後 0.3 秒的第一筆主動買（空單停損）成交價。
  實盤用標記價格觸發（workingType=MARK_PRICE），這裡用最新成交價觸發，崩盤時標記價格較慢，實際觸發可能更晚。
- 1R 減碼、時間出場：每根收盤後才判斷、送市價單 → 收盤 + 延遲之後的第一筆主動買。回測假設減碼剛好成交在 1R。
- 止損移到成本用的是訊號價（實盤 pos["entry"] 是訊號價），損益用實際成交價。
fill(kind, t_ms, ref_px, end_ms) 決定成交價；給「參考價成交」就是「實盤機制、沒有滑價」。"""

def replay(k, i0, entry_ref, stop0, R, fill, bar_ms=300000, d=-1):
    """k：K 線；i0：訊號棒；R：出場規則（config.RISK＋EXIT）。回傳 dict(r, reason, fills=[(kind, ref, px, frac)])。"""
    r_unit = abs(stop0 - entry_ref)
    if r_unit <= 0: return None
    ef = fill("open", k[i0]["t"] + bar_ms, entry_ref, None)
    if ef is None: return None
    fills = [("open", entry_ref, ef, 1.0)]
    rem, pnl, stop, tp1, be = 1.0, 0.0, stop0, False, False

    def close(kind, ref, px, frac):
        nonlocal pnl
        pnl += frac * d * (px - ef) / r_unit
        fills.append((kind, ref, px, frac))

    reason = "eod"
    for j in range(i0 + 1, len(k)):
        bar = k[j]; end = bar["t"] + bar_ms
        adv = bar["h"] if d < 0 else bar["l"]; fav = bar["l"] if d < 0 else bar["h"]
        if (adv >= stop) if d < 0 else (adv <= stop):
            px = fill("stop", bar["t"], stop, end)
            if px is None: return None
            close("stop", stop, px, rem); rem = 0; reason = "stop"; break
        r_now = d * (fav - entry_ref) / r_unit
        if R.get("tp1_r") is not None and not tp1 and r_now >= R["tp1_r"]:
            px = fill("close", end, bar["c"], None)
            if px is None: return None
            close("tp1", bar["c"], px, 0.5); rem = 0.5; tp1 = True; stop = entry_ref
        elif R.get("tp1_r") is None and not be and R.get("be_r") and r_now >= R["be_r"]:
            be = True; stop = entry_ref
        elif j - i0 >= R["max_hold_bars"]:
            px = fill("close", end, bar["c"], None)
            if px is None: return None
            close("time", bar["c"], px, rem); rem = 0; reason = "time"; break
        elif (tp1 or be) and r_now >= R["trail_after_r"]:
            seg = k[j - R["trail_bars"]:j + 1]
            stop = min(stop, max(x["h"] for x in seg)) if d < 0 else max(stop, min(x["l"] for x in seg))
    if rem:                                                     # 資料結束：用最後收盤，不算滑價
        close("eod", k[-1]["c"], k[-1]["c"], rem)
    fee = R.get("fee", 0) * sum(px * frac for _, _, px, frac in fills) / r_unit
    return dict(r=round(pnl - fee, 3), reason=reason, fills=fills)

def ref_fill(kind, t, ref, end):
    """參考價成交：市價單＝參考價、停損＝停損價。"""
    return ref

def trade_fill(get, latency_ms, d=-1, stop_delay_ms=300):
    """用主網逐筆成交決定成交價。get(start_ms, end_ms) → [(t_ms, price, buyer_is_maker)] 依時間排序。
    d=-1 做空：開倉＝主動賣（buyer_is_maker=True），平倉＝主動買。找不到就回 None（那筆不算，不拿參考價充數）。"""
    def first(after, aggressor_buy):
        for t, p, m in get(after, after + 60000):
            if t >= after and (not m) == aggressor_buy: return p
        return None
    def fill(kind, t, ref, end):
        if kind == "open":  return first(t + latency_ms, aggressor_buy=(d > 0))
        if kind == "close": return first(t + latency_ms, aggressor_buy=(d < 0))
        # 停損：這根之內第一筆越過停損價的成交＝觸發，之後 stop_delay 的第一筆反向主動成交
        trig = next((tt for tt, p, m in get(t, end) if t <= tt < end and ((p >= ref) if d < 0 else (p <= ref))), None)
        if trig is None: return None
        return first(trig + stop_delay_ms, aggressor_buy=(d < 0))
    return fill

def slip_pct(kind, ref, px, d=-1):
    """對我們不利為正（%）。做空：開倉賣低了、平倉（停損、減碼、時間）買高了都是不利。"""
    if not ref: return None
    s = (ref - px) / ref if kind == "open" else (px - ref) / ref
    return round(s * 100 * (1 if d < 0 else -1), 3)
