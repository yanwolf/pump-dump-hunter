"""測試檔的靜態檢查（清單用法第 5 點 r21～r27）。在專案根目錄執行：python -m tests.check_tests

每支 tests/test_rNN.py：
1. 用共用框架（`from tests.harness import *`），結尾呼叫 finish()（r27：框架各自複製會漏）。
2. 每個情境以 fresh() 開頭；情境裡換掉的模組層級物件，必須在框架的 RESET_ATTRS 裡（第 14 種）。
3. 否定句斷言看斷言本身（`not …`、`… not in …`、`== 0`、`is None`；and 全部否定才算、or 任一否定就算），
   同一情境裡它之前必須有前提（描述含「前提」或「對照組」）（第 15 種）。
4. 每個情境至少一條正向斷言（r26：只有否定句的情境，程式什麼都沒做也會全部通過）。
5. infra=True 的項目，條件只能讀模擬交易所（fx.…），不能碰程式（r27：基礎設施項目才能自動歸為無關）。
6. 檢查本身不能空跑（第 19 種）：印出掃了幾支、幾個情境、幾項斷言；任一支掃不到情境就失敗；
   並先掃一段已知有問題的人造測試（金絲雀），必須報出問題。
"""
import ast, glob, re, sys

SHARED = ("B", "manager", "main", "preflight", "store", "telegram")
ALLOW = {"telegram.send", "manager.telegram.send", "main.telegram.send", "time.sleep"}

def reset_names():
    """從框架讀出 fresh() 會還原的東西：RESET_ATTRS 裡的 (模組, 屬性)。"""
    src = open("tests/harness.py", encoding="utf-8").read()
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RESET_ATTRS" for t in n.targets):
            return {f"{ast.get_source_segment(src, e.elts[0])}.{e.elts[1].value}" for e in n.value.elts}
    return set()

def negative(expr):
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not): return True
    if isinstance(expr, ast.Compare):
        for op, right in zip(expr.ops, expr.comparators):
            if isinstance(op, ast.NotIn): return True
            if isinstance(op, (ast.Eq, ast.Is)) and isinstance(right, ast.Constant) and right.value in (0, None, False, "", ()): return True
    if isinstance(expr, ast.BoolOp):
        return all(negative(v) for v in expr.values) if isinstance(expr.op, ast.And) else any(negative(v) for v in expr.values)
    return False

def attr_name(node):
    parts = []
    while isinstance(node, ast.Attribute): parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name): parts.append(node.id); return ".".join(reversed(parts))
    return None

def only_fx(expr):
    """infra 項的條件只能讀模擬交易所：用到的名字只能是 fx 與內建函式。"""
    names = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}
    return names <= {"fx", "len", "any", "all", "abs", "float", "int", "p", "o", "c", "x"} and "fx" in names

def check_source(src, resets):
    tree = ast.parse(src); out = []
    if "from tests.harness import (" not in src:
        out.append("沒有用共用框架，或用了 import *（pyflakes 會因此查不到未定義名稱，第 19 種）")
    last = tree.body[-1] if tree.body else None
    if not (last is not None and "finish(" in (ast.get_source_segment(src, last) or "")): out.append("結尾沒有呼叫 finish()")
    top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
    scen, cur = [], None
    for n in top:
        seg = ast.get_source_segment(src, n) or ""
        if re.search(r"\bfresh\(", seg) and "finish(" not in seg:
            cur = dict(line=n.lineno, nodes=[]); scen.append(cur)
        if cur: cur["nodes"].append(n)
    checks = 0
    for s in scen:
        have_pre, positive = False, 0
        for n in s["nodes"]:
            for sub in ast.walk(n):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        name = attr_name(t)
                        if name and name.split(".")[0] in SHARED and name not in ALLOW:
                            norm = name[len("manager."):] if name.startswith("manager.B.") else name
                            if norm not in resets: out.append(f"第 {s['line']} 行起：換掉了 {name}，但框架的 RESET_ATTRS 沒有它（第 14 種）")
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "check" and len(sub.args) >= 3:
                    checks += 1
                    desc = sub.args[1].value if isinstance(sub.args[1], ast.Constant) else ""
                    infra = any(k.arg == "infra" and getattr(k.value, "value", False) for k in sub.keywords)
                    if infra and not only_fx(sub.args[2]):
                        out.append(f"第 {sub.lineno} 行：infra 項的條件碰到了程式，不能自動歸為無關：{desc[:40]}")
                    if "前提" in desc or "對照組" in desc: have_pre = True; positive += 1; continue
                    if negative(sub.args[2]):
                        if not have_pre: out.append(f"第 {sub.lineno} 行：否定句斷言沒有前提（第 15 種）：{desc[:50]}")
                    else: positive += 1
        if positive == 0: out.append(f"第 {s['line']} 行起的情境：沒有任何正向斷言（r26）")
    if not scen: out.append("一個情境都沒掃到（掃描方式錯了？）")
    return out, len(scen), checks

CANARY = '''from tests.harness import (check, fresh, manager)
fx = fresh()
manager.foo = 1
check("Z", "只有否定句", not fx.calls)
'''

def main():
    resets = reset_names()
    problems = 0
    canary, _, _ = check_source(CANARY, resets)                    # 金絲雀：必須報出問題（第 19 種）
    need = ["RESET_ATTRS 沒有它", "否定句斷言沒有前提", "沒有任何正向斷言", "結尾沒有呼叫 finish()"]
    missing = [k for k in need if not any(k in c for c in canary)]
    print(f"金絲雀（已知有問題的人造測試）：報出 {len(canary)} 個問題" + ("，四種都抓到" if not missing else f"，漏掉 {missing}"))
    if missing: problems += 1
    files = sorted(glob.glob("tests/test_r*.py"))
    tot_s = tot_c = 0
    for path in files:
        out, ns, nc = check_source(open(path, encoding="utf-8").read(), resets)
        tot_s += ns; tot_c += nc
        print(f"== {path}：{ns} 個情境、{nc} 項斷言、{len(out)} 個問題")
        for o in out: print("   " + o)
        problems += len(out)
    print(f"\n掃了 {len(files)} 支測試、{tot_s} 個情境、{tot_c} 項斷言；框架會還原 {len(resets)} 個模組層級物件")
    if len(files) < 5 or tot_s < 30 or not resets:
        print("掃到的數量太少（掃描方式錯了？）"); problems += 1
    print("通過" if not problems else f"共 {problems} 個問題"); sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
