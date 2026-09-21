# 清單 r19 → r21 差異的行為測試。app/binance.py 真的會跑，只在 HTTP 層換成模擬幣安。
# 在專案根目錄執行：python -m tests.test_r21
import os, re, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["TRADE"] = "1"
from app import config as C
C.API_KEY = "k"; C.API_SECRET = "s"; C.USE_TESTNET = True
from app import binance as B, store, telegram
from tests.fake_exchange import FakeBinance

TG = []
telegram.send = lambda m: TG.append(m)
time.sleep = lambda s: None
from app import manager, main
manager.telegram.send = telegram.send; main.telegram.send = telegram.send
ORIG = dict(klines=B.klines, _get=B._get, now=manager._now, retry_stop=manager.retry_stop, record_close=manager.record_close,
            scan=main.scanner.scan, reconcile=main.reconcile, push=store.push)

fails = []
def check(tag, name, cond, detail=""):
    fx_now = getattr(FakeBinance, "current", None)       # 印出這個情境到目前為止的突變命中次數，給 mutation_check 自動判定「無關」
    hits = f"〔命中{fx_now.mut_hits}〕" if fx_now is not None else ""
    print(f"  {'✅' if cond else '❌'} [{tag}] {name}" + (f"　{detail}" if detail else "") + hits)
    if not cond: fails.append(tag)

ALL_ERR = []
def fresh(hedge=False, algo="ok"):
    """每個情境都從乾淨狀態開始：模擬交易所、快取、模組層級的計數器與被換掉的函式都重設（清單用法第 5 點第 14 種）。"""
    ALL_ERR.extend(store.get().get("errors", [])); ALL_ERR.extend(TG)
    fx = FakeBinance(hedge=hedge, algo=algo).install()
    B._mode.update(hedge=None, t=0); B._F.clear(); B._algo.update(legacy_until=0.0)
    main._rc["t"] = 0
    # 第 14 種：情境可能換掉模組層級的函式、留下計數器；中途出錯時還原那行不一定走得到，一律在這裡重設
    B.klines = ORIG["klines"]; B._get = ORIG["_get"]; manager._now = ORIG["now"]; manager.retry_stop = ORIG["retry_stop"]
    manager.record_close = ORIG["record_close"]; main.scanner.scan = ORIG["scan"]; main.reconcile = ORIG["reconcile"]
    store.push = ORIG["push"]
    manager._errs.clear(); main._loop_errs.clear(); manager._missing.clear()
    store.update(open={}, pending={}, closed=[], leftover={}, trades=[], signals=[], errors=[], exchange=[])
    TG.clear()
    return fx

def run(fn):
    try: return fn(), None
    except Exception as e: return None, e

def since(fx, mark): return fx.calls[mark:]
def stop_posts(calls): return [c for c in calls if c[0] == "POST" and c[1] in ("/fapi/v1/algoOrder",) ]
EMPTY_ONE = lambda times=50: dict(path="/fapi/v2/positionRisk", match=lambda p: "symbol" in p, times=times, kind="empty")

def own_pos(fx, sym="XUSDT", qty=100, base=0, side="LONG", stop=0.9):
    o = B.stop_order(sym, "SELL" if side == "LONG" else "BUY", qty, stop)
    p = dict(engine="C", side=side, qty=qty, entry=1.0, fill=1.0, stop=stop, r_unit=0.1,
             stop_id=o["orderId"], stop_via=o["via"], ts=int(time.time() * 1000), bar_t=300_000, last_t=300_000, base_qty=base)
    store.update(open={**store.get().get("open", {}), sym: p}); return p

# =====================================================================
print("第 2 條 r20：移損掛新停損前要確認部位（寫在共用的送單處）")

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = own_pos(fx)
fx.pos.clear()                                                   # 部位已被平掉，但對帳還沒偵測到
mark = len(fx.calls)
r, e = run(lambda: manager.move_stop("XUSDT", dict(p), 1.0))
check("M1", "（前提）移損真的有動作：確認了部位（第 8 種）",
      any(c[1] == "/fapi/v2/positionRisk" and c[2].get("symbol") == "XUSDT" for c in since(fx, mark)))
