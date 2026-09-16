"""純標準庫的 Binance Futures 客戶端。公開資料不需 key；下單需要。"""
import hashlib, hmac, json, time, urllib.parse, urllib.request
import config as C

def _get(path, params=None, base=None, signed=False):
    base = base or (C.TESTNET if (signed and C.USE_TESTNET) else C.FAPI)
    params = dict(params or {})
    headers = {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        q = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(C.API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
        headers["X-MBX-APIKEY"] = C.API_KEY
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def _post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    q = urllib.parse.urlencode(params)
    sig = hmac.new(C.API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    base = C.TESTNET if C.USE_TESTNET else C.FAPI
    req = urllib.request.Request(f"{base}{path}", data=f"{q}&signature={sig}".encode(),
                                 headers={"X-MBX-APIKEY": C.API_KEY}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ---- 公開資料 ----
def perp_symbols():
    info = _get("/fapi/v1/exchangeInfo")
    return [s["symbol"] for s in info["symbols"]
            if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["status"] == "TRADING"]

def ticker_24h():
    return {t["symbol"]: t for t in _get("/fapi/v1/ticker/24hr")}

def funding_all():
    return {p["symbol"]: float(p["lastFundingRate"]) for p in _get("/fapi/v1/premiumIndex")}

def oi_hist(symbol, period="1h", limit=25):
    return _get("/futures/data/openInterestHist", dict(symbol=symbol, period=period, limit=limit))

def klines(symbol, interval="5m", limit=500, start=None, end=None):
    p = dict(symbol=symbol, interval=interval, limit=min(limit, 1500))
    if start: p["startTime"] = int(start)
    if end: p["endTime"] = int(end)
    rows = _get("/fapi/v1/klines", p)
    return [dict(t=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5]))
            for r in rows]

def klines_range(symbol, interval, start_ms, end_ms):
    """分頁抓完整區間，回測用。"""
    out, cur = [], start_ms
    while cur < end_ms:
        batch = klines(symbol, interval, 1500, cur, end_ms)
        if not batch: break
        out.extend(batch)
        cur = batch[-1]["t"] + 1
        if len(batch) < 1500: break
        time.sleep(0.2)
    return out

def wallet_balance(asset="USDT"):
    """錢包餘額（含未實現前的保證金），不是可用餘額。"""
    for x in _get("/fapi/v2/balance", signed=True):
        if x["asset"] == asset: return float(x["balance"])
    return 0.0

# ---- 下單（testnet / live 由 USE_TESTNET 決定）----
def position_mode_hedge():
    return _get("/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]

def set_leverage(symbol, lev):
    return _post("/fapi/v1/leverage", dict(symbol=symbol, leverage=int(lev)))

def market_order(symbol, side, qty, reduce_only=False):
    p = dict(symbol=symbol, side=side, type="MARKET", quantity=qty)
    if position_mode_hedge():
        p["positionSide"] = "SHORT" if (side == "SELL") != reduce_only else "LONG"
    elif reduce_only:
        p["reduceOnly"] = "true"
    return _post("/fapi/v1/order", p)

def stop_order(symbol, side, qty, stop_price):
    p = dict(symbol=symbol, side=side, type="STOP_MARKET", stopPrice=stop_price, closePosition="true")
    if position_mode_hedge():
        p["positionSide"] = "SHORT" if side == "BUY" else "LONG"
        p.pop("closePosition"); p["quantity"] = qty
    return _post("/fapi/v1/order", p)
