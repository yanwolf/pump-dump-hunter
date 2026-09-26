"""犯到第三次的測試錯誤，做成靜態檢查擋下（清單用法第 5 點 r63、r64）。在專案根目錄執行：python -m tests.check_repeat

pump-dump-hunter 自己犯過三次以上、而且之前只靠「記得」或執行期才抓到的兩種：
A. 「前提之後的動作讓測試本身崩掉」（r35 規則；r36、r44、r54、r57 各犯一次）：
   `fx.trigger(…)`／`fx._reduce(…)` 去平一個可能不存在的部位、`os.remove(檔)` 刪一個可能不在的檔。
   規則：這類呼叫所在的敘述、或包住它的 if，必須含對應的確認（`fx.qty(` ／ `os.path.exists(`）；寫在輔助函式裡也一樣掃。
B. 「呼叫新介面前先加存在性前提」（r38 規則；r44、r51 又各犯一次，都是在舊版程式上重跑才崩掉）：
   測試裡用到 manager／main／store／B／preflight／presets 的屬性，如果在 tests/legacy/ 任一版裡不存在，
   同一檔必須有 `hasattr(模組, "名稱")` 或 `getattr(模組, "名稱"…` 的守護。
「前提寫在動作之後」也犯了三次，但靜態分不出「前提要求動作發生了」（合法）跟「前提量到被測的結果」（不合法），只能靠突變檢查與
在舊版程式上重跑（執行期）抓；這裡不做。
附人造資料自我驗證：python -m tests.check_repeat --self-test
"""
import ast, glob, os, re, sys

# 測試裡指到程式模組的名稱 → 模組檔名。從 app/ 自動列出（r79：以前只列 6 個，stats、params、config 等都沒檢查）
def _app_modules():
    mods = {os.path.basename(f)[:-3] for f in glob.glob("app/*.py")} - {"__init__"}
    alias = {m: m for m in mods}
    alias.update(B="binance", C="config")
    return alias
MOD_FILE = _app_modules()

def _chain(node, alias, legacy_any):
    """把 main.scanner.scan、manager.telegram.send 這類鏈解成（模組檔, 屬性）。中間那層必須是程式模組（例如 main 裡的 scanner）。
    回 None 表示不是指到程式模組的屬性。"""
    parts = []
    while isinstance(node, ast.Attribute): parts.append(node.attr); node = node.value
    if not isinstance(node, ast.Name) or node.id not in alias: return None
    parts.reverse(); mod = alias[node.id]
    for i, p in enumerate(parts[:-1]):
        if p in alias.values() or p in ("scanner", "telegram", "store", "manager"):   # main.scanner、manager.telegram…
            mod = p if p in alias.values() else alias.get(p, p); continue
        return None                                                    # 物件的屬性（例如 C.SCAN["x"] 的 SCAN 之後），不是模組
    return mod, parts[-1]

def _scopes(tree):
    """用法的範圍（清單用法第 5 點 r79、r81）：每個函式是一個範圍；最上層依 fresh() 切成情境，每個情境是一個範圍。
    回 [(名稱, 敘述清單)]。範圍之間互不相通：前一個情境、別的函式裡的守護都不算。"""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)): out.append(("函式 " + n.name, list(n.body)))
    cur, k = [], 0
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)): continue
        if any(isinstance(x, ast.Call) and getattr(x.func, "id", "") == "fresh" for x in ast.walk(n)) and cur:
            out.append((f"情境 {k}", cur)); cur = []; k += 1
        cur.append(n)
    if cur: out.append((f"情境 {k}", cur))
    return out

def _parents(tree):
    p = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): p[c] = n
    return p

