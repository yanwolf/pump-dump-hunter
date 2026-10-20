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
import ast, glob, os, re, sys

SHARED = ("B", "manager", "main", "preflight", "store", "telegram")
ALLOW = {"telegram.send", "manager.telegram.send", "main.telegram.send", "time.sleep"}

def reset_names():
    """從框架讀出 fresh() 會還原的東西：RESET_ATTRS 裡的 (模組, 屬性)。"""
    src = open("tests/harness.py", encoding="utf-8").read()
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "RESET_ATTRS" for t in n.targets):
            return {f"{ast.get_source_segment(src, e.elts[0])}.{e.elts[1].value}" for e in n.value.elts}
    return set()

_MOD_CONSTS = {}                                 # 檔案最上層寫死的非空清單名稱（check_source 掃檔前填好）

def _never_empty(node):
    """迭代來源一定非空：寫死的非空 list／tuple／set、非空字串、檔案最上層寫死的非空清單名稱（r100）。"""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)): return bool(node.elts)
    if isinstance(node, ast.Constant) and isinstance(node.value, str): return bool(node.value)
    if isinstance(node, ast.Name): return node.id in _MOD_CONSTS
    return False

def negative(expr):
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not): return True
    # all(...) 對空清單是 True：程式什麼都沒做（清單是空的）也成立，跟否定句一樣（r44）
    if isinstance(expr, ast.Call) and getattr(expr.func, "id", "") == "all":
        # all(...) 在迭代來源空的時候成立（第 25 種，r44）。來源**一定非空、而且沒有 if 篩選**才是正向斷言（r99、r100）：
        # 寫死的非空 list／tuple／set、非空字串、檔案最上層寫死的非空清單名稱。帶篩選的照報（篩完可能空）、寫死但空的照報、執行時才算出來的照報
        g = expr.args[0] if expr.args else None
        if isinstance(g, (ast.GeneratorExp, ast.ListComp, ast.SetComp)) and all(not c.ifs and _never_empty(c.iter) for c in g.generators):
            return False
        return True
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

