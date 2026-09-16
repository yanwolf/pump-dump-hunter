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
    cands = []
    for s in B.perp_symbols():
        t = tick.get(s)
        if not t: continue
        qv = float(t["quoteVolume"])
        if P["min_quote_vol_24h"] <= qv <= P["max_quote_vol_24h"]:
            cands.append((float(t["priceChangePercent"]), s, qv))
    cands.sort(reverse=True); cands = cands[:P["top_n"]]
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
                                vol24=round(qv / 1e6, 1), hits="".join(k[0] for k, v in hits.items() if v)))
            time.sleep(0.15)
        except Exception as e:
            if verbose: print(s, "skip:", e)
    observe.sort(key=lambda r: (-r["score"], -r["ma20_dev"]))
    watch = [r for r in observe if r["score"] >= P["min_score"]]
    return watch, observe

if __name__ == "__main__":
    w, o = scan()
    for r in o: print(r)
    print("擁擠名單:", [r["symbol"] for r in w])
