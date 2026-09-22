"""突變檢查器的自我驗證：用固定的人造資料跑 evaluate()（清單用法第 5 點 r23）。
不拿當下的豁免清單來改壞——清單裡的項目可能本身就過期，檢查器對它根本不看引用，那樣測不出來。
在專案根目錄執行：python -m tests.mutation_selftest
"""
import sys
from tests.mutation_check import crashed_in_test, evaluate, parse

M = "tests.fake"
fails = []
def expect(name, mut, exempt, want, normal=None, min_items=0):
    problems, _ = evaluate({M: mut}, normal or {M: []}, exempt, min_items=min_items)
    got = [p for p in problems if want in p] if want else problems
    ok = bool(got) if want else not problems
    print(f"  {'✅' if ok else '❌'} {name}" + ("" if ok else f"　問題={problems}"))
    if not ok: fails.append(name)

print("突變檢查器自我驗證（固定人造資料）")
expect("命中 0 次、突變下仍通過、沒豁免 → 自動判定無關，不報問題", [(True, "甲", 0)], {}, None)
expect("命中 > 0、突變下仍通過、沒豁免 → 報空跑", [(True, "甲", 2)], {}, "空跑")
expect("命中 > 0、豁免寫「無關」→ 豁免不成立", [(True, "甲", 2)], {(M, "甲"): ("無關", "x")}, "不能寫「無關」")
expect("命中 > 0、引用的前提在突變下也通過 → 豁免不成立",
       [(True, "甲", 2), (True, "乙前提", 1)], {(M, "甲"): ("前提", "乙前提"), (M, "乙前提"): ("前提", "甲")}, "豁免不成立")
expect("命中 > 0、引用的前提在突變下失敗 → 成立",
       [(True, "甲", 2), (False, "乙前提", 1)], {(M, "甲"): ("前提", "乙前提")}, None)
expect("引用別的模組的對照組、但那個模組沒有失敗紀錄 → 豁免不成立", [(True, "甲", 2)], {(M, "甲"): ("對照組", "丙", "tests.other")}, "豁免不成立")
expect("豁免清單有、但那一項突變下已經失敗 → 清單過期", [(False, "甲", 2)], {(M, "甲"): ("前提", "乙")}, "清單過期")
expect("豁免清單有、但那一項命中 0 次 → 清單過期（已自動處理）", [(True, "甲", 0)], {(M, "甲"): ("無關", "x")}, "清單過期")
expect("豁免清單有、但測試裡已沒有這一項 → 清單過期", [], {(M, "不存在"): ("前提", "乙")}, "清單過期")
expect("正常情況就失敗 → 報問題", [(True, "甲", 0)], {}, "正常情況", normal={M: ["甲"]})
expect("沒有印命中次數 → 報問題（避免舊格式測試靜靜被當成無關）", [(True, "甲", None)], {}, "沒有命中次數")
expect("解析到的項目太少 → 報問題（檢查本身不能空跑，第 19 種）", [(True, "甲", 0)], {}, "只解析到", min_items=5)
problems, _ = evaluate({M: [(True, "甲", 0)]}, {M: []}, {}, min_items=0, crashes={(M, "突變下"): "test_x.py:12"})
ok = any("測試本身崩掉" in p and "test_x.py:12" in p for p in problems)
print(f"  {'✅' if ok else '❌'} 突變下測試本身崩掉 → 報出來（含位置）" + ("" if ok else f"　{problems}"))
if not ok: fails.append("crash")
tb_test = 'Traceback (most recent call last):\n  File "/x/app/main.py", line 5, in f\n  File "/x/tests/test_r9.py", line 12, in <module>\nKeyError: 1\n'
tb_app = 'Traceback (most recent call last):\n  File "/x/tests/test_r9.py", line 12, in <module>\n  File "/x/app/main.py", line 5, in f\nKeyError: 1\n'
ok = crashed_in_test(tb_test) == "test_r9.py:12" and crashed_in_test(tb_app) is None and crashed_in_test("") is None
print(f"  {'✅' if ok else '❌'} traceback 最後一層在 tests/ 才算測試本身崩掉（在 app/ 是被測程式拋錯）")
if not ok: fails.append("crash-parse")
from tests.mutation_check import crash_of
canary = 'Exception in thread 金絲雀:\nTraceback (most recent call last):\n  File "/x/tests/harness.py", line 59, in <lambda>\nRuntimeError: 執行緒金絲雀\n'
ok = crash_of(1, "  ❌ [A] 甲\n\n1 項失敗：[\'A\']\n", canary) is None and crash_of(1, "  ❌ [A] 甲\n", tb_test) == "test_r9.py:12"
print(f"  {'✅' if ok else '❌'} 有印出總結時，金絲雀的 traceback 不算崩掉；沒印出總結才算")
if not ok: fails.append("crash-canary")
_, st = evaluate({M: [(False, "（前提）甲", 1), (False, "乙", 1), (False, "（對照組）丙", 1), (True, "丁", 0)]}, {M: []}, {}, min_items=0)
ok = st["failed"] == 3 and st["failed_pre"] == 2
print(f"  {'✅' if ok else '❌'} 突變下的失敗分兩類：前提 2、行為 1　{st if not ok else ''}")
if not ok: fails.append("classify")
# 解析器
rows = parse("  ✅ [K2] 甲　細節〔命中3〕\n  ❌ [K2] 乙〔命中0〕\n  ✅ [K2] 丙　x\n  ✅ [K2] 丁〔命中0〕〔基礎設施〕\n")
ok = rows == [(True, "甲", 3), (False, "乙", 0), (True, "丙", None), (True, "丁", 0)]
print(f"  {'✅' if ok else '❌'} 解析：名稱、細節、命中次數分得開　{rows if not ok else ''}")
if not ok: fails.append("parse")

# 跨模組那一組要用兩個模組才驗得到：引用的那邊失敗 → 成立
problems, _ = evaluate({M: [(True, "甲", 2)], "tests.other": [(False, "丙", 1)]}, {M: [], "tests.other": []},
                       {(M, "甲"): ("對照組", "丙", "tests.other")}, min_items=0)
ok = not problems
print(f"  {'✅' if ok else '❌'} 跨模組對照組：引用的那邊在突變下失敗 → 成立" + ("" if ok else f"　{problems}"))
if not ok: fails.append("cross")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{fails}")
sys.exit(1 if fails else 0)
