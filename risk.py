"""倉位 = 由止損距離反推，槓桿只是結果。"""
import math
import config as C

def size(sig, equity=None):
    R = {**C.RISK, **C.EXIT.get(sig.engine, {})}     # 每引擎可覆蓋 risk_pct / 止損上下限
    equity = equity or R["equity"]
    risk_usdt = equity * R["risk_pct"]
    dist = abs(sig.entry - sig.stop)
    if dist <= 0 or not (R["min_stop_pct"] <= dist / sig.entry <= R["max_stop_pct"]): return None   # 止損太遠或太近：不進
    qty = risk_usdt / dist
    notional = qty * sig.entry
    lev = math.ceil(notional / equity)
    if lev > R["max_leverage"]:            # 止損太遠 → 縮部位，不是放大槓桿
        lev = R["max_leverage"]
        notional = equity * lev
        qty = notional / sig.entry
    return dict(qty=qty, notional=round(notional, 2), leverage=lev,
                risk_usdt=round(qty * dist, 2), stop_pct=round(sig.risk * 100, 2))
