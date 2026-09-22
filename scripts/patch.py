"""改程式用的字串取代：必須恰好命中 N 次，否則中止（清單第 14 條 r15）。
str.replace / sed 比對不到時什麼都不做、也不報錯；比對到多處時又會全部換掉。這裡兩種都擋。
用法（在專案根目錄）：
    from scripts.patch import patch
    patch("app/main.py", "舊字串", "新字串")            # 預設必須恰好 1 處
    patch("app/main.py", "舊字串", "新字串", count=2)
"""
import hashlib, json, os, re, sys

class PatchAbort(SystemExit):
    pass

ABORT_FILE = os.path.join(os.environ.get("TMPDIR", "/tmp"), ".pdh_patch_abort.json")

def _fp(e): return hashlib.sha1((e[0] + "\x00" + e[2]).encode("utf-8")).hexdigest()   # 指紋看（路徑, 新字串）：改錨點重跑時新字串不變，才對得上

def _abort(edits, msg):
    """整批中止：記下這批每一處的指紋。下一次 apply 如果只帶了這批的一部分（少掉的那些就掉了），擋下——
    中止後的重跑要用原本的整批清單（清單用法第 5 點 r64，crypto-screener 踩到）。"""
    try: json.dump(dict(fps=[_fp(e) for e in edits]), open(ABORT_FILE, "w"))
    except Exception: pass
    raise PatchAbort(msg)

def _check_subset(edits):
    try: last = json.load(open(ABORT_FILE)).get("fps") or []
    except Exception: return
    fps = [_fp(e) for e in edits]
    if fps and len(fps) < len(last) and set(fps) <= set(last):
        missing = len(last) - len(fps)
        raise PatchAbort(f"❌ apply 中止：這批是上一次中止那批的一部分（少了 {missing} 處）——中止後重跑要重跑整批，不然少掉的修改就掉了。"
                         f"確定只要這幾處，先刪掉 {ABORT_FILE}")

def apply(edits):
    """一批修改，全部比對成功才一次寫入（清單用法第 5 點 r26）。
    edits：[(路徑, 舊字串, 新字串[, 次數])]，可跨多個檔；同一個檔的多處依序在記憶體裡套用。
    任何一處命中次數不對、或舊字串與新字串結尾換行不一致（下一行會黏上來），就中止——一個檔都不寫。
    清單與版本號這類「必須一起改」的東西，放在同一次 apply 裡。
    中止後重跑：如果只帶了上一次中止那批的一部分，擋下（r64）。"""
    _check_subset(edits)
    buf = {}
    for i, e in enumerate(edits, 1):
        path, old, new = e[0], e[1], e[2]; count = e[3] if len(e) > 3 else 1
        s = buf[path] if path in buf else open(path, encoding="utf-8").read()
        n = s.count(old)
        head = old.strip().splitlines()[0][:70] if old.strip() else repr(old)
        if n != count:
            _abort(edits, f"❌ apply 中止（一個檔都沒寫）：第 {i} 處 {path} 預期命中 {count} 處，實際 {n} 處\n   比對字串開頭：{head}")
        # 裝飾器（清單用法第 5 點 r41）：錨點緊接在 @裝飾器 後面、替換又多出 def／class，裝飾器就套到新函式上——語法合法、編譯抓不到
        pos = s.find(old); prev = s[:pos].rstrip("\n").split("\n")[-1].strip() if pos > 0 else ""
        more = lambda t: len(re.findall(r"^\s*(def|class)\s", t, re.M))
        if prev.startswith("@") and more(new) > more(old):
            _abort(edits, f"❌ apply 中止（一個檔都沒寫）：第 {i} 處 {path} 的錨點緊接在 {prev} 後面，替換又多出 def／class——"
                             "裝飾器會套到新函式上；錨點改選在裝飾器之前")
        if new != "" and old.endswith("\n") != new.endswith("\n"):     # 整段刪除（替換成空字串）不檢查（清單用法第 5 點 r32）
            _abort(edits, f"❌ apply 中止（一個檔都沒寫）：第 {i} 處 {path} 舊字串與新字串結尾換行不一致，下一行會黏上來\n   比對字串開頭：{head}")
        buf[path] = s.replace(old, new)
    for path, s in buf.items():                     # 寫入前先編譯（清單用法第 5 點 r35）：全部命中、寫進去之後才發現語法錯，檔案已經壞了
        if path.endswith(".py"):
            try: compile(s, path, "exec")
            except SyntaxError as e:
                _abort(edits, f"❌ apply 中止（一個檔都沒寫）：{path} 改完後有語法錯（第 {e.lineno} 行：{e.msg}）")
    for path, s in buf.items(): open(path, "w", encoding="utf-8").write(s)
    try: os.remove(ABORT_FILE) if os.path.exists(ABORT_FILE) else None      # 整批成功寫入 → 忘掉上一次的中止
    except Exception: pass
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
    pa, pb = os.path.join(d, "a.py"), os.path.join(d, "b.py")
    open(pa, "w").write("x = 1\n"); open(pb, "w").write("y = 2\n")
    try: apply([(pa, "x = 1", "x = 2"), (pb, "y = 2", "y = (2")]); results.append(("改完有語法錯 → 中止", False))
    except PatchAbort: results.append(("改完有語法錯 → 中止", True))
    results.append(("語法錯中止後，另一個 .py 也沒被改動", open(pa).read() == "x = 1\n" and open(pb).read() == "y = 2\n"))
    pd = os.path.join(d, "deco.py")
    open(pd, "w").write("@property\ndef is_enabled(self):\n    return True\n")
    try: apply([(pd, "def is_enabled(self):\n", "def helper():\n    pass\n\ndef is_enabled(self):\n")]); results.append(("新函式插在裝飾器後面 → 中止", False))
    except PatchAbort: results.append(("新函式插在裝飾器後面 → 中止", True))
    try: apply([(pd, "    return True\n", "    return False\n")]); results.append(("修改被裝飾函式的內容 → 不誤擋", "return False" in open(pd).read()))
    except PatchAbort as e: results.append((f"修改被裝飾函式的內容 → 不誤擋（{e}）", False))
    # 中止後只重跑一部分 → 擋下；重跑整批 → 放行（r64）
    pc, pd2 = os.path.join(d, "c.txt"), os.path.join(d, "d.txt"); open(pc, "w").write("甲\n"); open(pd2, "w").write("乙\n")
    try: apply([(pc, "甲", "一"), (pd2, "找不到", "二")])   # 第二處錨點寫錯 → 整批中止
    except PatchAbort: pass
    try: apply([(pd2, "乙", "二")]); results.append(("中止後只重跑一部分 → 擋下", False))
    except PatchAbort: results.append(("中止後只重跑一部分 → 擋下", open(pc).read() == "甲\n" and open(pd2).read() == "乙\n"))
    try: apply([(pc, "甲", "一"), (pd2, "乙", "二")]); results.append(("中止後重跑整批（改好錨點）→ 放行", open(pc).read() == "一\n" and open(pd2).read() == "二\n"))
    except PatchAbort as e: results.append((f"中止後重跑整批（改好錨點）→ 放行（{e}）", False))
    try: apply([(pd2, "二", "三")]); results.append(("整批成功之後，下一批不受影響", open(pd2).read() == "三\n"))
    except PatchAbort as e: results.append((f"整批成功之後，下一批不受影響（{e}）", False))
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
