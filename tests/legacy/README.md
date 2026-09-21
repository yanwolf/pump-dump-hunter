# 舊版程式（只有後端 app/）

每輪修改前的程式存一份在這裡（zip 裡沒有 `.git`，靠 git 標籤取舊版要另外操作，清單用法第 5 點 r39）。
用現在的測試跑舊版：`python -m tests.rerun_old legacy:r37`，失敗分三類；「測試／框架崩掉」要是 0。
完整驗證（`python -m scripts.verify`）會對這裡每一版都跑。只留最近兩版，更早的刪掉。
