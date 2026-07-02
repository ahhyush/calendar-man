# calendar-man

A Telegram bot that adds things to your Google Calendar. Type an event in plain
English and it works out the date, time, and repeat for you.

## Usage

Send the bot a message like:

- `Meeting with John at 3pm Friday`
- `Gym every Monday at 7am`
- `Team standup tomorrow at 9:30am for 30 minutes`
- `Pay rent on the 1st of every month`

If you leave something out, it fills in the gaps: duration defaults to 60
minutes, repeat to never.

## Commands

- `/read` — pick a range (today, this week, this month) and see your events
- `/delete <event>` — e.g. `/delete gym tomorrow`, then tap to confirm
- `/start` — help and examples

You also get a summary of the day's events sent to you each morning.

## Running it

The bot runs on Vercel as serverless functions. After deploying, hit
`/api/set_webhook` once to point Telegram at your deployment and register the
commands. See [CLAUDE.md](CLAUDE.md) for the environment variables and setup.
