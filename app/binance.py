"""純標準庫的 Binance Futures 客戶端。公開資料不需 key；下單需要。"""
import contextlib, decimal, hashlib, hmac, json, re, threading, time, urllib.error, urllib.parse, urllib.request
from . import config as C

# ---- IP 權重與限流（清單第 6 條 r97）----
# 公開行情端點不用金鑰，但一樣算 IP 權重：K 線 limit=1500 一次 10，合約每分鐘上限 2400。
# 回測／事件掃描（sweep 子程序）連續抓 1 分 K，一分鐘內就能打滿 → 429；429 之後繼續打 → 418 封 IP，
# 同一個出口 IP 上的實盤（K 線、對帳、下單）會一起被封。
class RateLimited(RuntimeError):
    """幣安限流（429）。暫時性的——稍後再抓就有；**不能**當成「這段期間沒有資料」。
    訊息保留 "HTTP 429 …" 開頭，definite_reject() 照樣把被限流的下單判成「明確被拒」。"""

class IPBanned(RateLimited):
    """418：IP 被封。不重試；封鎖期間這個程序不再打 K 線（繼續打會延長封鎖）。"""

KL_CAP = 2400                  # 幣安合約每分鐘 IP 權重上限
KL_SOFT = KL_CAP // 2          # 批次抓 K 線（回測、事件掃描）用到一半就等下一分鐘，另一半留給實盤
KL_RETRY = 3                   # 429 照 Retry-After 等完重試的次數上限（批次才重試）
KL_WAIT_MAX = 120              # 單次最多等幾秒
_RL = dict(used=0, minute=-1, cool_until=0.0, ban_until=0.0, n429=0, n418=0, waits=0, waited=0.0, last="")
_KL_BATCH = threading.Lock()   # 批次 K 線排隊：一次一個請求（含等待）；實盤的 K 線不排這條隊，不會被研究端的等待卡住
_clock = time.time             # 測試換成虛擬時鐘

def _sleep(s): time.sleep(s)

def kline_weight(limit):
    """幣安合約 K 線的權重：limit <100 → 1、<500 → 2、≤1000 → 5、>1000 → 10。"""
    limit = int(limit)
    return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10

def _hdr(headers, name):
    try: return (headers or {}).get(name)
    except Exception: return None

