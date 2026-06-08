import os
import json
from dotenv import load_dotenv
from telegram import Update
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from datetime import date, datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


load_dotenv()

class CalendarResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"additionalProperties": False})

    date: str
    time: str
    duration_minutes: str
    all_day: bool
    description: str
    repeat: str
    location: str

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_google_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def push_to_google_calendar(event: dict):
    service = get_google_calendar_service()

    recurrence = []
    freq_map = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
    if event["repeat"] in freq_map:
        recurrence = [f"RRULE:FREQ={freq_map[event['repeat']]}"]

    if event.get("all_day"):
        body = {
            "summary": event["description"],
            "start": {"date": event["date"]},
            "end": {"date": event["date"]},
        }
    else:
        start = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=int(event['duration_minutes']))
        body = {
            "summary": event["description"],
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Singapore"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Singapore"},
        }

    if recurrence:
        body["recurrence"] = recurrence
    if event.get("location") and event["location"] != "None":
        body["location"] = event["location"]

    service.events().insert(calendarId="primary", body=body).execute()

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
        - If the event has no specific time and must span the whole day (e.g. "birthday", "holiday", "anniversary", "day off"), set all_day to true, time to "00:00", and duration_minutes to "0".
        - If a specific time is mentioned, set all_day to false.
        - If duration is not specified and all_day is false, use 60 minutes.
        - If repeat is not specified, use "never".
        - Location if unspecified is None
        - Repeat must be one of: daily, weekly, monthly, yearly, never.
        - Description should be concise and human-readable.
        - Do not invent information that is not implied by the input.
        - If a date cannot be determined, return null.
        - If a time cannot be determined and all_day is false, return null.
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

    if response_json.get("date") and (response_json.get("all_day") or response_json.get("time")):
        push_to_google_calendar(response_json)
        if response_json.get("all_day"):
            await update.message.reply_text(
                f"✅ Event added to your calendar!\n\n"
                f"📅 {response_json['description']}\n"
                f"🗓 {response_json['date']} (all day)\n"
                f"🔁 {response_json['repeat']}"
            )
        else:
            await update.message.reply_text(
                f"✅ Event added to your calendar!\n\n"
                f"📅 {response_json['description']}\n"
                f"🗓 {response_json['date']} at {response_json['time']}\n"
                f"⏱ {response_json['duration_minutes']} min\n"
                f"🔁 {response_json['repeat']}"
            )
    else:
        await update.message.reply_text("I couldn't determine a date/time from your message. Please try again.")




if __name__ == "__main__":
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    app.run_polling()
