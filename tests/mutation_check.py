"""突變檢查：逐項比對（清單用法第 5 點 r21、r23）。在專案根目錄執行：python -m tests.mutation_check

突變 no_base：模擬交易所的逐幣部位查詢一律回空清單（PDH_MUTATE=no_base）。
每一項 check() 會印出這個情境到那時為止「被突變的查詢被呼叫了幾次」〔命中n〕。
規則：
1. 突變下仍通過、命中 0 次 → 自動判定「無關」（沒經過被突變的查詢，突變打不到是正常的），不用人寫理由。
2. 突變下仍通過、命中 > 0 → 必須列在 tests/mutation_exempt.py，理由是「前提」或「對照組」，
   而且被引用的那一項在突變下**必須真的失敗**。命中 > 0 卻寫「無關」→ 不成立。
3. 豁免清單裡用不到的項目（已經會失敗、已經命中 0 次自動處理、或測試已不存在）→ 過期，要刪掉。
4. 正常（沒有突變）時所有測試必須全部通過。
判定邏輯在 evaluate()，自我驗證用固定人造資料跑它（tests/mutation_selftest.py），不拿當下的豁免清單來改壞（r23）。
"""
import os, re, subprocess, sys

TESTS = ["tests.test_r12", "tests.test_r15", "tests.test_r18", "tests.test_r21", "tests.test_r24", "tests.test_r27"]
LINE = re.compile(r"^  (✅|❌) \[[^\]]+\] (.+?)(?:　.*?)?(?:〔命中(\d+)〕)?(?:〔基礎設施〕)?$", re.M)

def parse(stdout):
    """回傳 [(通過?, 名稱, 命中次數)]"""
    return [(s == "✅", n, int(h) if h else None) for s, n, h in LINE.findall(stdout)]

def evaluate(mut, normal_fail, exempt, min_items=5):
    """純函式。mut: {模組: [(通過?, 名稱, 命中)]}（突變下的結果）；normal_fail: {模組: [失敗名稱]}；
    exempt: {(模組, 名稱開頭): (種類, 引用, [引用模組])}。回傳 (問題清單, 統計)。"""
    problems, used, auto = [], set(), 0
    for mod, items in mut.items():
        if len(items) < min_items: problems.append(f"{mod} 在突變下只解析到 {len(items)} 項（輸出格式變了？檢查本身不能空跑，第 19 種）")
    for mod, bad in normal_fail.items():
        if bad: problems.append(f"{mod} 正常情況就有 {len(bad)} 項失敗（先修好再做突變檢查）：{bad[:2]}")
    failed = {m: [n for ok, n, _ in items if not ok] for m, items in mut.items()}
    for mod, items in mut.items():
        for ok, name, hits in items:
            if not ok: continue
            keys = [k for k in exempt if k[0] == mod and name.startswith(k[1])]
            if hits == 0 and not keys:
                auto += 1; continue                                             # 自動判定無關
            if hits is None and not keys:
                problems.append(f"⚠️ 沒有命中次數：{mod}「{name[:50]}」（測試的 check() 要印〔命中n〕）"); continue
            if not keys:
                problems.append(f"⚠️ 空跑：{mod}「{name[:60]}」突變下仍通過、命中 {hits} 次，也不在豁免清單"); continue
            k = keys[0]; used.add(k); kind, arg = exempt[k][0], exempt[k][1]
            if hits == 0:
                problems.append(f"清單過期：{mod}「{name[:40]}」命中 0 次，已自動判定無關，請從豁免清單刪掉"); continue
            if kind == "無關":
                problems.append(f"⚠️ 豁免不成立：{mod}「{name[:40]}」命中 {hits} 次，不能寫「無關」"); continue
            ref_mod = exempt[k][2] if len(exempt[k]) > 2 else mod
            if not any(n.startswith(arg) for n in failed.get(ref_mod, [])):
                problems.append(f"⚠️ 豁免不成立：{mod}「{name[:40]}」引用的{kind}「{arg[:40]}」在突變下沒有失敗")
    for k in exempt:
        if k not in used and not any(p.startswith("清單過期") and k[1][:20] in p for p in problems):
            problems.append(f"清單過期：{k[0]}「{k[1][:40]}」突變下已不再通過（或已不存在），請從豁免清單刪掉")
    stats = dict(passed=sum(1 for items in mut.values() for ok, _, _ in items if ok),
                 failed=sum(len(v) for v in failed.values()), auto=auto, manual=len(used))
    return problems, stats

def run(module, mutate):
    env = dict(os.environ); env.pop("PDH_MUTATE", None)
    if mutate: env["PDH_MUTATE"] = mutate
    p = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, text=True, timeout=300)
    return parse(p.stdout)

def main():
    from tests.mutation_exempt import EXEMPT
    normal_fail, mut = {}, {}
    for mod in TESTS:
        normal_fail[mod] = [n for ok, n, _ in run(mod, None) if not ok]
        mut[mod] = run(mod, "no_base")
    problems, st = evaluate(mut, normal_fail, EXEMPT)
    print(f"突變 no_base：{len(TESTS)} 支測試，突變下失敗 {st['failed']} 項、仍通過 {st['passed']} 項"
          f"（命中 0 次自動判定無關 {st['auto']} 項、人工豁免 {st['manual']} 項）")
    for p in problems: print("  " + p)
    print("通過" if not problems else f"共 {len(problems)} 個問題")
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
