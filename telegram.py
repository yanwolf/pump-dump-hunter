import json, os, urllib.request
TOKEN = os.environ.get("TG_TOKEN", ""); CHAT = os.environ.get("TG_CHAT", "")
def send(text):
    if not TOKEN or not CHAT: return
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=json.dumps(dict(chat_id=CHAT, text=text)).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e: print("tg fail:", e)
