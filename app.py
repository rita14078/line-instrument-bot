from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import pandas as pd
from datetime import datetime
import os
import requests
import json
import psycopg2

app = Flask(__name__)

# ✅ 使用環境變數更安全
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DATA_PATH = "instrument_status.csv"
user_states = {}

# Neon PostgreSQL 連線
def get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode='require')

def get_user_name(user_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS user_names (user_id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("SELECT name FROM user_names WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def set_user_name(user_id, name):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS user_names (user_id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("INSERT INTO user_names (user_id, name) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name", (user_id, name))
    conn.commit()
    cur.close()
    conn.close()

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"Webhook error: {e}")
        abort(500)

    return 'OK', 200

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    # 第一次加好友時要求輸入姓名
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="歡迎加入！請輸入你的姓名（例如：我是王小明）"))

def fetch_instruments():
    df = pd.read_csv(DATA_PATH)
    return df.to_dict(orient="records")

def update_instrument(item, name, action):
    df = pd.read_csv(DATA_PATH)
    idx = df[df["儀器名稱"] == item].index[0]
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    if action == "borrow":
        df.at[idx, "狀態"] = "in_use"
        df.at[idx, "使用者"] = name
        df.at[idx, "借用時間"] = now
        df.at[idx, "使用時長"] = "0 分鐘"
    elif action == "return":
        df.at[idx, "狀態"] = "free"
        df.at[idx, "使用者"] = "-"
        df.at[idx, "借用時間"] = "-"
        df.at[idx, "使用時長"] = "-"
    df.to_csv(DATA_PATH, index=False)
    return {"success": True}

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()

    # 處理用戶輸入姓名與更改姓名
    if msg.startswith("我是") and len(msg) > 2:
        name = msg[2:].strip()
        if name:
            set_user_name(user_id, name)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"姓名已設定為：{name}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請正確輸入姓名，例如：我是王小明"))
        return

    # 若用戶尚未設定姓名，要求輸入
    name = get_user_name(user_id)
    if not name:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先輸入你的姓名（例如：我是王小明）"))
        return

    # 新增儀器列表指令
    if msg == "儀器列表":
        instruments = fetch_instruments()
        lines = []
        for row in instruments:
            lines.append(f"{row['儀器名稱']}：{'可借用' if row['狀態']=='free' else '使用中（'+row['使用者']+'）'}")
        reply = "\n".join(lines) if lines else "目前沒有儀器資料"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg in ["借用", "歸還"]:
        instruments = fetch_instruments()
        if msg == "借用":
            items = [row["儀器名稱"] for row in instruments if row["狀態"] == "free"]
            action = "borrow"
        else:
            items = [row["儀器名稱"] for row in instruments if row["狀態"] == "in_use"]
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

    if user_id in user_states and user_states[user_id]["step"] == "choose_item" and msg.startswith("選擇 "):
        item = msg.replace("選擇 ", "")
        action = user_states[user_id]["action"]
        if action == "borrow":
            name = get_user_name(user_id)
            update_instrument(item, name, "borrow")
            del user_states[user_id]
            now = datetime.now().strftime("%H:%M")
            msg_text = f"✅ 你已成功借用 {item}，時間：{now}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_text))
        else:
            update_instrument(item, "-", "return")
            del user_states[user_id]
            now = datetime.now().strftime("%H:%M")
            msg_text = f"🔁 你已成功歸還 {item}，時間：{now}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg_text))
        return

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：借用 或 歸還"))

# ✅ 必須綁定 0.0.0.0 並讀取 Render 的環境變數 PORT
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)