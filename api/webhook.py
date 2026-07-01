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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

app = Flask(__name__)

# Telegram limits inline-button callback_data to 64 bytes. Google event ids
# (and recurring-instance ids) fit comfortably, but we guard against overflow.
CALLBACK_DATA_LIMIT = 64


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


def delete_event(event_id: str):
    """Delete an event by id. Pass an instance id to delete a single occurrence,
    or a recurringEventId to delete a whole series."""
    service = get_google_calendar_service()
    service.events().delete(calendarId="primary", eventId=event_id).execute()


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


def _format_when(event: dict) -> str:
    """Human-readable date/time for an event, for button labels and messages."""
    start = event.get("start", {})
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"])
        return dt.strftime("%a %d %b, %I:%M %p")
    date_str = start.get("date", "")
    if date_str:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %d %b") + " (all day)"
    return ""


def find_events(date_str: str, keywords: str, limit: int = 5) -> list:
    """Find candidate events matching keywords, optionally scoped to a date.

    Models the events().list() call in get_events(). If a date is given, search
    that single day; otherwise search a 30-day window from today. Google's `q`
    pre-filters by text; results are returned newest-listing-order, capped at
    `limit`."""
    service = get_google_calendar_service()

    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            day = None
    else:
        day = None

    if day:
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    else:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=30)

    list_kwargs = dict(
        calendarId="primary",
        timeMin=start.isoformat() + "+08:00",
        timeMax=end.isoformat() + "+08:00",
        singleEvents=True,
        orderBy="startTime",
        maxResults=25,
    )
    if keywords:
        list_kwargs["q"] = keywords

    result = service.events().list(**list_kwargs).execute()
    return result.get("items", [])[:limit]


def parse_event(message: str, intent: str = "create") -> dict:
    today = date.today()
    today_str = today.strftime("%A, %Y-%m-%d")
    days_reference = "\n".join(
        f"        - {(today + timedelta(days=i)).strftime('%A')} = {(today + timedelta(days=i)).isoformat()}"
        for i in range(14)
    )
    common = """
        You extract calendar events from natural language.
        Use the reference calendar below to resolve relative dates. Do NOT compute dates yourself — use the mapping directly.

        Reference calendar (next 14 days):
{days}

        Today is {today}.
        "Tomorrow" = the day after today.
        "This Thursday" or "Thursday" = the next upcoming Thursday from the reference above.
        "Next Thursday" = the Thursday after the next upcoming Thursday from the reference above.
        "Next week" = the 7-day period starting from the next upcoming Monday from the reference above.
        "Next month" = the same date in the next calendar month, or the last date of the next calendar month if the same date doesn't exist.
        "In 2 weeks" = the same date 14 days from the reference today, or the last date of that month if the same date doesn't exist.
    """.format(days=days_reference, today=today_str)

    if intent == "delete":
        specifics = """
        The user wants to DELETE an existing event. Extract only what identifies it:
        - Put the event's identifying keywords in "description" (e.g. "gym", "dentist appointment").
        - Resolve any date mentioned into "date". If no date is mentioned, leave "date" empty — do NOT invent one.
        - A time is not required. Leave time, duration, repeat, location and all_day at their defaults.
        """
    else:
        specifics = """
        The user wants to CREATE an event. Rules:
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
        """

    prompt = common + specifics

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


def _delete_button(label: str, event_id: str):
    """Inline button that deletes `event_id` when tapped, or None if the id is
    too long to fit Telegram's callback_data limit.

    Deleting works the same whether event_id is a normal event id, a recurring
    instance id (deletes that one occurrence), or a recurringEventId (deletes
    the whole series) — so a single "d:" action covers all three; only the
    label differs."""
    data = f"d:{event_id}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        return None
    return InlineKeyboardButton(label, callback_data=data)


def build_delete_keyboard(events: list):
    """Given candidate events, return (message_text, InlineKeyboardMarkup|None).

    - 0 events  → a "not found" message, no keyboard.
    - 1 event   → confirm button (+ a "delete whole series" button if recurring).
    - many      → one button per candidate; tapping deletes that occurrence.
    """
    cancel = InlineKeyboardButton("✖ Cancel", callback_data="x")

    if not events:
        return ("I couldn't find an event matching that.", None)

    if len(events) == 1:
        event = events[0]
        summary = event.get("summary", "No title")
        when = _format_when(event)
        series_id = event.get("recurringEventId")
        rows = []

        if series_id:
            # One occurrence of a repeating event — offer both scopes.
            occurrence = _delete_button(f"🗑 Delete just {when}", event["id"])
            series_btn = _delete_button("🔁 Delete the whole series", series_id)
            if occurrence:
                rows.append([occurrence])
            if series_btn:
                rows.append([series_btn])
        else:
            occurrence = _delete_button(f"🗑 Delete “{summary}”", event["id"])
            if occurrence:
                rows.append([occurrence])

        if not rows:
            # Every id overflowed callback_data — can't offer a delete button.
            return ("I found the event but couldn't build a delete action for it.", None)

        rows.append([cancel])
        text = f"Delete this event?\n\n📅 {summary}\n🗓 {when}"
        return (text, InlineKeyboardMarkup(rows))

    # Multiple candidates: one button each, deleting that specific occurrence.
    rows = []
    lines = ["Which event should I delete?\n"]
    for event in events:
        summary = event.get("summary", "No title")
        when = _format_when(event)
        btn = _delete_button(f"🗑 {summary} — {when}", event["id"])
        if btn:
            rows.append([btn])
    rows.append([cancel])
    return ("\n".join(lines), InlineKeyboardMarkup(rows))


async def handle_delete_request(bot, chat_id, parsed: dict):
    keywords = parsed.get("description", "") or ""
    date_str = parsed.get("date") or ""
    events = find_events(date_str, keywords)
    text, keyboard = build_delete_keyboard(events)
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


async def handle_callback(query):
    """Handle an inline-button tap: delete the encoded event, or cancel."""
    await query.answer()
    data = query.data

    if data == "x":
        await query.edit_message_text("Cancelled. Nothing was deleted.")
        return

    if data.startswith("d:"):
        event_id = data[2:]
        try:
            delete_event(event_id)
            await query.edit_message_text("🗑 Deleted.")
        except Exception:
            # Event may already be gone (410), or the id may be stale.
            await query.edit_message_text(
                "I couldn't delete that — it may already have been removed."
            )
        return

    await query.edit_message_text("Sorry, that action is no longer valid.")


async def handle_update(update_data: dict):
    bot = telegram.Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    update = telegram.Update.de_json(update_data, bot)

    if update.callback_query:
        await handle_callback(update.callback_query)
        return

    if update.message and update.message.text:
        chat_id = update.message.chat_id
        text = update.message.text

        # Intent is driven by the command, not inferred by the LLM:
        #   /delete <query> → delete, /read → read, anything else → create.
        if text == "/start":
            first_name = update.effective_user.first_name
            welcome = (
                f"Welcome {first_name}!\n\n"
                "Send me an event or reminder in plain English, and I'll turn it into a structured calendar entry.\n\n"
                "Commands:\n"
                "/read - View today's events\n"
                "/delete <event> - Delete an event, e.g. /delete gym tomorrow\n\n"
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
        elif text == "/delete" or text.startswith("/delete "):
            query = text[len("/delete"):].strip()
            if not query:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Tell me what to delete, e.g. /delete gym tomorrow",
                )
                return
            parsed = parse_event(query, intent="delete")
            await handle_delete_request(bot, chat_id, parsed)
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

