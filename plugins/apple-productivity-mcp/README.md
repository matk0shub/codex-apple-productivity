# Apple Productivity MCP

Local stdio MCP server for Apple Calendar and Apple Reminders on macOS.

## What It Does

- exposes Apple Calendar tools through MCP
- exposes Apple Reminders tools through MCP
- reuses the existing Python wrappers and Swift/EventKit backends
- keeps a local-only deployment model with no remote service required

## Main Files

- `.mcp.json`
- `scripts/apple_productivity_mcp.py`

## Usage

This plugin is meant to be installed as a local Codex plugin. It launches a stdio MCP server with the tools:

- `calendar_list_calendars`
- `calendar_list_events`
- `calendar_find_events`
- `calendar_add_event`
- `calendar_update_event`
- `calendar_delete_event`
- `calendar_set_reminders`
- `calendar_clear_reminders`
- `calendar_export_ics`
- `calendar_import_ics`
- `reminders_list_lists`
- `reminders_list`
- `reminders_today`
- `reminders_overdue`
- `reminders_alarms_today`
- `reminders_find`
- `reminders_add`
- `reminders_update`
- `reminders_done`
- `reminders_reopen`
- `reminders_delete`
- `reminders_move_to_list`
- `reminders_flag`
- `reminders_unflag`

## Notes

- Requires macOS Calendar and Reminders permissions for the host app.
- This is the right base layer for a future ChatGPT custom app or a shared local MCP setup.

## Enable In Codex

1. Open this repository in Codex.
2. Make sure Codex can see [plugins/apple-productivity-mcp/.codex-plugin/plugin.json](/Users/matty/Documents/ai_projects/pinescript/plugins/apple-productivity-mcp/.codex-plugin/plugin.json).
3. Ensure macOS Calendar and Reminders permissions are enabled for the app running Codex.
4. Codex will use [plugins/apple-productivity-mcp/.mcp.json](/Users/matty/Documents/ai_projects/pinescript/plugins/apple-productivity-mcp/.mcp.json) to launch the local stdio MCP server.
5. The server process is [apple_productivity_mcp.py](/Users/matty/Documents/ai_projects/pinescript/plugins/apple-productivity-mcp/scripts/apple_productivity_mcp.py).
6. You can verify the server by running:

```bash
/usr/bin/python3 scripts/smoke_test_apple_mcp.py
```
