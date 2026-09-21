"""測試用的模擬幣安（在 HTTP 層攔截 urlopen，讓 app/binance.py 的程式碼真的跑過）。

清單用法第 5 點（r12）要求的行為：
- 條件單送到舊端點 /fapi/v1/order → -4120
- 單向模式：每個幣只有一列 BOTH，空單 positionAmt 是負數
- 雙向模式：LONG / SHORT 兩列，SHORT 的 positionAmt 是負數；平倉超量被拒
- 送錯模式參數 → -4061（帶/沒帶 positionSide 不符）或 -1106（雙向帶 reduceOnly）
- positionRisk 帶 symbol 時只回那個幣；可注入「200 加空清單」
- algo="404" 模擬沒有 Algo 服務的環境：Algo 端點全部 404、舊端點收條件單
- positionRisk 帶 symbol 時，一定回這個幣的列（單向 1 列 BOTH、雙向 LONG/SHORT 2 列），數量 0 也回（清單第 2 條 r17）
- 列上有 markPrice 與 unRealizedProfit（照這一列的合併均價算，含同側別人的部位）
- 突變：環境變數 PDH_MUTATE=no_base → 逐幣查詢一律回空清單（清單用法第 5 點 r18：驗證前提斷言有沒有空跑）
- 成交價 ≠ 標記價（清單用法第 5 點 r30）：市價單成交價加滑價（買貴 slip、賣便宜 slip），部位均價＝成交價的加權平均，
  標記價＝self.price。兩者一樣時，測試分不出程式用的是成交價還是標記價。
- 成交明細（userTrades）開倉、平倉都有，每筆有遞增 id、時間、手續費——跟真的一樣；
  有筆數上限（預設 500、最多 1000），帶 fromId 時從那筆往後；交易所時鐘可以比本機慢（clock_offset_ms）
另外可以注入：逾時、5xx、指定錯誤碼、「成交了但回應丟失」。
"""
import json, os, socket, time, urllib.error, urllib.parse, urllib.request


class _Resp:
    def __init__(self, d): self.d = d
    def read(self): return json.dumps(self.d).encode()
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _HTTPErr(urllib.error.HTTPError):
    def __init__(self, url, code, body):
        super().__init__(url, code, "err", {}, None); self._b = body
    def read(self): return self._b.encode()


