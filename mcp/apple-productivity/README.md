# Apple Productivity MCP Layer

[![Release](https://img.shields.io/github/v/release/matk0shub/apple-productivity-mcp?display_name=tag)](https://github.com/matk0shub/apple-productivity-mcp/releases/latest)
[![Repo](https://img.shields.io/badge/repo-apple--productivity--mcp-4B7BEC)](https://github.com/matk0shub/apple-productivity-mcp)

Shared local stdio MCP server for Apple Calendar and Apple Reminders on macOS.

## What This Folder Is

This is not a user-facing Codex plugin.

This folder is the shared backend layer used by:

- `plugins/apple-calendar`
- `plugins/apple-reminders`

## Main Files

- `server/apple_productivity_mcp.py`
- `mcp.template.json`

## Exposed Tools

The server provides:

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

## How It Gets Used

1. Run the installer from repo root:

```bash
/usr/bin/python3 scripts/install_local_plugins.py --repo-root "$(pwd)"
```

2. The installer rewrites plugin `.mcp.json` files to point at:
   - `mcp/apple-productivity/server/apple_productivity_mcp.py`
3. Codex loads the MCP server through the two installable plugins.

## Verify

You can verify the server directly:

```bash
/usr/bin/python3 scripts/smoke_test_apple_mcp.py
```

## Notes

- Requires macOS Calendar and Reminders permissions for the host app.
- This is the right base layer for a future ChatGPT custom app or any other MCP client integration.
