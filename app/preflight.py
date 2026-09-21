"""交易所相容性自檢。

這支程式的用途：幣安改 API 時，不要等到真的下單才發現。
啟動時跑一次，網頁「研究」頁也可以手動跑。每一項都對應一個真的踩過的坑，
列在 BINANCE_LESSONS.md；那份清單三個專案（crypto-screener / gold-scalper / pump-dump-hunter）
內容相同，發現新坑就三份一起更新。

這個檔案本身也刻意寫成可以整支複製到另外兩個專案：
只依賴自己的 binance 模組提供 _get / _post / filters / position_mode_hedge。
"""
import time
from . import binance as B, config as C, telegram, store

VERSION = "2026-09-21"        # 三個專案共用；複製過去時連同這行一起帶


def check(trade=False):
    """回傳 [(項目, 狀態, 說明)]。狀態：ok / warn / fail。
    trade=False 時不會送出任何真的會成交的單。"""
    out = []

    def add(name, st, msg=""): out.append(dict(item=name, status=st, msg=str(msg)[:200])); return st

    # 1. 公開資料與精度過濾器（stepSize / tickSize 拿不到就會噴 -1111）
    try:
        f = B.filters("BTCUSDT")
        add("精度過濾器", "ok" if f and f.get("tick") else "fail", f"tick={f.get('tick')} step={f.get('step')}")
    except Exception as e: add("精度過濾器", "fail", e)

    if not C.API_KEY:
        add("API 金鑰", "warn", "未設定，以下需要簽章的項目略過")
        return out
    add("API 金鑰", "ok", "測試網" if C.USE_TESTNET else "正式網")

    # 2. 持倉模式（單向/雙向決定停損單要帶 reduceOnly 還是 positionSide）
    try: add("持倉模式", "ok", "雙向 hedge" if B.position_mode_hedge() else "單向 one-way")
    except Exception as e: add("持倉模式", "fail", e)

    # 3. 條件單端點：2025-12-09 幣安搬到 Algo 服務，舊端點回 -4120
    try:
        stops, ok = B.open_stops("BTCUSDT")
        add("條件單端點", "ok" if ok else "fail",
            f"{'algo' if B._algo_ok[0] is not False else 'legacy'} 端點可查，目前掛單 {len(stops)} 張")
    except Exception as e: add("條件單端點", "fail", e)

    # 4. 帳戶餘額與槓桿上限（新子帳戶常被限 5x）
    try: add("錢包餘額", "ok", f"{B.wallet_balance():.2f} U")
    except Exception as e: add("錢包餘額", "fail", e)
    try:
        mx = B.max_leverage("BTCUSDT")
        want = C.SIZING["leverage"]
        add("槓桿上限", "ok" if (mx or 0) >= want else "warn", f"上限 {mx}x，策略要 {want}x")
    except Exception as e: add("槓桿上限", "warn", e)

    # 5. 速率限制：positionRisk 權重高，打太兇會 418
    try:
        t0 = time.time(); B.open_positions()
        add("查持倉", "ok", f"{(time.time() - t0) * 1000:.0f} ms")
    except Exception as e:
        add("查持倉", "fail", f"{e}（418 = 被限流，檢查輪詢頻率）")

    return out


def run_and_report(send=True):
    res = check()
    bad = [r for r in res if r["status"] != "ok"]
    store.update(preflight=dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), version=VERSION, results=res))
    if bad:
        lines = "\n".join(f"{'❌' if r['status'] == 'fail' else '⚠️'} {r['item']}：{r['msg']}" for r in bad)
        store.push("errors", f"{time.strftime('%m-%d %H:%M')} 自檢有 {len(bad)} 項異常")
        if send: telegram.send(f"🔎 交易所自檢（{VERSION}）\n{lines}")
    elif send:
        telegram.send(f"🔎 交易所自檢通過（{len(res)} 項）")
    return res
