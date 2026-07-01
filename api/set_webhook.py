from flask import Flask, request
import os
import asyncio
import telegram
from telegram import BotCommand

app = Flask(__name__)

# Commands shown in Telegram's "/" menu. Keep in sync with the handlers in
# api/webhook.py (handle_update).
COMMANDS = [
    BotCommand("start", "Show welcome message and instructions"),
    BotCommand("read", "View today's events"),
    BotCommand("delete", "Delete an event, e.g. /delete gym tomorrow"),
]


@app.route("/api/set_webhook", methods=["GET"])
def handler():
    host = request.headers.get("x-forwarded-host", request.host)
    webhook_url = f"https://{host}/api/webhook"

    async def set_it():
        bot = telegram.Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
        await bot.set_webhook(url=webhook_url)
        await bot.set_my_commands(COMMANDS)

    asyncio.run(set_it())

    commands = ", ".join("/" + c.command for c in COMMANDS)
    return f"Webhook set to {webhook_url}\nCommands registered: {commands}"
