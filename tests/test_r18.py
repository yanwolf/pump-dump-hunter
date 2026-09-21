# 清單 r16 → r18 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安。
# 在專案根目錄執行：python -m tests.test_r18
from tests.harness import (one, ALL_ERR, B, C, FakeBinance, TG, check, fresh, finish, main, manager, os, preflight,
                           re, run, since, store, sys, telegram, time)   # 共用案例框架（清單用法第 5 點 r27）；明列名稱，pyflakes 才查得到未定義名稱





def sent_market(calls, reduce=None):
    out = [c for c in calls if c[1] == "/fapi/v1/order" and c[2].get("type") == "MARKET"]
    if reduce is True: out = [c for c in out if c[2].get("reduceOnly") == "true"]
    if reduce is False: out = [c for c in out if c[2].get("reduceOnly") != "true"]
    return out
EMPTY_ONE = lambda times=50: dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=times, kind="empty")
EMPTY_ALL = lambda times=50: dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" not in p, times=times, kind="empty")
REJECT_CLOSE = lambda times=5: dict(path="/fapi/v1/order", method="POST", match=lambda p: p.get("reduceOnly") == "true",
                                    times=times, kind="http", code=400, body='{"code":-2019,"msg":"Margin is insufficient."}')

class Sig:
    def __init__(s, side="LONG", entry=1.0, stop=0.9): s.side, s.entry, s.stop, s.engine, s.reason = side, entry, stop, "C", "t"
SZ = dict(qty=100, leverage=10, risk_usdt=10, usable=1000, notional=100, stop_pct=10)

def own_pos(fx, sym="XUSDT", qty=100, base=0, side="LONG", stop_on_exchange=True, fill=1.0):
    sid = via = None
    if stop_on_exchange:
        o = B.stop_order(sym, "SELL" if side == "LONG" else "BUY", qty, 0.9 if side == "LONG" else 1.1)
        sid, via = o["orderId"], o["via"]
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=fill, stop=0.9 if side == "LONG" else 1.1, r_unit=0.1,
             stop_id=sid, stop_via=via, ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000, base_qty=base)
    store.update(open={**store.get().get("open", {}), sym: p}); return p

