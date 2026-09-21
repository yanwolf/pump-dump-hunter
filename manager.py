"""實盤出場管理：逐根收盤 K 棒重現 backtest.simulate 的出場規則，讓實盤跟回測是同一套策略。

分工：
- 停損觸發 → 交易所上的停損單（不靠輪詢，插針也能即時出場）
- 1R 減碼一半 / 保本 / 追蹤停損 / 時間出場 / 出場後冷卻 → 這裡，每輪迴圈跑一次
規則順序、判斷條件和 backtest.simulate 一模一樣；改其中一邊，另一邊要一起改。
"""
import time
import binance as B, config as C, store, telegram
from signals import ENGINE_TF

TF_MS = {"1m": 60_000, "5m": 300_000}

def tf_of(eid): return ENGINE_TF.get(eid, "5m")
def rules(eid): return {**C.RISK, **C.EXIT.get(eid, {})}
def _now(): return int(time.time() * 1000)
def _log(msg): store.push("errors", f"{time.strftime('%m-%d %H:%M')} {msg}")

def closed_bars(sym, tf, limit=150):
    """只留已收盤的 K 棒（Binance 最後一根是進行中的）。"""
    cut = _now()
    return [b for b in B.klines(sym, tf, limit + 1) if b["t"] + TF_MS[tf] <= cut]

def cooling(sym, eid):
    """回測：同引擎出場後 cooldown_bars 根內不再進場。"""
    t = store.get().get("cool", {}).get(f"{sym}:{eid}")
    return bool(t) and _now() - t < rules(eid)["cooldown_bars"] * TF_MS[tf_of(eid)]

def close_info(sym, pos, since_ms=None):
    """從成交明細抓整筆交易的平均出場價、已實現損益（扣手續費）、R。含減碼那一半。"""
    try:
        side = "SELL" if pos.get("side") == "LONG" else "BUY"
        since = since_ms or pos.get("ts") or _now() - 3 * 86_400_000
        tr = [t for t in B.user_trades(sym) if t["side"] == side and t["time"] >= since and float(t.get("realizedPnl") or 0) != 0]
        if not tr: return {}
        q = sum(float(t["qty"]) for t in tr)
        px = sum(float(t["price"]) * float(t["qty"]) for t in tr) / q
        pnl = sum(float(t["realizedPnl"]) for t in tr) - sum(float(t.get("commission") or 0) for t in tr)
        out = dict(exit=float(f"{px:.6g}"), pnl=round(pnl, 2))
        if pos.get("risk_usdt"): out["r"] = round(pnl / pos["risk_usdt"], 2)
        return out
    except Exception as e:
        _log(f"{sym} 抓出場價失敗 {e}"); return {}

def record_close(sym, pos, by, info=None):
    """持倉結束：撤掉殘留停損單（避免之後誤平到別的專案同幣的倉）、寫已平倉、記冷卻。"""
    if pos.get("stop_id"):
        try: B.cancel_order(sym, pos["stop_id"])
        except Exception: pass                      # 已觸發或已撤銷
    info = close_info(sym, pos) if info is None else info
    rec = dict(pos, symbol=sym, closed=time.strftime("%m-%d %H:%M"), by=by, **info)
    store.push("closed", rec)
    cool = dict(store.get().get("cool", {})); cool[f"{sym}:{pos.get('engine')}"] = _now(); store.update(cool=cool)
    return rec

def close_now(sym, pos, by):
    """市價平掉剩餘部位並記帳。"""
    is_long = pos["side"] == "LONG"
    o = B.market_order(sym, "SELL" if is_long else "BUY", pos["qty"], reduce_only=True)
    px = float(o.get("avgPrice") or 0) or None
    info = dict(exit=px, pnl=round((px - pos["entry"]) * pos["qty"] * (1 if is_long else -1), 2) if px else None)
    time.sleep(1)
    info.update({k: v for k, v in close_info(sym, pos).items() if v is not None})
    rec = record_close(sym, pos, by, info)
    telegram.send(f"🏁 {sym} 引擎{pos.get('engine')} {by}出場" +
                  (f" @ {rec['exit']:.6g}，損益 {rec['pnl']:+.2f} U" if rec.get("exit") and rec.get("pnl") is not None else "") +
                  (f"（{rec['r']:+.2f}R）" if rec.get("r") is not None else ""))
    return rec

