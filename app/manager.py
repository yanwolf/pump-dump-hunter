"""實盤出場管理：逐根收盤 K 棒重現 backtest.simulate 的出場規則，讓實盤跟回測是同一套策略。

分工：
- 停損觸發 → 交易所上的停損單（不靠輪詢，插針也能即時出場）
- 1R 減碼一半 / 保本 / 追蹤停損 / 時間出場 / 出場後冷卻 → 這裡，每輪迴圈跑一次
規則順序、判斷條件和 backtest.simulate 一模一樣；改其中一邊，另一邊要一起改。
"""
import time
from . import binance as B, config as C, store, telegram
from .signals import ENGINE_TF

TF_MS = {"1m": 60_000, "5m": 300_000}

def tf_of(eid): return ENGINE_TF.get(eid, "5m")
def rules(eid): return {**C.RISK, **C.EXIT.get(eid, {})}
def _now(): return int(time.time() * 1000)
def _log(msg): store.push("errors", f"{time.strftime('%m-%d %H:%M')} {msg}")

def nag(n):
    """需要人處理的持續狀態，提醒節奏三專案統一（清單第 8 條 r8）：第 1、5、30 次，之後第 30 次起每隔 120 次
    （150、270、390…）。本專案迴圈 60 秒一輪（POLL_SEC），120 次約 2 小時。"""
    return n in (1, 5, 30) or (n > 30 and (n - 30) % 120 == 0)

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
    queued = set(store.get().get("leftover", {}))
    for oid, via in [(pos.get("stop_id"), pos.get("stop_via"))] + [tuple(x) for x in pos.get("stale_ids", [])]:
        if not oid or str(oid) in queued: continue       # 已在待撤清單的交給 sweep_leftovers，不重複告警
        try: B.cancel_order(sym, oid, via)          # 已觸發／已撤（-2011）在 cancel_order 裡視為正常
        except Exception as e:
            queue_leftover(sym, oid, via, e)
    # 失敗中的狀態隨部位平倉結束 → 收尾通知，不能無聲消失（清單第 8 條 r11）
    open_fail = [f"移損到 {pos.get('want_stop'):.6g} 失敗 {pos['want_fail']} 次" if pos.get("want_fail") and pos.get("want_stop") is not None else None,
                 f"補掛停損失敗 {pos['guard_fail']} 次" if pos.get("guard_fail") else None]
    open_fail = [x for x in open_fail if x]
    if open_fail:
        telegram.send(f"ℹ️ {sym} 引擎{pos.get('engine')} 已平倉（{by}），" + "、".join(open_fail) + " 的狀態隨平倉結束")
    _missing.pop(sym, None)                          # 補掛連續次數不能留給同幣下一筆（清單第 8 條 r12）
    info = close_info(sym, pos) if info is None else info
    rec = dict(pos, symbol=sym, closed=time.strftime("%m-%d %H:%M"), by=by, **info)
    store.push("closed", rec)
    cool = dict(store.get().get("cool", {})); cool[f"{sym}:{pos.get('engine')}"] = _now(); store.update(cool=cool)
    return rec

class CloseFailed(Exception):
    """平倉沒完成；close_now 已計數並依節奏告警，部位與停損都保留。"""

def remaining(sym, side, base=0.0):
    """交易所上「自己的」這一側還剩多少：這一側總數扣掉送單前就有的基準數量（清單第 3 條 r14、第 7 條 r15）。
    - 帶 symbol 逐幣查，不用全量表：全量表可能回 200 加空清單（清單第 2 條 r15）。
    - 查詢失敗回 None——不能當成 0（清單第 2 條）。"""
    try: total = B.side_qty(sym, side)
    except Exception: return None
    return max(0.0, round(total - (base or 0.0), 10))

