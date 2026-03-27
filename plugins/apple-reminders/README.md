# Apple Reminders Plugin

[![Release](https://img.shields.io/github/v/release/matk0shub/apple-productivity-mcp?display_name=tag)](https://github.com/matk0shub/apple-productivity-mcp/releases/latest)
[![Repo](https://img.shields.io/badge/repo-apple--productivity--mcp-4B7BEC)](https://github.com/matk0shub/apple-productivity-mcp)

Local Codex plugin for macOS Reminders with a Python CLI wrapper, a Swift/EventKit backend, and an AppleScript fallback for flagging.

## What It Covers

- list reminder lists with aliases
- due-today, overdue, due-by-date, and alarm-today views
- add reminders with due time and remind time
- recurring reminders with daily or weekly recurrence
- update reminder fields
- move reminders between lists
- mark done, reopen, or delete
- flag and unflag reminders
- duplicate protection on add

## Main Files

- `scripts/apple_reminders.py`
- `scripts/apple_reminders_backend.swift`
- `.mcp.json`
- `config.json`
- `skills/apple-reminders/SKILL.md`

## Common Commands

Show reminders due today:

```bash
/usr/bin/python3 scripts/apple_reminders.py today
```

Show reminders whose alarm fires today:

```bash
/usr/bin/python3 scripts/apple_reminders.py alarms-today
```

Add a reminder:

```bash
/usr/bin/python3 scripts/apple_reminders.py add \
  --list todo \
  --title "Jít na oběd" \
  --date today \
  --time 13:00
```

Add a recurring reminder:

```bash
/usr/bin/python3 scripts/apple_reminders.py add \
  --list todo \
  --title "Týdenní report" \
  --date tomorrow \
  --time 09:00 \
  --repeat weekly \
  --repeat-weekdays MO,WE,FR
```

Move and complete:

```bash
/usr/bin/python3 scripts/apple_reminders.py move-to-list \
  --list todo \
  --title "Týdenní report" \
  --to-list dovolena

/usr/bin/python3 scripts/apple_reminders.py done \
  --list dovolena \
  --title "Týdenní report"
```

## Notes

- This plugin requires macOS Reminders permission for the app that runs Codex.
- The Swift backend auto-compiles on first use or when the Swift source changes.
- `flag` and `unflag` use AppleScript because EventKit does not expose the flagged property.

## Enable In Codex

1. Open this repository in Codex.
2. Install or expose the local plugin so Codex can see `plugins/apple-reminders/.codex-plugin/plugin.json`.
3. Ensure macOS Reminders access is enabled for the app running Codex.
4. The plugin now also points at the shared local MCP server via `.mcp.json`.
5. Start using either:
   - the skill prompts from `skills/apple-reminders/SKILL.md`
   - or the MCP tools exposed by the shared `apple-productivity` server

## MCP Note

This plugin now consumes the shared local MCP server from `mcp/apple-productivity`. That keeps the CLI, plugin skill, and MCP tools on top of one shared backend.

## Links

- [Latest release](https://github.com/matk0shub/apple-productivity-mcp/releases/latest)
- [Repository root](https://github.com/matk0shub/apple-productivity-mcp)
