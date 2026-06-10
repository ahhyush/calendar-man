from flask import Flask, request
import os
import asyncio
import telegram

app = Flask(__name__)

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from api.webhook import get_events


@app.route("/api/cron/daily", methods=["GET"])
def handler():
    auth = request.headers.get("Authorization")
    if auth != f"Bearer {os.environ.get('CRON_SECRET')}":
        return ("Unauthorized", 401)

    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    bot = telegram.Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    message = get_events(days=1)

    async def send():
        await bot.send_message(chat_id=chat_id, text=message)

    asyncio.run(send())
    return "ok"
