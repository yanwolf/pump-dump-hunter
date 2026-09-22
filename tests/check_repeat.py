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

MODS = ("manager", "main", "store", "B", "preflight", "presets")
MOD_FILE = {"B": "binance"}

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

def new_interface(src, legacy):
    tree = ast.parse(src); out = []; used = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in MODS and not n.attr.startswith("__"):
            used.setdefault((n.value.id, n.attr), n.lineno)
    for (mod, attr), ln in sorted(used.items(), key=lambda x: x[1]):
        modfile = MOD_FILE.get(mod, mod)
        missing = [v for v, names in legacy.items() if modfile in names and attr not in names[modfile]]
        if not missing: continue
        if re.search(rf'(hasattr|getattr)\({mod},\s*"{re.escape(attr)}"', src): continue
        out.append(f"第 {ln} 行：{mod}.{attr} 在舊版 {missing} 裡不存在，檔案裡沒有 hasattr／getattr 守護——在舊版上重跑會崩掉（第 B 類）")
    return out

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
SELF_B_LEGACY = {"r0": {"manager": {"run", "close_now"}, "main": {"reconcile"}}}
SELF_B = {
    "用了舊版沒有的屬性、沒守護": ("r = manager.brand_new()\n", 1),
    "有 hasattr 守護": ("check('a', '前提', hasattr(manager, 'brand_new'))\nr = manager.brand_new()\n".replace("'brand_new'", '"brand_new"'), 0),
    "舊版有的屬性": ("manager.run()\n", 0),
}

def self_test():
    bad = 0
    for name, (src, want) in SELF_A.items():
        n = len(crash_guards(src)); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} A：{name}（問題 {n}，預期 {want}）")
    for name, (src, want) in SELF_B.items():
        n = len(new_interface(src, SELF_B_LEGACY)); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} B：{name}（問題 {n}，預期 {want}）")
    return bad

def main():
    if "--self-test" in sys.argv: sys.exit(1 if self_test() else 0)
    legacy = legacy_names()
    files = sorted(glob.glob("tests/test_r*.py")); total = 0
    for p in files:
        src = open(p, encoding="utf-8").read()
        probs = crash_guards(src) + new_interface(src, legacy)
        print(f"== {p}：{len(probs)} 處")
        for o in probs: print("   " + o)
        total += len(probs)
    print(f"掃了 {len(files)} 支測試、舊版 {sorted(legacy)}")
    if len(files) < 5 or not legacy: print("掃到的太少（測試或舊版目錄不在？）"); total += 1
    print("通過" if not total else f"共 {total} 處"); sys.exit(1 if total else 0)

if __name__ == "__main__":
    main()
