"""參數預設集：存在 DATA_DIR/presets.json（Zeabur Volume），重佈不會掉。
- 具名預設：{"名稱": {"SCAN.watch_chg24": 15, ...}}
- live：目前套用到實盤的那組覆蓋，程式啟動時自動載入
"""
import json, os
import config as C, params

DATA_DIR = os.environ.get("DATA_DIR", "./data"); os.makedirs(DATA_DIR, exist_ok=True)
PATH = os.path.join(DATA_DIR, "presets.json")

def _load():
    try: return json.load(open(PATH))
    except Exception: return {"presets": {}, "live": {}, "live_name": None}

def _save(d): json.dump(d, open(PATH, "w"), ensure_ascii=False, indent=1)

def all(): return _load()

def save(name, form):
    d = _load(); d["presets"][name] = form; _save(d); return d

def delete(name):
    d = _load(); d["presets"].pop(name, None)
    if d.get("live_name") == name: d["live_name"] = None
    _save(d); return d

def apply_live(form, name=None):
    """套用到實盤（改動 config 的全域值），並記到檔案，下次啟動自動重套。"""
    C.apply_overrides(params.to_overrides(form))
    d = _load(); d["live"] = form; d["live_name"] = name; _save(d); return d

def clear_live():
    d = _load(); d["live"] = {}; d["live_name"] = None; _save(d)
    return d   # 需重啟服務才會回到程式預設

def boot():
    """啟動時重新套用上次的實盤覆蓋。"""
    d = _load()
    if d.get("live"):
        try: C.apply_overrides(params.to_overrides(d["live"]))
        except Exception as e: print("preset boot fail:", e)
    return d
