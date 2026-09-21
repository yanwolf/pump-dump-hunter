"""測試檔的靜態檢查（清單用法第 5 點 r21）。在專案根目錄執行：python -m tests.check_tests

檢查三件事：
1. 每個情境以 fresh() 開頭（重建乾淨狀態）。
2. 第 14 種：情境裡換掉的模組層級物件（B.xxx = ...、manager.xxx = ... 之類），fresh() 必須重設；
   否則前一個情境換掉的函式、留下的計數器會帶進下一個情境。
3. 第 15 種：否定句斷言看「斷言本身」判斷，不看描述。check() 的條件是
   `not ...`、`... not in ...`、`== 0`、`is None`、`len(...) == 0` 這類「什麼都沒發生也成立」的，
   同一情境裡、它之前必須有前提（描述含「前提」或「對照組」）。
靜態檢查抓得到大部分；「條件寫成正向、其實是否定」的仍要靠逐項突變（tests/mutation_check.py）。
"""
import ast, re, sys

FILES = ["tests/test_r12.py", "tests/test_r15.py", "tests/test_r18.py", "tests/test_r21.py"]
SHARED = ("B", "manager", "main", "preflight", "store", "telegram")
# 刻意在匯入時換掉、整個測試檔都不變的（不算洩漏）
ALLOW = {"telegram.send", "manager.telegram.send", "main.telegram.send", "time.sleep"}

def negative(expr):
    """這個條件在「程式什麼都沒做」時會不會自然成立。"""
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not): return True
    if isinstance(expr, ast.Compare):
        for op, right in zip(expr.ops, expr.comparators):
            if isinstance(op, ast.NotIn): return True
            if isinstance(op, (ast.Eq, ast.Is)) and isinstance(right, ast.Constant) and right.value in (0, None, False, "", ()): return True
    if isinstance(expr, ast.BoolOp):
        # A and B：只要有一項是正向（什麼都沒做時會失敗），整體就不會空跑 → 全部都否定才算否定
        # A or B ：任一項在什麼都沒做時成立，整體就成立 → 任一否定就算否定
        return all(negative(v) for v in expr.values) if isinstance(expr.op, ast.And) else any(negative(v) for v in expr.values)
    return False

def attr_name(node):
    parts = []
    while isinstance(node, ast.Attribute): parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name): parts.append(node.id); return ".".join(reversed(parts))
    return None

def main():
    problems = 0
    for path in FILES:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        fresh_def = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fresh"), None)
        fresh_src = ast.get_source_segment(src, fresh_def) if fresh_def else ""
        top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
        scen, cur = [], None
        for n in top:
            seg = ast.get_source_segment(src, n) or ""
            if re.search(r"\bfresh\(", seg):
                cur = dict(line=n.lineno, nodes=[]); scen.append(cur)
            if cur: cur["nodes"].append(n)
        out = []
        # 2. 換掉的模組層級物件要由 fresh() 重設
        patched = {}
        for s in scen:
            for n in s["nodes"]:
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            name = attr_name(t)
                            if name and name.split(".")[0] in SHARED and name not in ALLOW:
                                patched.setdefault(name, s["line"])
        for name, line in sorted(patched.items(), key=lambda x: x[1]):
            short = name.split(".", 1)[1] if name.startswith("manager.B.") else name
            reset_pats = [f"{name} =", f"{name}.clear(", f"{name}.update("]
            if name.startswith("manager.B."): reset_pats += [f"B.{name.split('.', 2)[2]} ="]
            if not any(p in fresh_src for p in reset_pats):
                out.append(f"第 {line} 行起：換掉了 {name}，但 fresh() 沒有重設（第 14 種）")
        # 3. 否定句斷言要有前提
        for s in scen:
            have_pre = False
            for n in s["nodes"]:
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "check" and len(sub.args) >= 3:
                        desc = sub.args[1].value if isinstance(sub.args[1], ast.Constant) else ""
                        if "前提" in desc or "對照組" in desc: have_pre = True; continue
                        if negative(sub.args[2]) and not have_pre:
                            out.append(f"第 {sub.lineno} 行：否定句斷言沒有前提（看斷言本身，第 15 種）：{desc[:50]}")
        print(f"== {path}：{len(scen)} 個情境，{len(out)} 個問題")
        for o in out: print("   " + o)
        problems += len(out)
    print("\n" + ("通過" if not problems else f"共 {problems} 個問題"))
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
