"""測試檔登記表（清單用法第 5 點 r101）：逐檔掃的檢查都從這裡拿清單，不各自寫死。

- **tests/test_*.py 全部自動列入**：新增的測試檔不用登記就會被每一種檢查掃到。
  以前否定句、新介面、索引、舊版重跑用的樣式是 `test_r*.py`（名字不是 test_rNN 的 test_stats、test_maxqty 從沒被掃過），
  突變檢查是寫死的清單（新增測試檔要靠人記得加）。
- 不參加某種檢查的檔案，在 SKIP 裡**寫明理由**。過期的登記、沒寫理由、不認得的檢查名稱，由 problems() 報出（scripts/verify 會跑）。
- 另外直接掃五支檢查工具的原始碼：還有人寫死 `test_r*.py` 樣式或測試清單，也報。
"""
import glob, os

KINDS = {
    "negative": "否定句、前提、框架還原等（tests/check_tests.py）",
    "repeat": "崩掉守護、新介面、先裝再重設（tests/check_repeat.py）",
    "indexing": "先索引沒先確認（tests/check_indexing.py）",
    "mutation": "逐項突變檢查（tests/mutation_check.py）",
    "rerun": "在專案裡存的舊版程式上重跑（tests/rerun_old.py）",
}
_OLD_STYLE = "最早期的舊式測試：用自己的簡化模擬 binance 模組、不走共用框架（沒有 fresh()／FakeBinance／〔命中n〕），這種檢查看不懂它"
SKIP = {
    "test_lessons.py": {"negative": _OLD_STYLE, "repeat": _OLD_STYLE, "mutation": _OLD_STYLE + "；也不經過模擬交易所，突變碰不到它"},
    "test_parity.py": {"negative": _OLD_STYLE, "repeat": _OLD_STYLE, "mutation": _OLD_STYLE + "；也不經過模擬交易所，突變碰不到它"},
}

def all_files():
    return sorted(os.path.basename(p) for p in glob.glob("tests/test_*.py"))

def files(kind):
    """這種檢查要掃的測試檔（檔名）。"""
    assert kind in KINDS, kind
    return [f for f in all_files() if kind not in SKIP.get(f, {})]

def modules(kind):
    return ["tests." + f[:-3] for f in files(kind)]

TOOLS = ("tests/check_tests.py", "tests/check_repeat.py", "tests/check_indexing.py", "tests/mutation_check.py", "tests/rerun_old.py")

def _code_only(src):
    """去掉註解（用 tokenize）：註解裡寫「以前 test_r*.py 漏掉…」不算寫死。"""
    import io, tokenize
    lines = src.splitlines(keepends=True)
    try:
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            if t.type == tokenize.COMMENT:                       # 只把註解那一段塗掉，其他原樣保留（接 token 會把 registry.files 拆開）
                r, c = t.start; lines[r - 1] = lines[r - 1][:c] + "\n"
    except Exception: return src
    return "".join(lines)

def problems(skip=None, tools=TOOLS, existing=None):
    skip = SKIP if skip is None else skip
    existing = set(all_files() if existing is None else existing)
    out = []
    for f, kinds in skip.items():
        if f not in existing: out.append(f"登記過期：{f} 已經不存在")
        for k, why in kinds.items():
            if k not in KINDS: out.append(f"{f}：不認得的檢查名稱 {k!r}")
            if not (why or "").strip(): out.append(f"{f} 不參加 {k} 沒寫理由")
    for t in tools:
        src = _code_only(open(t, encoding="utf-8").read()) if os.path.exists(t) else ""
        if not src: out.append(f"{t} 不存在"); continue
        if "test_r*.py" in src: out.append(f"{t} 還寫死 test_r*.py 樣式（名字不是 test_rNN 的測試檔掃不到）")
        if "TESTS = [" in src or '"tests.test_r' in src: out.append(f"{t} 還寫死測試清單")
        if "registry." not in src: out.append(f"{t} 沒有從登記表拿清單")
    return out

SELF = {
    "正常": (dict(SKIP), None, 0),
    "登記過期（檔案不在了）": ({"test_gone.py": {"negative": "x"}}, None, 1),
    "沒寫理由": ({"test_lessons.py": {"negative": "  "}}, None, 1),
    "不認得的檢查名稱": ({"test_lessons.py": {"negtive": "打錯"}}, None, 1),
}

def self_test():
    import tempfile
    bad = 0
    for name, (skip, _, want) in SELF.items():
        n = len([p for p in problems(skip, tools=()) ]); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} {name}（問題 {n}，預期 {want}）")
    d = tempfile.mkdtemp()
    for name, src, want in (("有用登記表、但同時還寫死 test_r*.py", 'files = registry.files("x") + glob.glob("tests/test_r*.py")\n', 1),
                            ("有用登記表、但同時還寫死測試清單", 'TESTS = ["tests.test_r12"] + registry.modules("x")\n', 1),
                            ("沒從登記表拿", 'files = []\n', 1),
                            ("從登記表拿", 'files = registry.files("negative")\n', 0),
                            ("註解裡提到 test_r*.py 不算", 'files = registry.files("negative")   # 以前用 test_r*.py 漏掉\n', 0)):
        p = os.path.join(d, "t.py"); open(p, "w", encoding="utf-8").write(src)
        n = len(problems({}, tools=(p,))); ok = n == want; bad += not ok
        print(f"  {'✅' if ok else '❌'} 工具原始碼：{name}（問題 {n}，預期 {want}）")
    return bad

if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv: sys.exit(1 if self_test() else 0)
    ps = problems()
    for k in KINDS: print(f"  {k}：{len(files(k))}/{len(all_files())} 個測試檔")
    for p in ps: print("  ❌", p)
    print("通過" if not ps else f"共 {len(ps)} 處"); sys.exit(1 if ps else 0)
