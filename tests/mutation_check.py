"""突變檢查：逐項比對（清單用法第 5 點 r21）。在專案根目錄執行：python -m tests.mutation_check

突變 no_base：模擬交易所的逐幣部位查詢一律回空清單（PDH_MUTATE=no_base）→ 基準查不到、平倉確認查不到、守衛確認查不到。
規則：
1. 突變下仍通過的**每一項**，都必須列在 tests/mutation_exempt.py 並寫理由；不在清單的 = 空跑。
   （r18 版只要求「每個會送單的情境至少一項失敗」，抓不到單項空跑。）
2. 理由是「前提」或「對照組」的，被引用的那一項在突變下**必須真的失敗**，否則豁免不成立。
3. 豁免清單裡、實際上已經不存在或突變下已經會失敗的項目要刪掉（避免清單過期、蓋住將來的空跑）。
4. 正常（沒有突變）時，所有測試必須全部通過——豁免只在突變下有意義。
"""
import os, re, subprocess, sys
from tests.mutation_exempt import EXEMPT

TESTS = ["tests.test_r12", "tests.test_r15", "tests.test_r18", "tests.test_r21"]
LINE = re.compile(r"^  (✅|❌) \[[^\]]+\] (.+?)(?:　.*)?$", re.M)

def run(module, mutate):
    env = dict(os.environ); env.pop("PDH_MUTATE", None)
    if mutate: env["PDH_MUTATE"] = mutate
    p = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, text=True, timeout=300)
    items = LINE.findall(p.stdout)
    return [n for s, n in items if s == "✅"], [n for s, n in items if s == "❌"]

def match(prefix, names):
    return [n for n in names if n.startswith(prefix)]

def main():
    problems = []
    normal_fail = {}
    passed, failed = {}, {}
    for mod in TESTS:
        ok, bad = run(mod, None)
        if bad: normal_fail[mod] = bad
        passed[mod], failed[mod] = run(mod, "no_base")
    for mod, bad in normal_fail.items():
        problems.append(f"{mod} 正常情況就有 {len(bad)} 項失敗（先修好再做突變檢查）：{bad[:2]}")

    used = set()
    for mod in TESTS:
        for name in passed[mod]:
            keys = [k for k in EXEMPT if k[0] == mod and name.startswith(k[1])]
            if not keys:
                problems.append(f"⚠️ 空跑：{mod}「{name[:60]}」突變下仍通過，也不在豁免清單"); continue
            k = keys[0]; used.add(k)
            kind, arg = EXEMPT[k][0], EXEMPT[k][1]
            if kind in ("前提", "對照組"):
                ref_mod = EXEMPT[k][2] if len(EXEMPT[k]) > 2 else mod
                if not match(arg, failed.get(ref_mod, [])):
                    problems.append(f"⚠️ 豁免不成立：{mod}「{name[:40]}」引用的{kind}「{arg[:40]}」在突變下沒有失敗")
    for k in EXEMPT:
        if k not in used:
            problems.append(f"清單過期：{k[0]}「{k[1][:40]}」突變下已不再通過（或已不存在），請從豁免清單刪掉")

    total_pass = sum(len(v) for v in passed.values()); total_fail = sum(len(v) for v in failed.values())
    print(f"突變 no_base：{len(TESTS)} 支測試，突變下失敗 {total_fail} 項、仍通過 {total_pass} 項（逐項比對豁免清單）")
    for p in problems: print("  " + p)
    print("通過：突變下仍通過的每一項都有理由，引用的前提／對照組都真的失敗" if not problems else f"共 {len(problems)} 個問題")
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