def _desc(node):
    """斷言描述：純字串，或 f-string 的固定部分（r47：f-string 描述的前提以前被當成空字串，「前提」兩個字認不出來）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
    if isinstance(node, ast.JoinedStr): return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""

def only_fx(expr):
    """infra 項的條件只能讀模擬交易所：用到的名字只能是 fx 與內建函式。"""
    names = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}
    return names <= {"fx", "len", "any", "all", "abs", "float", "int", "p", "o", "c", "x"} and "fx" in names

def check_source(src, resets):
    tree0 = ast.parse(src)
    _MOD_CONSTS.clear()
    for n in tree0.body:                         # 檔案最上層寫死的非空清單：FIELDS = ("a", "b")（只認寫一次、不會被改的）
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and _never_empty(n.value):
            _MOD_CONSTS[n.targets[0].id] = True
    for n in ast.walk(tree0):                    # 之後被重新指定或就地修改的不算寫死
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in _MOD_CONSTS and \
                sum(1 for m in tree0.body if isinstance(m, ast.Assign) and any(isinstance(t, ast.Name) and t.id == n.id for t in m.targets)) > 1:
            _MOD_CONSTS.pop(n.id, None)
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
                            if norm not in resets and ".".join(name.split(".")[-2:]) not in resets: out.append(f"第 {s['line']} 行起：換掉了 {name}，但框架的 RESET_ATTRS 沒有它（第 14 種）")
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "check" and len(sub.args) >= 3:
                    checks += 1
                    desc = _desc(sub.args[1])
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
CANARY_FIELDS = ("a", "b")
fx = fresh()
manager.foo = 1
check("Z", "只有否定句", not fx.calls)
check("Z", "all() 對空清單成立", all(c for c in fx.calls))
fx = fresh()
check("Z", "all() 迭代寫死的非空元組：是正向斷言，不該被當成否定句", all(k in "abc" for k in ("a", "b")))
fx = fresh()
check("Z", "all() 迭代寫死的非空 set：不該被當成否定句", all(k in "abc" for k in {"a", "b"}))
fx = fresh()
check("Z", "all() 迭代非空字串：不該被當成否定句", all(c in "abc" for c in "ab"))
fx = fresh()
check("Z", "all() 迭代檔案最上層寫死的清單名稱：不該被當成否定句", all(k in "abc" for k in CANARY_FIELDS))
fx = fresh()
check("Z", "all() 帶 if 篩選：篩完可能空，仍是否定句", all(k in "abc" for k in ("a", "b") if k in fx.calls))
fx = fresh()
check("Z", "all() 寫死但是空的：仍是否定句", all(k in "abc" for k in ()))
fx = fresh()
why = "x"
check("Z", f"（前提）{why}：f-string 的前提也要認得", len(fx.calls) >= 0)
check("Z", "f-string 前提之後的否定句不該被報", not fx.calls)
'''

def state_coverage():
    """app/ 裡底線開頭的模組層級 dict／list／set，都要在框架的 RESET_STATE 或 STATE_EXEMPT 裡（第 14 種，r44）。"""
    h = open("tests/harness.py", encoding="utf-8").read()
    block = h[h.index("RESET_STATE = ["):h.index("RESET_STATE = [(m, a)")]
    alias = {"B": "binance", "main": "main", "manager": "manager", "preflight": "preflight", "_presets": "presets",
             "_risk": "risk", "_signals": "signals", "store": "store", "telegram": "telegram"}
    listed = {f"{alias.get(m, m)}.{a}" for m, a in re.findall(r'\((\w+), "(\w+)"\)', block)}
    exempt = set(re.findall(r'"(\w+\.\w+)":', h[h.index("STATE_EXEMPT"):h.index("def _restore_state")]))
    found, missing = [], []
    for f in sorted(glob.glob("app/*.py")):
        mod = os.path.basename(f)[:-3]
        for n in ast.parse(open(f, encoding="utf-8").read()).body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                name, v = n.targets[0].id, n.value
                mutable = isinstance(v, (ast.Dict, ast.List, ast.Set)) or (isinstance(v, ast.Call) and getattr(v.func, "id", "") in ("dict", "list", "set"))
                if name.startswith("_") and not name.isupper() and mutable:
                    found.append(f"{mod}.{name}")
                    if f"{mod}.{name}" not in listed | exempt: missing.append(f"{mod}.{name}")
    return found, missing

def main():
    resets = reset_names()
    found, missing = state_coverage()
    print(f"模組層級的可變狀態：找到 {len(found)} 個，框架沒有重設的 {len(missing)} 個" + (f"：{missing}" if missing else ""))
    problems = len(missing) + (1 if len(found) < 5 else 0)          # 掃到太少 = 掃描方式錯了（第 19 種）
    canary, _, _ = check_source(CANARY, resets)                    # 金絲雀：必須報出問題（第 19 種）
    need = ["RESET_ATTRS 沒有它", "否定句斷言沒有前提", "沒有任何正向斷言", "結尾沒有呼叫 finish()"]
    if sum("否定句斷言沒有前提" in c for c in canary) < 2: missing_all = ["all() 沒被當成否定句"]
    elif any("f-string 前提之後的否定句" in c for c in canary): missing_all = ["f-string 描述的前提沒認出來"]
    elif any(k in c for c in canary for k in ("迭代寫死的非空元組", "迭代寫死的非空 set", "迭代非空字串", "迭代檔案最上層寫死的清單名稱")):
        missing_all = ["all() 迭代一定非空的來源被誤判成否定句"]
    elif not all(any(k in c for c in canary) for k in ("帶 if 篩選", "寫死但是空的")):
        missing_all = ["all() 帶篩選或寫死但空的沒有被當成否定句（r100：這兩種仍然要報）"]
    else: missing_all = []
    missing = [k for k in need if not any(k in c for c in canary)] + missing_all
    print(f"金絲雀（已知有問題的人造測試）：報出 {len(canary)} 個問題" + ("，四種都抓到" if not missing else f"，漏掉 {missing}"))
    if missing: problems += 1
    from tests import registry                       # 測試檔清單從登記表拿（r101：以前 test_r*.py 樣式漏掉 test_stats、test_maxqty）
    files = ["tests/" + f for f in registry.files("negative")]
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
