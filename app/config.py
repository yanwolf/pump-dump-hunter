"""全部參數集中在這裡，回測與實盤共用。"""
import os

FAPI = "https://fapi.binance.com"
TESTNET = "https://testnet.binancefuture.com"
USE_TESTNET = os.environ.get("USE_TESTNET", "1") == "1"
API_KEY = os.environ.get("BINANCE_KEY", "")
API_SECRET = os.environ.get("BINANCE_SECRET", "")

# ---- 掃描層：多頭擁擠名單 ----
SCAN = dict(
    top_n=60,                # 先取 24h 漲幅前 N 檔算細項（觀察名單）
    watch_chg24=40.0,        # 24h 漲幅 >= 40% 的一律進引擎監控（崩後引擎只在崩時開槍，多盯不多訊號）
    max_watch=40,            # 引擎監控名單上限：每檔每分鐘 2 個請求，超過迴圈會跑不完一分鐘
    exclude=("BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK", "DOT", "LTC", "BCH",
             "TON", "SUI", "XLM", "HBAR", "SHIB", "NEAR", "APT", "ARB", "OP", "UNI", "AAVE", "ATOM", "ETC", "FIL"),
    min_gain_48h=0.5,        # 48h 漲幅 >= 50%
    min_funding=0.0003,      # 資金費率 >= 0.03% / 8h（0.01% 是常態）
    min_oi_growth_24h=0.3,   # OI 24h 增幅 >= 30%
    min_ma20_dev=0.25,       # 價格偏離 1h MA20 >= 25%
    min_score=3,             # 四項中 >= 3 項才進「擁擠名單」
    crashed_drop=-30.0,      # 24h 跌幅 <= -30% 的也直接進名單（崩後引擎 B/C/D 要盯）
    min_quote_vol_24h=3e6,   # 24h 成交額 >= 300 萬 USDT（太薄的不碰）
    max_quote_vol_24h=None,  # 不再用成交額判大小幣：LSK 市值 2 億但瘋起來成交額 14 億，會被誤踢；改用 exclude 清單
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
    min_gain_24h=0.40,       # 進場當下 24h 內從低點漲幅 >= 40% 才做（跟實盤名單門檻一致；拉升初期的洗盤不追）
)

ENGINE_G = dict(  # 暴漲進行中順勢追多（1m）：軋空剛啟動的前幾分鐘，F 的鏡像
    window=15,               # 看最近 15 根 1m
    rise=0.10,               # 15 分鐘內從低到現在漲 >= 10%
    green_bars=3,            # 最近 N 根全部收紅（上漲）
    vol_mult=4.0,            # 最近 3 根平均量 >= 4x MAVOL20
    stop_bars=5,             # 止損放最近 5 根 1m 低點
    min_stop=0.03,
    gain24_min=0.15,         # 24h 漲幅下限：已經動起來
    gain24_max=0.60,         # 上限：超過就是追最後一棒（做多跟做空的不對稱處）
    max_funding=0.0015,      # 資金費率已經 >0.15%/8h 代表多頭擠爆，不追
)

# 各引擎出場覆蓋（沒寫的用 RISK 預設）
# 各引擎出場/風險覆蓋（risk_pct = 每筆風險佔本金；2026-09-16 崩盤日回測：C PF5.7 主力，B/F PF~1.1-1.3 收數據用）
EXIT = dict(
    A=dict(tp1_r=None, be_r=1.0, trail_after_r=2.0, trail_bars=3, max_hold_bars=144, cooldown_bars=48),  # 不減碼；出場後 4h 冷卻
    B=dict(cooldown_bars=24, risk_pct=0.01),
    C=dict(cooldown_bars=24, risk_pct=0.02),
    D=dict(max_hold_bars=24, trail_after_r=1.5, trail_bars=3, cooldown_bars=48),
    E=dict(max_hold_bars=288, trail_after_r=2.0, trail_bars=12, cooldown_bars=288),  # 一天最多一次
    F=dict(tp1_r=None, be_r=1.0, trail_after_r=1.5, trail_bars=3, max_hold_bars=60, cooldown_bars=30,
           min_stop_pct=0.03, risk_pct=0.01),
    G=dict(tp1_r=None, be_r=1.0, trail_after_r=1.5, trail_bars=3, max_hold_bars=60, cooldown_bars=30,
           min_stop_pct=0.03, risk_pct=0.01),   # 1m 引擎：不減碼、1R 保本、1.5R 起 3 根高點追蹤、最多 60 分鐘
)

# ---- 本金階梯：下單金額用「階梯本金」算，不是有多少用多少 ----
SIZING = dict(
    use_live_balance=True,   # True=讀帳戶錢包餘額；False=固定用 base
    base=500.0,              # 基準本金
    step=250.0,              # 每級 250：餘額 500-749 用 500，750-999 用 750，1000-1249 用 1000…
    floor=250.0,             # 虧損往下的最低階
    cap=1500.0,              # 最高階（demo 5000U 三個專案共用，這個策略最多用 1500）
    reserve_pct=0.25,        # 保留 25% 不用：實際算倉位的是階梯本金 × 0.75
    leverage=10,             # 固定槓桿；保證金 = 名目 / 槓桿
    max_positions=3,         # 同時最多幾筆；每筆保證金 <= 可用本金 / max_positions
    refresh_sec=60,          # 餘額多久讀一次
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
    snap = {k: copy.deepcopy(globals()[k]) for k in ("SCAN", "ENGINE_A", "ENGINE_B", "ENGINE_C", "ENGINE_D", "ENGINE_E", "ENGINE_F", "ENGINE_G", "EXIT", "RISK")}
    for k, v in (o or {}).items():
        if k not in snap or not isinstance(v, dict): continue
        if k == "EXIT":
            for e, ev in v.items(): globals()[k].setdefault(e, {}).update(ev)
        else: globals()[k].update(v)
    return snap

def restore(snap):
    for k, v in snap.items(): globals()[k].clear(); globals()[k].update(v)
