"""全部參數集中在這裡，回測與實盤共用。"""
import os

FAPI = "https://fapi.binance.com"
TESTNET = "https://testnet.binancefuture.com"
USE_TESTNET = os.environ.get("USE_TESTNET", "1") == "1"
API_KEY = os.environ.get("BINANCE_KEY", "")
API_SECRET = os.environ.get("BINANCE_SECRET", "")

# ---- 掃描層：多頭擁擠名單 ----
SCAN = dict(
    top_n=40,                # 先取 24h 漲幅前 N 檔算細項（觀察名單）
    min_gain_48h=0.5,        # 48h 漲幅 >= 50%
    min_funding=0.0003,      # 資金費率 >= 0.03% / 8h（0.01% 是常態）
    min_oi_growth_24h=0.3,   # OI 24h 增幅 >= 30%
    min_ma20_dev=0.25,       # 價格偏離 1h MA20 >= 25%
    min_score=3,             # 四項中 >= 3 項才進「擁擠名單」
    crashed_drop=-30.0,      # 24h 跌幅 <= -30% 的也直接進名單（崩後引擎 B/C/D 要盯）
    min_quote_vol_24h=3e6,   # 24h 成交額 >= 300 萬 USDT（太薄的不碰）
    max_quote_vol_24h=3e8,   # 太大的不是小幣
)

# ---- 進場層 ----
ENGINE_A = dict(  # 崩前：頂背馳 + 中樞跌破
    pivot_bars=36,           # 中樞 = 最近 3 小時（36 根 5m）的高低區間
    hot_bars=288,            # 24h 內從低點漲幅 >= hot_gain 才算「拉過頭」
    hot_gain=0.40,
    div_bars=48,             # 頂背馳比較窗：最近 48 根的高點 vs 前 48 根的高點
    min_pivot_width=0.03,    # 中樞寬度 >= 3%
    max_pivot_width=0.25,    # 太寬不是盤整，是還在噴
    near_top=0.85,           # 中樞上緣 >= 24h 高點的 85%，排除拉升途中的回檔
    brk_vol_mult=1.5,        # 跌破棒量 >= 1.5x MAVOL20
    top_age_bars=12,         # 區間高點至少 N 根前做的（0=不檢查）；過濾「還在噴的回檔」
    entry_min_of_high=0.75,  # 進場價 >= 24h 高點的 75%：只做從頂部下來的第一刀，不追第五段
)
ENGINE_B = dict(  # 崩後：死貓反彈做空
    crash_bars=12,           # 12 根 5m = 1h
    crash_drop=0.30,         # 1h 內跌幅 >= 30%
    vol_mult=3.0,            # 量 >= 3x MAVOL20
    bounce_min=0.20,         # 反彈幅度佔跌幅 20%~50%
    bounce_max=0.50,
    wait_bars=36,            # 崩後最多等 3 小時
    after_hi_bars=8,         # 力竭必須在反彈高點後 8 根內，否則反彈早結束了
)
ENGINE_C = dict(  # 崩盤延續：單根崩盤棒破低追空
    bar_drop=0.15,           # 單根 5m 跌幅 >= 15%
    vol_mult=3.0,
    confirm_bars=3,          # 3 根內破崩盤棒低點才進
)

ENGINE_D = dict(  # 崩後 V 反抄底（多）
    crash_bars=12, crash_drop=0.30, vol_mult=3.0,
    wick_ratio=0.5,          # 下影線 >= 全棒 50%
    recover=0.5,             # 收盤收回前一根實體一半以上
    wait_bars=24,            # 崩後 2 小時內才算
)
ENGINE_E = dict(  # 拉升初期突破回踩跟多（多）
    range_bars=864,          # 3 天區間（5m）
    vol_mult=3.0,
    pull_bars=24,            # 突破後 2 小時內回踩
    pull_tol=0.04,           # 回踩到突破位 +4% 以內
)

ENGINE_F = dict(  # 崩盤進行中順勢追空（1m K 線）：抓清算連鎖啟動的前幾分鐘
    window=15,               # 看最近 15 根 1m
    drop=0.10,               # 15 分鐘內從高到現在跌 >= 10%
    red_bars=3,              # 最近 N 根全部收黑
    vol_mult=4.0,            # 最近 3 根平均量 >= 4x MAVOL20（1m）
    stop_bars=5,             # 止損放最近 5 根 1m 高點
    min_stop=0.03,           # 止損距離至少 3%（1m 太貼容易被掃）
)

# 各引擎出場覆蓋（沒寫的用 RISK 預設）
EXIT = dict(
    A=dict(tp1_r=None, be_r=1.0, trail_after_r=2.0, trail_bars=3, max_hold_bars=144, cooldown_bars=48),  # 不減碼；出場後 4h 冷卻
    B=dict(cooldown_bars=24),
    C=dict(cooldown_bars=24),
    D=dict(max_hold_bars=24, trail_after_r=1.5, trail_bars=3, cooldown_bars=48),
    E=dict(max_hold_bars=288, trail_after_r=2.0, trail_bars=12, cooldown_bars=288),  # 一天最多一次
    F=dict(tp1_r=None, be_r=1.0, trail_after_r=1.5, trail_bars=3, max_hold_bars=60, cooldown_bars=30,
           min_stop_pct=0.03),   # 1m 引擎：不減碼、1R 保本、1.5R 起 3 根高點追蹤、最多 60 分鐘
)

# ---- 風控（低勝率高賠率的核心）----
RISK = dict(
    equity=500.0,
    risk_pct=0.02,           # 每筆最多賠本金 2%
    max_leverage=20,
    max_stop_pct=0.25,       # 止損距離 > 25% 的訊號一律不進（賠率已經壞掉）
    min_stop_pct=0.05,       # 止損距離 < 5% 的訊號也不進：手續費+滑價會吃掉 R 的兩成以上
    tp1_r=1.0,               # 1R 出一半、止損移到成本（None = 不減碼）
    be_r=None,               # 不減碼時，到幾 R 把止損移到成本
    trail_after_r=2.0,       # 2R 後用最近 3 根高點追蹤
    trail_bars=3,
    max_hold_bars=72,        # 最多持有 6 小時
    fee=0.0005,              # 單邊 taker
    slippage=0.003,          # 小幣先抓 0.3%，之後用實測數據覆蓋
    cooldown_bars=0,         # 同引擎出場後 N 根內不再進（各引擎可在 EXIT 覆蓋）
)

def apply_overrides(o):
    """回測/掃描用：以 dict 覆蓋參數，回傳還原用的快照。格式 {"ENGINE_A": {...}, "EXIT": {"A": {...}}, "RISK": {...}}"""
    import copy
    snap = {k: copy.deepcopy(globals()[k]) for k in ("SCAN", "ENGINE_A", "ENGINE_B", "ENGINE_C", "ENGINE_D", "ENGINE_E", "ENGINE_F", "EXIT", "RISK")}
    for k, v in (o or {}).items():
        if k not in snap or not isinstance(v, dict): continue
        if k == "EXIT":
            for e, ev in v.items(): globals()[k].setdefault(e, {}).update(ev)
        else: globals()[k].update(v)
    return snap

def restore(snap):
    for k, v in snap.items(): globals()[k].clear(); globals()[k].update(v)
