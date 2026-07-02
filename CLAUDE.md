# calendar-man

A Telegram bot that turns plain-English messages (e.g. "Gym every Monday at 7am")
into Google Calendar events. Natural-language parsing is done by an LLM; the parsed
result is written to the user's primary Google Calendar.

Deployed as **Vercel Python serverless functions** in region `sin1` (Singapore),
which matches the hardcoded `Asia/Singapore` timezone used throughout.

## Architecture

```
Telegram  ──POST──▶  /api/webhook  ──▶  handle_update()
                                          ├─ callback_query (button tap) → handle_callback() → delete_event()
                                          ├─ "/start"          → welcome message
                                          ├─ "/read"           → range picker (Today/Week/Month) → get_events(days=N)
                                          ├─ "/delete <query>" → parse_event(intent=delete) → find_events() → inline buttons
                                          └─ free text         → parse_event() → OpenAI (strict JSON schema)
                                                                → push_to_google_calendar()

Vercel Cron (daily) ──GET──▶  /api/cron/daily  ──▶  get_events(days=1) → daily agenda DM
```

Everything deployed lives under `api/`. There are three serverless functions, each a
standalone Flask app:

- **[api/webhook.py](api/webhook.py)** — the main endpoint (`/api/webhook`). Receives
  Telegram updates. `POST` handles messages **and inline-button taps**; `GET` is a health
  check ("Bot is running"). Contains the core logic: `parse_event()`,
  `push_to_google_calendar()`, `delete_event()`, `find_events()`, `get_events()`,
  `build_read_keyboard()`, `build_delete_keyboard()`, `handle_delete_request()`,
  `handle_callback()`, `get_google_calendar_service()`.
- **[api/set_webhook.py](api/set_webhook.py)** — one-time setup endpoint (`/api/set_webhook`,
  `GET`). Registers the webhook URL with Telegram, derived from the incoming request host.
  Hit this once after each deploy to a new URL.
- **[api/cron/daily.py](api/cron/daily.py)** — cron target (`/api/cron/daily`, `GET`).
  Sends a daily agenda to a fixed `TELEGRAM_CHAT_ID`. Guarded by a `CRON_SECRET` bearer
  token. Imports `get_events` from `api/webhook.py` via a `sys.path` insert (see gotchas).

Routing, builds, region, and the cron schedule are all declared in [vercel.json](vercel.json).
The cron runs `0 0 * * *` **UTC** = 8am Singapore (a morning agenda, not local midnight).

## LLM parsing

`parse_event(message, intent)` calls OpenAI (`gpt-4.1-nano`) with a `strict` JSON schema
derived from the `CalendarResponse` Pydantic model (fields: `date`, `time`,
`duration_minutes`, `all_day`, `description`, `repeat`, `location`). Defaults enforced by the
prompt: duration → 60 min, repeat → "never", location → "None".

**Intent is driven by the command, not the LLM.** `handle_update` decides intent from the
message: `/delete <query>` → delete, `/read` → read, anything else → create. It then passes
that `intent` into `parse_event`, which swaps the intent-specific half of the prompt. For
`delete`, only `date` and `description` (the identifying keywords) are extracted and the date
is optional (`/delete gym` with no date searches a 30-day window). This avoids relying on the
model to classify create-vs-delete.

**Date resolution is the tricky part.** LLMs are unreliable at date arithmetic, so the prompt
does **not** ask the model to compute dates. Instead `parse_event()` precomputes a **14-day
reference calendar** (weekday → ISO date) and instructs the model to look dates up from that
mapping directly. If you touch the parsing prompt, preserve this pattern.

`repeat` maps to a Google `RRULE:FREQ=...` in `push_to_google_calendar()`
(daily/weekly/monthly/yearly; "never" → no recurrence).

## Reading events (range picker)

`/read` doesn't fetch immediately — it replies with an inline keyboard (`build_read_keyboard()`)
offering **Today / This week / This month**. Tapping a button fires an `r:<key>` callback;
`handle_callback()` looks up the range in `READ_RANGES` (`key → (label, days)`) and calls
`get_events(days, label)`, editing the message in place with the results. `get_events` groups
events by day for multi-day ranges. The daily cron still calls `get_events(days=1)` directly.