check("M1", "（前提）確認的結果是「部位沒了」，不是「查不到」", r == "gone", f"回傳={r} err={e}")
check("M1", "部位已沒了 → 移損不掛新停損（否則就是孤兒單）", not stop_posts(since(fx, mark)), f"掛了 {len(stop_posts(since(fx, mark)))} 張 err={e}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); p = dict(own_pos(fx)); old = p["stop_id"]
fx.inject.append(EMPTY_ONE())
mark = len(fx.calls)
r, e = run(lambda: manager.move_stop("XUSDT", p, 1.0))
check("M1", "（前提）移損真的去確認了部位", any(c[1] == "/fapi/v2/positionRisk" and "symbol" in c[2] for c in since(fx, mark)))
check("M1", "部位查不到（逐幣回空）→ 這輪不掛新停損", not stop_posts(since(fx, mark)))
check("M1", "部位查不到 → 舊停損留著、想要的停損價記著（下一輪重試）", old in fx.algo_orders and p.get("want_stop") == 1.0, f"want={p.get('want_stop')}")
check("M1", "部位查不到 → 不算移損失敗（不告警、不計數）", not p.get("want_fail") and not any("未成功" in m for m in TG), f"{TG}")

fx = fresh(); fx.open("XUSDT", "LONG", 40); fx.pos[("XUSDT", "LONG")][0] = 120   # 交易所 120 = 基準 40 + 自己剩 80（在 App 減過、還沒對帳）
p = dict(own_pos(fx, qty=100, base=40))
mark = len(fx.calls)
run(lambda: manager.move_stop("XUSDT", p, 1.0))
posted = stop_posts(since(fx, mark))
check("M2", "（前提）移損真的掛出新停損", len(posted) == 1)
check("M2", "新停損數量 = min(帳上 100, 交易所 120 − 基準 40) = 80", posted and float(posted[0][2].get("quantity", 0)) == 80, f"{posted}")

fresh()                                                  # 靜態檢查自成一個情境
check("M1", "送停損單只有一個共用的地方（app/ 裡 B.stop_order 只出現一次）",
      sum(len(re.findall(r"\bB\.stop_order\(", open(f"app/{f}", encoding="utf-8").read())) for f in ("main.py", "manager.py", "preflight.py")) == 1)

# =====================================================================
print("第 8 條 r21：對帳的逐部位迴圈、通知都要各自 try（出錯的排在前面）")
fx = fresh(); fx.open("XUSDT", "LONG", 100)
own_pos(fx, sym="AUSDT"); own_pos(fx, sym="XUSDT")               # A 在前面、交易所上已沒了；X 還在
CALLED = []
def bad_close(sym, pos, by, info=None):
    CALLED.append(sym)
    if sym == "AUSDT": return dict(pos, symbol=sym, exit="n/a", pnl=1.0)   # exit 不是數字 → 通知格式化時會出錯
    return ORIG["record_close"](sym, pos, by, info)
manager.record_close = bad_close
main._rc["t"] = 0; r, e = run(lambda: main.reconcile(force=True))
check("M5", "（前提）對帳真的走到 A 的結帳（出錯的那一步之前的動作都發生了）", "AUSDT" in CALLED)
check("M5", "A 的平倉通知格式化出錯 → 對帳不中斷", e is None, f"err={e}")
check("M5", "A 出錯 → 排在後面的 X 照樣對帳（仍在帳上、交易所列表有更新）",
      "XUSDT" in store.get().get("open", {}) and any(x["symbol"] == "XUSDT" for x in store.get().get("exchange", [])))
check("M5", "A 出錯要推播（不能只進錯誤區）", any("AUSDT" in m and ("出錯" in m) for m in TG), f"{TG}")
check("M5", "A 已經結帳、只是通知出錯 → 不能被放回帳上（否則下一輪重複結帳）",
      "AUSDT" not in store.get().get("open", {}), f"open={list(store.get().get('open', {}))}")

fx = fresh(); fx.open("XUSDT", "LONG", 100); own_pos(fx)
manager.B.klines = lambda *a, **k: []
store.update(leftover={"bad": dict(via="algo", n=0), "5": dict(symbol="XUSDT", via="algo", n=1)})   # 第一筆資料壞掉；n=0 → 這次是第 1 次失敗、照節奏要推播
fx.algo_orders[5] = dict(algoId=5, symbol="XUSDT", side="SELL", orderType="STOP_MARKET")
mark = len(fx.calls)
run(lambda: manager.run())
check("M6", "（前提）第二筆真的被送出撤單", any(c[0] == "DELETE" and c[2].get("algoId") == "5" for c in since(fx, mark)))
check("M6", "殘留單清單第一筆壞掉 → 第二筆照樣重撤", 5 not in fx.algo_orders)
check("M6", "壞掉那筆的錯誤要推播，而且那筆不能被靜靜丟掉", any("殘留" in m and "出錯" in m for m in TG) and "bad" in store.get().get("leftover", {}),
      f"TG={TG[-2:]} leftover={list(store.get().get('leftover', {}))}")

# =====================================================================
print("用法第 5 點：通知在 try 裡的錯誤不能被吞")
fresh()                                                  # 全域檢查自成一個情境：不繼承上一個情境的突變命中次數（fresh 會先收集錯誤區與推播）
bugs = [x for x in ALL_ERR if any(k in x for k in ("NameError", "AttributeError", "TypeError", "KeyError", "is not defined"))
        and "AUSDT" not in x and "bad" not in x]
check("H10", "（前提）有收集到各情境的錯誤區與推播", len(ALL_ERR) > 0)
check("H10", "所有情境裡沒有非注入的程式錯誤", not bugs, f"{bugs[:3]}")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{sorted(set(fails))}")
sys.exit(1 if fails else 0)
