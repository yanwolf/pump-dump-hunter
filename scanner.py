"""掃描層：找出「多頭擁擠」的小幣。四個條件同時滿足才進名單。"""
import time
import binance as B
import config as C

def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None

def scan(verbose=True):
    P = C.SCAN
    tick = B.ticker_24h()
    fund = B.funding_all()
    cands = []
    for s in B.perp_symbols():
        t = tick.get(s)
        if not t: continue
        qv = float(t["quoteVolume"])
        if not (P["min_quote_vol_24h"] <= qv <= P["max_quote_vol_24h"]): continue
        if fund.get(s, 0) < P["min_funding"]: continue
        cands.append(s)
    if verbose: print(f"資金費率+成交額 初篩: {len(cands)} 檔")

    out = []
    for s in cands:
        try:
            k1h = B.klines(s, "1h", 60)
            closes = [k["c"] for k in k1h]
            gain48 = closes[-1] / closes[-49] - 1 if len(closes) >= 49 else 0
            ma20 = sma(closes, 20)
            dev = closes[-1] / ma20 - 1 if ma20 else 0
            oi = B.oi_hist(s, "1h", 25)
            oi_g = float(oi[-1]["sumOpenInterest"]) / float(oi[0]["sumOpenInterest"]) - 1 if len(oi) >= 2 else 0
            score = sum([gain48 >= P["min_gain_48h"], dev >= P["min_ma20_dev"], oi_g >= P["min_oi_growth_24h"]])
            row = dict(symbol=s, gain48=round(gain48, 3), ma20_dev=round(dev, 3),
                       oi_growth=round(oi_g, 3), funding=fund[s], score=score + 1)
            if score >= 2:  # 資金費率已過，再中兩項就列入
                out.append(row)
            time.sleep(0.15)
        except Exception as e:
            if verbose: print(s, "skip:", e)
    out.sort(key=lambda r: (-r["score"], -r["ma20_dev"]))
    return out

if __name__ == "__main__":
    for r in scan(): print(r)
