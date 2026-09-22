# 交接摘要（給新對話用）

> **先讀 `BINANCE_LESSONS.md`**：幣安踩坑共用清單，三個專案（crypto-screener / gold-scalper /
> pump-dump-hunter）內容相同。發現新坑就三份一起更新。

把這個 zip 丟給 Claude，先讀這頁就能接上。程式碼細節看 README.md 和各 .py。

## 目錄結構（2026-09-21 整理，對齊 gold-scalper）
```
app/                 程式全部在這裡，Dockerfile 只 COPY app
  main.py            入口：HTTP 伺服器 + 背景迴圈 + API 路由（python -m app.main）
  static/dashboard.html   網頁（原本內嵌在 server.py 裡）
  binance.py         交易所客戶端（精度、Algo 條件單、槓桿上限）
  manager.py         實盤出場管理（對應 backtest 的出場規則）
  preflight.py       交易所相容性自檢 ← 可整支複製到另外兩個專案
  signals.py scanner.py risk.py backtest.py sweep.py params.py presets.py config.py store.py telegram.py
tests/test_parity.py 回測 vs 實盤出場一致性測試（python -m tests.test_parity）
tests/test_lessons.py 清單 r6→r8 差異的行為測試（python -m tests.test_lessons）
tests/test_r12.py     清單 r10→r12 差異的行為測試（python -m tests.test_r12）
tests/test_r15.py     清單 r13→r15 差異的行為測試（python -m tests.test_r15）
tests/test_r18.py     清單 r16→r18 差異的行為測試（python -m tests.test_r18）
tests/test_r21.py     清單 r19→r21 差異的行為測試（python -m tests.test_r21）
tests/test_r24.py     清單 r22→r24 差異的行為測試（python -m tests.test_r24）
tests/test_r27.py     清單 r25→r27 差異的行為測試（python -m tests.test_r27）
tests/test_r30.py     清單 r28→r30 差異的行為測試（python -m tests.test_r30）
tests/test_r33.py     清單 r31→r33 差異的行為測試（python -m tests.test_r33）
tests/test_r36.py     清單 r34→r36 差異的行為測試（python -m tests.test_r36）
tests/test_r39.py     清單 r37→r39 差異的行為測試（python -m tests.test_r39）
tests/test_r43.py     清單 r40→r43 差異的行為測試（python -m tests.test_r43）
tests/test_r47.py     清單 r44→r47 差異的行為測試（python -m tests.test_r47）
tests/test_r50.py     清單 r48→r50 差異的行為測試（python -m tests.test_r50）
tests/legacy/rNN/     每輪修改前的後端程式（只留最近兩版）；python -m tests.rerun_old legacy:rNN
tests/check_indexing.py 先索引沒先確認的靜態檢查；--self-test 自我驗證
tests/rerun_old.py    拿現在的測試跑舊版程式、失敗分三類：python -m tests.rerun_old <舊版目錄>
舊版程式：部署前 git 打標籤（lessons-rNN），比對時 git worktree add ../pdh-rNN lessons-rNN
tests/harness.py      共用案例框架（check／fresh／finish）；自我驗證 python -m tests.harness_selftest
scripts/check_returns.py 回傳原因語法樹檢查（每個 return 帶值、不會掉出函式）；--self-test 自我驗證
scripts/verify.py     部署前完整驗證：python -m scripts.verify（全部通過才部署）
tests/mutation_selftest.py 突變檢查器的自我驗證（固定人造資料，python -m tests.mutation_selftest）
tests/mutation_check.py 突變檢查（逐項）：逐幣查詢回空；命中 0 次自動判定無關，命中過的要在 tests/mutation_exempt.py 寫前提／對照組
                      注入觸發紀錄：PDH_INJECT_LOG=1 python -m tests.test_xxx（核對注入是在被測那一步觸發，清單第 18 種）
tests/check_tests.py  測試的靜態檢查：fresh() 重設換掉的東西、否定句斷言要有前提（python -m tests.check_tests）
tests/fake_exchange.py 模擬幣安（HTTP 層）：舊端點條件單 -4120、空單負數、雙向兩列、逐幣查部位、無 Algo 環境，
                      可注入逾時／5xx／200 空清單／成交但回應丟失
scripts/patch.py      改程式用的字串取代：必須恰好命中 N 次，否則中止（清單第 14 條 r15）
部署前：所有 tests.test_* 都要過、python -m tests.check_tests 通過、python -m tests.mutation_selftest 通過、python -m tests.mutation_check 通過、python -m pyflakes app/ 沒有 undefined name
BINANCE_LESSONS.md   三專案共用的踩坑清單
```

## 這是什麼
小幣拉高崩盤的極短線策略。Zeabur 部署（tzujen-pump-dump-hunter.zeabur.app），純標準庫 Python，
手機用 Working Copy 整包覆蓋上傳，所以**所有操作都在網頁上，沒有終端機**。

## 引擎現況（2026-09-20 判決）
每個引擎用自己方向的無偏差母體評，90 天：

| 引擎 | 做什麼 | 母體 | 結果 | 實盤 |
|---|---|---|---|---|
| C | 5m 崩盤棒破低追空 | 崩盤日 196 事件 | 23 筆 PF 5.7 | ✅ 2%/筆 主力 |
| F | 1m 清算連鎖追空 | 崩盤日 | 230 筆 PF 1.32 | ✅ 1%/筆 |
| G | 1m 軋空追多（F 的鏡像） | 暴漲日 687 事件 | 389 筆 PF 1.68 | ✅ 1%/筆 |
| B | 崩後反彈力竭做空 | 三種母體 | PF 0.88–1.17 | ❌ 退場 |
| A | 崩前頂部中樞跌破 | 全區間 | PF 0.54–0.73 | ❌ 研究用 |
| E | 突破回踩跟多 | 暴漲日 | PF 0.82 | ❌ 研究用 |
| D | 崩後 V 反抄底 | — | 樣本不足 | ❌ 研究用 |

