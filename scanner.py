"""掃描層：24h 漲幅前 N 檔逐一算熱度，四項條件計分；>= min_score 進擁擠名單。
回傳 (watch, observe)：watch 是進場引擎要盯的，observe 是全部候選給 dashboard 看。"""
import time
import binance as B
import config as C

def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None

def scan(verbose=True):
    P = C.SCAN
    tick = B.ticker_24h(); fund = B.funding_all()
    cands, crashed_rows = [], []
    for s in B.perp_symbols():
        t = tick.get(s)
        if not t or s[:-4] in P["exclude"]: continue        # 主流幣不看
        qv, chg = float(t["quoteVolume"]), float(t["priceChangePercent"])
        if qv < P["min_quote_vol_24h"]: continue
        if chg <= P["crashed_drop"]:
            crashed_rows.append((chg, s, qv)); continue
        if P["max_quote_vol_24h"] and qv > P["max_quote_vol_24h"]: continue
        cands.append((chg, s, qv))
    cands.sort(reverse=True)
    crashed = {c[1] for c in crashed_rows}
    cands = cands[:P["top_n"]] + crashed_rows
    if verbose: print(f"候選 {len(cands)} 檔")
    observe = []
    for chg24, s, qv in cands:
        try:
            k1h = B.klines(s, "1h", 60); closes = [k["c"] for k in k1h]
            gain48 = closes[-1] / closes[-49] - 1 if len(closes) >= 49 else 0
            ma20 = sma(closes, 20); dev = closes[-1] / ma20 - 1 if ma20 else 0
            oi = B.oi_hist(s, "1h", 25)
            oi_g = float(oi[-1]["sumOpenInterest"]) / float(oi[0]["sumOpenInterest"]) - 1 if len(oi) >= 2 else 0
            f = fund.get(s, 0)
            hits = dict(gain=gain48 >= P["min_gain_48h"], dev=dev >= P["min_ma20_dev"],
                        oi=oi_g >= P["min_oi_growth_24h"], fund=f >= P["min_funding"])
            observe.append(dict(symbol=s, score=sum(hits.values()), chg24=round(chg24, 1),
                                gain48=round(gain48 * 100, 1), ma20_dev=round(dev * 100, 1),
                                oi_growth=round(oi_g * 100, 1), funding=round(f * 100, 4),
                                vol24=round(qv / 1e6, 1),
                                hits=("💥" if s in crashed else "🔥" if chg24 >= P["watch_chg24"] else "") + "".join(k[0] for k, v in hits.items() if v)))
            time.sleep(0.15)
        except Exception as e:
            if verbose: print(s, "skip:", e)
    observe.sort(key=lambda r: (-r["score"], -r["ma20_dev"]))
    watch = [r for r in observe if r["score"] >= P["min_score"] or r["symbol"] in crashed or r["chg24"] >= P["watch_chg24"]]
    return watch, observe

def why(symbol):
    """診斷：一檔幣為什麼沒進名單。"""
    P = C.SCAN
    info = {x["symbol"]: x for x in B._get("/fapi/v1/exchangeInfo")["symbols"]}.get(symbol)
    t = B.ticker_24h().get(symbol)
    if not info: return dict(symbol=symbol, reason="幣安 USDT 永續沒有這檔")
    out = dict(symbol=symbol, status=info["status"], contractType=info["contractType"])
    if not t: out["reason"] = "沒有 24h ticker"; return out
    qv, chg = float(t["quoteVolume"]), float(t["priceChangePercent"])
    out.update(quoteVolume=qv, chg24=chg, funding=B.funding_all().get(symbol))
    if info["status"] != "TRADING": out["reason"] = f"status={info['status']}，perp_symbols() 只收 TRADING"
    elif qv < P["min_quote_vol_24h"]: out["reason"] = "成交額低於下限"
    elif chg <= P["crashed_drop"]: out["reason"] = "應在崩盤名單（若沒有，重掃一次）"
    elif symbol[:-4] in P["exclude"]: out["reason"] = "在主流幣排除清單"
    else: out["reason"] = "在候選內，但 24h 漲幅沒進前 N"
    return out

if __name__ == "__main__":
    w, o = scan()
    for r in o: print(r)
    print("擁擠名單:", [r["symbol"] for r in w])