def crash_guards(src):
    tree = ast.parse(src); par = _parents(tree); out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        owner = ast.get_source_segment(src, f.value) if isinstance(f, ast.Attribute) else ""
        if owner == "fx" and name in ("trigger", "_reduce"): need, what = "qty(", f"fx.{name}"
        elif owner == "os" and name == "remove": need, what = "os.path.exists(", "os.remove"
        else: continue
        # 往上找：所在敘述本身、或任何一層 if 的條件，含確認就算有守護。包在 run(lambda: …) 裡（例外被接住、不會崩）也算；
        # 前一句是 fx.open(…)（直接設好部位，不會失敗）也算；os.remove 的對象來自 os.listdir 的迴圈也算
        cur, guarded = n, False
        while cur in par:
            cur = par[cur]
            if isinstance(cur, ast.Call) and getattr(cur.func, "id", "") == "run": guarded = True; break
            if isinstance(cur, ast.For) and "os.listdir(" in (ast.get_source_segment(src, cur.iter) or ""): guarded = True; break
            if isinstance(cur, ast.If) and need in (ast.get_source_segment(src, cur.test) or ""): guarded = True; break
            if isinstance(cur, ast.stmt) and not isinstance(cur, (ast.FunctionDef, ast.If, ast.For, ast.While, ast.With)):
                if need in (ast.get_source_segment(src, cur) or ""): guarded = True; break
                body = getattr(par.get(cur), "body", None) or []
                i = body.index(cur) if cur in body else -1
                prev = [(ast.get_source_segment(src, s) or "") for s in body[max(0, i - 2):i]]   # 前兩句（中間可能只是設價格）
                if any(s.startswith("fx.open(") for s in prev) and all(s.startswith(("fx.open(", "fx.price")) for s in prev): guarded = True; break
                continue                                      # 敘述本身沒有：再往上看有沒有包在 if 裡
            if isinstance(cur, (ast.FunctionDef, ast.Module)): break
        if not guarded: out.append(f"第 {n.lineno} 行：{what}(…) 沒有先確認（{need}…）——前提不成立時測試本身會崩掉（第 A 類）")
    return out

def legacy_names():
    """每個舊版：模組 → 頂層名稱集合。"""
    out = {}
    for d in sorted(glob.glob("tests/legacy/*/app")):
        ver = d.split("/")[2]; names = {}
        for f in glob.glob(os.path.join(d, "*.py")):
            mod = os.path.basename(f)[:-3]; s = set()
            for n in ast.parse(open(f, encoding="utf-8").read()).body:
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)): s.add(n.name)
                elif isinstance(n, ast.Assign): s |= {t.id for t in n.targets if isinstance(t, ast.Name)}
                elif isinstance(n, ast.ImportFrom): s |= {(a.asname or a.name) for a in n.names}
                elif isinstance(n, ast.Import): s |= {(a.asname or a.name.split(".")[0]) for a in n.names}
            names[mod] = s
        out[ver] = names
    return out

def _walk_no_defs(node):
    """走過節點，但不進函式定義（定義不等於執行：前面定義的函式裡有守護不算，r81）。lambda 會在這一句被呼叫，照走。"""
    yield node
    for c in ast.iter_child_nodes(node):
        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): continue
        yield from _walk_no_defs(c)

def _guard_target(n, alias):
    if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("hasattr", "getattr") and len(n.args) >= 2 \
            and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
        return _chain(ast.Attribute(value=n.args[0], attr=n.args[1].value, ctx=ast.Load()), alias, None)
    return None

def _positive(expr, tgt, alias):
    """expr 成立時，tgt 一定存在：守護出現在 expr 裡、不在 `not` 底下、不在 `or` 裡（r81）。"""
    if _guard_target(expr, alias) == tgt: return True
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not): return False
    if isinstance(expr, ast.BoolOp): return isinstance(expr.op, ast.And) and any(_positive(v, tgt, alias) for v in expr.values)
    return any(_positive(c, tgt, alias) for c in ast.iter_child_nodes(expr)
               if not isinstance(c, (ast.FunctionDef, ast.Lambda)))

def _contains(stmt, tgt, alias):
    """前面的敘述「擋得住」：不存在就離開（`if not hasattr(…): return／continue／break／raise`）、或 `assert hasattr(…)`。
    只是出現過守護不算——`check("（前提）…", hasattr(X, "a"))` 前提失敗只記一筆、不會停下來，後面照樣執行、照樣崩（r82）。"""
    if isinstance(stmt, ast.Assert): return _positive(stmt.test, tgt, alias)
    if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.UnaryOp) and isinstance(stmt.test.op, ast.Not) \
            and _positive(stmt.test.operand, tgt, alias) and stmt.body \
            and isinstance(stmt.body[-1], (ast.Return, ast.Continue, ast.Break, ast.Raise)):
        return True
    return False

