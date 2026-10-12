"""狀態全部放 Volume（DATA_DIR），repo 內不留任何部署狀態。
狀態檔是持倉、成交界線、待平倉、pending 能跨重啟的唯一依據（清單第 8 條 r36）：
- 讀檔失敗不能靜靜回到空白：壞檔另存一份、記進錯誤區、開機時由主程式推播（LOAD_ERROR）
- 存檔先寫暫存檔再換名，寫到一半當機不會留下壞檔
- 存檔失敗不往外拋進交易流程（記憶體裡的狀態照樣對），但要寫錯誤區、照節奏推播、恢復時通知"""
import json, os, shutil, threading, time
DATA_DIR = os.environ.get("DATA_DIR", "./data"); os.makedirs(DATA_DIR, exist_ok=True)
_lock = threading.RLock()   # 可重入：存檔失敗 → 推播 → 推播失敗寫錯誤區（store.push）會在同一條執行緒再拿一次鎖（r36）
_state = dict(watch=[], observe=[], signals=[], sweep={}, open={}, closed=[], loop=None, trades=[], last_scan=None, started=None, errors=[])
_path = os.path.join(DATA_DIR, "state.json")
_DEFAULT = json.loads(json.dumps(_state))
LOADED, LOAD_ERROR = True, None
_load_fail = dict(n=0, backed_up=False)

def _read_file():
    """回傳 (ok, 資料或錯誤)。「讀取失敗」與「沒有資料」不能是同一個回傳值（清單第 8 條 r38）：
    檔案不存在 = 第一次啟動，ok、空資料；檔案在但讀不了、解析不了、格式不對 = 失敗。"""
    if not os.path.exists(_path):
        # 讀取失敗期間原檔不見了（被搬走、改名）：仍是失敗。照開機的規則當成全新開始，就恢復開新倉、帳是空的——假恢復（r43）
        if _load_fail["n"]: return False, FileNotFoundError("讀取失敗期間原檔不見了；確定要全新開始請放一份內容為 {} 的檔")
        return True, {}
    try:
        with open(_path, encoding="utf-8") as f: d = json.load(f)
        if not isinstance(d, dict): raise ValueError("最外層不是物件")
        return True, d
    except Exception as e: return False, e

def load():
    """讀狀態檔。開機時呼叫一次；讀取失敗時，背景迴圈每輪呼叫（retry_load）直到成功。
    - 在鎖外面讀檔與解析；成功才在鎖裡一次換上（讀到一半出錯不會留下半套，r39）
    - 失敗：壞檔**複製**一份（原檔留著，r39）；標記還沒載入 → 暫停開新倉、暫停寫原檔；照節奏推播"""
    global LOADED, LOAD_ERROR
    ok, d = _read_file()
    if ok:
        with _lock:
            kept = [e for e in (_state.get("errors") or []) if e not in (d.get("errors") or [])]   # 讀取失敗期間記下的錯誤要留著
            new = json.loads(json.dumps(_DEFAULT)); new.update(d)
            new["errors"] = ((new.get("errors") or []) + kept)[-300:]
            _state.clear(); _state.update(new)                                                   # 全部解析成功才一次換上
            was = _load_fail["n"]; _load_fail.update(n=0, backed_up=False)
            LOADED, LOAD_ERROR = True, None
        if was: _say(f"✅ 狀態檔已載入（先前讀取失敗 {was} 次），恢復開倉與存檔")
        return True
    n = _load_fail["n"] = _load_fail["n"] + 1
    bad = ""
    if not _load_fail["backed_up"]:
        try:
            bad = f"{_path}.bad-{int(time.time())}"; shutil.copy2(_path, bad); _load_fail["backed_up"] = True
            bad = f"；壞檔已另存 {os.path.basename(bad)}"
        except Exception as e2: bad = f"；另存壞檔失敗 {e2}"
    LOADED = False
    LOAD_ERROR = (f"狀態檔讀取失敗（第 {n} 次）{type(d).__name__}: {d}{bad}。持倉、成交界線、待平倉、pending 還沒載入："
                  "暫停開新倉、暫停寫入狀態檔（原檔保留），每輪重試；請修好檔案或對照交易所處理")
    with _lock: _state["errors"] = ((_state.get("errors") or []) + [f"{time.strftime('%m-%d %H:%M')} {LOAD_ERROR}"])[-300:]
    if _nag(n): _say(f"🐞 {LOAD_ERROR}")
    return False

retry_load = load

_fail = dict(n=0)

def _nag(n):   # 同 manager.nag 的節奏（store 不能匯入 manager，會循環匯入）
    return n in (1, 5, 30) or (n > 30 and (n - 30) % 120 == 0)

def _say(msg):
    try:
        from . import telegram; telegram.send(msg)
    except Exception: pass                           # 推播本身失敗：錯誤區已經有了

def _save():
    if not LOADED:
        # 讀取失敗期間不能寫原檔：記憶體是空白的，寫下去原檔就被空白蓋掉，下次重試「讀取成功」——假恢復（清單第 8 條 r39）。
        # 這段期間的狀態寫到旁邊的檔，留作對照。
        try:
            with open(_path + ".unloaded", "w", encoding="utf-8") as f: json.dump(_state, f, ensure_ascii=False)
        except Exception: pass                    # 旁邊的檔只是留作對照；讀取失敗本身已經在推播
        return
    try:
        tmp = _path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(_state, f, ensure_ascii=False)
        os.replace(tmp, _path)
        if _fail["n"]:
            n = _fail["n"]; _fail["n"] = 0
            _say(f"✅ 狀態檔存檔已恢復（先前連續失敗 {n} 次）")
    except Exception as e:
        n = _fail["n"] = _fail["n"] + 1
        _state["errors"] = (_state.get("errors") or []) + [f"{time.strftime('%m-%d %H:%M')} 狀態檔存檔失敗（第 {n} 次）{type(e).__name__}: {e}"]
        if _nag(n):
            _say(f"🐞 狀態檔存檔失敗（第 {n} 次）{type(e).__name__}: {e} — 記憶體裡的狀態照常，但重啟會遺失這段期間的變更")

def get(): return dict(_state)
load()                                            # 開機讀一次（函式定義完才能呼叫）
def update(**kw):
    with _lock:
        _state.update(kw)
        for key in ("signals", "trades", "errors", "closed"): _state[key] = _state[key][-300:]
        _save()
def push(key, item):
    with _lock: _state[key].append(item)
    update()
