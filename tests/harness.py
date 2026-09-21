"""共用的測試案例框架（清單用法第 5 點 r27）。每支 tests/test_rNN.py 都用這一份，不要各自複製。

各自複製的後果（pump-dump-hunter r27 前）：五支測試各有一份 check()／fresh()，fresh() 重設的東西每份不同，
test_r12 那份結尾根本沒有「沒有程式錯誤被吞掉」的全域檢查，它的 50 多項從來沒檢查過。

用法：
    from tests.harness import (B, TG, check, fresh, finish, ...)   # 明列名稱；不要用 import *，pyflakes 會因此查不到未定義名稱
    fx = fresh()                       # 每個情境第一句
    check("A1", "描述", 條件, "細節")
    finish(allowed=("注入的錯誤字串",))  # 檔案最後一句：跑全域檢查、印總結、設定結束碼

自我驗證：python -m tests.harness_selftest
"""
import json, os, re, sys, tempfile, time
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ["TRADE"] = "1"
from app import config as C
C.API_KEY = "k"; C.API_SECRET = "s"; C.USE_TESTNET = True
from app import binance as B, store, telegram
from tests.fake_exchange import FakeBinance

# 通知：telegram.send 收到的已經是組好的字串（組字串在呼叫端），攔在這裡就是最底層——格式化照樣會執行到（清單第 8 條 r26）
TG = []
telegram.send = lambda m: TG.append(m)
time.sleep = lambda s: None
from app import manager, main, preflight
manager.telegram.send = telegram.send; main.telegram.send = telegram.send

# 情境會換掉的模組層級物件：fresh() 一律還原（第 14 種）。新增要換的東西時加在這裡，tests/check_tests.py 也看這份清單
RESET_ATTRS = [(B, "klines"), (B, "_get"), (manager, "_now"), (manager, "retry_stop"), (manager, "record_close"),
               (manager, "close_now"), (main.scanner, "scan"), (main, "reconcile"), (main, "tick"), (store, "push"),
               (json, "dump")]                   # 全域的 json 模組：換掉它會影響所有模組，更要還原
RESET_ATTRS = [(m, a) for m, a in RESET_ATTRS if hasattr(m, a)]   # 在舊版程式上重跑測試時（清單用法第 5 點 r32），略過不存在的
ORIG = {(id(m), a): getattr(m, a) for m, a in RESET_ATTRS}

fails, ALL_ERR = [], []

class _Tee:
    """攔 sys.stderr：程式各模組的 traceback、print 到標準錯誤的錯誤都寫在這裡（清單用法第 5 點 r29：錯誤掃描要攔到所有模組）。"""
    def __init__(self, real): self.real, self.lines = real, []
    def write(self, s):
        self.real.write(s)
        if s.strip(): self.lines.append(s.rstrip())
    def flush(self): self.real.flush()
STDERR = _Tee(sys.stderr); sys.stderr = STDERR

def selftest_modules():
    """前提（r29）：在框架底下讓不同模組各走一次真的出錯路徑，回傳攔到的模組。至少要攔到兩個。"""
    import threading
    got = set(); mark_e = len(store.get().get("errors", []))
    # 金絲雀寫到 stderr 的東西用另一個緩衝區接（清單用法第 5 點 r32）：traceback 的「Traceback」「Exception in thread」
    # 那幾行不含注入標記，跟測試期間的輸出混在一起會被當成程式錯誤；分開也才分得出「真的沒 traceback」和「沒攔到」。
    saved, canary = STDERR.lines, []
    STDERR.lines = canary
    try:
        # 在舊版程式上重跑時（清單用法第 5 點 r35），那個版本可能還沒有這些函式：有才做，沒有就不算攔到（前提照樣會失敗，但框架不崩）
        if not (hasattr(manager, "_step_err") and hasattr(main, "_loop_step")): raise LookupError("舊版程式沒有 _step_err／_loop_step")
        manager._step_err("SELFTEST", "框架自檢", RuntimeError("manager 自檢錯誤"))
        if any("manager 自檢錯誤" in x for x in store.get().get("errors", [])[mark_e:]): got.add("manager")
        main._loop_step("框架自檢", lambda: (_ for _ in ()).throw(RuntimeError("main 自檢錯誤")))
        if any("main 自檢錯誤" in x for x in canary) and any("main 自檢錯誤" in x for x in store.get().get("errors", [])): got.add("main")
        th = threading.Thread(target=lambda: (_ for _ in ()).throw(RuntimeError("執行緒金絲雀")), name="金絲雀")
        th.start(); th.join()
        if any("執行緒金絲雀" in x for x in canary) and any("Exception in thread" in x for x in canary): got.add("thread")
    except LookupError: pass
    finally:
        STDERR.lines = saved
    from app import presets
    orig = presets.C.apply_overrides
    presets.C.apply_overrides = lambda ov: (_ for _ in ()).throw(RuntimeError("presets 自檢錯誤"))
    presets.set_live({"SCAN.watch_chg24": 15}); presets.apply_live()
    presets.C.apply_overrides = orig; presets.set_live({}); presets.apply_live()
    if any("presets 自檢錯誤" in x for x in store.get().get("errors", [])): got.add("presets")
    for mod, name in ((manager, "_errs"), (main, "_loop_errs")):          # 有才清（舊版程式上重跑，r35）
        if hasattr(mod, name): getattr(mod, name).clear()
    return got
