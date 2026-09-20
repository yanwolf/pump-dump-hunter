"""純標準庫的 Binance Futures 客戶端。公開資料不需 key；下單需要。"""
import decimal, hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request
import config as C

def _open(req):
    """把 Binance 的錯誤內文帶出來，否則只看到 HTTP 400 不知道哪裡錯。"""
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: msg = e.read().decode()[:300]
        except Exception: msg = ""
        raise RuntimeError(f"HTTP {e.code} {msg}") from None

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
    return _open(urllib.request.Request(url, headers=headers))

def _post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    q = urllib.parse.urlencode(params)
    sig = hmac.new(C.API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    base = C.TESTNET if C.USE_TESTNET else C.FAPI
    req = urllib.request.Request(f"{base}{path}", data=f"{q}&signature={sig}".encode(),
                                 headers={"X-MBX-APIKEY": C.API_KEY}, method="POST")
    return _open(req)

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

def open_positions():
    """目前未平倉的 (symbol, positionSide) 數。"""
    return [p for p in _get("/fapi/v2/positionRisk", signed=True) if abs(float(p["positionAmt"])) > 0]

# ---- 交易所精度（下單一定要照 stepSize / tickSize，否則 -1111）----
_F = {}

def filters(symbol):
    if not _F:
        base = C.TESTNET if C.USE_TESTNET else C.FAPI
        try: info = _get("/fapi/v1/exchangeInfo", base=base)
        except Exception: info = _get("/fapi/v1/exchangeInfo", base=C.FAPI)
        for sy in info["symbols"]:
            f = {x["filterType"]: x for x in sy["filters"]}
            _F[sy["symbol"]] = dict(
                step=float(f["LOT_SIZE"]["stepSize"]), minqty=float(f["LOT_SIZE"]["minQty"]),
                tick=float(f["PRICE_FILTER"]["tickSize"]),
                minnot=float((f.get("MIN_NOTIONAL") or {}).get("notional") or 0))
    return _F.get(symbol) or dict(step=1.0, minqty=1.0, tick=1e-8, minnot=5.0)

def _fmt(v, unit, down=True):
    d, u = decimal.Decimal(str(v)), decimal.Decimal(str(unit))
    q = (d / u).to_integral_value(rounding=decimal.ROUND_DOWN if down else decimal.ROUND_HALF_UP) * u
    exp = -decimal.Decimal(str(unit)).normalize().as_tuple().exponent
    return f"{q:.{max(exp, 0)}f}"

def round_qty(symbol, qty):
    f = filters(symbol); q = _fmt(qty, f["step"])
    if float(q) < f["minqty"]: raise RuntimeError(f"數量 {q} 低於最小下單量 {f['minqty']}")
    return q

def round_price(symbol, price):
    return _fmt(price, filters(symbol)["tick"], down=False)

def user_trades(symbol, limit=50):
    """最近成交明細（含 realizedPnl、commission），平倉後拿實際出場價用。"""
    return _get("/fapi/v1/userTrades", dict(symbol=symbol, limit=limit), signed=True)

# ---- 下單（testnet / live 由 USE_TESTNET 決定）----
def position_mode_hedge():
    return _get("/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]

def set_leverage(symbol, lev):
    return _post("/fapi/v1/leverage", dict(symbol=symbol, leverage=int(lev)))

def market_order(symbol, side, qty, reduce_only=False):
    p = dict(symbol=symbol, side=side, type="MARKET", quantity=round_qty(symbol, qty), newOrderRespType="RESULT")   # RESULT 才有 avgPrice
    if position_mode_hedge():
        p["positionSide"] = "SHORT" if (side == "SELL") != reduce_only else "LONG"
    elif reduce_only:
        p["reduceOnly"] = "true"
    return _post("/fapi/v1/order", p)

def stop_order(symbol, side, qty, stop_price):
    p = dict(symbol=symbol, side=side, type="STOP_MARKET", stopPrice=round_price(symbol, stop_price),
             closePosition="true", workingType="MARK_PRICE")
    if position_mode_hedge():
        p["positionSide"] = "SHORT" if side == "BUY" else "LONG"
        p.pop("closePosition"); p["quantity"] = round_qty(symbol, qty)
    return _post("/fapi/v1/order", p)
