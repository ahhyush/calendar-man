import os
import json
from dotenv import load_dotenv
from telegram import Update
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from datetime import date


load_dotenv()

class CalendarResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"additionalProperties": False})
    
    date: str
    time: str
    duration_minutes: str
    description: str
    repeat: str

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = update.effective_user.first_name
    await update.message.reply_text(
        '''
        👋 Welcome {}!

Send me an event or reminder in plain English, and I'll turn it into a structured calendar entry.

Examples:
• Meeting with John at 3pm Friday
• Gym every Monday at 7am
• Team standup tomorrow at 9:30am for 30 minutes
• Pay rent on the 1st of every month
• Dentist appointment next Tuesday at 2pm

You can include:
📅 Dates (tomorrow, Friday, next week, 15 June)
🕒 Times (3pm, 14:30)
⏱️ Durations (for 30 mins, 2 hours)
🔁 Repeats (daily, weekly, monthly, yearly)

If you don't specify:
• Duration → 60 minutes
• Repeat → never

Just send me your event to get started!

        '''.format(first_name)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    today = date.today().isoformat()
    prompt = '''
        You extract calendar events from natural language.
        Use the provided current date and timezone to resolve relative dates such as "tomorrow", "next Friday", "next month", and "in 2 weeks".

        Rules:
        - If duration is not specified, use 60 minutes.
        - If repeat is not specified, use "never".
        - Repeat must be one of: daily, weekly, monthly, yearly, never.
        - Description should be concise and human-readable.
        - Do not invent information that is not implied by the input.
        - If a date cannot be determined, return null.
        - If a time cannot be determined, return null.            
        - Date today is {} of format YYYY-MM-DD
    '''.format(today)

    client = OpenAI()

    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "CalendarResponse",
                "schema": CalendarResponse.model_json_schema(),
                "strict": True
            }
        }
    )

    response_json = json.loads(response.choices[0].message.content)
    await update.message.reply_text(json.dumps(response_json, indent=2))




if __name__ == "__main__":
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    app.run_polling()
