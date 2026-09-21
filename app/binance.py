"""純標準庫的 Binance Futures 客戶端。公開資料不需 key；下單需要。"""
import decimal, hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request
from . import config as C

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

def _mode_err(e):
    return "-4061" in str(e) or "-1106" in str(e)

def _post(path, params, method="POST"):
    params["timestamp"] = int(time.time() * 1000)
    q = urllib.parse.urlencode(params)
    sig = hmac.new(C.API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    base = C.TESTNET if C.USE_TESTNET else C.FAPI
    if method == "DELETE":
        req = urllib.request.Request(f"{base}{path}?{q}&signature={sig}", headers={"X-MBX-APIKEY": C.API_KEY}, method="DELETE")
    else:
        req = urllib.request.Request(f"{base}{path}", data=f"{q}&signature={sig}".encode(),
                                     headers={"X-MBX-APIKEY": C.API_KEY}, method=method)
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
_mode = dict(hedge=None, t=0)

def position_mode_hedge():
    """單向／雙向持倉。快取 5 分鐘：原本每張單都打一次，白白多一次 API 又多一個失敗點。"""
    if _mode["hedge"] is not None and time.time() - _mode["t"] < 300: return _mode["hedge"]
    h = bool(_get("/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"])
    _mode.update(hedge=h, t=time.time()); return h

def max_leverage(symbol):
    """這個交易對帳戶能用的最高槓桿。新子帳戶常被限制在 5x，小幣分層本身也可能低於 10x。"""
    try:
        d = _get("/fapi/v1/leverageBracket", dict(symbol=symbol), signed=True)
        rows = d if isinstance(d, list) else [d]
        return max(int(x.get("initialLeverage") or 0) for x in rows[0].get("brackets") or []) or None
    except Exception: return None

def set_leverage(symbol, lev):
    """回傳 (實際槓桿, 說明)。設不上就退到帳戶允許的最高值——沉默失敗會讓保證金與強平距離都跟預期不符。"""
    try:
        _post("/fapi/v1/leverage", dict(symbol=symbol, leverage=int(lev))); return int(lev), None
    except Exception as e:
        mx = max_leverage(symbol)
        if mx and mx < lev:
            try:
                _post("/fapi/v1/leverage", dict(symbol=symbol, leverage=int(mx)))
                return int(mx), f"該交易對上限 {mx}x，已改用 {mx}x（原設定 {lev}x）"
            except Exception: pass
        return None, f"設定槓桿失敗（{e}），沿用帳戶現有值"

def _mode_fields(p, side, reduce_only):
    """雙向模式要帶 positionSide 且不能帶 reduceOnly；單向相反。"""
    p.pop("positionSide", None); p.pop("reduceOnly", None)
    if position_mode_hedge(): p["positionSide"] = "SHORT" if (side == "SELL") != reduce_only else "LONG"
    elif reduce_only: p["reduceOnly"] = "true"
    return p

def _send_mode_safe(path, p, side, reduce_only):
    """共用帳號的持倉模式可能被別的專案切掉（快取還是舊的）→ 回 -4061/-1106 時重偵測、重送一次。"""
    try: return _post(path, _mode_fields(dict(p), side, reduce_only))
    except Exception as e:
        if not _mode_err(e): raise
        _mode.update(hedge=None, t=0)
        return _post(path, _mode_fields(dict(p), side, reduce_only))

def market_order(symbol, side, qty, reduce_only=False):
    p = dict(symbol=symbol, side=side, type="MARKET", quantity=round_qty(symbol, qty), newOrderRespType="RESULT")   # RESULT 才有 avgPrice
    return _send_mode_safe("/fapi/v1/order", p, side, reduce_only)

def side_of(p):
    """positionRisk 一筆的方向。雙向模式看 positionSide，單向看數量正負。"""
    ps = p.get("positionSide", "BOTH")
    return ps if ps in ("LONG", "SHORT") else ("LONG" if float(p["positionAmt"]) > 0 else "SHORT")

_algo_ok = [None]          # None=還不確定 True/False=已測知

def stop_order(symbol, side, qty, stop_price):
    """停損單。幣安 2025-12-09 起把條件單搬到 Algo 服務（舊端點回 -4120），
    參數也改名：stopPrice → triggerPrice，並要帶 algoType=CONDITIONAL。
    先打新端點，只有「端點不存在」才退回舊寫法，讓不同版本的正式網與模擬網都能運作。
    回傳的 dict 會有 orderId（相容舊欄位）與 via（algo / legacy），撤單要靠 via 決定端點。"""
    base = dict(symbol=symbol, side=side, type="STOP_MARKET",
                quantity=round_qty(symbol, qty), workingType="MARK_PRICE")
    px = round_price(symbol, stop_price)

    if _algo_ok[0] is not False:
        try:
            o = _send_mode_safe("/fapi/v1/algoOrder", dict(base, algoType="CONDITIONAL", triggerPrice=px), side, True)
            _algo_ok[0] = True
            o["orderId"] = o.get("algoId"); o["via"] = "algo"
            return o
        except Exception as e:
            msg = str(e)
            # 參數錯不代表端點不存在；只有 404 / 未知端點才退回舊寫法
            if not ("404" in msg or "-1013" in msg or "Unknown" in msg): raise
            _algo_ok[0] = False
    o = _send_mode_safe("/fapi/v1/order", dict(base, stopPrice=px), side, True)
    o["via"] = "legacy"
    return o

def gone(e):
    """撤單回『查無此單』= 已觸發或已撤，不是錯誤。"""
    s = str(e); return "-2011" in s or "Unknown order" in s or "-2013" in s

def cancel_order(symbol, order_id, via=None):
    """撤掉停損單。via 沒記錄時兩種端點都試一次。已不存在（-2011）回傳 None、不拋錯。"""
    errs = []
    for kind in ([via] if via else ["algo", "legacy"]):
        try:
            if kind == "algo": return _post("/fapi/v1/algoOrder", dict(symbol=symbol, algoId=order_id), method="DELETE")
            return _post("/fapi/v1/order", dict(symbol=symbol, orderId=order_id), method="DELETE")
        except Exception as e:
            if gone(e): return None
            errs.append(f"{kind}: {e}")
    raise RuntimeError("; ".join(errs))

def all_open_stops():
    """全帳號的條件單（不帶 symbol，權重 40）。只給自檢用，不要放進迴圈。"""
    rows = []
    for path, params in (("/fapi/v1/openAlgoOrders", dict(algoType="CONDITIONAL")), ("/fapi/v1/openOrders", {})):
        if path.startswith("/fapi/v1/openAlgo") and _algo_ok[0] is False: continue
        if path == "/fapi/v1/openOrders" and _algo_ok[0] is True: continue
        d = _get(path, params, signed=True)
        rows += d if isinstance(d, list) else (d.get("orders") or [])
    return [o for o in rows if order_type(o) in ("STOP_MARKET", "STOP", "TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TRAILING_STOP_MARKET")]

def order_type(o):
    """Algo 端點欄位叫 orderType，舊端點叫 type。兩個都看。"""
    return str(o.get("orderType") or o.get("type") or o.get("origType") or "").upper()

def open_stops(symbol):
    """這個幣掛著的條件單，回傳 (orders, ok)。
    ok=False 代表查詢本身失敗——呼叫端絕對不能把「查不到」當成「不存在」，
    這是 crypto-screener 誤平倉事件的根源。"""
    paths = []
    if _algo_ok[0] is not False: paths.append(("/fapi/v1/openAlgoOrders", dict(symbol=symbol, algoType="CONDITIONAL")))
    if _algo_ok[0] is not True: paths.append(("/fapi/v1/openOrders", dict(symbol=symbol)))
    out, ok = [], False
    for path, params in paths:
        try: d = _get(path, params, signed=True)
        except Exception: continue
        rows = d if isinstance(d, list) else (d.get("orders") if isinstance(d, dict) else None)
        if rows is None: continue
        ok = True
        out += [o for o in rows if order_type(o) in ("STOP_MARKET", "STOP", "TRAILING_STOP_MARKET")]
    return out, ok
