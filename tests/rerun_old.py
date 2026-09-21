"""拿現在的測試跑舊版程式，失敗分三類（清單用法第 5 點 r35、r36）。在專案根目錄執行：
    python -m tests.rerun_old <舊版專案目錄> [測試名 …]
- 斷言失敗：程式行為不對（這是要找的）
- 被測程式拋錯：測試用 run() 接住的例外，細節裡有 err=（程式在那個版本會拋）
- 測試／框架崩掉：沒印出總結，traceback 最後一層在 tests/（測試或框架在舊版上不能用，要修的是測試）
舊版程式要留著才能做這種比對：部署前在 git 打標籤（例如 lessons-r34），之後用 `git worktree add ../pdh-r34 lessons-r34` 取出。
"""
import glob, os, re, shutil, subprocess, sys, tempfile
from tests.mutation_check import crash_of

def main():
    if len(sys.argv) < 2: sys.exit(__doc__)
    old = sys.argv[1]; names = sys.argv[2:] or sorted(os.path.basename(p)[:-3] for p in glob.glob("tests/test_r*.py"))
    work = tempfile.mkdtemp()
    shutil.copytree(old, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "data"))
    for f in glob.glob("tests/*.py"): shutil.copy(f, os.path.join(work, "tests"))
    os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
    for f in glob.glob("scripts/*.py"): shutil.copy(f, os.path.join(work, "scripts"))
    env = dict(os.environ); env.pop("PDH_MUTATE", None); env["DATA_DIR"] = tempfile.mkdtemp()
    tot = dict(assert_=0, prog=0, crash=0)
    for t in names:
        p = subprocess.run([sys.executable, "-m", f"tests.{t}"], cwd=work, env=env, capture_output=True, text=True, timeout=600)
        fails = re.findall(r"^  ❌ \[[^\]]+\] (.+)$", p.stdout, re.M)
        prog = [f for f in fails if re.search(r"err=(?!None)\S", f)]
        crash = crash_of(p.returncode, p.stdout, p.stderr)
        tot["assert_"] += len(fails) - len(prog); tot["prog"] += len(prog); tot["crash"] += bool(crash)
        print(f"  {t:10s} 斷言失敗 {len(fails) - len(prog):2d}　被測程式拋錯 {len(prog):2d}　測試／框架崩掉 {'是：' + crash if crash else '否'}")
    print(f"合計：斷言失敗 {tot['assert_']}、被測程式拋錯 {tot['prog']}、測試／框架崩掉 {tot['crash']} 支")
    sys.exit(1 if tot["crash"] else 0)

if __name__ == "__main__":
    main()