def _guarded(use, tgt, stmts, par, alias):
    """照執行順序判斷 use 之前 tgt 有沒有被確認存在（r81）：
    - 在「以守護為條件」的分支裡：`if` 的本體、條件運算式的本體、`and` 的後段（else 那一邊、`or`、`not` 底下都不算）
    - 或寫在前面的敘述裡：同一個區塊或外層區塊、排在前面（不越過自己的範圍；前面定義的函式不算）"""
    top = set(map(id, stmts)); node = use
    while True:
        p = par.get(node)
        if p is None: return False
        if isinstance(p, ast.If) and node in p.body and _positive(p.test, tgt, alias): return True
        if isinstance(p, ast.IfExp) and node is p.body and _positive(p.test, tgt, alias): return True
        if isinstance(p, ast.BoolOp) and isinstance(p.op, ast.And) and node in p.values:
            if any(_positive(v, tgt, alias) for v in p.values[:p.values.index(node)]): return True
        if isinstance(node, ast.stmt):
            if id(node) in top:
                # 走到範圍自己的最上層敘述：只看範圍自己的清單，不能再看外面（模組的敘述清單裡有前一個情境、前面定義的函式——r81 gold-scalper 那個錯）
                return any(_contains(s, tgt, alias) for s in stmts[:[id(x) for x in stmts].index(id(node))])
            for field in ("body", "orelse", "finalbody"):
                blk = getattr(p, field, None)
                if isinstance(blk, list) and node in blk:
                    if any(_contains(s, tgt, alias) for s in blk[:blk.index(node)]): return True
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)): return False
        node = p

def new_interface(src, legacy, alias=None):
    """「呼叫新介面前先確認存在」（清單用法第 5 點 r38；r79 逐處檢查；r81 照執行順序）：
    測試用到的程式屬性，在 tests/legacy/ 任一版不存在時，這一處執行之前、同一個範圍裡要確認過**同一個（模組, 屬性）**存在（見 _guarded）。
    賦值目標（測試把屬性換掉）不算用法；在這個範圍被當成區域變數的名稱不是那個模組（`for manager in …`）。"""
    alias = alias or MOD_FILE
    tree = ast.parse(src); out = []; seen = set(); par = _parents(tree)
    for scope, stmts in _scopes(tree):
        local = set()
        for st in stmts:
            for x in _walk_no_defs(st):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store): local.add(x.id)
        if scope.startswith("函式 "):
            fn = next(f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and list(f.body) == stmts)
            local |= {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        al = {k: v for k, v in alias.items() if k not in local}
        for st in stmts:
            for n in _walk_no_defs(st):
                if not isinstance(n, ast.Attribute) or isinstance(n.ctx, ast.Store) or n.attr.startswith("__"): continue
                tgt = _chain(n, al, None)
                if not tgt: continue
                mod, attr = tgt
                missing = [v for v, names in legacy.items() if mod in names and attr not in names[mod]]
                if not missing or _guarded(n, tgt, stmts, par, al): continue
                key = (scope, mod, attr, n.lineno)
                if key in seen: continue
                seen.add(key)
                out.append(f"第 {n.lineno} 行（{scope}）：{mod}.{attr} 在舊版 {missing} 裡不存在，執行到這裡之前沒有確認同一個屬性存在——"
                           "在舊版上重跑會崩掉（第 B 類）")
    return out

def reset_then_use(src, alias=None):
    """C 類（清單用法第 5 點 r81、r82）：「先裝再重設」——測試在程式模組上換了東西、之後 fresh()（整份還原）或重新載入模組（模擬重啟），
    重設之後**還直接用那個屬性**、中間沒重新換上：還原把它換回真的，測試照樣通過、沒有任何失敗訊號（等於悄悄在真的函式上跑）。
    只看最上層的敘述（依執行順序）；輔助函式裡的換上／使用照它被呼叫的地方算不進來，這裡不追。"""
    alias = alias or MOD_FILE
    tree = ast.parse(src); out = []; patched, reset = {}, {}          # patched：換上的屬性 → 換上的行號
    def targets(node, ctx):
        for n in _walk_no_defs(node):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ctx) and isinstance(n.value, ast.Name) and n.value.id in alias:
                yield (n.value.id, n.attr), n
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)): continue
        for key, n in targets(st, ast.Load):                           # 先看這一句有沒有用到已經被重設的
            if key in reset:
                out.append(f"第 {n.lineno} 行：{key[0]}.{key[1]} 在第 {reset[key][1]} 行換上、第 {reset[key][0]} 行{reset[key][2]}之後沒重新換上就用了——"
                           "用到的其實是真的（C 類：先裝再重設）")
                reset.pop(key)
        for k, n in targets(st, ast.Store): patched[k] = n.lineno; reset.pop(k, None)
        for n in _walk_no_defs(st):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "fresh":
                for k in list(patched): reset[k] = (n.lineno, patched.pop(k), "fresh()")
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "reload" and n.args and isinstance(n.args[0], ast.Name):
                for k in [k for k in patched if k[0] == n.args[0].id]: reset[k] = (n.lineno, patched.pop(k), "重新載入")
    return out

