"""可調參數的說明表：dashboard 用它畫表單，使用者只填數字/選項，不用寫 JSON。
每項：group / key（config 路徑）/ label / unit / help（調大調小的意義）/ step"""
import config as C

SCHEMA = [
  # ---- A 崩前 ----
  dict(g="A 崩前（頂部中樞跌破做空）", k="ENGINE_A.pivot_bars",   label="中樞長度", unit="根5m", step=6,
       help="頂部盤整要看幾根 K（36=3小時）。調大：只抓磨得久的頂，訊號少；調小：小盤整也算，訊號多"),
  dict(g="A 崩前（頂部中樞跌破做空）", k="ENGINE_A.hot_gain",     label="拉升門檻", unit="倍", step=0.1,
       help="24h 內從低到高至少漲幾倍才算拉過頭（0.4=40%）。調大：只做瘋狂的，訊號少"),
  dict(g="A 崩前（頂部中樞跌破做空）", k="ENGINE_A.entry_min_of_high", label="第一刀門檻", unit="×24h高", step=0.05,
       help="進場價要在 24h 高點的幾成以上（0.75）。調大：只做剛從頂部掉下來那一刀；調小：跌一段了還會進（容易追第 N 段）"),
  dict(g="A 崩前（頂部中樞跌破做空）", k="ENGINE_A.brk_vol_mult", label="跌破放量", unit="×MAVOL", step=0.5,
       help="跌破那根的量要是均量幾倍。調大：要真的放量才進，訊號少但假跌破少"),
  dict(g="A 崩前（頂部中樞跌破做空）", k="ENGINE_A.top_age_bars", label="頂部至少幾根前", unit="根5m", step=6,
       help="區間高點要多久以前做的（12=1小時；0=不檢查）。調大：排除還在噴的回檔"),
  dict(g="A 崩前（頂部中樞跌破做空）", k="EXIT.A.cooldown_bars", label="冷卻期", unit="根5m", step=12,
       help="出場後幾根內不再進同一檔（48=4小時）。調大：一檔只做一兩次"),
  # ---- B ----
  dict(g="B 崩後（反彈力竭做空）", k="ENGINE_B.crash_drop",   label="崩盤門檻", unit="", step=0.05,
       help="1 小時內跌幅要多少才算崩（0.30=30%）。調大：只做真正的崩"),
  dict(g="B 崩後（反彈力竭做空）", k="ENGINE_B.bounce_min",   label="反彈下限", unit="佔跌幅", step=0.05,
       help="反彈至少收回跌幅的幾成（0.2）才算反彈完成"),
  dict(g="B 崩後（反彈力竭做空）", k="ENGINE_B.bounce_max",   label="反彈上限", unit="佔跌幅", step=0.05,
       help="反彈超過幾成（0.5）就當 V 反，不做空"),
  dict(g="B 崩後（反彈力竭做空）", k="ENGINE_B.after_hi_bars", label="力竭時限", unit="根5m", step=2,
       help="力竭訊號必須在反彈高點後幾根內（8=40分鐘），太晚代表反彈早結束"),
  # ---- C ----
  dict(g="C 崩盤延續（破崩盤棒低點追空）", k="ENGINE_C.bar_drop", label="單根跌幅", unit="", step=0.02,
       help="一根 5m 跌幅至少多少（0.15=15%）算崩盤棒"),
  dict(g="C 崩盤延續（破崩盤棒低點追空）", k="ENGINE_C.confirm_bars", label="確認窗", unit="根5m", step=1,
       help="幾根內破低才追（3）。調大：等久一點也進，訊號多"),
  # ---- D ----
  dict(g="D 崩後 V 反（抄底做多）", k="ENGINE_D.wick_ratio", label="下影線比例", unit="", step=0.1,
       help="下影線佔全棒的比例（0.5）。調大：要更明顯的長下影"),
  dict(g="D 崩後 V 反（抄底做多）", k="ENGINE_D.wait_bars", label="崩後時限", unit="根5m", step=6,
       help="崩盤低點後幾根內才抄（24=2小時）"),
  # ---- E ----
  dict(g="E 拉升初期（突破回踩做多）", k="ENGINE_E.range_bars", label="突破基準", unit="根5m", step=144,
       help="突破幾根內的高點才算（864=3天）。調大：更大級別的突破，訊號少"),
  dict(g="E 拉升初期（突破回踩做多）", k="ENGINE_E.vol_mult", label="突破放量", unit="×MAVOL", step=0.5,
       help="突破棒的量要是均量幾倍（3）"),
  dict(g="E 拉升初期（突破回踩做多）", k="ENGINE_E.pull_tol", label="回踩容忍", unit="", step=0.01,
       help="回踩到突破位上方幾 % 內算回踩（0.04）。調大：回踩不深也進"),
  dict(g="E 拉升初期（突破回踩做多）", k="EXIT.E.cooldown_bars", label="冷卻期", unit="根5m", step=48,
       help="出場後幾根內不再進（288=一天）"),
  # ---- 出場/風控 ----
  dict(g="出場與風控（全部引擎）", k="RISK.tp1_r",   label="1R 出一半", unit="R", step=0.5,
       help="到幾 R 先出一半並移止損到成本（1）。A 引擎固定不減碼，不受此影響"),
  dict(g="出場與風控（全部引擎）", k="RISK.trail_after_r", label="追蹤起點", unit="R", step=0.5,
       help="幾 R 之後開始用近幾根高低點追蹤止損（2）"),
  dict(g="出場與風控（全部引擎）", k="RISK.trail_bars", label="追蹤根數", unit="根5m", step=1,
       help="追蹤止損看最近幾根（3）。調大：給更多回檔空間，抱得久但回吐多"),
  dict(g="出場與風控（全部引擎）", k="RISK.max_hold_bars", label="最長持有", unit="根5m", step=12,
       help="超過就平倉（72=6小時）"),
  dict(g="出場與風控（全部引擎）", k="RISK.max_stop_pct", label="止損上限", unit="", step=0.05,
       help="止損距離超過幾 %（0.25）的訊號不做"),
  dict(g="出場與風控（全部引擎）", k="RISK.slippage", label="滑價假設", unit="", step=0.001,
       help="每邊滑價（0.003=0.3%）。調大＝更保守，看策略撐不撐得住"),
]

def _get(path):
    parts = path.split("."); obj = getattr(C, parts[0])
    for p in parts[1:]: obj = obj[p]
    return obj

def schema():
    out = []
    for s in SCHEMA:
        try: cur = _get(s["k"])
        except Exception: cur = None
        out.append(dict(s, default=cur))
    return out

def to_overrides(form):
    """{"ENGINE_A.hot_gain": 0.5, "EXIT.A.cooldown_bars": 0} -> config 覆蓋 dict；只含與預設不同的。"""
    o = {}
    for k, v in form.items():
        if v is None or v == "": continue
        try: cur = _get(k); v = type(cur)(v) if cur is not None and not isinstance(cur, bool) else float(v)
        except Exception: v = float(v)
        if cur == v: continue
        parts = k.split(".")
        if parts[0] == "EXIT": o.setdefault("EXIT", {}).setdefault(parts[1], {})[parts[2]] = v
        else: o.setdefault(parts[0], {})[parts[1]] = v
    return o
