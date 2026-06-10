import os
import json
from datetime import date, datetime, timedelta
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
import telegram

class CalendarResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"additionalProperties": False})

    date: str
    time: str
    duration_minutes: str
    all_day: bool
    description: str
    repeat: str
    location: str


def get_google_calendar_service():
    token_json = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(token_json)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())

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
        end = start + timedelta(minutes=int(event["duration_minutes"]))
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


def parse_event(message: str) -> dict:
    today = date.today().isoformat()
    prompt = """
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
    """.format(today)

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
                "strict": True,
            },
        },
    )

    return json.loads(response.choices[0].message.content)


async def handle_update(update_data: dict):
    bot = telegram.Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    update = telegram.Update.de_json(update_data, bot)

    if update.message and update.message.text:
        chat_id = update.message.chat_id
        text = update.message.text

        if text == "/start":
            first_name = update.effective_user.first_name
            welcome = (
                f"Welcome {first_name}!\n\n"
                "Send me an event or reminder in plain English, and I'll turn it into a structured calendar entry.\n\n"
                "Examples:\n"
                "- Meeting with John at 3pm Friday\n"
                "- Gym every Monday at 7am\n"
                "- Team standup tomorrow at 9:30am for 30 minutes\n"
                "- Pay rent on the 1st of every month\n\n"
                "Just send me your event to get started!"
            )
            await bot.send_message(chat_id=chat_id, text=welcome)
        else:
            response_json = parse_event(text)

            if response_json.get("date") and (response_json.get("all_day") or response_json.get("time")):
                push_to_google_calendar(response_json)
                if response_json.get("all_day"):
                    reply = (
                        f"Event added to your calendar!\n\n"
                        f"{response_json['description']}\n"
                        f"{response_json['date']} (all day)\n"
                        f"Repeat: {response_json['repeat']}"
                    )
                else:
                    reply = (
                        f"Event added to your calendar!\n\n"
                        f"{response_json['description']}\n"
                        f"{response_json['date']} at {response_json['time']}\n"
                        f"Duration: {response_json['duration_minutes']} min\n"
                        f"Repeat: {response_json['repeat']}"
                    )
                await bot.send_message(chat_id=chat_id, text=reply)
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="I couldn't determine a date/time from your message. Please try again.",
                )


def handler(request):
    if request.method == "GET":
        return "Bot is running"

    if request.method == "POST":
        update_data = request.json

        import asyncio
        asyncio.run(handle_update(update_data))

        return "ok"

    return ("Method not allowed", 405)
