#!/usr/bin/env python3
"""Smoke test for the local Apple Productivity MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "apple-productivity" / "server" / "apple_productivity_mcp.py"


def request(proc: subprocess.Popen, message: dict) -> dict:
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout unexpectedly.")
        payload = json.loads(line)
        if payload.get("id") == message.get("id"):
            return payload


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This smoke test only runs on macOS.")

    suffix = str(int(time.time()))
    event_title = f"Codex MCP Event {suffix}"
    reminder_title = f"Codex MCP Reminder {suffix}"
    proc = subprocess.Popen(
        ["/usr/bin/python3", str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    created_event_id = None
    created_reminder_id = None
    try:
        init = request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0.1"},
                },
            },
        )
        assert init["result"]["serverInfo"]["name"] == "apple-productivity"
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        proc.stdin.flush()

        tools = request(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert "calendar_add_event" in names
        assert "reminders_add" in names

        add_event = request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "calendar_add_event",
                    "arguments": {
                        "calendar": "doma",
                        "title": event_title,
                        "date": "2026-03-30",
                        "start_time": "18:00",
                        "duration_minutes": 30,
                        "if_free": True,
                    },
                },
            },
        )
        event_payload = add_event["result"]["structuredContent"]
        created_event_id = event_payload["uid"]

        add_reminder = request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "reminders_add",
                    "arguments": {
                        "list": "todo",
                        "title": reminder_title,
                        "date": "2026-03-28",
                        "time": "08:00",
                    },
                },
            },
        )
        reminder_payload = add_reminder["result"]["structuredContent"]
        created_reminder_id = reminder_payload["id"]

        delete_event = request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "calendar_delete_event",
                    "arguments": {"id": created_event_id, "calendars": ["doma"]},
                },
            },
        )
        assert delete_event["result"]["structuredContent"]["deleted"] is True
        created_event_id = None

        delete_reminder = request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "reminders_delete",
                    "arguments": {"id": created_reminder_id, "list": "todo"},
                },
            },
        )
        assert delete_reminder["result"]["structuredContent"]["deleted"] is True
        created_reminder_id = None

        print(json.dumps({"ok": True, "tools": len(names)}, indent=2))
    finally:
        if created_event_id:
            subprocess.run(
                [
                    "/usr/bin/python3",
                    "/Users/matty/Documents/ai_projects/pinescript/plugins/apple-calendar/scripts/apple_calendar.py",
                    "delete-event",
                    "--id",
                    created_event_id,
                    "--calendar",
                    "doma",
                ],
                capture_output=True,
                text=True,
            )
        if created_reminder_id:
            subprocess.run(
                [
                    "/usr/bin/python3",
                    "/Users/matty/Documents/ai_projects/pinescript/plugins/apple-reminders/scripts/apple_reminders.py",
                    "delete",
                    "--id",
                    created_reminder_id,
                    "--list",
                    "todo",
                ],
                capture_output=True,
                text=True,
            )
        proc.kill()


if __name__ == "__main__":
    main()
