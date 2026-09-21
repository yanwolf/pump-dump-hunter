"""部署前的完整驗證。在專案根目錄執行：python -m scripts.verify
每一步都要證明自己真的執行了（清單用法第 5 點第 19 種：全域／靜態檢查本身也會空跑）：
- pyflakes 沒裝 → 失敗（不是印警告後照樣通過）；先掃一個已知有 undefined name 的金絲雀檔，必須報出來
- 行為測試：每支都要印出「全部通過」，而且至少跑了一定數量的斷言
- 其他檢查器都有自己的自我驗證，這裡一併跑
"""
import glob, os, re, subprocess, sys, tempfile

def sh(*cmd):
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True, timeout=1200)
    return p.returncode, p.stdout + p.stderr

steps, bad = [], 0
def step(name, ok, note=""):
    global bad
    print(f"  {'✅' if ok else '❌'} {name}" + (f"　{note}" if note else "")); bad += not ok

# 1. pyflakes：必須裝了、而且抓得到金絲雀
code, out = sh("-m", "pyflakes", "--version")
step("pyflakes 有裝", code == 0, out.strip()[:40])
d = tempfile.mkdtemp(); canary = os.path.join(d, "canary.py"); open(canary, "w").write("def f():\n    return not_defined_anywhere\n")
code, out = sh("-m", "pyflakes", canary)
step("pyflakes 抓得到金絲雀（已知的 undefined name）", "undefined name" in out)
code, out = sh("-m", "pyflakes", "app/", "tests/", "scripts/")
n_undef = len(re.findall(r"undefined name '", out)) + len(re.findall(r"unable to detect undefined names", out))   # import * 會讓它查不到，也算失敗
n_files = len(glob.glob("app/*.py") + glob.glob("tests/*.py") + glob.glob("scripts/*.py"))
step("app/ tests/ scripts/ 沒有 undefined name", n_undef == 0, f"掃 {n_files} 個檔、undefined {n_undef}")

# 2. 行為測試
tests = sorted(os.path.basename(p)[:-3] for p in glob.glob("tests/test_*.py"))
step("找到行為測試", len(tests) >= 7, f"{len(tests)} 支")
for t in tests:
    code, out = sh("-m", f"tests.{t}")
    n = len(re.findall(r"^  (✅|❌) ", out, re.M)) if t != "test_parity" else int((re.findall(r"(\d+)/\1 筆", out) or ["0"])[0])
    ok = code == 0 and ("全部通過" in out or "完全一致" in out)
    step(f"tests.{t}", ok and n >= 5, f"{n} 項" + ("" if ok else "　" + out.strip().splitlines()[-1][:80]))

# 3. 檢查器與它們的自我驗證
for name, args in [("測試框架自我驗證", ["-m", "tests.harness_selftest"]),
                   ("測試靜態檢查（含金絲雀）", ["-m", "tests.check_tests"]),
                   ("先索引沒先確認：自我驗證", ["-m", "tests.check_indexing", "--self-test"]),
                   ("先索引沒先確認（含前提本身）", ["-m", "tests.check_indexing"]),
                   ("回傳原因語法樹檢查：自我驗證", ["-m", "scripts.check_returns", "--self-test"]),
                   ("回傳原因語法樹檢查", ["-m", "scripts.check_returns"]),
                   ("改寫工具 apply() 自我驗證", ["-m", "scripts.patch"]),
                   ("突變檢查器自我驗證", ["-m", "tests.mutation_selftest"]),
                   ("逐項突變檢查", ["-m", "tests.mutation_check"]),
                   *[(f"全部測試跑專案裡存的舊版 {v}：測試／框架崩掉 0 支", ["-m", "tests.rerun_old", f"legacy:{v}"])
                     for v in sorted(os.listdir("tests/legacy")) if os.path.isdir(os.path.join("tests/legacy", v))],
                   ("全部測試跑上一版程式：框架崩掉 0 支（需要 PDH_PREV 指向上一版目錄，沒設就略過）",
                    ["-c", "import os,subprocess,sys; p=os.environ.get('PDH_PREV'); sys.exit(subprocess.call([sys.executable,'-m','tests.rerun_old',p]) if p else 0)"])]:
    code, out = sh(*args)
    step(name, code == 0, out.strip().splitlines()[-1][:90] if out.strip() else "（沒有輸出）")

print("\n驗證通過，可以部署" if not bad else f"\n{bad} 項沒過，不能部署"); sys.exit(1 if bad else 0)
