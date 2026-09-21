"""回傳原因的語法樹檢查（清單第 2 條 r24～r27）。在專案根目錄執行：python -m scripts.check_returns

檢查 TARGETS 裡每個函式：
1. 每個 return 都帶值；明寫 `return None` 也算沒帶。
2. 函式最後不會掉出去——最後一句不是 return／raise 時，Python 一樣回 None。
   「不會掉出去」的判斷：最後一句是 return／raise；或是 if/else 兩邊都不會掉出去；或是 try 的本體與每個 except 都不會掉出去
   （有 finally 時看 finally 之前）；while True 視為不會掉出去。
附 6 組人造函式自我驗證：python -m scripts.check_returns --self-test
"""
import ast, sys

TARGETS = {"app/manager.py": ["ensure_stop", "move_stop", "retry_stop", "place_stop", "retry_close"]}

def bare(ret):
    return ret.value is None or (isinstance(ret.value, ast.Constant) and ret.value.value is None)

def ends(stmts):
    """這段敘述結尾是不是一定離開（return／raise），不會往下掉。"""
    if not stmts: return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise)): return True
    if isinstance(last, ast.If): return ends(last.body) and ends(last.orelse)
    if isinstance(last, ast.Try):
        if last.orelse and not ends(last.orelse) and not ends(last.body): return False
        return (ends(last.body) or ends(last.orelse)) and all(ends(h.body) for h in last.handlers)
    if isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value is True: return True
    return False

def check_fn(fn):
    probs = []
    for r in ast.walk(fn):
        if isinstance(r, ast.Return) and bare(r): probs.append(f"第 {r.lineno} 行 return 沒帶值")
    if not ends(fn.body): probs.append("最後會掉出函式（回 None）")
    return probs

def check_file(path, names):
    tree = ast.parse(open(path, encoding="utf-8").read())
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    out = {}
    for name in names:
        out[name] = ["找不到這個函式"] if name not in fns else check_fn(fns[name])
    return out

SELF = {
    "全部帶值、最後是 return":            ("def f(x):\n    if x: return 'a'\n    return 'b'\n", 0),
    "有裸 return":                       ("def f(x):\n    if x: return\n    return 'b'\n", 1),
    "明寫 return None":                  ("def f(x):\n    if x: return None\n    return 'b'\n", 1),
    "最後一句是 if、會掉出去":            ("def f(x):\n    if x: return 'a'\n", 1),
    "try／except 兩邊都 return":          ("def f(x):\n    try:\n        return 'a'\n    except Exception:\n        return 'b'\n", 0),
    "except 沒 return、會掉出去":          ("def f(x):\n    try:\n        return 'a'\n    except Exception:\n        pass\n", 1),
}

def self_test():
    bad = 0
    for name, (src, want) in SELF.items():
        n = len(check_fn(ast.parse(src).body[0])); ok = n == want
        print(f"  {'✅' if ok else '❌'} {name}（問題 {n}，預期 {want}）"); bad += not ok
    return bad

def main():
    if "--self-test" in sys.argv: sys.exit(1 if self_test() else 0)
    total, scanned = 0, 0
    for path, names in TARGETS.items():
        for name, probs in check_file(path, names).items():
            scanned += 1
            print(f"  {'✅' if not probs else '❌'} {path}:{name}" + ("" if not probs else "　" + "；".join(probs)))
            total += len(probs)
    print(f"掃了 {scanned} 個函式（預期 {sum(len(v) for v in TARGETS.values())}）")
    if scanned != sum(len(v) for v in TARGETS.values()): total += 1
    print("通過" if not total else f"共 {total} 個問題"); sys.exit(1 if total else 0)

if __name__ == "__main__":
    main()