# =====================================================================
print("第 2 條 r17：逐幣查詢回空清單 = 查不到，不是數量 0")

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(REJECT_CLOSE())
p = dict(store.get()["open"]["XUSDT"])
mark = len(fx.calls)
fx.inject.append(EMPTY_ONE())                                       # 平倉「前」的確認也會空：第 12 種，前提要涵蓋
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))
check("K2", "（前提）平倉流程真的走到平倉前的逐幣確認", any(c[1] == "/fapi/v2/positionRisk" and "symbol" in c[2] for c in since(fx, mark)))
check("K2", "平倉前確認就回空清單 → 不能當成「已經沒了」直接結帳", not store.get().get("closed") and so["stop_id"] in fx.algo_orders,
      f"err={e} closed={len(store.get().get('closed') or [])}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(REJECT_CLOSE())
p = dict(store.get()["open"]["XUSDT"])
orig_get = B._get
def get_then_empty(path, params=None, base=None, signed=False):
    # 「平倉單送出之後」逐幣查詢才回空——綁在被測那一步上，不用「第幾次查詢」計數（清單用法第 5 點第 18 種）
    closed_sent = any(c[1] == "/fapi/v1/order" and c[2].get("reduceOnly") == "true" for c in fx.calls)
    if closed_sent and path == "/fapi/v2/positionRisk" and params and "symbol" in params: return []
    return orig_get(path, params, base, signed)
B._get = get_then_empty
mark = len(fx.calls)
r, e = run(lambda: manager.close_now("XUSDT", p, "時間"))
B._get = orig_get
check("K2", "（前提）平倉前確認正常、平倉單真的送出並被拒（第 12 種）", len(sent_market(since(fx, mark), reduce=True)) >= 1)
check("K2", "平倉被拒、平倉後確認回空清單 → 不能記成已平倉、不能撤停損",
      not store.get().get("closed") and so["stop_id"] in fx.algo_orders, f"err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(EMPTY_ALL()); fx.inject.append(EMPTY_ONE())
mark = len(fx.calls)
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("K2", "（前提）對帳跑完，而且全量表找不到時真的逐幣再查了", e is None and
      any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") == "XUSDT" for c in since(fx, mark)), f"err={e}")
check("K2", "對帳：全量表與逐幣都回空 → 部位不能被判平倉、停損不能被撤",
      "XUSDT" in store.get().get("open", {}) and so["stop_id"] in fx.algo_orders, f"err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100)                      # 對照組：全量表回空、逐幣正常 → 靠逐幣查詢認領
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=1, base_qty=0)})
fx.inject.append(EMPTY_ALL())
main._rc["t"] = 0; run(lambda: main.reconcile(force=True))
check("K2", "（對照組）全量表回空、逐幣查詢正常 → pending 靠逐幣查詢認領回來", "XUSDT" in store.get().get("open", {}))

fx = fresh(); fx.open("XUSDT", "LONG", 100)
store.update(pending={"XUSDT": dict(engine="C", side="LONG", time="t", entry=1.0, stop=0.9, ts=1, base_qty=0)})   # 早已過期
fx.inject.append(EMPTY_ALL()); fx.inject.append(EMPTY_ONE())
mark = len(fx.calls)
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("K2", "（前提）對帳跑完，而且對 pending 真的逐幣查了", e is None and
      any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") == "XUSDT" for c in since(fx, mark)), f"err={e}")
check("K2", "pending 到期：逐幣回空清單 → 不能判定未成交丟掉", bool(store.get().get("pending")), f"pending={store.get().get('pending')}")

fx = fresh()                                                       # 突變豁免：本情境要測的就是「基準查不到」
mark = len(fx.calls)
main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("K2", "（對照組）沒注入時，同樣的呼叫確實會送進場單——下面的「不送」才是因為基準查不到",
      bool(sent_market(since(fx, mark), reduce=False)))
fx = fresh()                                                       # 突變豁免
fx.inject.append(EMPTY_ONE())
mark = len(fx.calls)
rec = main.place("XUSDT", "C", Sig(), dict(SZ), dict(time="t", bar_t=300_000))
check("K2", "（前提）place 真的去逐幣查了基準", any(c[1] == "/fapi/v2/positionRisk" and "symbol" in c[2] for c in since(fx, mark)))
check("K2", "送單前基準查詢回空清單 → 不送單", not sent_market(since(fx, mark), reduce=False), f"skipped={rec.get('skipped')}")
check("K2", "不送單的原因是基準查不到（不是別的原因）", "基準" in (rec.get("skipped") or ""), f"skipped={rec.get('skipped')}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); so = own_pos(fx)
fx.inject.append(EMPTY_ONE(3)); manager._missing["XUSDT"] = 2
manager.B.klines = lambda *a, **k: []
del_stop = so["stop_id"]; fx.algo_orders.pop(del_stop)            # 停損不見了，而且查部位的逐幣查詢回空
mark = len(fx.calls)
run(lambda: manager.run())
placed = [c for c in since(fx, mark) if c[1] == "/fapi/v1/algoOrder" and c[0] == "POST"]
check("K2", "（前提）守衛這輪真的走到補掛前的部位確認（第 8 種）",
      any(c[1] == "/fapi/v2/positionRisk" and "symbol" in c[2] for c in since(fx, mark)))
check("K2", "守衛補掛前確認部位：逐幣查詢回空 → 這輪不補（部位可能已被平掉，補了就是孤兒單）", not placed, f"補掛 {len(placed)} 次")
fx.inject.clear(); manager._missing["XUSDT"] = 2
mark2 = len(fx.calls)
run(lambda: manager.run())
placed2 = [c for c in since(fx, mark2) if c[1] == "/fapi/v1/algoOrder" and c[0] == "POST"]
check("K2", "下一輪查詢正常、確認部位還在 → 這一輪補掛（看這一輪新送出的，不看最終狀態：第 13 種）", len(placed2) == 1, f"這一輪補掛 {len(placed2)} 次")

# =====================================================================
print("第 2 條 r18：「沒有部位才能做」的判斷（自檢的孤兒單）")
fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx)
fx.inject.append(EMPTY_ALL(5))
mark = len(fx.calls)
res = preflight.check()
orphan = next((x for x in res if x["item"] == "孤兒條件單"), {})
check("K3", "（前提）自檢真的跑到孤兒單這項，並對這張停損逐幣確認了部位（第 8 種）",
      bool(orphan) and any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") == "XUSDT" for c in since(fx, mark)), f"{orphan}")
check("K3", "全量表回空清單 → 不能把所有停損都列成孤兒", "XUSDT" not in orphan.get("msg", ""), f"{orphan}")

# =====================================================================
print("第 3 條 r18：即時損益要扣基準")
fx = fresh(); fx.price = 1.0
fx.open("XUSDT", "LONG", 40, entry=0.8); fx.pos[("XUSDT", "LONG")] = [140, (40 * 0.8 + 100 * 1.0) / 140]   # 合併均價
own_pos(fx, qty=100, base=40, fill=1.0)
fx.price = 1.1
main._rc["t"] = 0; main.reconcile(force=True)
row = next((x for x in store.get().get("exchange", []) if x["symbol"] == "XUSDT"), {})
check("K4", "（前提）交易所那一列的未實現損益含別人的部位：自己 10 ＋ 別人 40×(1.1−0.8)=12 ＝ 22",
      abs(row.get("upnl", 0) - 22.0) < 0.01, f"{row}")
check("K4", "本策略那一列顯示的損益 = 自己的 100 ×（1.1 − 1.0）= 10", abs((row.get("upnl_mine") or 0) - 10.0) < 0.01, f"{row}")

# =====================================================================
print("第 8 條 r18：守衛迴圈裡一個部位出錯，其他部位與自己的守衛照跑（出錯的排在前面，第 11 種）")
fx = fresh()
fx.open("AUSDT", "LONG", 100); fx.open("XUSDT", "LONG", 100)
own_pos(fx, sym="AUSDT", stop_on_exchange=False)             # A 排在前面：出錯的部位
own_pos(fx, sym="XUSDT", stop_on_exchange=False)
orig_retry = manager.retry_stop
def boom(sym, pos):
    if sym == "AUSDT": raise RuntimeError("模擬的程式錯誤")
    return orig_retry(sym, pos)
manager.retry_stop = boom
manager.B.klines = lambda *a, **k: []
for _ in range(5): manager.run()
manager.retry_stop = orig_retry
stops = {o["symbol"] for o in fx.algo_orders.values()}
check("K7", "排在前面的 A 出錯 → 排在後面的 X 守衛照樣補掛", "XUSDT" in stops, f"停損={stops}")
check("K7", "A 自己的其他步驟出錯 → A 的停損守衛也照樣補掛", "AUSDT" in stops, f"停損={stops}")
n = len([m for m in TG if "AUSDT" in m and "模擬的程式錯誤" in m])
check("K7", "A 的錯誤要推播，而且照節奏（5 輪只發第 1、5 次）", n == 2, f"推播 {n} 則")

print("第 8 條 r18：背景迴圈每一步各自 try")
fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx, stop_on_exchange=False)
manager.B.klines = lambda *a, **k: []
orig_scan, orig_rec = main.scanner.scan, main.reconcile
def bad_scan(**k): raise RuntimeError("掃描器壞了")
def bad_rec(force=False): raise RuntimeError("對帳壞了")
main.scanner.scan = bad_scan; main.reconcile = bad_rec
state = dict(watch={}, last={}, last_scan=0)
r, e = run(lambda: [main.tick(state) for _ in range(4)])
main.scanner.scan = orig_scan; main.reconcile = orig_rec
check("K7", "（前提）背景迴圈有可單獨呼叫的一輪（tick）", e is None, f"err={e}")
check("K7", "掃描器與對帳都出錯 → 這一輪的停損守衛照樣補掛", any(o["symbol"] == "XUSDT" for o in fx.algo_orders.values()))
check("K7", "迴圈步驟出錯要推播（不能只進錯誤區）", one("🐞 背景迴圈「掃描」出錯", "掃描器壞了")[0] and one("🐞 背景迴圈「對帳」出錯", "對帳壞了")[0], f"{one('🐞 背景迴圈「掃描」出錯', '掃描器壞了')[1]}；{one('🐞 背景迴圈「對帳」出錯', '對帳壞了')[1]}")

# =====================================================================

finish(allowed=('模擬的程式錯誤',))   # 本檔刻意注入的錯誤字串；其餘程式錯誤一律算失敗
