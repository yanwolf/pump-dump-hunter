# 清單 r88 → r97 差異的行為測試：第 6 條「公開行情端點一樣算 IP 權重」。
# 模擬交易所照真的幣安算 K 線權重（tests/fake_exchange.py：超過上限 429、429 之後還打 418、封鎖期間 418），
# 時間用虛擬時鐘（等待真的讓時間往前走），在 HTTP 層攔截，app/binance.py 的程式真的跑過。
# 在專案根目錄執行：python -m tests.test_r97
import glob, json, os
from tests.harness import (B, check, fresh, finish, one, preflight, run)   # 共用案例框架（清單用法第 5 點 r27）
from app import sweep

MIN0 = 29_333_334 * 60.0                 # 某一分鐘的開頭（虛擬時鐘從這分鐘的第 1 秒開始）
DAY = 86_400_000


def clock(fx):
    """虛擬時鐘：程式的等待（_sleep）讓時間往前走；模擬交易所用同一個時鐘換分鐘、算權重。"""
    vt, slept = [MIN0 + 1.0], []
    def sleep(s):
        slept.append(s); vt[0] += s
    B._clock = lambda: vt[0]; B._sleep = sleep; fx.clock = lambda: vt[0]
    return vt, slept


def statuses(fx): return [c.get("status") for c in fx.kl_calls]


print("批次連續抓：10 檔 × 30 天 1 分 K（300 個請求、3000 權重，一分鐘上限 2400）——要自己讓路，不能打到 429／418")
fx = fresh(); vt, slept = clock(fx)
end = int(MIN0 * 1000) - 60_000
got = {}
for s in ("XUSDT", "YUSDT") * 5:
    k, err = run(lambda: B.klines_range(s, "1m", end - 30 * DAY, end))
    got[len(got)] = (len(k) if k is not None else None, err)
check("V1", "（前提）模擬交易所真的照權重記帳：K 線請求有記錄、回應帶已用權重", len(fx.kl_calls) > 0 and all("used" in c for c in fx.kl_calls),
      f"{len(fx.kl_calls)} 個請求")
check("V1", "10 段都抓完整（每段 30 天 × 1440 根，含兩端共 43201 根），沒有一段因限流中斷", all(n == 30 * 1440 + 1 and e is None for n, e in got.values()),
      f"{[(n, str(e)[:40] if e else None) for n, e in got.values()][:4]}")
check("V1", "（對照組）總權重確實超過一分鐘上限——不讓路就一定會撞到", sum(fx.kl_weight(c["limit"]) for c in fx.kl_calls) > 2400,
      f"總權重 {sum(fx.kl_weight(c['limit']) for c in fx.kl_calls)}")
check("V1", "整段沒有任何 429／418", not any(st in (429, 418) for st in statuses(fx)), f"狀態 {sorted(set(statuses(fx)), key=str)}")
check("V1", "每一分鐘的已用權重都沒超過上限的一半（1200），另一半留給實盤", max(c.get("used", 0) for c in fx.kl_calls) <= 1200,
      f"最高 {max(c.get('used', 0) for c in fx.kl_calls)}")
check("V1", "有等到下一分鐘（等待讓時間往前走了一分鐘以上）", sum(slept) >= 60, f"等待合計 {sum(slept):.0f} 秒、{len(slept)} 次")


