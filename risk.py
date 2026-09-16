"""倉位 = 由止損距離反推，槓桿只是結果；本金用階梯值。"""
import math, time
import config as C

_bal = dict(t=0, balance=None, error=None)

def ladder(balance):
    S = C.SIZING
    if balance is None: return S["base"]
    tier = S["base"] + math.floor((balance - S["base"]) / S["step"]) * S["step"]
    return max(S["floor"], min(S["cap"], tier))

def equity_now():
    """回傳 (階梯本金, 實際餘額)。讀不到餘額就用 base。"""
    S = C.SIZING
    if not (S["use_live_balance"] and C.API_KEY): return S["base"], None
    if time.time() - _bal["t"] > S["refresh_sec"]:
        try:
            import binance as B
            _bal.update(balance=B.wallet_balance(), error=None)
        except Exception as e:
            _bal["error"] = str(e)
        _bal["t"] = time.time()
    return ladder(_bal["balance"]), _bal["balance"]

def size(sig, equity=None):
    R = {**C.RISK, **C.EXIT.get(sig.engine, {})}     # 每引擎可覆蓋 risk_pct / 止損上下限
    if equity is None: equity, _ = equity_now()
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
    return dict(qty=qty, notional=round(notional, 2), leverage=lev, equity=equity,
                risk_usdt=round(qty * dist, 2), stop_pct=round(sig.risk * 100, 2))