實盤 `ENGINES=C,F,G`，testnet，收真實滑價數據中。

## 方法論教訓（重要）
**事件母體必須配合引擎方向。** 「拉高崩盤事件」模式對做多引擎有事後偏差——
E 在那個母體是 PF 2.07，換到無偏差的「暴漲日」母體只剩 0.82。
測做空用崩盤日、測做多用暴漲日。

## 倉位規則
階梯本金（基準 500 / 每級 250 / 上限 1500，因為 demo 帳號 5000U 三個專案共用）
× 保留 25% = 可用；固定 10x；最多同時 3 筆；每筆風險 1–2% 可用本金。
持倉數只算自己開的（其他專案的倉不算），每分鐘跟交易所對帳。

## 操作方式
網頁三分頁：監控 / 交易 / 研究。
- 研究頁「參數調整」可存具名預設集、套用到實盤，存在 Volume 重佈不掉
- 歷史掃描背景執行，K 線快取在 Volume
- 環境變數看 .env.example

## 下單流程（2026-09-21 修過）
送出市價單前先寫 `pending` → 成交後**立刻**記帳（open/trades）→ 最後才掛停損。
停損單失敗會重試一次，再失敗預設立刻市價平倉（`STOP_FAIL=keep` 可改成只告警）。
數量與停損價一律照交易所 stepSize / tickSize 取整（之前 `round(qty)`、`round(stop,6)` 會噴 -1111）。
程式重啟或中途崩潰留下的無紀錄持倉，下一次對帳會從 pending 認領回來。
交易分頁有「帳號全部持倉」對帳表，owner 欄分本策略／其他專案。

## 跟 crypto-screener 學到的（別再踩）
對過 crypto-screener v2 的 trader.py，把它用代價換來的四個設計搬過來：
1. **查不到 ≠ 不存在**：`open_stops` 回傳 (orders, ok)。查詢失敗一律跳過，這是那邊誤平倉的根源。
2. **連續 3 輪才動作**：停損確認不在要連 3 輪；補掛被拒且原因含 existing/already 視為誤判；
   補不上只告警不平倉（強制平倉是那次事故的放大器）。
3. **Algo 端點探測 + 退回**：先打新端點，只有 404／未知端點才退回舊寫法，參數錯不能誤判成端點不存在。
   撤單要照 `via` 決定打哪個端點。
4. **槓桿設不上要退而求其次**：新子帳戶常被限 5x、小幣分層也可能低於 10x。
   設不上就退到 `leverageBracket` 的上限並縮量，沉默失敗會讓保證金與強平距離都跟預期不符。
另外 hedge/oneway 模式改成快取 5 分鐘，原本每張單都打一次 API。

## 停損單走 Algo 端點（2026-09-21）
Binance 2025-12-09 起把條件單搬到 `/fapi/v1/algoOrder`，舊的 `/fapi/v1/order` 送 STOP_MARKET 會回
`-4120 Order type not supported for this endpoint`。這就是 NIL 和 SAGA 兩次「停損掛不上」的真正原因。
- 掛停損：POST `/fapi/v1/algoOrder`，`algoType=CONDITIONAL`、`triggerPrice`（不是 stopPrice）、回傳 `algoId`
- 撤停損：DELETE `/fapi/v1/algoOrder`，帶 `algoId`
- 查掛單：GET `/fapi/v1/openAlgoOrders`；每輪迴圈確認持倉的停損單還在，不在就自動補掛
- 市價單仍走 `/fapi/v1/order`，沒變

## 實盤出場管理（2026-09-21 補上，之前完全沒有）
之前實盤只掛初始停損單，回測的減碼／保本／追蹤／時間出場／冷卻全部沒做，實盤結果跟回測對不上。
現在 `manager.py` 每輪迴圈逐根收盤棒重現 `backtest.simulate` 的出場規則：
- 停損觸發 → 交易所停損單（reduce-only，可先掛新再撤舊，移動時沒有空窗）
- 1R 減碼一半＋停損移成本（C）、1R 保本（F/G）、追蹤停損、時間出場（C 72 根 5m、F/G 60 根 1m）、出場後冷卻
- 訊號只用**已收盤**的 K 棒判斷（之前拿剛開盤幾秒的進行中棒判斷）；迴圈對齊整分後 2 秒
- 一致性測試 `python test_parity.py`：回測和實盤管理的出場原因、出場根數必須完全一樣
**改出場規則時 backtest.py 和 manager.py 要一起改，改完跑 test_parity.py。**

## 安全與顯示（2026-09-21）
- 網頁有密碼保護：Zeabur 環境變數 `DASH_PASSWORD`。沒設就只能看、不能操作。
- 所有會改東西的 API 需要本頁送出的 `X-PDH` 標頭，外站連結點不動。
- 全站時間統一 UTC+8（`APP_TZ`）。例外：掃描的 peak_day 是 Binance 日 K 日期（UTC 切日）。
- 已平倉會記實際出場價、損益（扣手續費）、R 值；「清除」會一起清訊號和已平倉，已下單紀錄保留。

## 還沒做的
- F 和 G 的實盤滑價數據還沒累積夠（回測假設 0.3%）
- 監控門檻 40% 對 G 太嚴（G 要求 24h 漲幅 15–60%），考慮降到 15–25%
- 上主網前要開獨立子帳號
- F/G 的停損單掛單成功率要追蹤（看訊號表 stop_error 欄）
