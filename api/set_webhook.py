from flask import Flask, request
import os
import asyncio
import telegram

app = Flask(__name__)


@app.route("/api/set_webhook", methods=["GET"])
def handler():
    host = request.headers.get("x-forwarded-host", request.host)
    webhook_url = f"https://{host}/api/webhook"

    async def set_it():
        bot = telegram.Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
        await bot.set_webhook(url=webhook_url)

    asyncio.run(set_it())

    return f"Webhook set to {webhook_url}"