def close_now(sym, pos, by):
    """市價平掉剩餘部位並記帳。平倉單送出後一定看結果（清單第 8 條 r12）：
    - 不管回應成功、被拒、逾時，都再查一次交易所部位；確實沒了才記帳、撤停損。
    - 還有剩（數量不符被拒、部分成交）→ 用交易所實際數量重送一次。
    - 還是沒平掉 → 保留部位與停損、依節奏告警、拋 CloseFailed；呼叫端每輪會再試（want_close）。"""
    side = pos["side"]; close_side = "SELL" if side == "LONG" else "BUY"
    base = pos.get("base_qty") or 0.0
    px, err = None, None
    # 送單前先確認自己還有多少（清單第 7 條 r15）：扣掉基準後沒有了就不送——
    # 單向共用帳號裡同側有別人的部位時，reduceOnly 單會把別人的平掉。數量取「交易所這一側 − 基準」與帳上的小者。
    left = remaining(sym, side, base)
    if left is None: qty = None
    elif left == 0: qty = 0
    else: qty = min(pos["qty"], left)
    for attempt in (1, 2):
        if not qty: break                            # 查不到（None）或自己已經沒了（0）→ 不送單
        try:
            o = B.market_order(sym, close_side, qty, reduce_only=True)
            px = float(o.get("avgPrice") or 0) or px
        except Exception as e: err = e
        time.sleep(0.5)                              # 讓交易所的部位表跟上
        left = remaining(sym, side, base)
        if left is None or left == 0: break
        if attempt == 1 and left != qty: qty = min(left, pos["qty"]); continue   # 部分成交或帳實不符 → 用自己的實際剩餘重送
        if attempt == 1 and err is not None and not B.definite_reject(err): continue   # 逾時／5xx 且數量沒變 → 再送一次
        break
    if left != 0:
        if left: pos["qty"] = left
        pos["want_close"] = by
        n = pos["close_fail"] = pos.get("close_fail", 0) + 1
        why = "無法確認交易所部位" if left is None else f"交易所上還有 {left:g}"
        if nag(n):
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} {by}平倉未完成（第 {n} 次，{why}"
                          + (f"，{str(err)[:100]}" if err else "") + "）— 部位與停損都保留，每輪重試")
        raise CloseFailed(why)

    was = pos.pop("close_fail", 0); pos.pop("want_close", None)
    info = dict(exit=px, pnl=round((px - pos["entry"]) * pos["qty"] * (1 if side == "LONG" else -1)
                                   + sum(x.get("pnl", 0) for x in pos.get("partials", [])), 2) if px else None)
    info.update({k: v for k, v in close_info(sym, pos).items() if v is not None})   # 成交明細優先，分段加總
    rec = record_close(sym, pos, by, info)
    own = dict(store.get().get("open", {})); own.pop(sym, None); store.update(open=own)
    telegram.send(f"🏁 {sym} 引擎{pos.get('engine')} {by}出場" +
                  (f" @ {rec['exit']:.6g}，損益 {rec['pnl']:+.2f} U" if rec.get("exit") and rec.get("pnl") is not None else "") +
                  (f"（{rec['r']:+.2f}R）" if rec.get("r") is not None else "") +
                  (f"（先前平倉失敗 {was} 次，已恢復）" if was >= 1 else ""))
    return rec

def retry_close(sym, pos):
    """上次平倉沒完成 → 每輪重試，直到交易所上確實沒有這個部位。"""
    if not pos.get("want_close"): return None
    try: close_now(sym, pos, pos["want_close"]); return "closed"
    except CloseFailed: return "pending"

class StopMoveFailed(Exception):
    """移損失敗；move_stop 已計數並依節奏告警，呼叫端不要再發告警。"""

def move_stop(sym, pos, new_stop, qty=None):
    """先掛新停損、再撤舊的，中間不會有沒保護的空窗。
    失敗計數、告警、恢復通知都在這裡——不管是出場判斷、重試、還是哪條路徑呼叫，
    第一次失敗都算第 1 次，任何一次成功都會發恢復（清單第 8 條 r8）。"""
    is_long = pos["side"] == "LONG"
    qty = qty or pos["qty"]
    pos["want_stop"] = new_stop                     # 先記意圖：掛不上時下一輪 retry_stop 會重試
    try: o = B.stop_order(sym, "SELL" if is_long else "BUY", qty, new_stop)
    except Exception as e:
        if "-2021" in str(e): raise                  # 價格已穿過 → 呼叫端直接出場，不算移損失敗
        n = pos["want_fail"] = pos.get("want_fail", 0) + 1
        if nag(n):
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} 停損應移到 {new_stop:.6g} 未成功（第 {n} 次，{e}）"
                          f"— 目前停損還在 {pos.get('stop'):.6g}")
        raise StopMoveFailed(str(e)) from None
    old, old_via = pos.get("stop_id"), pos.get("stop_via")
    if old:
        try: B.cancel_order(sym, old, old_via)
        except Exception as e:
            pos.setdefault("stale_ids", []).append([old, old_via])   # 平倉時也會再撤一次
            queue_leftover(sym, old, old_via, e, why="移損後舊停損")
    was = pos.get("want_fail", 0)
    pos.update(stop=new_stop, stop_id=o.get("orderId"), stop_via=o.get("via"), want_stop=None, want_fail=0)
    if was >= 1:
        telegram.send(f"✅ {sym} 停損已移到 {new_stop:.6g}（先前失敗 {was} 次，已恢復）")

