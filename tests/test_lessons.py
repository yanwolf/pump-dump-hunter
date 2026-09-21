# 清單 r6 → r8 差異的行為測試。不連網，全部用假交易所。
# 在專案根目錄執行：python -m tests.test_lessons
# 每一項對應清單條目；修改前在 r6 程式上跑過，確認會失敗（清單第 7 條：用測試確認修改前會失敗）。
import json, os, sys, tempfile, threading, types, urllib.error, urllib.request

os.environ["DATA_DIR"] = tempfile.mkdtemp()
from app import config as C
C.API_KEY = "k"; C.API_SECRET = "s"

fails = []
def check(tag, name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} [{tag}] {name}" + (f"　{detail}" if detail else ""))
    if not cond: fails.append(tag)

# =====================================================================
print("第 7 條：持倉模式偵測與反轉")
from app import binance as B
B._F["XUSDT"] = dict(step=1.0, minqty=1.0, tick=0.001, minnot=5)

class _R:
    def __init__(s, d): s.d = d
    def read(s): return json.dumps(s.d).encode()
    def __enter__(s): return s
    def __exit__(s, *a): pass
class _E(urllib.error.HTTPError):
    def __init__(s, u, code, msg): super().__init__(u, code, "x", {}, None); s._m = msg
    def read(s): return s._m.encode()

def exchange(account_hedge, detect_ok, sent, on_reject=None):
    """假交易所：帳戶實際模式 account_hedge；detect_ok=False 時偵測端點被限流；送錯模式的單回 -4061。"""
    def fake(req, timeout=None):
        path = req.full_url.split("?")[0].split(".com")[-1]
        if path == "/fapi/v1/positionSide/dual":
            if not detect_ok: raise _E(req.full_url, 429, "rate limited")
            return _R({"dualSidePosition": account_hedge})
        body = (req.data or b"").decode(); sent.append(body)
        if ("positionSide" in body) != account_hedge:
            if on_reject: on_reject()
            raise _E(req.full_url, 400, '{"code":-4061,"msg":"position side does not match"}')
        return _R({"avgPrice": "1", "algoId": 1})
    urllib.request.urlopen = fake

def try_order(fn):
    try: fn(); return True
    except Exception as e: return str(e)[:80]

# C1 / C4：送單後快取被別的執行緒改掉 → 反轉要看被拒單的參數
sent = []
B._mode.update(hedge=False, t=9e18)
exchange(account_hedge=True, detect_ok=False, sent=sent,
         on_reject=lambda: B._mode.update(hedge=True, t=9e18))    # 被拒的同時，別的執行緒把快取改成雙向
r = try_order(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True))
check("C1/C4", "送單後快取被改 → 依被拒單參數反轉，重送成功", r is True and "positionSide" in sent[-1], f"結果={r} 送了{len(sent)}次")

# C3：快取從沒偵測成功過（空值）、偵測又失敗 → 單仍要送出
sent = []
B._mode.update(hedge=None, t=0)
exchange(account_hedge=True, detect_ok=False, sent=sent)
r = try_order(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True))
check("C3", "快取為空、偵測失敗 → 單仍送出並成功", r is True, f"結果={r} 送了{len(sent)}次")

# C6：快取過期、第一次送單時偵測失敗 → 不能放棄，要用舊值送（錯了再靠 -4061 反轉）
sent = []
B._mode.update(hedge=True, t=0)                                 # 過期但有舊值
exchange(account_hedge=True, detect_ok=False, sent=sent)
r = try_order(lambda: B.stop_order("XUSDT", "SELL", 100, 0.9))
check("C6", "快取過期 + 偵測失敗 → 掛停損不放棄（用舊值送出）", r is True and len(sent) == 1, f"結果={r} 送了{len(sent)}次")

# C2：重送成功後，快取 = 實際成功的那個假設；重送也失敗時，快取不能留著沒驗證過的值
sent = []
B._mode.update(hedge=False, t=9e18)
exchange(account_hedge=True, detect_ok=False, sent=sent)
try_order(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True))
check("C2", "重送成功 → 快取更新為成功的假設（雙向）", B._mode["hedge"] is True)
sent = []
B._mode.update(hedge=False, t=9e18)
def both_fail(req, timeout=None):
    path = req.full_url.split("?")[0].split(".com")[-1]
    if path == "/fapi/v1/positionSide/dual": raise _E(req.full_url, 429, "rate limited")
    sent.append((req.data or b"").decode())
    raise _E(req.full_url, 400, '{"code":-4061,"msg":"position side does not match"}')
urllib.request.urlopen = both_fail
try_order(lambda: B.market_order("XUSDT", "SELL", 100, reduce_only=True))
check("C2", "重送也失敗 → 快取不留沒驗證過的值（下一張單會重新偵測）",
      B._mode["t"] == 0 or B._mode["hedge"] is None, f"快取={B._mode}")

