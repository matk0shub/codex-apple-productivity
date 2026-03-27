#!/usr/bin/env python3
"""Integration smoke tests for apple-calendar and apple-reminders CLI wrappers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path("/Users/matty/Documents/ai_projects/pinescript")
CAL = ROOT / "plugins" / "apple-calendar" / "scripts" / "apple_calendar.py"
REM = ROOT / "plugins" / "apple-reminders" / "scripts" / "apple_reminders.py"


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{result.stderr or result.stdout}")
    return result.stdout.strip()


def run_json(cmd: list[str]) -> object:
    return json.loads(run(cmd))


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This smoke test only runs on macOS.")

    suffix = str(int(time.time()))
    cal_title = f"Codex Smoke Event {suffix}"
    rem_title = f"Codex Smoke Reminder {suffix}"
    imported_cal_title = cal_title
    temp_ics = Path(tempfile.gettempdir()) / f"codex-apple-smoke-{suffix}.ics"

    created_calendar_uid = None
    imported_calendar_uid = None
    created_reminder_id = None
    moved_list = "Dovolená"

    try:
        created_event = run_json(
            [
                str(CAL),
                "add",
                "--calendar",
                "doma",
                "--title",
                cal_title,
                "--date",
                "2026-03-30",
                "--start-time",
                "09:00",
                "--duration-minutes",
                "30",
                "--location",
                "Smoke",
                "--notes",
                "smoke",
                "--repeat",
                "weekly",
                "--repeat-weekdays",
                "MO,WE",
                "--if-free",
            ]
        )
        created_calendar_uid = created_event["uid"]

        updated_event = run_json(
            [
                str(CAL),
                "set-reminder",
                "--id",
                created_calendar_uid,
                "--calendar",
                "doma",
                "--minutes-before",
                "10",
            ]
        )
        created_calendar_uid = updated_event["uid"]

        exported = run_json(
            [
                str(CAL),
                "export-ics",
                "--calendar",
                "doma",
                "--date",
                "2026-03-30",
                "--days",
                "1",
                "--output",
                str(temp_ics),
            ]
        )
        assert exported["count"] >= 1

        deleted_original = run_json(
            [
                str(CAL),
                "delete-event",
                "--id",
                created_calendar_uid,
                "--calendar",
                "doma",
            ]
        )
        assert deleted_original["deleted"] is True
        created_calendar_uid = None

        imported = run_json(
            [
                str(CAL),
                "import-ics",
                "--calendar",
                "doma",
                "--input",
                str(temp_ics),
                "--if-free",
            ]
        )
        assert imported["count"] >= 1
        imported_match = next(
            event
            for event in imported["events"]
            if event.get("summary") == imported_cal_title or event.get("event", {}).get("summary") == imported_cal_title
        )
        imported_calendar_uid = imported_match.get("uid") or imported_match["event"]["uid"]

        found_imported = run_json(
            [
                str(CAL),
                "find-events",
                "--calendar",
                "doma",
                "--title",
                imported_cal_title,
                "--date",
                "2026-03-30",
            ]
        )
        assert found_imported and found_imported[0]["recurrence"]["frequency"] == "weekly"
        assert found_imported[0]["remindersMinutesBefore"] == [10]

        created_reminder = run_json(
            [
                str(REM),
                "add",
                "--list",
                "todo",
                "--title",
                rem_title,
                "--date",
                "2026-03-28",
                "--time",
                "10:00",
                "--remind-datetime",
                "2026-03-27T23:00:00+01:00",
                "--repeat",
                "daily",
            ]
        )
        created_reminder_id = created_reminder["id"]
        assert created_reminder["recurrence"]["frequency"] == "daily"

        alarms_today = run([str(REM), "alarms-today", "--list", "todo"])
        assert rem_title in alarms_today

        updated_reminder = run_json(
            [
                str(REM),
                "update",
                "--list",
                "todo",
                "--id",
                created_reminder_id,
                "--set-title",
                f"{rem_title} Updated",
                "--set-body",
                "updated",
                "--priority",
                "5",
            ]
        )
        created_reminder_id = updated_reminder["id"]
        rem_title_updated = updated_reminder["name"]

        moved = run_json(
            [
                str(REM),
                "move-to-list",
                "--list",
                "todo",
                "--id",
                created_reminder_id,
                "--to-list",
                "dovolena",
            ]
        )
        assert moved["list"] == moved_list

        flagged = run_json([str(REM), "flag", "--list", "dovolena", "--id", created_reminder_id])
        assert flagged["flagged"] is True

        unflagged = run_json([str(REM), "unflag", "--list", "dovolena", "--id", created_reminder_id])
        assert unflagged["flagged"] is False

        done = run_json([str(REM), "done", "--list", "dovolena", "--id", created_reminder_id])
        assert done["completed"] is True

        reopened = run_json(
            [
                str(REM),
                "reopen",
                "--list",
                "dovolena",
                "--id",
                created_reminder_id,
                "--include-completed",
            ]
        )
        assert reopened["completed"] is False

        deleted_reminder = run_json([str(REM), "delete", "--list", "dovolena", "--id", created_reminder_id])
        assert deleted_reminder["deleted"] is True
        created_reminder_id = None

        print(
            json.dumps(
                {
                    "ok": True,
                    "calendar_imported_uid": imported_calendar_uid,
                    "reminder_test_title": rem_title,
                    "ics": str(temp_ics),
                },
                indent=2,
            )
        )
    finally:
        if imported_calendar_uid:
            subprocess.run(
                [str(CAL), "delete-event", "--id", imported_calendar_uid, "--calendar", "doma"],
                capture_output=True,
                text=True,
            )
        if created_calendar_uid:
            subprocess.run(
                [str(CAL), "delete-event", "--id", created_calendar_uid, "--calendar", "doma"],
                capture_output=True,
                text=True,
            )
        if created_reminder_id:
            subprocess.run(
                [str(REM), "delete", "--list", "todo", "--id", created_reminder_id],
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [str(REM), "delete", "--list", "dovolena", "--id", created_reminder_id],
                capture_output=True,
                text=True,
            )
        if temp_ics.exists():
            temp_ics.unlink()


if __name__ == "__main__":
    main()
