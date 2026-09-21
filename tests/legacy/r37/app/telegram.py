import json, os, urllib.request
TOKEN = os.environ.get("TG_TOKEN", ""); CHAT = os.environ.get("TG_CHAT", "")
def send(text):
    if not TOKEN or not CHAT: return
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=json.dumps(dict(chat_id=CHAT, text=text)).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("tg fail:", e)
        try:                                        # 推播本身失敗沒辦法再推播，至少進錯誤區（清單用法第 5 點 r29）
            from . import store; store.push("errors", f"Telegram 送出失敗 {type(e).__name__}: {e}")
        except Exception: pass