## Deleting events (stateless, button-driven)

Deletion is triggered by the `/delete <query>` command, where the query is still natural
language ("gym tomorrow", "dentist appointment"). `handle_update` strips the command, calls
`parse_event(query, intent="delete")` to extract the date + keywords, then:

1. `find_events(date, keywords)` searches the calendar (same `events().list()` pattern as
   `get_events`, using Google's free-text `q` param), scoped to the parsed day or a 30-day
   window if no date was given.
2. `build_delete_keyboard()` turns the candidates into a Telegram **inline keyboard**:
   0 matches → "not found"; 1 match → a confirm button (recurring events also get a
   "delete the whole series" button); many → one button per candidate.
3. The user taps a button; `handle_callback()` runs `delete_event(event_id)` and edits the
   message in place to report the outcome.

**Why buttons, not a "reply YES" flow:** the bot is stateless serverless with no datastore,
so it can't remember a pending delete between requests. The event id to delete is encoded
directly in the button's `callback_data`, so the "state" lives in the button. Deleting a
recurring **instance id** removes one occurrence; deleting the **`recurringEventId`** removes
the series — both are the same `events().delete(eventId=...)` call, so one `d:<id>` callback
action covers all cases and only the button label differs.

**callback_data 64-byte limit:** Telegram caps `callback_data` at 64 bytes. Normal Google ids
fit; `_delete_button()` returns `None` if an id would overflow, and the keyboard builder omits
that button rather than sending an invalid one.

**Callback prefixes** handled by `handle_callback()`: `r:<key>` (read a range), `d:<id>`
(delete an event/series), `x` (cancel).

## Environment variables

| Var | Used by | Purpose |
|-----|---------|---------|
| `TELEGRAM_BOT_TOKEN` | all `api/` functions | Telegram bot auth |
| `OPENAI_API_KEY`     | webhook (`OpenAI()` reads it implicitly) | LLM calls |
| `GOOGLE_TOKEN_JSON`  | webhook, cron | Google OAuth creds as a JSON string (contents of `token.json`) |
| `CRON_SECRET`        | cron | Bearer token protecting the cron endpoint |
| `TELEGRAM_CHAT_ID`   | cron | Chat that receives the daily agenda |

## Bootstrapping Google OAuth credentials

`GOOGLE_TOKEN_JSON` is the string contents of a `token.json` produced by an OAuth
Installed-App flow (scope `https://www.googleapis.com/auth/calendar`). To (re)generate it
locally you need a `credentials.json` from Google Cloud, then run the standard
`InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)` flow and paste the
resulting `token.json` into the `GOOGLE_TOKEN_JSON` env var. In production the token is only
refreshed in memory per request (serverless can't persist it back), which works because the
refresh token is long-lived.

`credentials.json` and `token.json` are gitignored and must never be committed.

## Gotchas

- **Two Flask apps per module.** Each `api/*.py` defines its own `app = Flask(__name__)`;
  Vercel routes to each file independently. Don't try to unify them into one app.
- **`sys.path` hack in cron.** [api/cron/daily.py](api/cron/daily.py) inserts the repo root
  onto `sys.path` to `import get_events` from `api/webhook.py`. Shared logic currently lives
  in `webhook.py`; if that grows, consider extracting a shared module both import.
- **Per-request clients.** `telegram.Bot`, `OpenAI`, and the Google Calendar service are all
  constructed fresh on every request. Fine for serverless; just don't expect connection reuse.
- **`get_events(days=N)`** supports a multi-day/weekly view (with per-day headers) but nothing
  currently calls it with `days > 1`.

## Local development

There is no long-polling local runner in the repo. Test against the deployed webhook, or run
the Flask apps locally and point Telegram at a tunnel (e.g. via `set_webhook`). Dependencies
are pinned in [requirements.txt](requirements.txt).
