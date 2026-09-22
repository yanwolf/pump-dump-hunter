"""參數預設集：存在 DATA_DIR/presets.json（Zeabur Volume），重佈不會掉。
- 具名預設：{"名稱": {"SCAN.watch_chg24": 15, ...}}
- live：目前套用到實盤的那組覆蓋，程式啟動時自動載入
介面（server.py 用）：all / all_presets / live / save_preset / delete_preset / set_live / apply_live / boot
"""
import json, os, copy, threading
from . import config as C, params

DATA_DIR = os.environ.get("DATA_DIR", "./data"); os.makedirs(DATA_DIR, exist_ok=True)
PATH = os.path.join(DATA_DIR, "presets.json")
_lock = threading.Lock()
_KEYS = ("SCAN", "ENGINE_A", "ENGINE_B", "ENGINE_C", "ENGINE_D", "ENGINE_E", "ENGINE_F", "ENGINE_G", "EXIT", "RISK")
_DEFAULTS = {k: copy.deepcopy(getattr(C, k)) for k in _KEYS}   # 程式預設值快照（import 時、尚未套任何覆蓋）

def _load():
    try:
        d = json.load(open(PATH))
        d.setdefault("presets", {}); d.setdefault("live", {}); d.setdefault("live_name", None)
        return d
    except Exception: return {"presets": {}, "live": {}, "live_name": None}

def _save(d):
    tmp = PATH + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, PATH)

def all(): return _load()
def all_presets(): return _load()["presets"]
def live(): return _load()["live"] or {}

def save_preset(name, form):
    with _lock: d = _load(); d["presets"][name] = form; _save(d)

def delete_preset(name):
    with _lock:
        d = _load(); d["presets"].pop(name, None)
        if d.get("live_name") == name: d["live_name"] = None
        _save(d)

def set_live(form, name=None):
    with _lock: d = _load(); d["live"] = form or {}; d["live_name"] = name; _save(d)

def apply_live():
    """先回到程式預設，再套 Volume 裡的 live 覆蓋；回傳套用的表單。空表單 = 立即回預設，不用重啟。"""
    form = live()
    C.restore(copy.deepcopy(_DEFAULTS))
    if form:
        try: C.apply_overrides(params.to_overrides(form))
        except Exception as e:
            # 只 print 的話，錯誤區與推播都看不到（清單用法第 5 點 r29：錯誤掃描要攔到所有模組）
            print("apply_live fail:", e)
            from . import store, telegram
            store.push("errors", f"套用實盤參數覆蓋失敗 {type(e).__name__}: {e}")
            telegram.send(f"🐞 套用實盤參數覆蓋失敗：{type(e).__name__}: {e} — 目前用的是程式預設值")
    return form

def boot():
    apply_live(); return _load()

# 舊介面相容
def save(name, form): save_preset(name, form); return _load()
def delete(name): delete_preset(name); return _load()
def clear_live(): set_live({}); apply_live(); return _load()