def _note(headers, code=200):
    """每個幣安回應都記：標頭的已用權重；429 → 冷卻到 Retry-After（最多 120 秒）；418 → 封鎖到 Retry-After。
    下單、查部位的回應也記——它們被 429／418，K 線一樣要停。"""
    now = _clock()
    try: used = int(_hdr(headers, "X-MBX-USED-WEIGHT-1M"))
    except Exception: used = None
    if used is not None: _RL.update(used=used, minute=int(now // 60))
    if code not in (418, 429): return
    try: ra = float(_hdr(headers, "Retry-After") or 0)
    except Exception: ra = 0.0
    if code == 418:
        _RL["ban_until"] = max(_RL["ban_until"], now + (ra or KL_WAIT_MAX)); _RL["n418"] += 1
    else:
        _RL["cool_until"] = max(_RL["cool_until"], now + min(ra or 60.0, KL_WAIT_MAX)); _RL["n429"] += 1
    _RL["last"] = time.strftime("%m-%d %H:%M:%S", time.localtime(now)) + f" HTTP {code}"

def rate_status():
    """給網頁與自檢看的：最近一次回應的已用權重、冷卻／封鎖剩幾秒、累計 429／418 與等待。"""
    now = _clock()
    return dict(used=_RL["used"] if _RL["minute"] == int(now // 60) else 0, cap=KL_CAP, soft=KL_SOFT,
                cool=max(0, int(_RL["cool_until"] - now)), ban=max(0, int(_RL["ban_until"] - now)),
                n429=_RL["n429"], n418=_RL["n418"], waits=_RL["waits"], waited=round(_RL["waited"]), last=_RL["last"])

def _wait(s, why):
    s = max(0.0, min(float(s), KL_WAIT_MAX))
    _RL["waits"] += 1; _RL["waited"] += s
    _sleep(s)

def _open(req):
    """把 Binance 的錯誤內文帶出來，否則只看到 HTTP 400 不知道哪裡錯。429 → RateLimited、418 → IPBanned（都是 RuntimeError）。"""
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            _note(getattr(r, "headers", None))
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        _note(e.headers, e.code)
        try: msg = e.read().decode()[:300]
        except Exception: msg = ""
        if e.code == 418: raise IPBanned(f"HTTP 418 {msg}") from None
        if e.code == 429: raise RateLimited(f"HTTP 429 {msg}") from None
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

def definite_reject(e):
    """交易所明確拒絕（4xx 且帶錯誤碼）= 這張單一定沒成交。
    逾時、連線中斷、5xx、沒有錯誤碼的回應 = 結果不明，單可能已經成交（清單第 3 條 r12）。"""
    m = re.match(r"HTTP (\d+) (.*)", str(e), re.S)
    return bool(m) and 400 <= int(m.group(1)) < 500 and '"code"' in m.group(2)

def reduce_only_rejected(e):
    """交易所明確拒絕 reduceOnly 平倉單（-2022 ReduceOnly Order is rejected，清單第 16 條 r99）。
    同樣的單送幾次都一樣（交易所那邊的狀態卡住了）；跟逾時、5xx 這類暫時性錯誤分開。"""
    s = str(e)
    return "-2022" in s or "ReduceOnly Order is rejected" in s

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

def _kl_get(p, batch):
    """K 線請求（清單第 6 條 r97）。
    - 418 封鎖中：不送，直接 IPBanned。
    - 429 冷卻中：批次等到冷卻結束；實盤不等，直接 RateLimited（這輪沒抓到，下一輪再來）。
    - 批次：這一分鐘已用權重 + 這次的權重超過一半 → 等到下一分鐘再送；整個程序的批次請求排隊（_KL_BATCH）。
    - 429：批次照 Retry-After 等完重試，最多 KL_RETRY 次，還是被擋就拋 RateLimited——呼叫端不能當成沒資料。"""
    w = kline_weight(p.get("limit", 500))
    with (_KL_BATCH if batch else contextlib.nullcontext()):
        tries = 0
        while True:
            now = _clock()
            if now < _RL["ban_until"]:
                raise IPBanned(f"幣安 418 封鎖中（還要 {int(_RL['ban_until'] - now)} 秒），這段期間不打 K 線")
            if now < _RL["cool_until"]:
                if not batch:
                    raise RateLimited(f"幣安 429 冷卻中（還要 {int(_RL['cool_until'] - now)} 秒），這輪 K 線沒抓——不是沒有資料")
                _wait(_RL["cool_until"] - now + 0.5, "429"); continue
            if batch and _RL["minute"] == int(now // 60) and _RL["used"] + w > KL_SOFT:
                _wait(60 - now % 60 + 1, "soft"); continue
            try:
                return _get("/fapi/v1/klines", p)
            except IPBanned:
                raise
            except RateLimited as e:
                tries += 1
                if not batch or tries > KL_RETRY:
                    raise RateLimited(f"幣安限流，K 線重試 {tries - 1} 次仍被擋（暫時性，不是沒有資料）：{e}") from None

def klines(symbol, interval="5m", limit=500, start=None, end=None, batch=False):
    """batch=True：回測、事件掃描這類一次抓很多的（讓路給實盤、429 會等）；預設是實盤用的（不等）。"""
    p = dict(symbol=symbol, interval=interval, limit=min(limit, 1500))
    if start: p["startTime"] = int(start)
    if end: p["endTime"] = int(end)
    rows = _kl_get(p, batch)
    return [dict(t=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5]))
            for r in rows]

def klines_range(symbol, interval, start_ms, end_ms):
    """分頁抓完整區間，回測用（批次）。中途被限流 → 拋 RateLimited，不回「抓到一半」的資料。"""
    out, cur = [], start_ms
    while cur < end_ms:
        batch = klines(symbol, interval, 1500, cur, end_ms, batch=True)
        if not batch: break
        out.extend(batch)
        cur = batch[-1]["t"] + 1
        if len(batch) < 1500: break
        _sleep(0.2)
    return out

def wallet_balance(asset="USDT"):
    """錢包餘額（含未實現前的保證金），不是可用餘額。"""
    for x in _get("/fapi/v2/balance", signed=True):
        if x["asset"] == asset: return float(x["balance"])
    return 0.0

def open_positions():
    """全帳號未平倉的部位列。注意：維護或閘門異常時可能回 200 加空清單（清單第 2 條 r15）——
    全量表裡找不到某個部位時，不能直接當成「平倉了」，要用 position_rows(symbol) 逐幣再查。"""
    return [p for p in _get("/fapi/v2/positionRisk", signed=True) if abs(float(p["positionAmt"])) > 0]

def position_rows(symbol, include_zero=False):
    """單一幣的部位列（帶 symbol 查）。
    帶 symbol 查時交易所一定回這個幣的列（單向 1 列 BOTH、雙向 LONG/SHORT 2 列，數量 0 也回）。
    回空清單、或回傳裡沒有這個幣的列 = 查詢異常 → 拋例外，**不能當成數量 0**（清單第 2 條 r17）。
    include_zero=False 時只回有部位的列（呼叫端要的是「有哪些部位」時用）。"""
    d = _get("/fapi/v2/positionRisk", dict(symbol=symbol), signed=True)
    rows = [p for p in d if p.get("symbol") == symbol] if isinstance(d, list) else []
    if not rows:
        raise RuntimeError(f"positionRisk 逐幣查詢沒有回 {symbol} 的列（查詢異常，不能當成數量 0）")
    return rows if include_zero else [p for p in rows if abs(float(p["positionAmt"])) > 0]

def side_qty(symbol, side):
    """這個幣「這一側」在交易所上的總數量（含別人的，呼叫端要自己扣基準）。查詢失敗拋例外。"""
    return sum(abs(float(p["positionAmt"])) for p in position_rows(symbol) if side_of(p) == side)

# ---- 交易所精度（下單一定要照 stepSize / tickSize，否則 -1111）----
_F = {}

def filters(symbol):
    if not _F:
        base = C.TESTNET if C.USE_TESTNET else C.FAPI
        try: info = _get("/fapi/v1/exchangeInfo", base=base)
        except Exception: info = _get("/fapi/v1/exchangeInfo", base=C.FAPI)
        for sy in info["symbols"]:
            f = {x["filterType"]: x for x in sy["filters"]}
            mk = f.get("MARKET_LOT_SIZE") or {}
            maxes = [float(x) for x in (f["LOT_SIZE"].get("maxQty"), mk.get("maxQty")) if x and float(x) > 0]
            _F[sy["symbol"]] = dict(
                step=float(f["LOT_SIZE"]["stepSize"]), minqty=float(f["LOT_SIZE"]["minQty"]),
                # 市價單的數量上限：LOT_SIZE 與 MARKET_LOT_SIZE 取小的（市價單的通常小很多）。以前沒讀，小幣換算出的顆數超過上限就被 -4005 拒絕
                mmax=min(maxes) if maxes else None,
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

def cap_market_qty(symbol, qty):
    """市價單數量照交易所上限往下壓。回傳 (數量, 有沒有壓, 上限)。上限查不到就不壓（交易所會拒絕，照原本的錯誤處理）。"""
    mx = filters(symbol).get("mmax")
    if not mx or qty <= mx: return qty, False, mx
    return float(round_qty(symbol, mx)), True, mx

def round_price(symbol, price):
    return _fmt(price, filters(symbol)["tick"], down=False)

def user_trades(symbol, limit=1000, from_id=None):
    """成交明細（含 realizedPnl、commission、positionSide）。帶 from_id 時從那一筆往後（清單第 8 條 r35：
    不帶時只回最近 limit 筆，持倉期間成交一多，界線之後的平倉成交會掉出範圍）。"""
    p = dict(symbol=symbol, limit=limit)
    if from_id is not None: p["fromId"] = int(from_id)
    return _get("/fapi/v1/userTrades", p, signed=True)

# ---- 下單（testnet / live 由 USE_TESTNET 決定）----
_mode = dict(hedge=None, t=0)

def _detect_mode():
    """向交易所查持倉模式（不看快取），成功就寫進快取。"""
    h = bool(_get("/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"])
    _mode.update(hedge=h, t=time.time()); return h

def position_mode_hedge(strict=True):
    """單向／雙向持倉。快取 5 分鐘：原本每張單都打一次，白白多一次 API 又多一個失敗點。
    strict=False 給送單用：偵測失敗不能讓單送不出去（清單第 7 條 r8「偵測失敗就放棄」）——
    有舊值就用舊值，從沒偵測成功過就先假設單向；假設錯了會被 -4061/-1106 拒絕，再依被拒單反轉。"""
    if _mode["hedge"] is not None and time.time() - _mode["t"] < 300: return _mode["hedge"]
    try: return _detect_mode()
    except Exception:
        if strict: raise
        return _mode["hedge"] if _mode["hedge"] is not None else False

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

def _mode_fields(p, side, reduce_only, hedge=None):
    """雙向模式要帶 positionSide 且不能帶 reduceOnly；單向相反。hedge 沒指定時用快取／偵測。"""
    if hedge is None: hedge = position_mode_hedge(strict=False)
    p.pop("positionSide", None); p.pop("reduceOnly", None)
    if hedge: p["positionSide"] = "SHORT" if (side == "SELL") != reduce_only else "LONG"
    elif reduce_only: p["reduceOnly"] = "true"
    return p

def _send_mode_safe(path, p, side, reduce_only):
    """共用帳號的持倉模式可能被別的專案切掉 → 回 -4061/-1106 時重新偵測、重送一次（清單第 7 條 r8）。
    - 偵測失敗時，反轉的是「這張被拒的單送出時的假設」，不是快取：快取可能從沒偵測成功過（空值），
      也可能在送單後被別的執行緒改過（本服務有背景迴圈與網頁手動操作兩條執行緒）。
    - 重送成功才用結果更新快取；重送也失敗就清掉快取，不留沒驗證過的值。"""
    sent = _mode_fields(dict(p), side, reduce_only)
    try: return _post(path, sent)
    except Exception as e:
        if not _mode_err(e): raise
        sent_hedge = "positionSide" in sent                   # 被拒那張單的假設
        try: actual = _detect_mode()
        except Exception: actual = not sent_hedge
        try: o = _post(path, _mode_fields(dict(p), side, reduce_only, hedge=actual))
        except Exception:
            _mode.update(hedge=None, t=0); raise
        _mode.update(hedge=actual, t=time.time())
        return o

def market_order(symbol, side, qty, reduce_only=False):
    p = dict(symbol=symbol, side=side, type="MARKET", quantity=round_qty(symbol, qty), newOrderRespType="RESULT")   # RESULT 才有 avgPrice
    return _send_mode_safe("/fapi/v1/order", p, side, reduce_only)

FINAL = ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH")

def get_order(symbol, order_id):
    return _get("/fapi/v1/order", dict(symbol=symbol, orderId=order_id), signed=True)

LOG = print                      # 日誌（Zeabur 的執行紀錄）；測試換成收集器

def _avg(o):
    """回應裡的均價：avgPrice；沒有（欄位整個不存在或 0）但有成交額時，成交額 ÷ 成交量就是實際均價（U 本位 1 張 = 1 單位，清單第 15 條 r71）。"""
    a = float(o.get("avgPrice") or 0) or None
    if a: return a
    cq, ex = float(o.get("cumQuote") or 0), float(o.get("executedQty") or 0)
    return cq / ex if cq > 0 and ex > 0 else None

def fill_from_trades(symbol, oid, executed):
    """用單號在成交明細裡算加權均價。**數量要湊滿成交量**（r71）：成交明細是非同步寫入的，剛成交時常常只出現前幾筆——
    拿前幾筆算，得到的是前半段的價格，還會被當成實際成交價，比記未知更糟。沒湊滿或查不到 → None。"""
    try: tr = [t for t in user_trades(symbol) if str(t.get("orderId")) == str(oid)]
    except Exception as e: LOG(f"[{symbol}] 查成交明細失敗（單號 {oid}）：{e}"); return None
    q = sum(float(t["qty"]) for t in tr)
    if not q or q < executed * 0.999:
        LOG(f"[{symbol}] 成交明細只有 {q:g}/{executed:g}（單號 {oid}），當成還查不到"); return None
    return sum(float(t["price"]) * float(t["qty"]) for t in tr) / q

def confirm_fill(symbol, o, tries=3, wait=0.3, avg_tries=3, avg_wait=0.5):
    """市價單送出後確認成交（清單第 15 條）。回 dict(executed, avg, status, known, oid)。
    - 「成功」看 executedQty > 0，不是 HTTP 200；不是最終狀態就用單號查幾次
    - 查了仍不是最終狀態 → **撤掉那張單**再查一次拿最終成交量（r43）
    - known=False：查不到最終狀態（查詢失敗）→ 呼叫端當成結果不明
    - 均價（r71）：avgPrice → 成交額 ÷ 成交量 → 用單號查訂單（最多 avg_tries 次、每次寫日誌）→ 成交明細（數量要湊滿）；
      都沒有記 None（呼叫端排背景補登）。次數有上限：單已經成交，不能為了一個價格卡住流程。原始回應寫日誌、不進通知。"""
    st = o.get("status"); ex = float(o.get("executedQty") or 0); avg = _avg(o)
    oid = o.get("orderId")
    if not (st == "FILLED" and ex > 0):
        if oid is None: return dict(executed=ex, avg=avg, status=st, known=False, oid=oid)
        for _ in range(tries):
            if st in FINAL: break
            time.sleep(wait)
            try: q = get_order(symbol, oid); st, ex, avg = q.get("status"), float(q.get("executedQty") or 0), _avg(q)
            except Exception: pass
        if st not in FINAL:
            try: cancel_order(symbol, oid, "legacy")
            except Exception: pass                                   # 撤不掉（可能剛好成交了）→ 下面再查一次見分曉
            try: q = get_order(symbol, oid); st, ex, avg = q.get("status"), float(q.get("executedQty") or 0), _avg(q)
            except Exception: return dict(executed=ex, avg=avg, status=st, known=False, oid=oid)
    if ex > 0 and not avg and oid is not None:
        LOG(f"[{symbol}] 成交 {ex:g} 但回應沒有均價（單號 {oid}），原始回應：{o}")
        for i in range(1, avg_tries + 1):
            time.sleep(avg_wait)
            try:
                q = get_order(symbol, oid); avg = _avg(q)
                LOG(f"[{symbol}] 查訂單第 {i} 次（單號 {oid}）：{'均價 ' + format(avg, 'g') if avg else '仍沒有均價'}")
            except Exception as e: LOG(f"[{symbol}] 查訂單第 {i} 次（單號 {oid}）失敗：{e}")
            if avg: break
        if not avg: avg = fill_from_trades(symbol, oid, ex)
    return dict(executed=ex, avg=avg, status=st, known=st in FINAL or (st == "FILLED"), oid=oid)

def side_of(p):
    """positionRisk 一筆的方向。雙向模式看 positionSide，單向看數量正負。"""
    ps = p.get("positionSide", "BOTH")
    return ps if ps in ("LONG", "SHORT") else ("LONG" if float(p["positionAmt"]) > 0 else "SHORT")

_algo = dict(legacy_until=0.0)
LEGACY_RETRY_SEC = 600

def algo_active():
    """目前條件單走 Algo 端點嗎。退回舊端點只是暫時的（清單第 1 條 r12）。"""
    return time.time() >= _algo["legacy_until"]

def _is_404(e): return str(e).startswith("HTTP 404")

def stop_order(symbol, side, qty, stop_price):
    """停損單。幣安 2025-12-09 起把條件單搬到 Algo 服務（舊端點回 -4120），
    參數也改名：stopPrice → triggerPrice，並要帶 algoType=CONDITIONAL。
    退回舊端點的規則（清單第 1 條 r12）：
    - 只有 HTTP 404 才當成「Algo 端點不存在」；-1000（暫時性）、-1013（參數錯）等都照樣往上拋。
    - 退回只維持 LEGACY_RETRY_SEC 秒，之後重新試 Algo；一次誤判不能讓之後所有條件單永久走舊端點。
    - 舊端點回 -4120 = 交易所明確說要用 Algo → 立刻改回 Algo 重送這一張。
    回傳的 dict 會有 orderId（相容舊欄位）與 via（algo / legacy），撤單要靠 via 決定端點。"""
    base = dict(symbol=symbol, side=side, type="STOP_MARKET",
                quantity=round_qty(symbol, qty), workingType="MARK_PRICE")
    px = round_price(symbol, stop_price)

    def via_algo():
        o = _send_mode_safe("/fapi/v1/algoOrder", dict(base, algoType="CONDITIONAL", triggerPrice=px), side, True)
        o["orderId"] = o.get("algoId"); o["via"] = "algo"; return o

    def via_legacy():
        o = _send_mode_safe("/fapi/v1/order", dict(base, stopPrice=px), side, True)
        o["via"] = "legacy"; return o

    if algo_active():
        try: return via_algo()
        except Exception as e:
            if not _is_404(e): raise
            _algo["legacy_until"] = time.time() + LEGACY_RETRY_SEC
    try: return via_legacy()
    except Exception as e:
        if "-4120" not in str(e): raise
        _algo["legacy_until"] = 0.0
        return via_algo()

def gone(e):
    """撤單回『查無此單』= 已觸發或已撤，不是錯誤（清單第 13 條）。"""
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
    """全帳號的條件單（不帶 symbol，權重 40）。只給自檢用，不要放進迴圈。兩個端點都查。"""
    rows = []
    for path, params in (("/fapi/v1/openAlgoOrders", dict(algoType="CONDITIONAL")), ("/fapi/v1/openOrders", {})):
        try: d = _get(path, params, signed=True)
        except Exception:
            if path.startswith("/fapi/v1/openAlgo") and not algo_active(): continue
            raise
        rows += d if isinstance(d, list) else (d.get("orders") or [])
    return [o for o in rows if order_type(o) in ("STOP_MARKET", "STOP", "TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TRAILING_STOP_MARKET")]

def order_type(o):
    """Algo 端點欄位叫 orderType，舊端點叫 type。兩個都看。"""
    return str(o.get("orderType") or o.get("type") or o.get("origType") or "").upper()

def open_stops(symbol):
    """這個幣掛著的停損單，回傳 (orders, ok)。兩個端點都查（退回舊端點期間兩邊都可能有單）。
    ok=False 代表查詢本身失敗——呼叫端絕對不能把「查不到」當成「不存在」，
    這是 crypto-screener 誤平倉事件的根源。Algo 查詢失敗只有在「目前暫時走舊端點」時才可以忽略。"""
    out, ok_algo, ok_legacy = [], False, False
    for path, params in (("/fapi/v1/openAlgoOrders", dict(symbol=symbol, algoType="CONDITIONAL")),
                         ("/fapi/v1/openOrders", dict(symbol=symbol))):
        try: d = _get(path, params, signed=True)
        except Exception as e:
            # 查詢也回 404 = Algo 端點不存在 → 跟掛單一樣暫時改走舊端點，改用舊端點的查詢結果判斷（清單第 1 條 r15）。
            # 只靠掛單時設定的話，服務重啟後還沒掛過新停損，守衛會每輪「查詢失敗、跳過」，永遠不檢查。
            if path.startswith("/fapi/v1/openAlgo") and _is_404(e):
                _algo["legacy_until"] = max(_algo["legacy_until"], time.time() + LEGACY_RETRY_SEC)
            continue
        rows = d if isinstance(d, list) else (d.get("orders") if isinstance(d, dict) else None)
        if rows is None: continue
        if path.startswith("/fapi/v1/openAlgo"): ok_algo = True
        else: ok_legacy = True
        # 移動停利不算「停損還在」：要到啟動價才生效、通常只涵蓋部分數量（清單第 7 條，r5）
        out += [o for o in rows if order_type(o) in ("STOP_MARKET", "STOP")]
    return out, ok_legacy and (ok_algo or not algo_active())
