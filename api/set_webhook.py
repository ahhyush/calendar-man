import os
from flask import Request, Response
import telegram


TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def handler(request: Request) -> Response:
    if request.method != "GET":
        return Response("Use GET to set webhook", status=405)

    host = request.headers.get("x-forwarded-host", request.host)
    webhook_url = f"https://{host}/api/webhook"

    import asyncio

    async def set_it():
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.set_webhook(url=webhook_url)

    asyncio.run(set_it())

    return Response(f"Webhook set to {webhook_url}", status=200)