class FakeBinance:
    def __init__(self, hedge=False, algo="ok", price=1.0):
        self.hedge = hedge
        self.algo = algo                 # "ok" / "404"
        self.price = price
        self.slip = 0.001                # 市價單滑價比例
        self.fee = 0.0004                # 手續費率
        self.tid = 0
        self.clock_offset_ms = 0         # 交易所時鐘比本機快（正）或慢（負）幾毫秒（清單第 8 條 r36）
        self.pos = {}                    # (symbol, "LONG"/"SHORT") -> [qty>0, entry]
        self.algo_orders = {}            # algoId -> dict
        self.legacy_orders = {}          # orderId -> dict（只有 algo="404" 的環境才收）
        self.next_id = 1000
        self.inject = []                 # [dict(path=, method=None, match=None, times=1, kind=, code=, body=, then_execute=False)]
        self.calls = []                  # (method, path, params)
        self.trades = []
        self.mutate = os.environ.get("PDH_MUTATE", "")
        self.fired = []                  # 注入觸發紀錄
        self._traced = {}                # 經過成交（_add／_reduce）或 open() 設定的部位數量；跟 self.pos 不一致 = 測試直接改了數量、沒留成交
        self.trade_queries = []          # 每次查成交明細：{symbol, untraced}（清單用法第 5 點 r33：歸零不留成交）
        self.mut_hits = 0                # 突變命中次數（被突變的查詢在這個情境被呼叫了幾次）

    # ---------- 狀態輔助 ----------
    def open(self, symbol, side, qty, entry=None):
        self.pos[(symbol, side)] = [float(qty), entry or self.price]
        self._traced[(symbol, side)] = float(qty)

    def untraced(self, symbol):
        """這個幣的部位數量，是不是被測試直接改過（沒有留下成交）。"""
        keys = {k for k in list(self.pos) + list(self._traced) if k[0] == symbol}
        return any(abs(self.qty(*k) - self._traced.get(k, 0.0)) > 1e-9 for k in keys)

    def trigger(self, symbol, side, qty, px=None):
        """交易所端的出場（停損觸發、App 手動平倉、ADL）：真的成交一筆，留下成交明細。
        取代「直接把部位數量改掉」——那樣不會留成交，程式查成交明細時只能記未知（清單用法第 5 點 r33）。"""
        self._reduce(symbol, side, qty, self.price if px is None else px)
        return self.trades[-1]

    def qty(self, symbol, side):
        return self.pos.get((symbol, side), [0, 0])[0]

    def _row(self, s, ps, amt, entry):
        upnl = (self.price - entry) * amt if amt else 0.0          # amt 帶正負號
        return dict(symbol=s, positionSide=ps, positionAmt=str(amt), entryPrice=str(entry if amt else 0),
                    markPrice=str(self.price), unRealizedProfit=str(round(upnl, 8)))

    def rows(self, symbol=None):
        """全量表：只列有部位的（程式本來就會濾掉 0）。逐幣：一定回這個幣的列，數量 0 也回。"""
        out = []
        syms = [symbol] if symbol else sorted({s for s, _ in self.pos})
        for s in syms:
            L, S = self.qty(s, "LONG"), self.qty(s, "SHORT")
            if self.hedge:
                for side, q in (("LONG", L), ("SHORT", S)):
                    if q or symbol:
                        out.append(self._row(s, side, q if side == "LONG" else -q, self.pos.get((s, side), [0, 0])[1]))
            else:
                net = L - S
                if net or symbol:
                    side = "LONG" if net > 0 else "SHORT"
                    out.append(self._row(s, "BOTH", net, self.pos.get((s, side), [0, 0])[1]))
        return out

    # ---------- 安裝 ----------
    def install(self):
        urllib.request.urlopen = self._urlopen
        FakeBinance.current = self       # 測試的 check() 用來印出這個情境的突變命中次數
        return self

    def _err(self, url, code, body): raise _HTTPErr(url, code, body)

    def _urlopen(self, req, timeout=None):
        url = req.full_url
        path = url.split("?")[0].split(".com", 1)[-1]
        method = req.get_method()
        raw = (req.data or b"").decode() or (url.split("?", 1)[1] if "?" in url else "")
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        self.calls.append((method, path, params))
        # 突變命中：被突變的函式（逐幣部位查詢）在這個情境被「呼叫」了幾次——不管最後是注入還是突變回的（清單用法第 5 點 r23）。
        # 只算突變分支實際執行的次數會漏掉「情境自己的注入先回了空清單」的項目，把它們錯判成「無關」。
        if self.mutate == "no_base" and path == "/fapi/v2/positionRisk" and "symbol" in params: self.mut_hits += 1

        for rule in list(self.inject):
            if rule["path"] != path: continue
            if rule.get("method") and rule["method"] != method: continue
            if rule.get("match") and not rule["match"](params): continue
            # 記錄每次注入是被哪一張請求觸發的（清單用法第 5 點第 18 種：注入條件太寬，會在被測那一步之前就生效）
            what = " ".join(f"{k}={params[k]}" for k in ("symbol", "side", "type", "reduceOnly", "positionSide", "quantity", "algoId") if k in params)
            self.fired.append(dict(n=len(self.calls), path=path, method=method, kind=rule["kind"], what=what))
            if os.environ.get("PDH_INJECT_LOG"):
                print(f"      〔注入觸發〕第 {len(self.calls)} 個請求 {method} {path} {what} → {rule['kind']} {rule.get('code', '')}")
            rule["times"] -= 1
            if rule["times"] <= 0: self.inject.remove(rule)
            if rule.get("then_execute"):                       # 交易所端執行了，但回應在路上丟了
                self._route(url, path, method, params)
            if rule["kind"] == "timeout": raise socket.timeout("timed out")
            if rule["kind"] == "empty": return _Resp([])      # 200 加空清單（維護、閘門異常，清單第 2 條 r15）
            self._err(url, rule["code"], rule.get("body", ""))

        return _Resp(self._route(url, path, method, params))

    # ---------- 路由 ----------
    def _route(self, url, path, method, p):
        if path == "/fapi/v1/positionSide/dual": return {"dualSidePosition": self.hedge}
        if path == "/fapi/v2/positionRisk":
            if "symbol" in p:
                if self.mutate == "no_base": return []                 # 突變：逐幣查詢回空清單（命中次數在上面算）
                return self.rows(p["symbol"])
            return self.rows()
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [dict(symbol=s, filters=[
                dict(filterType="LOT_SIZE", stepSize="1", minQty="1"),
                dict(filterType="PRICE_FILTER", tickSize="0.0001"),
                dict(filterType="MIN_NOTIONAL", notional="5")]) for s in ("XUSDT", "YUSDT")]}
        if path == "/fapi/v1/leverage": return {"leverage": int(p.get("leverage", 1))}
        if path == "/fapi/v1/userTrades":
            self.trade_queries.append(dict(symbol=p.get("symbol"), untraced=self.untraced(p.get("symbol"))))
            rows = [t for t in self.trades if t["symbol"] == p.get("symbol")]
            limit = min(int(p.get("limit", 500)), 1000)             # 真的幣安：預設 500、最多 1000 筆
            if "fromId" in p: return [t for t in rows if t["id"] >= int(p["fromId"])][:limit]   # 帶 fromId：從那筆往後
            return rows[-limit:]                                     # 不帶：最近 limit 筆
        if path == "/fapi/v1/openAlgoOrders":
            if self.algo == "404": self._err(url, 404, "Not Found")
            return [o for o in self.algo_orders.values() if o["symbol"] == p.get("symbol", o["symbol"])]
        if path == "/fapi/v1/openOrders":
            return [dict(o) for o in self.legacy_orders.values() if o["symbol"] == p.get("symbol", o["symbol"])]
        if path == "/fapi/v1/algoOrder":
            if self.algo == "404": self._err(url, 404, "Not Found")
            if method == "DELETE":
                aid = int(p["algoId"])
                if aid not in self.algo_orders: self._err(url, 400, '{"code":-2011,"msg":"Unknown order sent."}')
                return self.algo_orders.pop(aid)
            self._check_mode(url, p, reduce=True)
            self.next_id += 1
            o = dict(algoId=self.next_id, symbol=p["symbol"], side=p["side"], orderType=p["type"],
                     triggerPrice=p.get("triggerPrice"), quantity=p.get("quantity"), positionSide=p.get("positionSide", "BOTH"))
            self.algo_orders[self.next_id] = o
            return dict(o)
        if path == "/fapi/v1/order":
            if method == "DELETE":
                oid = int(p.get("orderId", 0))
                if oid not in self.legacy_orders: self._err(url, 400, '{"code":-2011,"msg":"Unknown order sent."}')
                return self.legacy_orders.pop(oid)
            if p.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"):
                if self.algo != "404":      # 真實幣安：條件單只收 Algo 端點
                    self._err(url, 400, '{"code":-4120,"msg":"Order type not supported for this endpoint. Please use the Algo Order API endpoints instead."}')
                self._check_mode(url, p, reduce=True)          # 模擬「沒有 Algo 服務的環境」：舊端點收條件單
                self.next_id += 1
                o = dict(orderId=self.next_id, symbol=p["symbol"], side=p["side"], type=p["type"],
                         stopPrice=p.get("stopPrice"), origQty=p.get("quantity"), positionSide=p.get("positionSide", "BOTH"))
                self.legacy_orders[self.next_id] = o
                return dict(o)
            return self._market(url, p)
        return {}

    def _check_mode(self, url, p, reduce):
        has_ps = "positionSide" in p
        if self.hedge and not has_ps: self._err(url, 400, '{"code":-4061,"msg":"Order\'s position side does not match user\'s setting."}')
        if not self.hedge and has_ps: self._err(url, 400, '{"code":-4061,"msg":"Order\'s position side does not match user\'s setting."}')
        if self.hedge and p.get("reduceOnly"): self._err(url, 400, '{"code":-1106,"msg":"Parameter \'reduceonly\' sent when not required."}')

    def _market(self, url, p):
        self._check_mode(url, p, reduce=False)
        s, side, q = p["symbol"], p["side"], float(p["quantity"])
        px = round(self.price * (1 + self.slip if side == "BUY" else 1 - self.slip), 10)   # 成交價 ≠ 標記價
        if self.hedge:
            ps = p["positionSide"]
            closing = (ps == "LONG" and side == "SELL") or (ps == "SHORT" and side == "BUY")
            if closing:
                have = self.qty(s, ps)
                if q > have + 1e-9: self._err(url, 400, '{"code":-2022,"msg":"ReduceOnly Order is rejected."}')
                self._reduce(s, ps, q, px); return dict(status="FILLED", avgPrice=str(px), executedQty=str(q))
            self._add(s, ps, q, px); return dict(status="FILLED", avgPrice=str(px), executedQty=str(q))
        # 單向
        L, S = self.qty(s, "LONG"), self.qty(s, "SHORT")
        net = L - S
        if p.get("reduceOnly"):
            if (side == "SELL" and net <= 0) or (side == "BUY" and net >= 0):
                self._err(url, 400, '{"code":-2022,"msg":"ReduceOnly Order is rejected."}')
            q = min(q, abs(net))                              # 單向 reduceOnly 會被截到部位大小
            self._reduce(s, "LONG" if net > 0 else "SHORT", q, px)
            return dict(status="FILLED", avgPrice=str(px), executedQty=str(q))
        self._add(s, "LONG" if side == "BUY" else "SHORT", q, px)
        return dict(status="FILLED", avgPrice=str(px), executedQty=str(q))

    def _trade(self, s, side, q, px, pnl, pos_side):
        self.tid += 1
        self.trades.append(dict(id=self.tid, symbol=s, side=side, time=int(time.time() * 1000) + self.clock_offset_ms, qty=str(q), price=str(px),
                                realizedPnl=str(round(pnl, 10)), commission=str(round(px * q * self.fee, 10)),
                                positionSide=pos_side if self.hedge else "BOTH"))   # 真的幣安成交明細有這個欄位

    def _add(self, s, side, q, px):
        cur = self.pos.get((s, side), [0, px])
        avg = (cur[0] * cur[1] + q * px) / (cur[0] + q)                   # 加權平均（真實交易所的 entryPrice）
        self.pos[(s, side)] = [cur[0] + q, avg]
        self._traced[(s, side)] = self._traced.get((s, side), 0.0) + q
        self._trade(s, "BUY" if side == "LONG" else "SELL", q, px, 0.0, side)   # 開倉成交也在明細裡

    def _reduce(self, s, side, q, px):
        cur = self.pos[(s, side)]
        pnl = (px - cur[1]) * q * (1 if side == "LONG" else -1)
        self._trade(s, "SELL" if side == "LONG" else "BUY", q, px, pnl, side)
        cur[0] -= q
        self._traced[(s, side)] = self._traced.get((s, side), 0.0) - q
        if cur[0] <= 1e-9: del self.pos[(s, side)]
