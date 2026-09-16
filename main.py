"""用法:
  python main.py scan                  掃描多頭擁擠名單
  python main.py backtest AINUSDT 30   回測單一幣種 30 天
  python main.py sweep 30              對掃描名單全部回測
  python main.py paper                 監控名單，訊號出現 → testnet 下單 + 印出
"""
import sys, time, json
import binance as B, config as C, risk, scanner, backtest
from signals import ENGINES

def paper():
    watch, last = {}, {}
    while True:
        if time.time() - last.get("scan", 0) > 1800:           # 每 30 分重掃
            watch = {r["symbol"]: r for r in scanner.scan(verbose=False)}
            last["scan"] = time.time(); print("名單:", list(watch))
        for s in watch:
            try:
                k = B.klines(s, "5m", 300)
                if last.get(s) == k[-1]["t"]: continue          # 同一根不重複判斷
                last[s] = k[-1]["t"]
                for eid, fn in ENGINES.items():
                    sig = fn(k, len(k) - 1)
                    if not sig: continue
                    sz = risk.size(sig)
                    print(json.dumps(dict(sym=s, **sig.__dict__, **sz), ensure_ascii=False))
                    if C.API_KEY:
                        B.set_leverage(s, sz["leverage"])
                        B.market_order(s, "SELL", round(sz["qty"], 0))
                        B.stop_order(s, "BUY", round(sz["qty"], 0), round(sig.stop, 6))
                    break
            except Exception as e: print(s, e)
        time.sleep(60)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        for r in scanner.scan(): print(r)
    elif cmd == "backtest":
        sys.argv = sys.argv[1:]; backtest.__name__ = "__main__"; exec(open("backtest.py").read())
    elif cmd == "sweep":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        end = int(time.time() * 1000); allt = []
        for r in scanner.scan(verbose=False):
            k = B.klines_range(r["symbol"], "5m", end - days * 86400000, end)
            allt += backtest.simulate_each(k)
        backtest.report(allt)
    elif cmd == "paper": paper()
