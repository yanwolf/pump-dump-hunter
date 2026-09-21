"""突變檢查（清單用法第 5 點 r18）：故意弄壞新規則的前置條件，找出「空跑通過」的測試。

突變 no_base：模擬交易所的逐幣部位查詢一律回空清單 → 送單前基準查不到 → 進場單不送。
在這個突變下，**要靠送出進場單才成立的測試必須明確失敗**。還通過的，要逐一判斷：
  - 本來就跟進場無關（例如直接建好部位、測平倉或守衛）→ 正常
  - 需要進場單卻還通過 → 空跑：補前提斷言「單真的送出去了」

在專案根目錄執行：python -m tests.mutation_check
列出每支測試在突變下「仍通過」的項目，並標出其中屬於「會送進場單」情境的。
"""
import os, re, subprocess, sys

TESTS = ["tests.test_r12", "tests.test_r15", "tests.test_r18"]
# 這些情境的程式路徑會呼叫 main.place（送進場單）。在突變下，情境內至少要有一項明確失敗。
PLACE_MARKERS = ("main.place(",)

def scenarios(module):
    """把測試檔依 fresh() 切成情境，回傳 [(起始行號, 是否呼叫 place, 該情境的 check 名稱)]"""
    src = open(module.replace(".", "/") + ".py", encoding="utf-8").read().splitlines()
    out, cur = [], None
    for i, line in enumerate(src, 1):
        if "fresh(" in line and "def fresh" not in line:
            cur = dict(line=i, place=False, checks=[], exempt="突變豁免" in line); out.append(cur)
        if cur is None: continue
        if any(m in line for m in PLACE_MARKERS): cur["place"] = True
        m = re.search(r'check\("[^"]+",\s*"([^"]+)"', line)
        if m: cur["checks"].append(m.group(1))
    return out

def run(module, mutate):
    env = dict(os.environ, PDH_MUTATE=mutate)
    p = subprocess.run([sys.executable, "-m", module], env=env, capture_output=True, text=True, timeout=300)
    passed = set(re.findall(r"✅ \[[^\]]+\] (.+?)(?:　|$)", p.stdout, re.M))
    failed = set(re.findall(r"❌ \[[^\]]+\] (.+?)(?:　|$)", p.stdout, re.M))
    return passed, failed, p.stdout

def main():
    bad = 0
    for mod in TESTS:
        passed, failed, _ = run(mod, "no_base")
        print(f"\n===== {mod}（突變 no_base：逐幣查詢回空 → 基準查不到 → 不送進場單）=====")
        print(f"  仍通過 {len(passed)} 項、失敗 {len(failed)} 項")
        for sc in scenarios(mod):
            if not sc["place"]: continue
            if sc["exempt"]:
                print(f"  （豁免）第 {sc['line']} 行起的情境：本來就在測突變條件本身"); continue
            names = [n for n in sc["checks"] if n]
            still = [n for n in names if any(n.startswith(x[:40]) or x.startswith(n[:40]) for x in passed)]
            broke = [n for n in names if any(n.startswith(x[:40]) or x.startswith(n[:40]) for x in failed)]
            flag = "⚠️ 空跑" if not broke else "✅ 有明確失敗"
            if not broke: bad += 1
            print(f"  {flag}　第 {sc['line']} 行起的情境（會送進場單）：失敗 {len(broke)} 項、仍通過 {len(still)} 項")
            for n in still: print(f"      仍通過：{n}")
    print("\n" + ("所有會送進場單的情境，在突變下都有明確失敗的項目" if not bad else f"{bad} 個情境在突變下沒有任何項目失敗 → 空跑，要補前提斷言"))
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
