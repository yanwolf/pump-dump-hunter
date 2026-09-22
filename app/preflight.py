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

VERSION = "2026-09-22r54"      # 對應 BINANCE_LESSONS.md 版本；複製過去時連同這行一起帶


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
            f"{'algo' if B.algo_active() else '暫時走 legacy'} 端點可查，目前掛單 {len(stops)} 張")
    except Exception as e: add("條件單端點", "fail", e)

    # 4. 帳戶餘額與槓桿上限（新子帳戶常被限 5x）
    try: add("錢包餘額", "ok", f"{B.wallet_balance():.2f} U")
    except Exception as e: add("錢包餘額", "fail", e)
    try:
        mx = B.max_leverage("BTCUSDT")
        want = C.SIZING["leverage"]
        add("槓桿上限", "ok" if (mx or 0) >= want else "warn", f"上限 {mx}x，策略要 {want}x")
    except Exception as e: add("槓桿上限", "warn", e)

    # 推播設定：沒設的話所有告警都不會送出（清單第 8 條 r39）
    from . import telegram as _tg
    add("推播設定", "ok" if (_tg.TOKEN and _tg.CHAT) else "fail", "Telegram 已設定" if (_tg.TOKEN and _tg.CHAT) else "TG_TOKEN／TG_CHAT 沒設定，告警不會送出")
    # 狀態檔（r38、r39）
    add("狀態檔", "ok" if getattr(store, "LOADED", True) else "fail", "已載入" if getattr(store, "LOADED", True) else (store.LOAD_ERROR or "讀取失敗"))

    # 5. 速率限制：positionRisk 權重高，打太兇會 418
    pos = None
    try:
        t0 = time.time(); pos = B.open_positions()
        add("查持倉", "ok", f"{(time.time() - t0) * 1000:.0f} ms")
    except Exception as e:
        add("查持倉", "fail", f"{e}（418 = 被限流，檢查輪詢頻率）")

    # 6. 孤兒條件單（清單第 13 條）：掛著但帳號裡沒有對應部位的。只列出、不自動撤——共用帳號，可能是別的專案的
    if pos is not None:
        try:
            keys = {(p["symbol"], B.side_of(p)) for p in pos}
            orphan, unsure = [], []
            for o in B.all_open_stops():
                protects = "LONG" if o.get("side") == "SELL" else "SHORT"
                ps = o.get("positionSide")
                if ps in ("LONG", "SHORT"): protects = ps
                if (o.get("symbol"), protects) not in keys:
                    # 全量表可能回空清單（清單第 2 條 r15、r18）：列成孤兒前逐幣確認；查不到就標「無法確認」
                    try:
                        has = any(B.side_of(p) == protects for p in B.position_rows(o.get("symbol")))
                    except Exception: has = None
                    tag = f"{o.get('symbol')} {o.get('side')} {o.get('algoId') or o.get('orderId')}"
                    if has is None: unsure.append(tag)
                    elif not has: orphan.append(tag)
            msg = ("、".join(orphan[:8]) + (f" 等 {len(orphan)} 張" if len(orphan) > 8 else "")) if orphan else "沒有"
            if unsure: msg += f"；無法確認 {len(unsure)} 張（逐幣查詢異常）：" + "、".join(unsure[:5])
            add("孤兒條件單", "warn" if orphan or unsure else "ok", msg)
        except Exception as e: add("孤兒條件單", "warn", f"查詢失敗 {e}")

    return out


_last = dict(t=0, res=None)

def check_throttled(cooldown=60):
    """網頁按鈕用：自檢會打權重 40 的全帳號查詢，60 秒內重複按直接回上次結果（清單第 6 條延伸）。"""
    if _last["res"] is not None and time.time() - _last["t"] < cooldown:
        return dict(results=_last["res"], version=VERSION, cached=True, age=int(time.time() - _last["t"]))
    _last.update(t=time.time(), res=check())
    return dict(results=_last["res"], version=VERSION, cached=False)


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
