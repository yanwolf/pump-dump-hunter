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
    "一般項目印這個情境的命中次數": (
        'fx = fresh()\nfx.mut_hits = 3\ntelegram.send("x")\ncheck("A", "一般", True)\nfinish()\n', 0, "一般〔命中3〕"),
}

fails = 0
for name, (body, want_code, want_text) in CASES.items():
    code = "from tests.harness import *\n" + body
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    ok = p.returncode == want_code and want_text in p.stdout
    print(f"  {'✅' if ok else '❌'} {name}" + ("" if ok else f"　結束碼={p.returncode} 輸出末段={p.stdout[-200:]!r} {p.stderr[-200:]!r}"))
    fails += not ok
print("\n全部通過" if not fails else f"\n{fails} 組失敗"); sys.exit(1 if fails else 0)
