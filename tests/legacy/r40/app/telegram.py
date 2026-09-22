import json, os, urllib.request
TOKEN = os.environ.get("TG_TOKEN", ""); CHAT = os.environ.get("TG_CHAT", "")
_unset = dict(warned=False)

def send(text):
    if not TOKEN or not CHAT:
        # 設定錯：所有告警都不會送出。不能靜靜 return——記進錯誤區一次（清單第 8 條 r39），自檢也會列出來
        if not _unset["warned"]:
            _unset["warned"] = True
            print("Telegram 未設定，推播不會送出")
            try:
                from . import store; store.push("errors", "Telegram 未設定（TG_TOKEN／TG_CHAT 是空的），所有推播都不會送出")
            except Exception as e: print("寫錯誤區也失敗:", e)
        return
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=json.dumps(dict(chat_id=CHAT, text=text)).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("tg fail:", e)
        try:                                        # 推播本身失敗沒辦法再推播，至少進錯誤區（清單用法第 5 點 r29）
            from . import store; store.push("errors", f"Telegram 送出失敗 {type(e).__name__}: {e}")
        except Exception as e2: print("寫錯誤區也失敗:", e2)   # 最後一層：推播與錯誤區都不行，至少留在標準錯誤