# =====================================================================
print("第 8 條：告警節奏")
fake = types.ModuleType("binance")
from app import manager, store
manager.B = fake
tg = []; manager.telegram.send = lambda m: tg.append(m)
hits = [n for n in range(1, 400) if manager.nag(n)]
check("C7", "提醒落點 1、5、30、150、270、390", hits == [1, 5, 30, 150, 270, 390], str(hits))

# =====================================================================
print("第 8 條：三條路徑的計數與恢復通知")
S = dict(place_fail=0, cancel_fail=0, stops=[])
def so(*a):
    if S["place_fail"] > 0: S["place_fail"] -= 1; raise RuntimeError("HTTP 400 rejected")
    return dict(orderId=77, via="algo")
def cancel(s, i, v=None):
    if S["cancel_fail"] > 0: S["cancel_fail"] -= 1; raise RuntimeError("HTTP 503 busy")
fake.stop_order = so; fake.cancel_order = cancel
fake.open_stops = lambda s: (S["stops"], True); fake.user_trades = lambda s: []
fake.round_qty = lambda s, q: f"{q:.0f}"
fake.market_order = lambda *a, **k: {"avgPrice": "1"}
fake.klines = lambda *a, **k: []

def count_of(msg):
    import re
    m = re.search(r"第 (\d+) 次", msg); return int(m.group(1)) if m else None

# 移損：第一次失敗發生在 step（出場判斷），要被計為第 1 次
tg.clear()
pos = dict(engine="C", side="LONG", qty=100, stop=0.9, entry=1.0, stop_id=1, stop_via="algo")
S["place_fail"] = 2
try: manager.move_stop("X", pos, 1.0)             # step 裡那次（失敗 1）
except Exception: pass
manager.retry_stop("X", pos)                     # 下一輪重試（失敗 2）
check("C12", "移損：實際失敗 2 次 → 計數是 2（出場判斷裡那次也要算）", pos.get("want_fail") == 2,
      f"計數={pos.get('want_fail')} 告警={tg}")
# 移損：失敗過後，改由出場判斷的另一次移損（例如追蹤）成功 → 也要發恢復
tg.clear()
manager.move_stop("X", pos, 1.02)                # 不經過 retry_stop 的成功
check("C12", "移損：由其他路徑移損成功也要發恢復通知", any("恢復" in m for m in tg), f"告警={tg}")

# 補掛：失敗 1 次後，下一輪發現停損其實在（上次逾時但交易所端成功）→ 要發恢復
tg.clear(); manager._missing.clear(); S["place_fail"] = 1; S["stops"] = []
pos = dict(engine="C", side="LONG", qty=100, stop=0.9, entry=1.0, stop_id=5)
for _ in range(3): manager.ensure_stop("X", pos)      # 第 3 輪補掛失敗
S["stops"] = [dict(algoId=5, side="SELL", orderType="STOP_MARKET")]
manager.ensure_stop("X", pos)                          # 停損其實在
check("C12", "補掛：失敗後發現停損其實在 → 發恢復並清掉失敗次數",
      any("恢復" in m for m in tg) and not pos.get("guard_fail"), f"告警={tg} guard_fail={pos.get('guard_fail')}")
# 補掛：失敗 1 次後，下一輪補掛成功 → 要發恢復
tg.clear(); manager._missing.clear(); S["place_fail"] = 1; S["stops"] = []
pos = dict(engine="C", side="LONG", qty=100, stop=0.9, entry=1.0, stop_id=5)
for _ in range(4): manager.ensure_stop("X", pos)
check("C12", "補掛：失敗恰好 1 次、下一輪補掛成功 → 發恢復", any("恢復" in m for m in tg), f"告警={tg}")

# 殘留單：平倉當下撤不掉 = 第 1 次，要告警；下一輪撤掉要發恢復
tg.clear(); S["cancel_fail"] = 1
manager.record_close("X", dict(engine="C", side="LONG", stop_id=9, stop_via="algo"), "停損單", info={})
first = list(tg)
manager.sweep_leftovers()
check("C12", "殘留單：平倉當下撤不掉就告警（第 1 次）", any(count_of(m) == 1 for m in first), f"平倉當下告警={first}")
check("C12", "殘留單：失敗恰好 1 次、下一輪撤掉 → 發恢復", any("撤掉" in m for m in tg[len(first):]), f"全部={tg}")

# =====================================================================
print("順帶：1R 減碼後移損失敗，減碼本身仍要通知")
tg.clear(); S["place_fail"] = 1
pos = dict(engine="C", side="LONG", qty=100, stop=0.9, entry=1.0, r_unit=0.1, stop_id=1, stop_via="algo",
           bar_t=0, last_t=0)
fake.klines = lambda *a, **k: [dict(t=300_000, o=1.0, h=1.12, l=1.0, c=1.1, v=1)]
manager._now = lambda: 10**12
manager.step("X", pos)
check("附帶", "減碼一半已執行 → 就算移損失敗也要發減碼通知", any("減碼" in m for m in tg), f"告警={tg}")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{sorted(set(fails))}")
sys.exit(1 if fails else 0)
