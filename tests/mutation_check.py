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

TESTS = ["tests.test_r12", "tests.test_r15", "tests.test_r18", "tests.test_r21", "tests.test_r24", "tests.test_r27", "tests.test_r30", "tests.test_r33", "tests.test_r36", "tests.test_r39", "tests.test_r43", "tests.test_r47", "tests.test_r50", "tests.test_maxqty", "tests.test_r53", "tests.test_r56", "tests.test_r58", "tests.test_r61", "tests.test_r64"]
LINE = re.compile(r"^  (✅|❌) \[[^\]]+\] (.+?)(?:　.*?)?(?:〔命中(\d+)〕)?(?:〔基礎設施〕)?$", re.M)

def parse(stdout):
    """回傳 [(通過?, 名稱, 命中次數)]"""
    return [(s == "✅", n, int(h) if h else None) for s, n, h in LINE.findall(stdout)]

def crashed_in_test(stderr):
    """traceback 的最後一層在 tests/ 裡 → 是測試程式本身拋錯（不是被測程式）。回傳那一層的描述或 None（清單用法第 5 點 r35、r36）。"""
    frames = re.findall(r'File "([^"]+)", line (\d+)', stderr or "")
    if not frames or "Traceback" not in stderr: return None
    path, line = frames[-1]
    return f"{path.split('/tests/')[-1]}:{line}" if "/tests/" in path.replace("\\", "/") else None

def crash_of(returncode, stdout, stderr):
    """真的崩掉 = 結束碼非 0 而且沒印出最後的總結（finish() 沒跑到）。只看結束碼加 traceback，
    會把框架故意製造的金絲雀（背景執行緒的 traceback，最後一層在 tests/harness.py）誤判成崩掉。"""
    finished = "全部通過" in stdout or re.search(r"\d+ 項失敗", stdout)
    return crashed_in_test(stderr) if returncode != 0 and not finished else None

def evaluate(mut, normal_fail, exempt, min_items=5, crashes=None):
    """純函式。mut: {模組: [(通過?, 名稱, 命中)]}（突變下的結果）；normal_fail: {模組: [失敗名稱]}；
    exempt: {(模組, 名稱開頭): (種類, 引用, [引用模組])}。回傳 (問題清單, 統計)。"""
    problems, used, auto = [], set(), 0
    for (mod, mode), where in (crashes or {}).items():
        if where: problems.append(f"⚠️ 測試本身崩掉：{mod}（{mode}）在 {where} 拋錯——後面的項目全部沒跑")
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
    # 突變下的失敗分兩類（清單用法第 5 點 r60）：失敗在前提＝只證明這項對被突變的查詢敏感（常常是準備階段就沒走到）；
    # 失敗在行為＝要測的那一步在突變下確實出了不同的結果。前者多，不代表「那一步查不到時該怎樣」有被驗證——那要靠直接測試。
    pre = sum(1 for v in failed.values() for n in v if n.startswith("（前提") or n.startswith("（對照組"))
    stats = dict(passed=sum(1 for items in mut.values() for ok, _, _ in items if ok),
                 failed=sum(len(v) for v in failed.values()), failed_pre=pre, auto=auto, manual=len(used))
    return problems, stats

def run(module, mutate):
    env = dict(os.environ); env.pop("PDH_MUTATE", None)
    if mutate: env["PDH_MUTATE"] = mutate
    p = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, text=True, timeout=300)
    return parse(p.stdout), crash_of(p.returncode, p.stdout, p.stderr)

def main():
    from tests.mutation_exempt import EXEMPT
    normal_fail, mut, crashes = {}, {}, {}
    for mod in TESTS:
        rows, c1 = run(mod, None); normal_fail[mod] = [n for ok, n, _ in rows if not ok]
        mut[mod], c2 = run(mod, "no_base")
        crashes[(mod, "正常")], crashes[(mod, "突變下")] = c1, c2
    problems, st = evaluate(mut, normal_fail, EXEMPT, crashes=crashes)
    print(f"突變 no_base：{len(TESTS)} 支測試，突變下失敗 {st['failed']} 項（失敗在前提 {st['failed_pre']}、失敗在行為 {st['failed'] - st['failed_pre']}）、仍通過 {st['passed']} 項"
          f"（命中 0 次自動判定無關 {st['auto']} 項、人工豁免 {st['manual']} 項）")
    for p in problems: print("  " + p)
    print("通過" if not problems else f"共 {len(problems)} 個問題")
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
