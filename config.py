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
    pivot_bars=9,            # 中樞 = 最近 N 根 5m 的重疊區
    lookback=48,             # 48 根內曾經偏離 MA20 >= dev
    ma20_dev=0.30,
    min_pivot_width=0.03,    # 中樞寬度 >= 3%，太窄=止損被插針
)
ENGINE_B = dict(  # 崩後：死貓反彈做空
    crash_bars=12,           # 12 根 5m = 1h
    crash_drop=0.30,         # 1h 內跌幅 >= 30%
    vol_mult=3.0,            # 量 >= 3x MAVOL20
    bounce_min=0.20,         # 反彈幅度佔跌幅 20%~50%
    bounce_max=0.50,
    wait_bars=36,            # 崩後最多等 3 小時
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

# 各引擎出場覆蓋（沒寫的用 RISK 預設）
EXIT = dict(
    D=dict(max_hold_bars=24, trail_after_r=1.5, trail_bars=3),     # 快進快出，只吃第一段反彈
    E=dict(max_hold_bars=288, trail_after_r=2.0, trail_bars=12),   # 拿久一點，用 1h 級別高低追蹤
)

# ---- 風控（低勝率高賠率的核心）----
RISK = dict(
    equity=500.0,
    risk_pct=0.02,           # 每筆最多賠本金 2%
    max_leverage=20,
    tp1_r=1.0,               # 1R 出一半、止損移到成本
    trail_after_r=2.0,       # 2R 後用最近 3 根高點追蹤
    trail_bars=3,
    max_hold_bars=72,        # 最多持有 6 小時
    fee=0.0005,              # 單邊 taker
    slippage=0.003,          # 小幣先抓 0.3%，之後用實測數據覆蓋
)
