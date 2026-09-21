"""改程式用的字串取代：必須恰好命中 N 次，否則中止（清單第 14 條 r15）。
str.replace / sed 比對不到時什麼都不做、也不報錯；比對到多處時又會全部換掉。這裡兩種都擋。
用法（在專案根目錄）：
    from scripts.patch import patch
    patch("app/main.py", "舊字串", "新字串")            # 預設必須恰好 1 處
    patch("app/main.py", "舊字串", "新字串", count=2)
"""
import sys

def patch(path, old, new, count=1):
    s = open(path, encoding="utf-8").read()
    n = s.count(old)
    if n != count:
        head = old.strip().splitlines()[0][:70] if old.strip() else repr(old)
        sys.exit(f"❌ patch 中止：{path} 預期命中 {count} 處，實際 {n} 處\n   比對字串開頭：{head}")
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
    return n