print("實盤的 K 線不排研究端的隊：同一 IP 這一分鐘已用 1500（例如事件掃描子程序），實盤照抓、批次等下一分鐘")
fx = fresh(); vt, slept = clock(fx)
fx.ipw.update(minute=int(MIN0 // 60), used=1500)
k, err = run(lambda: B.klines("XUSDT", "1m", 100))
check("V2", "實盤 K 線立刻抓到（不等）", k is not None and len(k) > 0 and not slept, f"{len(k or [])} 根、等待 {slept}、err={err}")
n0 = len(fx.kl_calls)
k2, err2 = run(lambda: B.klines_range("XUSDT", "5m", int(MIN0 * 1000) - DAY, int(MIN0 * 1000)))
new = fx.kl_calls[n0:]
check("V2", "（前提）批次抓到資料了", k2 is not None and len(k2) > 0 and len(new) > 0, f"{len(k2 or [])} 根、err={err2}")
check("V2", "批次的請求等到下一分鐘才送（這一分鐘已超過一半）", all(c["t"] >= MIN0 + 60 for c in new),
      f"送出時間 {[round(c['t'] - MIN0, 1) for c in new]} 秒（以這一分鐘開頭為 0）")


print("429：照 Retry-After 等完重試、資料照樣完整；不是「這段期間沒有資料」")
fx = fresh(); vt, slept = clock(fx)
fx.ipw.update(minute=int(MIN0 // 60), used=2395)      # 別的程序把這一分鐘幾乎用完了；程式這邊還不知道
k, err = run(lambda: B.klines_range("XUSDT", "5m", int(MIN0 * 1000) - DAY, int(MIN0 * 1000)))
check("V3", "（前提）第一個請求真的被 429", statuses(fx)[:1] == [429], f"狀態 {statuses(fx)}")
check("V3", "等完重試、抓到完整的一天 5 分 K（288 根）", k is not None and len(k) == 288, f"{len(k or [])} 根、err={err}")
check("V3", "重試是在 Retry-After 之後（下一分鐘）送的，沒有在冷卻中又打", len(fx.kl_calls) >= 2 and fx.kl_calls[1]["t"] >= MIN0 + 60,
      f"送出時間 {[round(c['t'] - MIN0, 1) for c in fx.kl_calls]}")
check("V3", "（前提已確認被 429）之後沒有升級成 418", 418 not in statuses(fx), f"狀態 {statuses(fx)}")


print("一直被 429：重試 3 次（每次照 Retry-After、最多 120 秒）還是被擋 → 拋「暫時性限流」，不回空的 K 線")
fx = fresh(); vt, slept = clock(fx)
fx.weight_cap = 0                                    # 每個請求都超過上限
k, err = run(lambda: B.klines_range("XUSDT", "5m", int(MIN0 * 1000) - DAY, int(MIN0 * 1000)))
RL = getattr(B, "RateLimited", None)
check("V4", "（前提）每個請求都被 429", statuses(fx)[:1] == [429], f"狀態 {statuses(fx)}")
check("V4", "拋的是限流例外（RateLimited），訊息講明是暫時性的", RL is not None and isinstance(err, RL) and "暫時性" in str(err), f"err={err!r}")
check("V4", "總共 4 個請求（第一次 + 重試 3 次）", len(fx.kl_calls) == 4, f"{len(fx.kl_calls)} 個")
check("V4", "每次等待都不超過 120 秒，而且都有等", len(slept) >= 3 and max(slept) <= 120, f"等待 {[round(s) for s in slept]}")
check("V4", "（前提已確認一直被 429）沒有在冷卻中打到 418", 418 not in statuses(fx), f"狀態 {statuses(fx)}")


print("418：不重試、立刻停；封鎖期間這個程序不再打 K 線（批次、實盤都一樣）")
fx = fresh(); vt, slept = clock(fx)
fx.ban_until = MIN0 + 600
k, err = run(lambda: B.klines_range("XUSDT", "5m", int(MIN0 * 1000) - DAY, int(MIN0 * 1000)))
BAN = getattr(B, "IPBanned", None)
check("V5", "（前提）第一個請求被 418", statuses(fx) [:1] == [418], f"狀態 {statuses(fx)}")
check("V5", "拋的是封鎖例外（IPBanned），只送了一個請求", BAN is not None and isinstance(err, BAN) and len(fx.kl_calls) == 1,
      f"err={err!r}、{len(fx.kl_calls)} 個請求")
n0 = len(fx.kl_calls)
_, err2 = run(lambda: B.klines_range("YUSDT", "5m", int(MIN0 * 1000) - DAY, int(MIN0 * 1000)))
_, err3 = run(lambda: B.klines("YUSDT", "1m", 50))
check("V5", "封鎖期間再抓（批次、實盤）：一個請求都沒送，照樣拋封鎖例外", len(fx.kl_calls) == n0 and BAN is not None
      and isinstance(err2, BAN) and isinstance(err3, BAN), f"多送了 {len(fx.kl_calls) - n0} 個、err2={err2!r}")
check("V5", "（前提已確認封鎖）沒有為了重試去等", sum(slept) == 0, f"等待 {slept}")


def sweep_env(days=3, mode="crash"):
    os.environ.update(SWEEP_DAYS=str(days), SWEEP_MODE=mode, SWEEP_LABEL="t97", SWEEP_OVERRIDES="{}")
    for f in glob.glob(os.path.join(sweep.KDIR, "*.json")):
        if os.path.exists(f): os.remove(f)
    if os.path.exists(sweep.STATE): os.remove(sweep.STATE)
    B.perp_symbols = lambda: ["XUSDT", "YUSDT"]
    B.ticker_24h = lambda: {s: {"quoteVolume": "50000000"} for s in ("XUSDT", "YUSDT")}
    return f"events_{mode}_{days}_"


def cached_events(prefix): return glob.glob(os.path.join(sweep.KDIR, prefix + "*.json"))


print("事件掃描（崩盤日母體）：掃日線時一檔被限流 → 整個中止、不存當天的母體快取（以前那檔直接從母體消失、還被快取一整天）")
fx = fresh(); vt, slept = clock(fx)
pre = sweep_env()
fx.inject.append(dict(path="/fapi/v1/klines", match=lambda p: p.get("symbol") == "YUSDT" and p.get("interval") == "1d",
                      kind="err", code=429, body='{"code":-1003,"msg":"Too many requests"}', times=20))
_, err = run(sweep.main)
st = sweep.state()
check("V6", "（前提）掃描真的跑了、YUSDT 的日線真的被 429", any(c[1] == "/fapi/v1/klines" and c[2].get("symbol") == "YUSDT" for c in fx.calls)
      and any(f["kind"] == "err" for f in fx.fired), f"注入觸發 {len(fx.fired)} 次")
check("V6", "狀態寫明「中止」與限流，不是「完成」", str(st.get("status", "")).startswith("中止") and "限流" in st.get("status", ""), st.get("status"))
check("V6", "（前提已確認被限流）當天的母體沒有寫進快取", not cached_events(pre), f"{cached_events(pre)}")
check("V6", "（前提已確認被限流）比較表沒有多一列", not any(r.get("label", "").startswith("t97") for r in st.get("runs", [])), f"{st.get('runs')}")


print("事件掃描：掃日線時一檔是別的錯誤（5xx）→ 照樣跑完，但狀態寫明幾檔抓不到、母體不快取")
fx = fresh(); vt, slept = clock(fx)
pre = sweep_env()
fx.inject.append(dict(path="/fapi/v1/klines", match=lambda p: p.get("symbol") == "YUSDT" and p.get("interval") == "1d",
                      kind="err", code=503, body="Service Unavailable", times=20))
_, err = run(sweep.main)
st = sweep.state()
check("V7", "（前提）YUSDT 的日線真的出錯", any(f["kind"] == "err" for f in fx.fired), f"注入觸發 {len(fx.fired)} 次")
check("V7", "狀態是「完成」，而且寫明有幾檔抓不到日線、母體可能不完整", str(st.get("status", "")).startswith("完成") and "抓不到日線" in st.get("status", ""),
      st.get("status"))
check("V7", "（前提已確認有幣抓不到）這份不完整的母體沒有寫進快取（下次重跑會補齊）", not cached_events(pre), f"{cached_events(pre)}")


print("事件掃描（對照組）：都抓得到 → 完成、母體照常快取")
fx = fresh(); vt, slept = clock(fx)
pre = sweep_env()
_, err = run(sweep.main)
st = sweep.state()
check("V8", "完成，而且母體寫進快取", str(st.get("status", "")).startswith("完成") and len(cached_events(pre)) == 1, f"{st.get('status')}、{cached_events(pre)}")


print("回測事件時被限流 → 整個中止、不彙整一部分（只彙整抓到的事件會偏）、比較表不多一列")
fx = fresh(); vt, slept = clock(fx)
pre = sweep_env()
peak = int(MIN0 * 1000) // DAY * DAY - 2 * DAY
json.dump([dict(symbol="XUSDT", peak_day="t", peak_ts=peak, pump=10, dump=35)],
          open(os.path.join(sweep.KDIR, pre + __import__("time").strftime("%Y%m%d") + ".json"), "w"))
fx.inject.append(dict(path="/fapi/v1/klines", match=lambda p: p.get("symbol") == "XUSDT" and p.get("interval") == "5m",
                      kind="err", code=429, body='{"code":-1003,"msg":"Too many requests"}', times=20))
_, err = run(sweep.main)
st = sweep.state()
check("V9", "（前提）事件的 5 分 K 真的被 429", any(f["kind"] == "err" for f in fx.fired), f"注入觸發 {len(fx.fired)} 次")
check("V9", "狀態寫明「中止」與限流", str(st.get("status", "")).startswith("中止") and "限流" in st.get("status", ""), st.get("status"))
check("V9", "（前提已確認被限流）比較表沒有多一列", not any(r.get("label", "").startswith("t97") for r in st.get("runs", [])), f"{st.get('runs')}")
check("V9", "（前提已確認被限流）沒有把這個事件的 K 線寫成空的快取", not glob.glob(os.path.join(sweep.KDIR, "XUSDT_5m_*.json")),
      f"{glob.glob(os.path.join(sweep.KDIR, 'XUSDT_5m_*.json'))}")


print("自檢：多一項 IP 權重（第 6 條 r97），平常 ok、被封鎖時 fail")
fx = fresh(); vt, slept = clock(fx)
res = preflight.check()
it = [r for r in res if "IP 權重" in r["item"]]
check("V10", "自檢有「IP 權重」這一項，而且是 ok、寫出這一分鐘的已用權重", len(it) == 1 and it[0]["status"] == "ok" and "已用" in it[0]["msg"], f"{it}")
fx = fresh(); vt, slept = clock(fx)
fx.ban_until = MIN0 + 600
res = preflight.run_and_report(send=True)
it = [r for r in res if "IP 權重" in r["item"]]
check("V10", "被封鎖時這一項是 fail", len(it) == 1 and it[0]["status"] == "fail", f"{it}")
ok, why = one("🔎 交易所自檢", "IP 權重", "418")
check("V10", "推播的自檢結果裡有這一項、寫明是 418 封鎖", ok, why)

for k in ("SWEEP_DAYS", "SWEEP_MODE", "SWEEP_LABEL", "SWEEP_OVERRIDES"): os.environ.pop(k, None)
finish(allowed=("HTTP 429", "HTTP 418", "HTTP 503", "限流", "封鎖"))
