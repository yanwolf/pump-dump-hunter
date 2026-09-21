"""改程式用的字串取代：必須恰好命中 N 次，否則中止（清單第 14 條 r15）。
str.replace / sed 比對不到時什麼都不做、也不報錯；比對到多處時又會全部換掉。這裡兩種都擋。
用法（在專案根目錄）：
    from scripts.patch import patch
    patch("app/main.py", "舊字串", "新字串")            # 預設必須恰好 1 處
    patch("app/main.py", "舊字串", "新字串", count=2)
"""
import sys

class PatchAbort(SystemExit):
    pass

def apply(edits):
    """一批修改，全部比對成功才一次寫入（清單用法第 5 點 r26）。
    edits：[(路徑, 舊字串, 新字串[, 次數])]，可跨多個檔；同一個檔的多處依序在記憶體裡套用。
    任何一處命中次數不對、或舊字串與新字串結尾換行不一致（下一行會黏上來），就中止——一個檔都不寫。
    清單與版本號這類「必須一起改」的東西，放在同一次 apply 裡。"""
    buf = {}
    for i, e in enumerate(edits, 1):
        path, old, new = e[0], e[1], e[2]; count = e[3] if len(e) > 3 else 1
        s = buf[path] if path in buf else open(path, encoding="utf-8").read()
        n = s.count(old)
        head = old.strip().splitlines()[0][:70] if old.strip() else repr(old)
        if n != count:
            raise PatchAbort(f"❌ apply 中止（一個檔都沒寫）：第 {i} 處 {path} 預期命中 {count} 處，實際 {n} 處\n   比對字串開頭：{head}")
        if new != "" and old.endswith("\n") != new.endswith("\n"):     # 整段刪除（替換成空字串）不檢查（清單用法第 5 點 r32）
            raise PatchAbort(f"❌ apply 中止（一個檔都沒寫）：第 {i} 處 {path} 舊字串與新字串結尾換行不一致，下一行會黏上來\n   比對字串開頭：{head}")
        buf[path] = s.replace(old, new)
    for path, s in buf.items(): open(path, "w", encoding="utf-8").write(s)
    return len(edits)

def self_test():
    """故意讓第二處比對不到，確認第一處的檔案沒被改動。"""
    import os, tempfile
    d = tempfile.mkdtemp(); a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
    open(a, "w").write("甲乙丙\n"); open(b, "w").write("丁戊\n")
    results = []
    try: apply([(a, "甲", "一"), (b, "不存在", "x")]); results.append(("第二處對不到 → 中止", False))
    except PatchAbort: results.append(("第二處對不到 → 中止", True))
    results.append(("中止後第一個檔沒被改動", open(a).read() == "甲乙丙\n"))
    try: apply([(a, "乙丙\n", "二三")]); results.append(("結尾換行不一致 → 中止", False))
    except PatchAbort: results.append(("結尾換行不一致 → 中止", True))
    try: apply([(a, "甲", "一"), (a, "一乙", "一二"), (b, "丁", "四")]); results.append(("同檔依序套用、跨檔一次寫入", open(a).read() == "一二丙\n" and open(b).read() == "四戊\n"))
    except PatchAbort as e: results.append((f"同檔依序套用、跨檔一次寫入（{e}）", False))
    try: apply([(a, "丙", "三", 2)]); results.append(("次數不對 → 中止", False))
    except PatchAbort: results.append(("次數不對 → 中止", True))
    open(b, "w").write("第一行\n要刪的一行\n第三行\n")
    try: apply([(b, "要刪的一行\n", "")]); results.append(("整段刪除（換成空字串）不被換行檢查擋下", open(b).read() == "第一行\n第三行\n"))
    except PatchAbort as e: results.append((f"整段刪除（換成空字串）不被換行檢查擋下（{e}）", False))
    bad = 0
    for name, ok in results: print(f"  {'✅' if ok else '❌'} {name}"); bad += not ok
    return bad

def patch(path, old, new, count=1):
    s = open(path, encoding="utf-8").read()
    n = s.count(old)
    if n != count:
        head = old.strip().splitlines()[0][:70] if old.strip() else repr(old)
        sys.exit(f"❌ patch 中止：{path} 預期命中 {count} 處，實際 {n} 處\n   比對字串開頭：{head}")
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
    return n

if __name__ == "__main__":
    import sys as _s
    _s.exit(1 if self_test() else 0)