def move_stop(sym, pos, new_stop, qty=None):
    """先掛新停損、再撤舊的，中間不會有沒保護的空窗。"""
    is_long = pos["side"] == "LONG"
    qty = qty or pos["qty"]
    o = B.stop_order(sym, "SELL" if is_long else "BUY", qty, new_stop)
    old = pos.get("stop_id")
    if old:
        try: B.cancel_order(sym, old)
        except Exception as e: _log(f"{sym} 撤舊停損失敗 {e}")
    pos.update(stop=new_stop, stop_id=o.get("orderId"))

def step(sym, pos):
    """處理這筆持倉自上次以來新收盤的 K 棒。回傳 "closed" 或 None（pos 會就地更新）。"""
    eid = pos.get("engine"); R = rules(eid); tf = tf_of(eid); ms = TF_MS[tf]
    d = 1 if pos["side"] == "LONG" else -1
    start = pos.get("bar_t") or (pos.get("ts", _now()) // ms) * ms       # 訊號棒 = 回測的 t["i"]
    r_unit = pos.get("r_unit") or abs(pos["entry"] - pos["stop"])
    if not r_unit: return None
    k = closed_bars(sym, tf, min(R["max_hold_bars"] + R["trail_bars"] + 10, 1400))
    for j, bar in enumerate(k):
        if bar["t"] <= pos.get("last_t", start): continue
        pos["last_t"] = bar["t"]
        n = (bar["t"] - start) // ms                                     # = 回測的 i - t["i"]
        fav = bar["h"] if d > 0 else bar["l"]
        r_now = d * (fav - pos["entry"]) / r_unit
        try:
            if R["tp1_r"] is not None and not pos.get("tp1") and r_now >= R["tp1_r"]:
                half = float(B.round_qty(sym, pos["qty"] / 2))
                B.market_order(sym, "SELL" if d > 0 else "BUY", half, reduce_only=True)
                pos["qty"] = round(pos["qty"] - half, 8); pos["tp1"] = True; pos["state"] = "已減碼"
                move_stop(sym, pos, pos["entry"])
                telegram.send(f"✂️ {sym} 引擎{eid} 到 {R['tp1_r']}R 減碼一半，停損移到成本 {pos['entry']:.6g}")
            elif R["tp1_r"] is None and not pos.get("be") and R.get("be_r") and r_now >= R["be_r"]:
                pos["be"] = True; pos["state"] = "保本"
                move_stop(sym, pos, pos["entry"])
                telegram.send(f"🛡 {sym} 引擎{eid} 到 {R['be_r']}R，停損移到成本 {pos['entry']:.6g}")
            elif n >= R["max_hold_bars"]:
                close_now(sym, pos, "時間"); return "closed"
            elif (pos.get("tp1") or pos.get("be")) and r_now >= R["trail_after_r"] and j >= R["trail_bars"]:
                seg = k[j - R["trail_bars"]:j + 1]
                ns = max(pos["stop"], min(x["l"] for x in seg)) if d > 0 else min(pos["stop"], max(x["h"] for x in seg))
                if ns != pos["stop"]:
                    move_stop(sym, pos, ns); pos["state"] = "追蹤"
        except Exception as e:
            msg = str(e)
            if "-2021" in msg:                       # 新停損價已經被穿過 = 本來就該停損了
                close_now(sym, pos, "停損"); return "closed"
            _log(f"{sym} 出場管理 {msg}")
            telegram.send(f"⚠️ {sym} 引擎{eid} 出場管理失敗：{msg}（原停損單仍在）")
            return None
    return None

def run():
    """每輪迴圈呼叫一次（在 reconcile 之後，已被交易所停損掉的倉不會進來）。"""
    for sym in list(store.get().get("open", {})):
        pos = dict(store.get().get("open", {}).get(sym) or {})
        if not pos: continue
        try: res = step(sym, pos)
        except Exception as e: _log(f"{sym} 出場管理 {e}"); continue
        own = dict(store.get().get("open", {}))
        if res == "closed": own.pop(sym, None)
        elif sym in own: own[sym] = pos
        store.update(open=own)