def retry_stop(sym, pos):
    """上次移損沒成功 → 每輪重試。計數、告警、恢復通知都在 move_stop 裡。
    crypto-screener 曾因移損失敗只試一次而『移損到成本』安靜失效好幾天。"""
    want = pos.get("want_stop")
    if want is None or want == pos.get("stop"): pos["want_stop"] = None; return
    try: move_stop(sym, pos, want)
    except StopMoveFailed: pass
    except Exception as e:
        if "-2021" in str(e):                       # 價格已穿過想要的停損 = 本來就該出場
            try: close_now(sym, pos, "停損"); return "closed"
            except CloseFailed: return None
        _log(f"{sym} 重試移損 {e}")

_missing = {}          # symbol → 連續幾輪確認停損不在

def ensure_stop(sym, pos):
    """確認停損單還掛著，不在就補掛。

    設計原則直接沿用 crypto-screener 那次誤平倉的教訓：
    1. 查詢失敗 ≠ 停損不在。查不到就跳過，下一輪再查。
    2. 連續 3 輪都確認不在才動作，單次讀取錯誤不該觸發任何事。
    3. 補掛被拒且原因是「已存在」→ 代表停損其實在，是判斷錯了，不動作。
    4. 只補掛、不平倉。補不上就大聲告警，交給人決定。
    """
    stops, ok = B.open_stops(sym)
    if not ok: return                                     # 原則 1
    oid = lambda o: o.get("algoId") or o.get("orderId")
    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    mine_ids = {pos.get("stop_id")} | {x[0] for x in pos.get("stale_ids", [])}
    # 只認自己記過 id 的單；沒記 id（舊版認領的倉）才退而用方向判斷。共用帳號裡同幣的別人單不能碰（清單第 7 條）
    mine = [o for o in stops if oid(o) in mine_ids] or \
           ([o for o in stops if o.get("side") == close_side][:1] if not pos.get("stop_id") else [])
    if mine:
        _missing[sym] = 0
        was = pos.pop("guard_fail", 0)
        if was >= 1:                                      # 補掛失敗過，現在停損在了（例如上次逾時但交易所端其實成功）
            telegram.send(f"🔧 {sym} 引擎{pos.get('engine')} 停損單已確認存在（先前補掛失敗 {was} 次，已恢復）")
        cur = next((o for o in mine if oid(o) == pos.get("stop_id")), mine[0])
        pos["stop_id"] = oid(cur)
        for extra in mine:                                # 只撤自己記錄在案的殘留
            if oid(extra) == pos["stop_id"]: continue
            try: B.cancel_order(sym, oid(extra), pos.get("stop_via")); _log(f"{sym} 撤掉殘留停損 {oid(extra)}")
            except Exception: pass
        pos["stale_ids"] = [x for x in pos.get("stale_ids", []) if x[0] != pos["stop_id"] and x[0] in {oid(o) for o in stops}]
        return
    n = _missing.get(sym, 0) + 1; _missing[sym] = n
    if n < 3: return                                      # 原則 2
    try:
        o = B.stop_order(sym, "SELL" if pos["side"] == "LONG" else "BUY", pos["qty"], pos["stop"])
        pos["stop_id"] = o.get("orderId"); pos["stop_via"] = o.get("via"); _missing[sym] = 0
        was = pos.pop("guard_fail", 0)
        _log(f"{sym} 停損單不見了，已補掛 {pos['stop']}")
        telegram.send(f"🔧 {sym} 引擎{pos.get('engine')} 停損單不見了，已補掛 {pos['stop']:.6g}" +
                      (f"（先前失敗 {was} 次，已恢復）" if was else ""))
    except Exception as e:
        msg = str(e).lower()
        if "-2021" in msg:                                # 價格已穿過停損：停損本來就該觸發了 → 直接出場
            try: close_now(sym, pos, "停損"); return "closed"
            except CloseFailed: return None
        if "existing" in msg or "already" in msg:         # 原則 3
            _missing[sym] = 0; _log(f"{sym} 停損其實在（補掛回報已存在），誤判")
            was = pos.pop("guard_fail", 0)
            if was >= 1: telegram.send(f"🔧 {sym} 停損單已確認存在（先前補掛失敗 {was} 次，已恢復）")
            return
        k = pos["guard_fail"] = pos.get("guard_fail", 0) + 1
        if nag(k):                                                          # 原則 4：只告警、不平倉
            telegram.send(f"🚨 {sym} 引擎{pos.get('engine')} 沒有停損單、補掛失敗（第 {k} 次）"
                          f"應掛 {pos['stop']:.6g}，錯誤：{e} — 請手動處理")

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
                telegram.send(f"✂️ {sym} 引擎{eid} 到 {R['tp1_r']}R 減碼一半，停損移到成本 {pos['entry']:.6g}")   # 減碼已成交，先通知
                move_stop(sym, pos, pos["entry"])
            elif R["tp1_r"] is None and not pos.get("be") and R.get("be_r") and r_now >= R["be_r"]:
                pos["be"] = True; pos["state"] = "保本"
                move_stop(sym, pos, pos["entry"])
                telegram.send(f"🛡 {sym} 引擎{eid} 到 {R['be_r']}R，停損移到成本 {pos['entry']:.6g}")
            elif n >= R["max_hold_bars"]:
                close_now(sym, pos, "時間"); return "closed"      # 失敗會拋 CloseFailed（已告警、已記 want_close）
            elif (pos.get("tp1") or pos.get("be")) and r_now >= R["trail_after_r"] and j >= R["trail_bars"]:
                seg = k[j - R["trail_bars"]:j + 1]
                ns = max(pos["stop"], min(x["l"] for x in seg)) if d > 0 else min(pos["stop"], max(x["h"] for x in seg))
                if ns != pos["stop"]:
                    move_stop(sym, pos, ns); pos["state"] = "追蹤"
        except StopMoveFailed:
            return None                              # 已記下想要的停損並告警，下一輪 retry_stop 重試
        except CloseFailed:
            return None                              # 已記下 want_close 並告警，下一輪 retry_close 重試
        except Exception as e:
            msg = str(e)
            if "-2021" in msg:                       # 新停損價已經被穿過 = 本來就該停損了
                try: close_now(sym, pos, "停損"); return "closed"
                except CloseFailed: return None
            _log(f"{sym} 出場管理 {msg}")
            telegram.send(f"⚠️ {sym} 引擎{eid} 出場管理失敗：{msg}（原停損單仍在）")
            return None
    return None

