# Apple Calendar Plugin

[![Release](https://img.shields.io/github/v/release/matk0shub/apple-productivity-mcp?display_name=tag)](https://github.com/matk0shub/apple-productivity-mcp/releases/latest)
[![Repo](https://img.shields.io/badge/repo-apple--productivity--mcp-4B7BEC)](https://github.com/matk0shub/apple-productivity-mcp)

Local Codex plugin for macOS Calendar with a Python CLI wrapper and a Swift/EventKit backend.

## What It Covers

- list calendars with aliases
- daily agenda views with `today`, `tomorrow`, and `agenda`
- free-window lookup with `free`
- create events with conflict and duplicate protection
- find events by title and day
- update or delete existing events
- set or clear reminders
- recurring events with daily or weekly recurrence
- `.ics` export and import

## Main Files

- `scripts/apple_calendar.py`
- `scripts/apple_calendar_backend.swift`
- `.mcp.json`
- `config.json`
- `skills/apple-calendar/SKILL.md`

## Common Commands

Show today's agenda:

```bash
/usr/bin/python3 scripts/apple_calendar.py today
```

Add an event:

```bash
/usr/bin/python3 scripts/apple_calendar.py add \
  --calendar doma \
  --title "Stolní Tenis" \
  --date today \
  --start-time 15:00 \
  --duration-minutes 60 \
  --if-free
```

Find an event:

```bash
/usr/bin/python3 scripts/apple_calendar.py find-events \
  --calendar doma \
  --title "Stolní Tenis" \
  --date today
```

Update reminders:

```bash
/usr/bin/python3 scripts/apple_calendar.py set-reminder \
  --calendar doma \
  --title "Stolní Tenis" \
  --date today \
  --minutes-before 30
```

Export one day to `.ics`:

```bash
/usr/bin/python3 scripts/apple_calendar.py export-ics \
  --calendar doma \
  --date tomorrow \
  --days 1 \
  --output /tmp/events.ics
```

## Notes

- This plugin requires macOS Calendar permission for the app that runs Codex.
- The Swift backend auto-compiles on first use or when the Swift source changes.
- Event identifiers remain stable under the EventKit backend, including recurring updates.

## Enable In Codex

1. Open this repository in Codex.
2. Install or expose the local plugin so Codex can see `plugins/apple-calendar/.codex-plugin/plugin.json`.
3. Ensure macOS Calendar access is enabled for the app running Codex.
4. The plugin now also points at the shared local MCP server via `.mcp.json`.
5. Start using either:
   - the skill prompts from `skills/apple-calendar/SKILL.md`
   - or the MCP tools exposed by the shared `apple-productivity` server

## MCP Note

This plugin now consumes the shared local MCP server from `mcp/apple-productivity`. That means:

- you can use the Apple Calendar skill directly in Codex
- and the same backend is also available as MCP tools without duplicating business logic

## Links

- [Latest release](https://github.com/matk0shub/apple-productivity-mcp/releases/latest)
- [Repository root](https://github.com/matk0shub/apple-productivity-mcp)
