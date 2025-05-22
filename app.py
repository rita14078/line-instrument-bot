from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import pandas as pd
from datetime import datetime

app = Flask(__name__)

# ======== Replace these with your LINE credentials ========
LINE_CHANNEL_ACCESS_TOKEN = "YOUR_CHANNEL_ACCESS_TOKEN"
LINE_CHANNEL_SECRET = "YOUR_CHANNEL_SECRET"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ========== Load instrument data from CSV ==========
DATA_PATH = "instrument_status.csv"
user_states = {}  # store temporary user conversation states

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    handler.handle(body, signature)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()

    # === Step 1: User enters "借用" or "歸還" ===
    if msg in ["借用", "歸還"]:
        df = pd.read_csv(DATA_PATH)
        if msg == "借用":
            items = df[df["狀態"] == "free"]["儀器名稱"].tolist()
            action = "borrow"
        else:
            items = df[df["狀態"] == "in_use"]["儀器名稱"].tolist()
            action = "return"

        if not items:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前無可用儀器"))
            return

        quick_items = [QuickReplyButton(action=MessageAction(label=name, text=f"選擇 {name}")) for name in items]
        user_states[user_id] = {"step": "choose_item", "action": action}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請選擇儀器：", quick_reply=QuickReply(items=quick_items))
        )
        return

    # === Step 2: User selects an item ===
    if user_id in user_states and user_states[user_id]["step"] == "choose_item" and msg.startswith("選擇 "):
        item = msg.replace("選擇 ", "")
        user_states[user_id].update({"step": "input_name", "item": item})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"請輸入你的姓名以{ '借用' if user_states[user_id]['action'] == 'borrow' else '歸還' } {item}"))
        return

    # === Step 3: User inputs name ===
    if user_id in user_states and user_states[user_id]["step"] == "input_name":
        name = msg
        action = user_states[user_id]["action"]
        item = user_states[user_id]["item"]
        del user_states[user_id]

        df = pd.read_csv(DATA_PATH)
        now = datetime.now().strftime("%Y/%m/%d %H:%M")
        idx = df[df["儀器名稱"] == item].index[0]

        if action == "borrow":
            df.at[idx, "狀態"] = "in_use"
            df.at[idx, "使用者"] = name
            df.at[idx, "借用時間"] = now
            df.at[idx, "使用時長"] = "0 分鐘"
            msg_text = f"✅ 你已成功借用 {item}，時間：{now.split(' ')[1]}"
        else:
            df.at[idx, "狀態"] = "free"
            df.at[idx, "使用者"] = "-"
            df.at[idx, "借用時間"] = "-"
            df.at[idx, "使用時長"] = "-"
            msg_text = f"🔁 你已成功歸還 {item}，時間：{now.split(' ')[1]}"

        df.to_csv(DATA_PATH, index=False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_text))
        return

    # === Default ===
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：借用 或 歸還"))

if __name__ == "__main__":
    app.run()