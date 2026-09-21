# 工作交辦單：把踩坑清單套到另外兩個專案

給另一個對話視窗用。**這份單子只動 crypto-screener 和 gold-scalper，不要動 pump-dump-hunter。**

---

## 先講版本規則（避免弄混）

| 專案 | 目前最新版在哪 | 誰在改 |
|---|---|---|
| pump-dump-hunter | 本視窗剛產出的 zip（已改成 `app/` 套件結構） | **只有本視窗** |
| crypto-screener | 你手邊的 crypto-screener-v2.zip | 另一個視窗 |
| gold-scalper | 你手邊的 gold-scalper.zip | 另一個視窗 |

規則三條：

1. **一個專案同時只給一個視窗改。** 不要兩邊同時改同一個 repo。
2. **每次改完就整包上傳回 Working Copy**，下次要再改時，重新上傳最新的 zip 給對話，不要沿用舊的。
3. `BINANCE_LESSONS.md` 最上面有版本日期。三份的版本日期必須一樣；不一樣就代表有人漏更新。

## 要帶去另一個視窗的檔案

1. `BINANCE_LESSONS.md`（本視窗產出）
2. `preflight.py`（本視窗產出，交易所相容性自檢）
3. 該專案自己的 zip

開場白可以直接說：

> 這是 crypto-screener（或 gold-scalper）。先讀 BINANCE_LESSONS.md，那是我三個幣安專案共用的踩坑清單。
> 另外附上 preflight.py，是 pump-dump-hunter 在用的交易所自檢，請改寫成這個專案的介面。
> 然後照下面的檢查清單逐項確認。

---

## A. crypto-screener 要做的事

這個專案的 Algo 端點、誤平倉守衛、槓桿退讓都已經做得比另外兩個好，**不要動那幾塊**。要補的是：

1. **放進 `BINANCE_LESSONS.md`**（放專案根目錄）。
2. **加 `preflight.py`**：它的下單層是 `backend/trader.py` 的 `_request(method, path, params, signed)`，回傳 `(status, data)` 而不是丟例外，preflight 要照這個介面改寫。檢查項目照清單那 11 條。
3. **時區**：程式裡沒有任何時區設定，`backend/main.py:1708` 用 `time.gmtime()`，顯示會差 8 小時。
   啟動最前面加 `os.environ["TZ"]="CST-8"; time.tzset()`，並確認前端顯示的時間跟著變對。
   （日 K 日期那類欄位要保持 UTC，改之前先確認哪些是日線衍生的。）
4. **未收盤 K 棒**：沒找到過濾未收盤棒的程式碼，要確認訊號判斷是不是用到進行中的最後一根。
   如果是，過濾掉 `開盤時間 + 週期 > 現在` 的棒（清單第 9 條）。
5. **`cancel_conditional(symbol)` 用的是整個 symbol 全撤**（`algoOpenOrders` + `allOpenOrders`，trader.py:461），
   在 `trader.py:852` 手動平倉時會呼叫。三個專案共用 demo 帳號，如果另一個專案剛好也有同一個幣的掛單，
   會被一起撤掉。改成用記錄下來的 `algoId` / `orderId` 精準撤（清單第 7 條）。
   它自己的 `cancel_stop_orders` 已經是精準撤了，可以參考那個寫法。
6. **一致性測試**：確認它的實盤出場規則跟回測是同一套（清單第 11 條）。
   pump-dump-hunter 的 `tests/test_parity.py` 是現成範例：同一組 K 線丟給兩邊，出場原因與根數必須一致。

## B. gold-scalper 要做的事

1. **放進 `BINANCE_LESSONS.md`**。
2. **加 `preflight.py`**：它的下單層是 `app/execution.py` 的 `_signed_request`，改寫成那個介面。
   注意它是多帳戶設計（`account=DEFAULT_ACCOUNT`），自檢要能指定帳戶。
3. **最重要的一項：它完全沒有在交易所掛停損單。**
   `app/trading_core.py` 的停損、移動停損全部是程式內判斷（`sl_price` 比對現價）。
   代表 **Zeabur 服務一掛掉、網路一斷，倉位就完全沒有保護**。
   要決定走哪條路：
   - (a) 補掛交易所停損單當作保險（程式內邏輯照舊，掛單只是最後防線）。做法照清單第 1、8 條。
   - (b) 維持現狀，但要明確知道這個風險，並且加上服務健康檢查告警。
   這件事請先跟我確認要走哪條再動手，不要自己決定。
4. **精度**：`get_symbol_precision` 只取 `quantityPrecision`，沒有取 `stepSize` / `tickSize`。
   如果之後要掛停損單（走 a 路線），價格一定要照 `tickSize` 取整，否則 -1111（清單第 4 條）。
5. **槓桿上限**：沒有 `leverageBracket` 查詢，設定被拒會沉默失敗（清單第 5 條）。
   上主網或換子帳號前一定要補。
6. **時區**：同樣沒有時區設定，要確認顯示時間是不是 UTC。

## C. 兩個專案都要做的收尾

- 三份 `BINANCE_LESSONS.md` 版本日期一致。
- 每個專案的網頁都能手動執行自檢，開機也跑一次、有異常發 Telegram。
- 新發現的坑 → 加進清單 → 三份一起更新 → 順手加一項對應的自檢。

---

## 目前三個專案的狀態速查

| 項目 | pump-dump-hunter | crypto-screener | gold-scalper |
|---|---|---|---|
| Algo 條件單端點 | ✅ 已修（含退回舊端點） | ✅ 原本就有 | 不適用（沒掛條件單） |
| 查不到≠不存在守衛 | ✅ 已補 | ✅ 原本就有 | 不適用 |
| 下單記帳原子性 | ✅ 已修 | 待查 | 待查 |
| 精度 step/tick | ✅ | ✅ | ⚠️ 只有 quantityPrecision |
| 槓桿退讓 | ✅ 已補 | ✅ 原本就有 | ❌ 沒有 |
| 限流快取 | ✅ 已補 | 待查 | 待查 |
| 精準撤單（不全撤） | ✅ | ⚠️ 手動平倉時全撤 | 待查 |
| 未收盤 K 棒過濾 | ✅ 已修 | ⚠️ 待查 | 待查 |
| 時區 UTC+8 | ✅ 已修 | ❌ 沒設定 | ❌ 沒設定 |
| 回測/實盤出場一致性測試 | ✅ 有 | 待查 | 待查 |
| 交易所自檢 preflight | ✅ 有 | 要加 | 要加 |
