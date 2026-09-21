"""測試裡「先索引、沒先確認有東西」的靜態檢查（清單用法第 5 點 r35、r36）。在專案根目錄執行：python -m tests.check_indexing

對清單取 [0]／[-1]（含前提本身的條件）之前，同一個情境裡要先確認它有東西，否則突變或修改前的程式讓它是空的時，
測試本身會崩掉（腳本式測試一崩，後面的項目全部不跑）。算作「已確認」的寫法：
- 同一個敘述裡的短路保護：`x and x[-1]…`、`len(x) >= n and x[0]…`、`x[-1] if x else …`
- 之前同一情境有 `if x`／`if not x`／`len(x)`／`x and` 提到同一個名字，或有前提（描述含「前提」）的條件提到它
- 不會空的寫法：`(x or [預設])[-1]`、切片 `x[-1:]`、字面清單、迴圈變數
附人造資料自我驗證：python -m tests.check_indexing --self-test
"""
import ast, glob, re, sys

def idx_targets(tree):
    """找出 X[0]／X[-1]：回傳 (節點, X 的原始碼)"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and not isinstance(n.slice, ast.Slice):
            s = n.slice
            v = s.value if isinstance(s, ast.Constant) else (-s.operand.value if isinstance(s, ast.UnaryOp) and isinstance(s.operand, ast.Constant) else None)
            if v in (0, -1): out.append(n)
    return out

def safe_value(node):
    """(x or [非空預設])、字面清單、固定回傳兩個元素的 one()／run()、寫入（賦值目標）→ 不算"""
    v = node.value
    if isinstance(node.ctx, ast.Store): return True
    if isinstance(v, ast.Call) and getattr(v.func, "id", "") in ("one", "run"): return True
    if isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or) and isinstance(v.values[-1], (ast.List, ast.Tuple)) and v.values[-1].elts: return True
    if isinstance(v, (ast.List, ast.Tuple)) and v.elts: return True
    return False

def guarded_in_stmt(stmt_src, name):
    n = re.escape(name)
    return bool(re.search(rf"(\b{n}\s+and\b|len\({n}\)\s*(>=?|==)\s*[1-9]|\bif\s+{n}\b|{n}\[[^\]]*\]\s+if\s+{n}\b)", stmt_src))

def check_source(src):
    tree = ast.parse(src); out = []
    top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
    seen = ""                                        # 同一情境裡、這一句之前的原始碼
    loopvars = set()
    for n in top:
        seg = ast.get_source_segment(src, n) or ""
        if re.search(r"\bfresh\(", seg): seen = ""
        for f in ast.walk(n):
            if isinstance(f, (ast.For, ast.comprehension)):
                loopvars |= {x.id for x in ast.walk(f.target) if isinstance(x, ast.Name)}
        for sub in idx_targets(n):
            if safe_value(sub): continue
            name = ast.get_source_segment(src, sub.value) or ""
            if not name or name in loopvars or name.startswith(("[", "(")): continue
            base = re.sub(r"\[.*$", "", name)
            if guarded_in_stmt(seg, name) or guarded_in_stmt(seg, base): continue
            n_ = re.escape(name)
            if re.search(rf"(\bif\s+(not\s+)?{n_}\b|len\({n_}\)|\b{n_}\s+and\b|\bnot\s+{n_}\b)", seen): continue
            out.append((sub.lineno, name))
        seen += "\n" + seg
    return out

SELF = {
    "沒確認就取 [-1]":                   ("fx = fresh()\nx = fx.trades\ncheck('A', 'a', x[-1] == 1)\n", 1),
    "同一句短路保護":                     ("fx = fresh()\nx = fx.trades\ncheck('A', 'a', x and x[-1] == 1)\n", 0),
    "之前有 if not x":                   ("fx = fresh()\nx = fx.trades\nif not x: x = [1]\ncheck('A', 'a', x[-1] == 1)\n", 0),
    "(x or [預設])[-1]":                 ("fx = fresh()\ncheck('A', 'a', (fx.trades or [{}])[-1] == 1)\n", 0),
    "前提本身先索引（r36）":               ("fx = fresh()\ncheck('A', '（前提）有成交', fx.trades[-1]['id'] > 0)\n", 1),
    "換情境後之前的確認不算":              ("fx = fresh()\nif fx.trades: pass\nfx = fresh()\ncheck('A', 'a', fx.trades[0] == 1)\n", 1),
    "one()／run() 固定兩個元素、賦值目標不算": ("fx = fresh()\nfx.pos[k][0] = 60\ncheck('A', 'a', *one('x'))\nr = one('y')[0]\n", 0),
}

def self_test():
    bad = 0
    for name, (src, want) in SELF.items():
        n = len(check_source(src)); ok = n == want
        print(f"  {'✅' if ok else '❌'} {name}（問題 {n}，預期 {want}）"); bad += not ok
    return bad

def main():
    if "--self-test" in sys.argv: sys.exit(1 if self_test() else 0)
    total, files = 0, sorted(glob.glob("tests/test_r*.py"))
    for p in files:
        probs = check_source(open(p, encoding="utf-8").read())
        print(f"== {p}：{len(probs)} 處")
        for ln, name in probs: print(f"   第 {ln} 行：{name}[…] 之前沒確認有東西")
        total += len(probs)
    print(f"掃了 {len(files)} 支測試")
    if len(files) < 5: print("掃到的測試太少（掃描路徑錯了？）"); total += 1
    print("通過" if not total else f"共 {total} 處"); sys.exit(1 if total else 0)

if __name__ == "__main__":
    main()
