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
    """回傳倉位；equity 給的是階梯本金（未扣保留）。"""
    R = {**C.RISK, **C.EXIT.get(sig.engine, {})}     # 每引擎可覆蓋 risk_pct / 止損上下限
    S = C.SIZING
    if equity is None: equity, _ = equity_now()
    usable = equity * (1 - S["reserve_pct"])          # 扣掉保留後真正拿來算的本金
    dist = abs(sig.entry - sig.stop)
    if dist <= 0 or not (R["min_stop_pct"] <= dist / sig.entry <= R["max_stop_pct"]): return None   # 止損太遠或太近：不進
    risk_usdt = usable * R["risk_pct"]
    qty = risk_usdt / dist
    notional = qty * sig.entry
    lev = S["leverage"]
    margin_cap = usable / S["max_positions"]          # 三筆全開也只用掉 usable
    if notional / lev > margin_cap:                   # 止損太窄 → 名目太大：縮倉位，不加槓桿
        notional = margin_cap * lev; qty = notional / sig.entry
    return dict(qty=qty, notional=round(notional, 2), leverage=lev, margin=round(notional / lev, 2), equity=equity,
                usable=round(usable, 2), risk_usdt=round(qty * dist, 2), stop_pct=round(sig.risk * 100, 2))
