"""在 5m K 線上跑五個引擎（A/B/C 空、D/E 多），統一出場規則，輸出 R 倍數分布。"""
import json, sys, time
import binance as B, config as C, risk
from signals import ENGINES

def simulate(k, engine_ids=("A", "B", "C", "D", "E")):
    trades, open_ = [], None
    for i in range(len(k)):
        bar = k[i]
        if open_:
            t, R, d = open_, open_["R"], open_["dir"]          # d=+1 多 / -1 空
            fav = bar["h"] if d > 0 else bar["l"]                # 對我有利的極值
            adv = bar["l"] if d > 0 else bar["h"]                # 對我不利的極值
            r_now = d * (fav - t["entry"]) / t["r_unit"]
            hit_stop = adv <= t["stop"] if d > 0 else adv >= t["stop"]
            if hit_stop:
                exit_px = t["stop"] * (1 - d * R["slippage"]); reason = "stop"
            elif R["tp1_r"] is not None and not t["tp1"] and r_now >= R["tp1_r"]:
                t["tp1"] = True; t["stop"] = t["entry"]; t["half_pnl"] = 0.5 * R["tp1_r"]
                continue
            elif R["tp1_r"] is None and not t["be"] and R.get("be_r") and r_now >= R["be_r"]:
                t["be"] = True; t["stop"] = t["entry"]            # 不減碼，只保本
                continue
            elif i - t["i"] >= R["max_hold_bars"]:
                exit_px = bar["c"]; reason = "time"
            else:
                if (t["tp1"] or t["be"]) and r_now >= R["trail_after_r"]:
                    seg = k[i - R["trail_bars"]:i + 1]
                    t["stop"] = max(t["stop"], min(x["l"] for x in seg)) if d > 0 else min(t["stop"], max(x["h"] for x in seg))
                continue
            r_exit = d * (exit_px - t["entry"]) / t["r_unit"]
            r_total = (t["half_pnl"] + 0.5 * r_exit) if t["tp1"] else r_exit
            r_total -= (R["fee"] * 2 + R["slippage"]) * t["entry"] / t["r_unit"]
            trades.append(dict(engine=t["engine"], side=t["side"], t=t["t"], entry=t["entry"], exit=exit_px,
                               r=round(r_total, 2), reason=reason, bars=i - t["i"]))
            open_ = None
            continue
        for eid in engine_ids:
            sig = ENGINES[eid](k, i)
            if sig and sig.risk <= C.RISK["max_stop_pct"]:
                d = 1 if sig.side == "LONG" else -1
                open_ = dict(engine=eid, side=sig.side, dir=d, i=i, t=bar["t"], entry=sig.entry, stop=sig.stop,
                             r_unit=abs(sig.stop - sig.entry), tp1=False, be=False, half_pnl=0,
                             R={**C.RISK, **C.EXIT.get(eid, {})})
                break
    if open_:  # 資料結束仍持倉，用最後收盤結算
        t = open_; r_exit = t["dir"] * (k[-1]["c"] - t["entry"]) / t["r_unit"]
        r_total = (t["half_pnl"] + 0.5 * r_exit) if t["tp1"] else r_exit
        trades.append(dict(engine=t["engine"], side=t["side"], t=t["t"], entry=t["entry"], exit=k[-1]["c"],
                           r=round(r_total, 2), reason="eod", bars=len(k) - 1 - t["i"]))
    return trades

def simulate_each(k):
    """三個引擎各自獨立跑，互不佔用倉位，才能公平比較。"""
    out = []
    for e in ENGINES: out += simulate(k, (e,))
    return sorted(out, key=lambda t: t["t"])

def summary(trades):
    """給網頁用的每引擎統計。"""
    by = {}
    for t in trades: by.setdefault(t["engine"], []).append(t["r"])
    out = []
    for e, rs in sorted(by.items()):
        wins = [r for r in rs if r > 0]; loss = [r for r in rs if r <= 0]
        out.append(dict(engine=e, n=len(rs), win=round(len(wins) / len(rs) * 100), exp=round(sum(rs) / len(rs), 2),
                        pf=round(sum(wins) / abs(sum(loss)), 2) if loss else None, best=max(rs), worst=min(rs)))
    return out

def run(symbol, days):
    end = int(time.time() * 1000); k = B.klines_range(symbol, "5m", end - days * 86400000, end)
    tr = simulate_each(k)
    for t in tr: t["time"] = time.strftime("%m-%d %H:%M", time.gmtime(t["t"] / 1000)) + " UTC"
    return dict(symbol=symbol, days=days, bars=len(k), trades=tr, summary=summary(tr))

def diag_a(symbol, days):
    """引擎 A 診斷：列出所有「跌破中樞」的棒，以及它們被哪個條件擋掉。"""
    from signals import engine_a_flags
    end = int(time.time() * 1000); k = B.klines_range(symbol, "5m", end - days * 86400000, end)
    need = ("hot", "pivot", "top", "vol")
    rows, cnt = [], dict(bars=len(k), hot=0, pivot=0, top=0, vol=0, div=0, brk=0, all=0)
    for i in range(len(k)):
        f = engine_a_flags(k, i)
        for key in need + ("div", "brk"): cnt[key] += bool(f[key])
        if f["brk"]:
            ok = all(f[x] for x in need)
            cnt["all"] += ok
            rows.append(dict(time=time.strftime("%m-%d %H:%M", time.gmtime(k[i]["t"] / 1000)),
                             close=k[i]["c"], zd=f["zd"], zg=f["zg"], width=f["width"],
                             hot=f["hot"], pivot=f["pivot"], top=f["top"], vol=f["vol"], div=f["div"],
                             fire="✅" if ok else "缺:" + ",".join(x for x in need if not f[x])))
    return dict(symbol=symbol, counts=cnt, breaks=rows[-40:])

def report(trades):
    if not trades: print("無交易"); return
    by = {}
    for t in trades: by.setdefault(t["engine"], []).append(t["r"])
    for e, rs in sorted(by.items()):
        wins = [r for r in rs if r > 0]
        pf = sum(wins) / abs(sum(r for r in rs if r <= 0) or 1e-9)
        print(f"引擎{e}: {len(rs)}筆 勝率{len(wins)/len(rs):.0%} 期望{sum(rs)/len(rs):+.2f}R "
              f"PF{pf:.2f} 最大{max(rs):+.1f}R 最小{min(rs):+.1f}R")

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "AINUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    end = int(time.time() * 1000); start = end - days * 86400000
    k = B.klines_range(sym, "5m", start, end)
    print(f"{sym} 5m {len(k)} 根")
    tr = simulate_each(k)
    report(tr)
    json.dump(tr, open(f"trades_{sym}.json", "w"), ensure_ascii=False, indent=1)
