from flask import Flask, request
import os
import json
from datetime import date, datetime, timedelta
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
import telegram

app = Flask(__name__)


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


def get_events(days: int = 1) -> str:
    service = get_google_calendar_service()
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start_of_day + timedelta(days=days)

    result = service.events().list(
        calendarId="primary",
        timeMin=start_of_day.isoformat() + "+08:00",
        timeMax=end.isoformat() + "+08:00",
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    if not events:
        if days == 1:
            return "You have no events scheduled for today."
        return "You have no events scheduled this week."

    if days == 1:
        lines = [f"Your events for {now.strftime('%A, %d %B %Y')}:\n"]
    else:
        end_date = (now + timedelta(days=days - 1)).strftime('%A, %d %B')
        lines = [f"Your events for the week ({now.strftime('%d %B')} - {end_date}):\n"]

    current_date = None
    for event in events:
        summary = event.get("summary", "No title")
        start = event["start"]
        if "dateTime" in start:
            event_dt = datetime.fromisoformat(start["dateTime"])
            if days > 1:
                event_date_str = event_dt.strftime("%A, %d %B")
                if event_date_str != current_date:
                    current_date = event_date_str
                    lines.append(f"\n{current_date}:")
            time_str = event_dt.strftime("%I:%M %p")
            end_time = datetime.fromisoformat(event["end"]["dateTime"]).strftime("%I:%M %p")
            lines.append(f"  - {time_str} - {end_time}: {summary}")
        else:
            event_date = start.get("date", "")
            if days > 1:
                event_date_str = datetime.strptime(event_date, "%Y-%m-%d").strftime("%A, %d %B")
                if event_date_str != current_date:
                    current_date = event_date_str
                    lines.append(f"\n{current_date}:")
            lines.append(f"  - All day: {summary}")

    return "\n".join(lines)


def parse_event(message: str) -> dict:
    today = date.today()
    today_str = today.strftime("%A, %Y-%m-%d")
    days_reference = "\n".join(
        f"        - {(today + timedelta(days=i)).strftime('%A')} = {(today + timedelta(days=i)).isoformat()}"
        for i in range(14)
    )
    prompt = """
        You extract calendar events from natural language.
        Use the reference calendar below to resolve relative dates. Do NOT compute dates yourself — use the mapping directly.

        Reference calendar (next 14 days):
{days}

        Today is {today}.
        "Tomorrow" = the day after today.
        "This Thursday" or "Thursday" = the next upcoming Thursday from the reference above.

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
    """.format(days=days_reference, today=today_str)

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
                "Commands:\n"
                "/read - View today's events\n\n"
                "Examples:\n"
                "- Meeting with John at 3pm Friday\n"
                "- Gym every Monday at 7am\n"
                "- Team standup tomorrow at 9:30am for 30 minutes\n"
                "- Pay rent on the 1st of every month\n\n"
                "Just send me your event to get started!"
            )
            await bot.send_message(chat_id=chat_id, text=welcome)
        elif text == "/read":
            reply = get_events(days=1)
            await bot.send_message(chat_id=chat_id, text=reply)
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


@app.route("/api/webhook", methods=["GET", "POST"])
def handler():
    if request.method == "GET":
        return "Bot is running"

    update_data = request.get_json()

    import asyncio
    asyncio.run(handle_update(update_data))

    return "ok"

