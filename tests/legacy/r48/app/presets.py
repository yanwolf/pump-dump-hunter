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

_fail = dict(n=0, backed_up=False)

class PresetsUnreadable(RuntimeError):
    pass

def _load():
    """讀參數集檔（清單第 8 條 r43：設定檔比照狀態檔）。
    - 檔案不存在才是「沒設定」；但讀取失敗期間原檔不見了，仍是失敗（要全新開始就放一份 {}）
    - 讀不了、解析不了、格式不對 → 壞檔複製一份（原檔留著）、照節奏推播，拋 PresetsUnreadable；寫入的函式因此不會把壞檔蓋掉"""
    if not os.path.exists(PATH):
        if _fail["n"]: return _unreadable(FileNotFoundError("讀取失敗期間原檔不見了；確定要全新開始請放一份內容為 {} 的檔"))
        return {"presets": {}, "live": {}, "live_name": None}
    try:
        with open(PATH, encoding="utf-8") as f: d = json.load(f)
        if not isinstance(d, dict): raise ValueError("最外層不是物件")
    except Exception as e: return _unreadable(e)
    d.setdefault("presets", {}); d.setdefault("live", {}); d.setdefault("live_name", None)
    if _fail["n"]:
        n = _fail["n"]; _fail.update(n=0, backed_up=False)
        _notify(f"✅ 參數集檔已恢復讀取（先前失敗 {n} 次）")
    return d

def _unreadable(e):
    n = _fail["n"] = _fail["n"] + 1
    note = ""
    if not _fail["backed_up"] and os.path.exists(PATH):
        try:
            import shutil, time as _t
            bad = f"{PATH}.bad-{int(_t.time())}"; shutil.copy2(PATH, bad); _fail["backed_up"] = True
            note = f"；壞檔已另存 {os.path.basename(bad)}"
        except Exception as e2: note = f"；另存壞檔失敗 {e2}"
    msg = (f"參數集檔讀取失敗（第 {n} 次）{type(e).__name__}: {e}{note}。實盤參數覆蓋沒有套用（用的是程式預設值），"
           "參數集暫不寫入（原檔保留）")
    if n in (1, 5, 30) or (n > 30 and (n - 30) % 120 == 0): _notify(f"🐞 {msg}", error=msg)
    raise PresetsUnreadable(msg)

def _notify(text, error=None):
    try:
        from . import store, telegram
        if error: store.push("errors", error)
        telegram.send(text)
    except Exception as e: print("參數集通知失敗:", e, text)

def _save(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)

def all():
    try: return _load()
    except PresetsUnreadable: return {"presets": {}, "live": {}, "live_name": None, "unreadable": True}
def all_presets(): return all()["presets"]
def live(): return all()["live"] or {}

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
    try: form = _load()["live"] or {}
    except PresetsUnreadable: form = {}                    # 讀不到：用程式預設值（已推播），不當成「沒有覆蓋」靜靜帶過
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
