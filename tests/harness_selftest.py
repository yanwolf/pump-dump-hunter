"""共用案例框架 tests/harness.py 的自我驗證：用人造的小測試跑它（清單用法第 5 點 r27）。
每一組都在獨立的子程序裡跑，因為 finish() 會結束程序、框架會換掉模組層級的函式。
在專案根目錄執行：python -m tests.harness_selftest
"""
import subprocess, sys

CASES = {
    "全部通過 → 結束碼 0、印「全部通過」": (
        'fx = fresh()\ntelegram.send("hi")\ncheck("A", "一加一", 1 + 1 == 2)\nfinish()\n', 0, "全部通過"),
    "有一項失敗 → 結束碼 1": (
        'fx = fresh()\ntelegram.send("hi")\ncheck("A", "錯的", False)\nfinish()\n', 1, "1 項失敗"),
    "情境裡有被吞掉的程式錯誤 → 全域檢查抓到": (
        'fx = fresh()\nstore.push("errors", "boom NameError: x")\ncheck("A", "正常", True)\nfinish()\n', 1, "沒有非注入的程式錯誤"),
    "刻意注入的錯誤字串在 allowed 裡 → 不算": (
        'fx = fresh()\nstore.push("errors", "注入的 KeyError: q")\ncheck("A", "正常", True)\nfinish(allowed=("注入的",))\n', 0, "全部通過"),
    "什麼都沒收集到 → 全域檢查的前提失敗（第 19 種：檢查本身不能空跑）": (
        'fx = fresh()\ncheck("A", "正常", True)\nfinish()\n', 1, "（前提）有收集到"),
    "fresh() 還原被換掉的函式": (
        'fx = fresh()\nmanager._now = lambda: 1\nfx = fresh()\nprint("還原", manager._now() != 1)\ntelegram.send("x")\ncheck("A", "還原了", manager._now() != 1)\nfinish()\n', 0, "還原 True"),
    "fresh() 清掉計數器與帳本": (
        'fx = fresh()\nmanager._missing["X"] = 2; manager._errs[("X","s")] = 3; store.update(open={"X": {}})\nfx = fresh()\n'
        'telegram.send("x")\ncheck("A", "清掉了", not manager._missing and not manager._errs and not store.get()["open"])\nfinish()\n', 0, "全部通過"),
    "infra=True → 印〔命中0〕〔基礎設施〕": (
        'fx = fresh()\ntelegram.send("x")\ncheck("A", "基礎設施", True, infra=True)\nfinish()\n', 0, "〔命中0〕〔基礎設施〕"),
    "錯誤攔截涵蓋多個模組（manager、main、presets），至少兩個（r29 的前提）": (
        'fx = fresh()\ngot = selftest_modules()\nprint("攔到", sorted(got))\ntelegram.send("x")\n'
        'check("A", "至少兩個模組", len(got) >= 2 and {"manager", "main", "presets", "thread"} <= got)\nfinish(allowed=("自檢錯誤",))\n', 0, "攔到 [\'main\', \'manager\', \'presets\', \'thread\']"),
    "金絲雀的 traceback 不會混進測試期間的錯誤掃描（用另一個緩衝區）": (
        'fx = fresh()\ntelegram.send("x")\nselftest_modules()\nprint("混進", sum("金絲雀" in x or "自檢錯誤" in x for x in STDERR.lines))\n'
        'check("A", "沒混進", not any("金絲雀" in x or "自檢錯誤" in x for x in STDERR.lines))\nfinish()\n', 0, "混進 0"),
    "只寫到標準錯誤的 traceback 也會被全域檢查掃到": (
        'import traceback\nfx = fresh()\ntelegram.send("x")\ntry: {}["k"]\nexcept KeyError: traceback.print_exc()\n'
        'check("A", "正常", True)\nfinish()\n', 1, "沒有非注入的程式錯誤"),
    "探針：往每一個模組層級狀態塞值，fresh() 之後全部消失（r45、r47）": (
        'from tests.harness import RESET_STATE\nfx = fresh()\nfor m, a in RESET_STATE:\n'
        '    v = getattr(m, a)\n'
        '    if isinstance(v, dict): v["__探針__"] = 1\n'
        '    elif isinstance(v, list): v.append("__探針__")\n'
        '    elif isinstance(v, set): v.add("__探針__")\n'
        'fx = fresh()\n'
        'left = [f"{m.__name__}.{a}" for m, a in RESET_STATE if "__探針__" in getattr(m, a)]\n'
        'print("探針數", len(RESET_STATE), "殘留", left)\ntelegram.send("x")\n'
        'check("A", "全部消失", len(RESET_STATE) >= 10 and not left)\nfinish()\n', 0, "殘留 []"),
    "繞過框架預設、真的開了補登執行緒 → 結尾報出來（數得到才算有檢查，r75）": (
        'from tests.harness import REAL_START_BACKFILL\nimport time as _t\nfx = fresh()\n'
        'manager._start_backfill = REAL_START_BACKFILL\nmanager._start_backfill(lambda: None)\n_t.sleep(0.2)\n'
        'telegram.send("x")\ncheck("A", "a", True)\nfinish()\n', 1, "真的開了 1 個"),
    "沒開補登執行緒 → 這一項通過": (
        'fx = fresh()\ntelegram.send("x")\ncheck("A", "a", True)\nfinish()\n', 0, "真的開了 0 個"),
    "結構性還原：新長出來的屬性刪掉、換掉的不可變值與旗標還原（清單式管不到的兩種，r79）": (
        'fx = fresh()\nd0 = manager.BACKFILL_DELAYS\n'
        'manager._brand_new_flag = True\nmanager.BACKFILL_DELAYS = (0,)\nmain._resumed["done"] = True\n'
        'fx = fresh()\nleft = [x for x, ok in (("新屬性", not hasattr(manager, "_brand_new_flag")), ("不可變值", manager.BACKFILL_DELAYS == d0),'
        ' ("旗標", not main._resumed.get("done"))) if not ok]\n'
        'print("沒還原", left)\ntelegram.send("x")\ncheck("A", "全部還原", not left)\nfinish()\n', 0, "沒還原 []"),
    "一般項目印這個情境的命中次數": (
        'fx = fresh()\nfx.mut_hits = 3\ntelegram.send("x")\ncheck("A", "一般", True)\nfinish()\n', 0, "一般〔命中3〕"),
}

fails = 0
for name, (body, want_code, want_text) in CASES.items():
    code = "from tests.harness import (B, STDERR, TG, check, finish, fresh, main, manager, selftest_modules, store, telegram)\n" + body
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    ok = p.returncode == want_code and want_text in p.stdout
    print(f"  {'✅' if ok else '❌'} {name}" + ("" if ok else f"　結束碼={p.returncode} 輸出末段={p.stdout[-200:]!r} {p.stderr[-200:]!r}"))
    fails += not ok
print("\n全部通過" if not fails else f"\n{fails} 組失敗"); sys.exit(1 if fails else 0)