SELF_C = {
    "換上 → fresh() → 直接用（沒重新換上）": ("fx = fresh()\nmanager._now = lambda: 1\nfx = fresh()\nx = manager._now()\n", 1),
    "換上 → fresh() → 重新換上 → 用": ("fx = fresh()\nmanager._now = lambda: 1\nfx = fresh()\nmanager._now = lambda: 2\nx = manager._now()\n", 0),
    "換上 → 重新載入同一個模組 → 用": ("store.get = lambda: 1\nimportlib.reload(store)\nx = store.get()\n", 1),
    "換上 → 重新載入別的模組 → 用": ("store.get = lambda: 1\nimportlib.reload(main)\nx = store.get()\n", 0),
    "換上之後同一個情境裡用（沒有重設）": ("fx = fresh()\nmanager._now = lambda: 1\nx = manager._now()\n", 0),
}

SELF_A = {
    "trigger 沒守護": ("fx = fresh()\nfx.trigger('X', 'LONG', 100)\n", 1),
    "同一句 if 守護": ("fx = fresh()\nif fx.qty('X', 'LONG') >= 100: fx.trigger('X', 'LONG', 100)\n", 0),
    "輔助函式裡守護": ("def hit(fx):\n    if fx.qty('X', 'LONG') >= 100: fx.trigger('X', 'LONG', 100)\n", 0),
    "輔助函式裡沒守護": ("def hit(fx):\n    fx.price = 0.9\n    fx.trigger('X', 'LONG', 100)\n", 1),
    "os.remove 沒守護": ("os.remove(path)\n", 1),
    "os.remove 有守護": ("if os.path.exists(path): os.remove(path)\n", 0),
    "包在 run(lambda: …) 裡": ("t, e = run(lambda: fx.trigger('X', 'LONG', 100))\n", 0),
    "前一句是 fx.open": ("fx.open('X', 'LONG', 30); old = fx.trigger('X', 'LONG', 30)\n", 0),
    "fx.open 後只設價格再 trigger": ("fx.open('X', 'LONG', 30); fx.price = 2.0; old = fx.trigger('X', 'LONG', 30)\n", 0),
    "fx.open 後中間有別的動作": ("fx.open('X', 'LONG', 30); main.place('X')\nold = fx.trigger('X', 'LONG', 30)\n", 1),
    "listdir 迴圈裡的 os.remove": ("for f in os.listdir(d):\n    if f.startswith('p'): os.remove(os.path.join(d, f))\n", 0),
}
SELF_B_LEGACY = {"r0": {"manager": {"run", "close_now", "telegram"}, "main": {"reconcile", "scanner"}, "scanner": {"scan"}, "telegram": {"send"}}}
SELF_B_ALIAS = {"manager": "manager", "main": "main", "scanner": "scanner", "telegram": "telegram"}
SELF_B = {
    "用了舊版沒有的屬性、沒守護": ("r = manager.brand_new()\n", 1),
    "前提 check 裡有 hasattr、之後使用：前提失敗不會停下來，擋不住（r79 時預期寫成 0，r82 更正）": ('check("a", "前提", hasattr(manager, "brand_new"))\nr = manager.brand_new()\n', 1),
    "舊版有的屬性": ("manager.run()\n", 0),
    "守的是別的屬性（r79）": ('if hasattr(manager, "other"): pass\nr = manager.brand_new()\n', 1),
    "守護寫在用法之後（r79）": ('r = manager.brand_new()\nok = hasattr(manager, "brand_new")\n', 1),
    "守護在別的情境（r79）": ('fx = fresh()\nok = hasattr(manager, "brand_new")\nfx = fresh()\nr = manager.brand_new()\n', 1),
    "同一行短路守護": ('r = hasattr(manager, "brand_new") and manager.brand_new()\n', 0),
    "兩層：main.scanner.新函式、沒守護（r79）": ("main.scanner.new_scan()\n", 1),
    "兩層：有守同一個（main.scanner, 屬性）": ('if hasattr(main.scanner, "new_scan"): main.scanner.new_scan()\n', 0),
    "輔助函式裡沒守護（r79）": ("def helper():\n    return manager.brand_new()\n", 1),
    "輔助函式裡有守護": ('def helper():\n    if hasattr(manager, "brand_new"): return manager.brand_new()\n', 0),
    "pump-dump-hunter r78 的 main._resumed[...] = True（沒守護）": ('fx = fresh()\nmain._resumed["done"] = True\n', 1),
    "賦值目標（把屬性換掉）不算用法": ("manager.brand_new = lambda: 1\n", 0),
    "條件運算式：守護寫在後面但先判斷": ('f = manager.brand_new if hasattr(manager, "brand_new") else None\n', 0),
    "條件運算式：守的是別的屬性": ('f = manager.brand_new if hasattr(manager, "other") else None\n', 1),
    "條件運算式跨行寫（r81：比行號會誤報）": ('f = (manager.brand_new\n     if hasattr(manager, "brand_new") else None)\n', 0),
    "用法在條件運算式的 else 那邊（r81）": ('f = None if hasattr(manager, "brand_new") else manager.brand_new\n', 1),
    "用法在 if … else: 的 else 區塊（r81）": ('if hasattr(manager, "brand_new"):\n    pass\nelse:\n    manager.brand_new()\n', 1),
    "or 後段（r81）": ('ok = hasattr(manager, "brand_new") or manager.brand_new()\n', 1),
    "not 底下（r81）": ('ok = not hasattr(manager, "brand_new") and manager.brand_new()\n', 1),
    "isinstance(getattr(...), dict) 當條件": ('if isinstance(getattr(manager, "brand_new", None), dict): manager.brand_new["x"] = 1\n', 0),
    "前面定義的函式裡守過（定義不等於執行，r81）": ('def f():\n    return hasattr(manager, "brand_new")\nmanager.brand_new()\n', 1),
    "前一個情境守過（範圍不越界）": ('fx = fresh()\nif hasattr(manager, "brand_new"): pass\nfx = fresh()\nmanager.brand_new()\n', 1),
    "前面只是出現過守護（例如前提 check），擋不住（r82）": ('check("A", "（前提）", hasattr(manager, "brand_new"))\nmanager.brand_new()\n', 1),
    "前面 `if not hasattr: return` 擋得住": ('def f():\n    if not hasattr(manager, "brand_new"): return\n    manager.brand_new()\n', 0),
    "外層區塊前面 `if not hasattr: continue`": ('for i in range(2):\n    if not hasattr(manager, "brand_new"): continue\n    if i: manager.brand_new()\n', 0),
    "前面 `if not hasattr: pass`（沒離開）擋不住": ('if not hasattr(manager, "brand_new"): pass\nmanager.brand_new()\n', 1),
    "跟別名同名的區域變數不是那個模組（r81）": ('for manager in [1]:\n    manager.brand_new\n', 0),
    "函式參數同名也不是": ('def f(manager):\n    return manager.brand_new\n', 0),
}

