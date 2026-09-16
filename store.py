"""狀態全部放 Volume（DATA_DIR），repo 內不留任何部署狀態。"""
import json, os, threading
DATA_DIR = os.environ.get("DATA_DIR", "./data"); os.makedirs(DATA_DIR, exist_ok=True)
_lock = threading.Lock()
_state = dict(watch=[], signals=[], trades=[], last_scan=None, started=None, errors=[])
_path = os.path.join(DATA_DIR, "state.json")
if os.path.exists(_path):
    try: _state.update(json.load(open(_path)))
    except Exception: pass
def get(): return dict(_state)
def update(**kw):
    with _lock:
        _state.update(kw)
        for key in ("signals", "trades", "errors"): _state[key] = _state[key][-300:]
        json.dump(_state, open(_path, "w"), ensure_ascii=False)
def push(key, item):
    with _lock: _state[key].append(item)
    update()