_counts = dict(checks=0, positive=0)

def check(tag, name, cond, detail="", infra=False):
    """一項斷言。印出這個情境到目前為止的突變命中次數〔命中n〕，給 tests/mutation_check.py 自動判定「無關」。
    infra=True：這一項檢查的是模擬交易所本身的狀態（不是程式的行為），印〔命中0〕讓它自動歸為無關（清單用法第 5 點 r27）。
    tests/check_tests.py 會確認 infra 項的條件只讀模擬交易所（fx.…），不碰程式。"""
    _counts["checks"] += 1
    fx_now = getattr(FakeBinance, "current", None)
    hits = "〔命中0〕〔基礎設施〕" if infra else (f"〔命中{fx_now.mut_hits}〕" if fx_now is not None else "")
    if os.environ.get("PDH_AUDIT") and fx_now is not None:        # 第 22 種稽核：這個情境查成交明細時，部位是不是被直接改過
        q = fx_now.trade_queries
        hits = f"〔查成交{len(q)}次、未留成交{sum(x['untraced'] for x in q)}次〕" + hits
    print(f"  {'✅' if cond else '❌'} [{tag}] {name}" + (f"　{detail}" if detail else "") + hits)
    if not cond: fails.append(tag)

def fresh(hedge=False, algo="ok"):
    """每個情境的第一句：新的模擬交易所、清快取、還原換掉的函式、清計數器與帳本。先把上一個情境的錯誤區與推播收進 ALL_ERR。"""
    ALL_ERR.extend(store.get().get("errors", [])); ALL_ERR.extend(TG)
    fx = FakeBinance(hedge=hedge, algo=algo).install()
    B._mode.update(hedge=None, t=0); B._F.clear()
    if hasattr(B, "_algo"): B._algo.update(legacy_until=0.0)
    if hasattr(B, "_algo_ok"): B._algo_ok[0] = None           # r12 以前的永久旗標：在舊版程式上重跑時也要重設（第 14 種）
    for m, a in RESET_ATTRS: setattr(m, a, ORIG[(id(m), a)])
    main._rc["t"] = 0
    for mod, name in ((manager, "_errs"), (main, "_loop_errs"), (manager, "_missing")):
        if hasattr(mod, name): getattr(mod, name).clear()
    store.update(open={}, pending={}, closed=[], leftover={}, trades=[], signals=[], errors=[], exchange=[], cool={})
    TG.clear()
    return fx

def one(title, *must):
    """指定哪一則推播（清單用法第 5 點第 21 種）：開頭是 title 的推播**恰好一則**，而且內容含 must 裡每一段字。
    回傳 (是否成立, 說明)。不要在全部推播裡找關鍵字——別的推播也可能含同樣的字（r31、r32）。"""
    hit = [m for m in TG if m.startswith(title)]
    ok = len(hit) == 1 and all(k in hit[0] for k in must)
    return ok, f"開頭「{title}」的推播 {len(hit)} 則" + (f"：{hit[0][:90]}" if hit else f"；全部推播={[m[:40] for m in TG]}")

def run(fn):
    try: return fn(), None
    except Exception as e: return None, e

def since(fx, mark): return fx.calls[mark:]

BUG_MARKS = ("NameError", "AttributeError", "TypeError", "KeyError", "is not defined", "UnboundLocalError")

def finish(allowed=()):
    """檔案最後一句。全域檢查自成一個情境（不繼承上一個情境的命中次數），而且有前提（第 19 種：檢查本身也會空跑）：
    - 前提：真的收集到錯誤區與推播、真的跑過至少一項斷言
    - 所有情境的錯誤區與推播裡，沒有非注入的程式錯誤（allowed 是本檔刻意注入的錯誤字串）"""
    fresh()
    check("H10", "（前提）有收集到各情境的錯誤區與推播，而且這支測試跑過斷言", len(ALL_ERR) > 0 and _counts["checks"] >= 1,
          f"收集 {len(ALL_ERR)} 則、斷言 {_counts['checks']} 項")
    got = selftest_modules()
    check("H10", "（前提）錯誤攔截涵蓋至少兩個不同模組（r29），而且背景執行緒的 traceback 攔得到（stderr 金絲雀，r32）",
          len(got - {"thread"}) >= 2 and "thread" in got, f"攔到 {sorted(got)}")
    ALL_ERR.extend(store.get().get("errors", [])); ALL_ERR.extend(TG)
    allowed = tuple(allowed) + ("自檢錯誤",)              # 上面自檢故意製造的錯誤
    scanned = ALL_ERR + STDERR.lines                     # 錯誤區、推播、標準錯誤（traceback）都掃
    bugs = [x for x in scanned if any(k in x for k in BUG_MARKS) and not any(a in x for a in allowed)]
    check("H10", "所有情境的錯誤區與推播裡，沒有非注入的程式錯誤", not bugs, f"{bugs[:3]}")
    print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{sorted(set(fails))}")
    sys.exit(1 if fails else 0)
