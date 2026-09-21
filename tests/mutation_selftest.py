"""突變檢查器的自我驗證：用固定的人造資料跑 evaluate()（清單用法第 5 點 r23）。
不拿當下的豁免清單來改壞——清單裡的項目可能本身就過期，檢查器對它根本不看引用，那樣測不出來。
在專案根目錄執行：python -m tests.mutation_selftest
"""
import sys
from tests.mutation_check import evaluate, parse

M = "tests.fake"
fails = []
def expect(name, mut, exempt, want, normal=None):
    problems, _ = evaluate({M: mut}, normal or {M: []}, exempt)
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
# 解析器
rows = parse("  ✅ [K2] 甲　細節〔命中3〕\n  ❌ [K2] 乙〔命中0〕\n  ✅ [K2] 丙　x\n")
ok = rows == [(True, "甲", 3), (False, "乙", 0), (True, "丙", None)]
print(f"  {'✅' if ok else '❌'} 解析：名稱、細節、命中次數分得開　{rows if not ok else ''}")
if not ok: fails.append("parse")

# 跨模組那一組要用兩個模組才驗得到：引用的那邊失敗 → 成立
problems, _ = evaluate({M: [(True, "甲", 2)], "tests.other": [(False, "丙", 1)]}, {M: [], "tests.other": []},
                       {(M, "甲"): ("對照組", "丙", "tests.other")})
ok = not problems
print(f"  {'✅' if ok else '❌'} 跨模組對照組：引用的那邊在突變下失敗 → 成立" + ("" if ok else f"　{problems}"))
if not ok: fails.append("cross")

print("\n全部通過" if not fails else f"\n{len(fails)} 項失敗：{fails}")
sys.exit(1 if fails else 0)