def queue_leftover(sym, oid, via, err, why="平倉後停損"):
    """撤不掉的條件單放進引擎層級的待撤清單：當下就是第 1 次、要告警，之後每輪重撤（清單第 8、13 條）。"""
    lo = dict(store.get().get("leftover", {}))
    if str(oid) in lo: return
    lo[str(oid)] = dict(symbol=sym, via=via, n=1, err=str(err)[:120], why=why)
    store.update(leftover=lo)
    _log(f"{sym} {why}單 {oid} 撤不掉 {err}（之後每輪重撤）")
    telegram.send(f"⚠️ {sym} {why}單 {oid} 撤不掉（第 1 次，{str(err)[:120]}）— 之後每輪重撤，可能動到同幣其他倉")

def sweep_leftovers():
    """平倉後沒撤掉的條件單：每輪重撤，照節奏提醒直到清掉（清單第 13 條 + 第 8 條 r6 告警節奏）。"""
    lo = dict(store.get().get("leftover", {}))
    if not lo: return
    for oid, x in list(lo.items()):
        try:
            B.cancel_order(x["symbol"], int(oid) if str(oid).isdigit() else oid, x.get("via"))
            if x["n"] >= 1: telegram.send(f"✅ {x['symbol']} 殘留停損單 {oid} 已撤掉（先前失敗 {x['n']} 次，已恢復）")
            lo.pop(oid)
        except Exception as e:
            x["n"] += 1; x["err"] = str(e)[:120]
            if nag(x["n"]):
                telegram.send(f"⚠️ {x['symbol']} 已平倉但停損單 {oid} 撤不掉（第 {x['n']} 次，{x['err']}）"
                              f"— 可能動到同幣其他倉，請到交易所手動撤")
    store.update(leftover=lo)

def run():
    """每輪迴圈呼叫一次（在 reconcile 之後，已被交易所停損掉的倉不會進來）。"""
    try: sweep_leftovers()
    except Exception as e: _log(f"重撤殘留單 {e}")
    for sym in list(store.get().get("open", {})):
        pos = dict(store.get().get("open", {}).get(sym) or {})
        if not pos: continue
        try:
            res = retry_close(sym, pos)                  # 上次平倉沒完成 → 先重試
            if res == "pending": res = None
            elif res != "closed": res = retry_stop(sym, pos)
            if res != "closed":
                res = ensure_stop(sym, pos)              # 等平倉期間停損也要一直在；補掛遇 -2021 會直接出場
                if res != "closed" and not pos.get("want_close"): res = step(sym, pos)
        except Exception as e: _log(f"{sym} 出場管理 {e}"); continue
        own = dict(store.get().get("open", {}))
        if res == "closed": own.pop(sym, None)
        elif sym in own: own[sym] = pos
        store.update(open=own)