def self_test():
    bad = 0
    for name, (src, want) in SELF_A.items():
        n = len(crash_guards(src)); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} A：{name}（問題 {n}，預期 {want}）")
    for name, (src, want) in SELF_C.items():
        n = len(reset_then_use(src, SELF_B_ALIAS | {"store": "store"})); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} C：{name}（問題 {n}，預期 {want}）")
    for name, (src, want) in SELF_B.items():
        n = len(new_interface(src, SELF_B_LEGACY, SELF_B_ALIAS)); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} B：{name}（問題 {n}，預期 {want}）")
    return bad

def main():
    if "--self-test" in sys.argv: sys.exit(1 if self_test() else 0)
    legacy = legacy_names()
    files = sorted(glob.glob("tests/test_r*.py")); total = 0
    for p in files:
        src = open(p, encoding="utf-8").read()
        probs = crash_guards(src) + new_interface(src, legacy) + reset_then_use(src)
        print(f"== {p}：{len(probs)} 處")
        for o in probs: print("   " + o)
        total += len(probs)
    print(f"掃了 {len(files)} 支測試、舊版 {sorted(legacy)}")
    if len(files) < 5 or not legacy: print("掃到的太少（測試或舊版目錄不在？）"); total += 1
    print("通過" if not total else f"共 {total} 處"); sys.exit(1 if total else 0)

if __name__ == "__main__":
    main()
