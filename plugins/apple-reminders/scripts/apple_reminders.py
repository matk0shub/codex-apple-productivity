#!/usr/bin/env python3
"""CLI wrapper for the Swift/EventKit Reminders backend."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time as time_module
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PLUGIN_ROOT / "config.json"
LOCK_PATH = PLUGIN_ROOT / ".reminders.lock"
BACKEND_SOURCE = SCRIPT_DIR / "apple_reminders_backend.swift"
BACKEND_BINARY = SCRIPT_DIR / ".apple_reminders_backend"
DEFAULT_DEDUPE_BY = "title+list+dueDate"
DEFAULT_CONFIG = {
    "defaultWriteList": "To Do",
    "defaultReadLists": ["To Do", "Tarifix", "Dovolená"],
    "listAliases": {
        "todo": "To Do",
        "to do": "To Do",
        "inbox": "To Do",
        "tarifix": "Tarifix",
        "dovolena": "Dovolená",
        "dovolená": "Dovolená",
    },
    "lockTimeoutSeconds": 12,
    "commandTimeoutSeconds": 45,
    "defaultReminderLeadMinutes": 30,
    "defaultAddPriority": 0,
    "defaultDueTime": "09:00",
    "defaultSearchLimit": 200,
}


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This helper only works on macOS.")
    if shutil.which("swiftc") is None:
        raise SystemExit("swiftc is not available on PATH.")


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _load_config() -> dict:
    payload = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        user_payload = json.loads(CONFIG_PATH.read_text())
        if not isinstance(user_payload, dict):
            raise SystemExit(f"{CONFIG_PATH} must contain a JSON object.")
        payload.update({key: value for key, value in user_payload.items() if value is not None})
    aliases = payload.get("listAliases", {})
    payload["listAliases"] = {
        _normalize_name(key): value for key, value in aliases.items() if isinstance(key, str)
    }
    payload["defaultReadLists"] = list(payload.get("defaultReadLists", []))
    return payload


def _parse_user_date(value: str | None, *, offset_days: int = 0) -> date:
    if value is None:
        return datetime.now().date() + timedelta(days=offset_days)
    normalized = _normalize_name(value)
    if normalized == "today":
        return datetime.now().date()
    if normalized == "tomorrow":
        return datetime.now().date() + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("Invalid date. Use YYYY-MM-DD, today, or tomorrow.") from exc


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(
            "Invalid datetime. Use ISO 8601 like 2026-03-27T09:00:00."
        ) from exc


def _parse_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("Invalid time. Use HH:MM or HH:MM:SS.") from exc


def _as_local(value: datetime) -> datetime:
    return value.astimezone()


def _from_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone()


def _to_iso_local(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat(timespec="seconds")
    return value.astimezone().isoformat(timespec="seconds")


def _backend_lock(config: dict):
    lock_timeout = int(config.get("lockTimeoutSeconds", DEFAULT_CONFIG["lockTimeoutSeconds"]))
    lock_handle = LOCK_PATH.open("a+")
    deadline = time_module.monotonic() + lock_timeout
    while True:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            return lock_handle
        except BlockingIOError:
            if time_module.monotonic() >= deadline:
                lock_handle.close()
                raise SystemExit(
                    "Reminders.app is busy. Retry in a few seconds; the helper serializes access."
                )
            time_module.sleep(0.1)


def _ensure_backend_binary(config: dict) -> None:
    source_mtime = BACKEND_SOURCE.stat().st_mtime
    binary_mtime = BACKEND_BINARY.stat().st_mtime if BACKEND_BINARY.exists() else 0
    if BACKEND_BINARY.exists() and binary_mtime >= source_mtime:
        return
    compile_timeout = max(60, int(config.get("commandTimeoutSeconds", 45)) * 2)
    result = subprocess.run(
        ["swiftc", str(BACKEND_SOURCE), "-o", str(BACKEND_BINARY)],
        check=False,
        capture_output=True,
        text=True,
        timeout=compile_timeout,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Failed to compile Swift backend."
        raise SystemExit(message)


def _run_backend(args: list[str], config: dict) -> object:
    _ensure_backend_binary(config)
    lock_handle = _backend_lock(config)
    timeout_seconds = int(config.get("commandTimeoutSeconds", DEFAULT_CONFIG["commandTimeoutSeconds"]))
    try:
        try:
            result = subprocess.run(
                [str(BACKEND_BINARY), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(f"Reminders command timed out after {timeout_seconds}s.") from exc
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown reminders backend error."
        raise SystemExit(message)
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Unexpected output from reminders backend: {output}") from exc


def _run_jxa(script: str, config: dict) -> object:
    lock_handle = _backend_lock(config)
    timeout_seconds = int(config.get("commandTimeoutSeconds", DEFAULT_CONFIG["commandTimeoutSeconds"]))
    try:
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(f"Reminders JXA command timed out after {timeout_seconds}s.") from exc
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown reminders JXA error."
        raise SystemExit(message)
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Unexpected output from reminders JXA: {output}") from exc


def _js_string(value: str) -> str:
    return json.dumps(value)


def _apple_script_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_applescript_lines(lines: list[str], config: dict) -> str:
    lock_handle = _backend_lock(config)
    timeout_seconds = int(config.get("commandTimeoutSeconds", DEFAULT_CONFIG["commandTimeoutSeconds"]))
    args = ["osascript"]
    for line in lines:
        args.extend(["-e", line])
    try:
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(f"Reminders AppleScript command timed out after {timeout_seconds}s.") from exc
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown reminders AppleScript error."
        raise SystemExit(message)
    return result.stdout.strip()


@lru_cache(maxsize=1)
def _available_list_names() -> tuple[str, ...]:
    config = _load_config()
    payload = _run_backend(["list-lists"], config)
    return tuple(payload or [])


def _resolve_single_list_name(value: str, available_names: tuple[str, ...], config: dict) -> str:
    alias_target = config["listAliases"].get(_normalize_name(value))
    candidates = [alias_target] if alias_target else []
    candidates.append(value)
    for candidate in candidates:
        for available_name in available_names:
            if candidate == available_name:
                return available_name
            if _normalize_name(candidate) == _normalize_name(available_name):
                return available_name

    normalized = _normalize_name(value)
    prefix_matches = [
        available_name
        for available_name in available_names
        if _normalize_name(available_name).startswith(normalized)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise SystemExit(f"Ambiguous list name '{value}': {', '.join(prefix_matches)}")
    raise SystemExit(f"List not found: {value}")


def _resolve_selected_lists(requested: list[str] | None, config: dict) -> list[str]:
    available_names = _available_list_names()
    if requested:
        return [
            _resolve_single_list_name(list_name, available_names, config) for list_name in requested
        ]
    selected = [name for name in config["defaultReadLists"] if name in available_names]
    return selected or list(available_names)


def _resolve_write_list(list_name: str | None, config: dict) -> str:
    target = list_name or config["defaultWriteList"]
    return _resolve_single_list_name(target, _available_list_names(), config)


def _list_records(config: dict) -> list[dict]:
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in config["listAliases"].items():
        reverse_aliases.setdefault(target, []).append(alias)
    return [
        {
            "name": name,
            "aliases": sorted(reverse_aliases.get(name, [])),
            "defaultWrite": name == config["defaultWriteList"],
            "preferredRead": name in config["defaultReadLists"],
        }
        for name in _available_list_names()
    ]


def _fetch_list_reminders(
    list_names: list[str],
    config: dict,
    *,
    include_completed: bool = False,
    search_limit: int | None = None,
) -> list[dict]:
    payload = _run_backend(
        [
            "list",
            "--lists-json",
            json.dumps(list_names),
            "--include-completed",
            "1" if include_completed else "0",
            "--limit",
            str(search_limit or int(config["defaultSearchLimit"])),
        ],
        config,
    )
    return payload or []


def _query_day_reminders(list_names: list[str], day: date, config: dict) -> list[dict]:
    payload = _run_backend(
        ["query-day", "--lists-json", json.dumps(list_names), "--date", day.isoformat()],
        config,
    )
    return payload or []


def _query_overdue_reminders(list_names: list[str], now_value: datetime, config: dict) -> list[dict]:
    payload = _run_backend(
        ["query-overdue", "--lists-json", json.dumps(list_names), "--before", _to_iso_local(now_value)],
        config,
    )
    return payload or []


def _query_alarm_day_reminders(list_names: list[str], day: date, config: dict) -> list[dict]:
    payload = _run_backend(
        ["query-alarm-day", "--lists-json", json.dumps(list_names), "--date", day.isoformat()],
        config,
    )
    return payload or []


def _query_matching_reminders(
    list_names: list[str],
    *,
    reminder_id: str | None,
    title: str | None,
    include_completed: bool,
    config: dict,
) -> list[dict]:
    args = [
        "find",
        "--lists-json",
        json.dumps(list_names),
        "--include-completed",
        "1" if include_completed else "0",
    ]
    if reminder_id:
        args.extend(["--id", reminder_id])
    if title:
        args.extend(["--title", title])
    payload = _run_backend(args, config)
    return payload or []


def _sort_reminders(items: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        due_value = _from_iso_datetime(item.get("dueDate"))
        remind_value = _from_iso_datetime(item.get("remindMeDate"))
        target = due_value or remind_value
        fallback = datetime.max.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return (
            target is None,
            target or fallback,
            _normalize_name(item.get("name", "")),
        )

    return sorted(items, key=key)


def _same_due(item: dict, due_value: datetime | None) -> bool:
    item_due = _from_iso_datetime(item.get("dueDate"))
    if due_value is None and item_due is None:
        return True
    if due_value is None or item_due is None:
        return False
    return item_due.replace(tzinfo=None) == _as_local(due_value).replace(tzinfo=None)


def _match_items(
    items: list[dict],
    *,
    reminder_id: str | None = None,
    title: str | None = None,
    allow_completed: bool = False,
) -> list[dict]:
    results = items
    if reminder_id:
        results = [item for item in results if item["id"] == reminder_id]
    if title:
        normalized = _normalize_name(title)
        results = [item for item in results if _normalize_name(item.get("name", "")) == normalized]
    if not allow_completed:
        results = [item for item in results if not item.get("completed")]
    return results


def _format_target_datetime(item: dict) -> str:
    due_value = _from_iso_datetime(item.get("dueDate"))
    remind_value = _from_iso_datetime(item.get("remindMeDate"))
    target = due_value or remind_value
    if target is None:
        return "no date"
    return target.strftime("%Y-%m-%d %H:%M")


def _format_reminder_line(item: dict) -> str:
    due_text = _format_target_datetime(item)
    flagged = " [flagged]" if item.get("flagged") else ""
    return f"{due_text} {item['list']} | {item['name']}{flagged}"


def _format_reminder_block(title: str, items: list[dict]) -> str:
    if not items:
        return f"{title}\nNo reminders."
    lines = [title]
    lines.extend(_format_reminder_line(item) for item in _sort_reminders(items))
    return "\n".join(lines)


def _create_reminder(
    list_name: str,
    title: str,
    body: str | None,
    due_value: datetime | None,
    remind_value: datetime | None,
    priority: int,
    flagged: bool,
    *,
    dedupe_by: str,
    repeat: str | None,
    repeat_interval: int,
    repeat_weekdays: str | None,
    config: dict,
) -> dict:
    if flagged:
        raise SystemExit("Flagged reminders are not supported by the EventKit backend yet.")
    existing = _query_matching_reminders(
        [list_name],
        reminder_id=None,
        title=title,
        include_completed=True,
        config=config,
    )
    duplicates = []
    if dedupe_by == DEFAULT_DEDUPE_BY:
        normalized_title = _normalize_name(title)
        duplicates = [
            item
            for item in existing
            if _normalize_name(item.get("name", "")) == normalized_title and _same_due(item, due_value)
        ]
    if duplicates:
        return {
            "created": False,
            "reason": "duplicate",
            "list": list_name,
            "reminder": duplicates[0],
        }

    args = [
        "add",
        "--list",
        list_name,
        "--title",
        title,
        "--priority",
        str(priority),
    ]
    if body:
        args.extend(["--body", body])
    if due_value:
        args.extend(["--due-iso", _to_iso_local(due_value)])
    if remind_value:
        args.extend(["--remind-iso", _to_iso_local(remind_value)])
    if repeat:
        args.extend(["--repeat", repeat, "--repeat-interval", str(repeat_interval)])
    if repeat_weekdays:
        args.extend(["--repeat-weekdays", repeat_weekdays])
    payload = _run_backend(args, config)
    return payload or {}


def _complete_reminder(list_name: str, reminder_id: str, config: dict) -> dict:
    payload = _run_backend(["complete", "--list", list_name, "--id", reminder_id], config)
    return payload or {}


def _update_reminder(
    *,
    list_name: str | None,
    reminder_id: str,
    title: str | None,
    body: str | None,
    due_value: datetime | None,
    remind_value: datetime | None,
    clear_due: bool,
    clear_remind: bool,
    priority: int | None,
    move_to_list: str | None,
    repeat: str | None,
    repeat_interval: int,
    repeat_weekdays: str | None,
    clear_repeat: bool,
    config: dict,
) -> dict:
    args = ["update", "--id", reminder_id]
    if list_name:
        args.extend(["--list", list_name])
    if title is not None:
        args.extend(["--title", title])
    if body is not None:
        args.extend(["--body", body])
    if due_value is not None:
        args.extend(["--due-iso", _to_iso_local(due_value)])
    if remind_value is not None:
        args.extend(["--remind-iso", _to_iso_local(remind_value)])
    if clear_due:
        args.extend(["--clear-due", "1"])
    if clear_remind:
        args.extend(["--clear-remind", "1"])
    if priority is not None:
        args.extend(["--priority", str(priority)])
    if move_to_list:
        args.extend(["--move-to-list", move_to_list])
    if repeat:
        args.extend(["--repeat", repeat, "--repeat-interval", str(repeat_interval)])
    if repeat_weekdays:
        args.extend(["--repeat-weekdays", repeat_weekdays])
    if clear_repeat:
        args.extend(["--clear-repeat", "1"])
    payload = _run_backend(args, config)
    return payload or {}


def _delete_reminder(list_name: str | None, reminder_id: str, config: dict) -> dict:
    args = ["delete", "--id", reminder_id]
    if list_name:
        args.extend(["--list", list_name])
    payload = _run_backend(args, config)
    return payload or {}


def _reopen_reminder(list_name: str | None, reminder_id: str, config: dict) -> dict:
    args = ["reopen", "--id", reminder_id]
    if list_name:
        args.extend(["--list", list_name])
    payload = _run_backend(args, config)
    return payload or {}


def _set_flagged(match: dict, flagged: bool, config: dict) -> dict:
    reminder_name = match["name"]
    list_name = match["list"]
    state_literal = "true" if flagged else "false"
    output = _run_applescript_lines(
        [
            'tell application "Reminders"',
            f'tell list "{_apple_script_string(list_name)}"',
            f'set flagged of first reminder whose name is "{_apple_script_string(reminder_name)}" to {state_literal}',
            f'set outVal to flagged of first reminder whose name is "{_apple_script_string(reminder_name)}"',
            'end tell',
            'end tell',
            'return outVal',
        ],
        config,
    )
    return {
        "flagged": output.strip().lower() == "true",
        "list": list_name,
        "id": match["id"],
        "name": reminder_name,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and manage reminders in Reminders.app.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-lists", help="List reminder lists and aliases")

    list_parser = subparsers.add_parser("list", help="List reminders in selected lists")
    list_parser.add_argument("--list", action="append", help="Reminder list name or alias")
    list_parser.add_argument("--include-completed", action="store_true", help="Include completed reminders")
    list_parser.add_argument("--json", action="store_true", help="Emit raw JSON")

    today_parser = subparsers.add_parser("today", help="Show reminders due or reminding today")
    today_parser.add_argument("--list", action="append", help="Reminder list name or alias")

    overdue_parser = subparsers.add_parser("overdue", help="Show overdue reminders")
    overdue_parser.add_argument("--list", action="append", help="Reminder list name or alias")

    alarms_today = subparsers.add_parser("alarms-today", help="Show reminders whose alarm fires today")
    alarms_today.add_argument("--list", action="append", help="Reminder list name or alias")

    due_parser = subparsers.add_parser("due", help="Show reminders for a specific day")
    due_parser.add_argument("--list", action="append", help="Reminder list name or alias")
    due_parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD, today, or tomorrow")

    add_parser = subparsers.add_parser("add", help="Add a reminder")
    add_parser.add_argument("--list", help="Reminder list name or alias")
    add_parser.add_argument("--title", required=True, help="Reminder title")
    add_parser.add_argument("--body", help="Reminder notes/body")
    add_parser.add_argument("--date", help="Due date in YYYY-MM-DD, today, or tomorrow")
    add_parser.add_argument("--time", help="Due time in HH:MM")
    add_parser.add_argument("--due-datetime", help="Exact due datetime in ISO 8601")
    add_parser.add_argument(
        "--remind-minutes-before",
        type=int,
        help="Lead time before due date for remindMeDate",
    )
    add_parser.add_argument("--remind-datetime", help="Exact remind datetime in ISO 8601")
    add_parser.add_argument("--repeat", choices=["daily", "weekly"], help="Repeat frequency")
    add_parser.add_argument("--repeat-interval", type=int, default=1, help="Repeat interval")
    add_parser.add_argument("--repeat-weekdays", help="Comma-separated weekdays like MO,WE,FR")
    add_parser.add_argument(
        "--priority", type=int, choices=[0, 1, 5, 9], help="Apple reminder priority"
    )
    add_parser.add_argument("--flagged", action="store_true", help="Flag the reminder")
    add_parser.add_argument(
        "--dedupe-by",
        choices=[DEFAULT_DEDUPE_BY, "none"],
        default=DEFAULT_DEDUPE_BY,
        help="Duplicate detection rule before create",
    )

    done_parser = subparsers.add_parser("done", help="Mark a reminder complete")
    done_parser.add_argument("--list", help="Reminder list name or alias")
    done_parser.add_argument("--title", help="Exact reminder title")
    done_parser.add_argument("--id", help="Exact reminder id")
    done_parser.add_argument(
        "--include-completed", action="store_true", help="Allow matching already completed reminders"
    )

    update_parser = subparsers.add_parser("update", help="Update an existing reminder")
    update_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    update_parser.add_argument("--id", help="Exact reminder id")
    update_parser.add_argument("--title", help="Exact existing reminder title")
    update_parser.add_argument("--set-title", help="New reminder title")
    update_parser.add_argument("--set-body", help="New reminder notes/body")
    update_parser.add_argument("--date", help="New due date in YYYY-MM-DD, today, or tomorrow")
    update_parser.add_argument("--time", help="New due time in HH:MM")
    update_parser.add_argument("--due-datetime", help="Exact new due datetime in ISO 8601")
    update_parser.add_argument("--clear-due", action="store_true", help="Clear the due date")
    update_parser.add_argument("--remind-minutes-before", type=int, help="Lead time before due date")
    update_parser.add_argument("--remind-datetime", help="Exact new reminder datetime in ISO 8601")
    update_parser.add_argument("--clear-remind", action="store_true", help="Clear reminder alarms")
    update_parser.add_argument("--repeat", choices=["daily", "weekly"], help="Repeat frequency")
    update_parser.add_argument("--repeat-interval", type=int, default=1, help="Repeat interval")
    update_parser.add_argument("--repeat-weekdays", help="Comma-separated weekdays like MO,WE,FR")
    update_parser.add_argument("--clear-repeat", action="store_true", help="Clear recurrence")
    update_parser.add_argument("--priority", type=int, choices=[0, 1, 5, 9], help="New priority")
    update_parser.add_argument("--move-to-list", help="Move the reminder to another list")

    delete_parser = subparsers.add_parser("delete", help="Delete a reminder")
    delete_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    delete_parser.add_argument("--id", help="Exact reminder id")
    delete_parser.add_argument("--title", help="Exact existing reminder title")

    reopen_parser = subparsers.add_parser("reopen", help="Reopen a completed reminder")
    reopen_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    reopen_parser.add_argument("--id", help="Exact reminder id")
    reopen_parser.add_argument("--title", help="Exact existing reminder title")
    reopen_parser.add_argument("--include-completed", action="store_true", help="Search completed reminders too")

    move_parser = subparsers.add_parser("move-to-list", help="Move a reminder to another list")
    move_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    move_parser.add_argument("--id", help="Exact reminder id")
    move_parser.add_argument("--title", help="Exact existing reminder title")
    move_parser.add_argument("--to-list", required=True, help="Target list name or alias")

    flag_parser = subparsers.add_parser("flag", help="Flag a reminder")
    flag_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    flag_parser.add_argument("--id", help="Exact reminder id")
    flag_parser.add_argument("--title", help="Exact existing reminder title")

    unflag_parser = subparsers.add_parser("unflag", help="Unflag a reminder")
    unflag_parser.add_argument("--list", help="Reminder list name or alias used to narrow the search")
    unflag_parser.add_argument("--id", help="Exact reminder id")
    unflag_parser.add_argument("--title", help="Exact existing reminder title")

    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_text(text: str) -> None:
    print(text)


def main() -> None:
    _require_macos()
    config = _load_config()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "list-lists":
        _print_json(_list_records(config))
        return

    if args.command == "list":
        list_names = _resolve_selected_lists(args.list, config)
        items = _fetch_list_reminders(
            list_names,
            config,
            include_completed=args.include_completed,
            search_limit=int(config["defaultSearchLimit"]),
        )
        if args.json:
            _print_json(_sort_reminders(items))
        else:
            _print_text(_format_reminder_block("Reminders", items))
        return

    if args.command == "today":
        list_names = _resolve_selected_lists(args.list, config)
        due_items = _query_day_reminders(list_names, datetime.now().date(), config)
        alarm_items = _query_alarm_day_reminders(list_names, datetime.now().date(), config)
        combined = {item["id"]: item for item in due_items}
        for item in alarm_items:
            combined.setdefault(item["id"], item)
        items = list(combined.values())
        _print_text(_format_reminder_block("Today", items))
        return

    if args.command == "overdue":
        list_names = _resolve_selected_lists(args.list, config)
        items = _query_overdue_reminders(list_names, datetime.now().astimezone(), config)
        _print_text(_format_reminder_block("Overdue", items))
        return

    if args.command == "alarms-today":
        list_names = _resolve_selected_lists(args.list, config)
        items = _query_alarm_day_reminders(list_names, datetime.now().date(), config)
        _print_text(_format_reminder_block("Alarms Today", items))
        return

    if args.command == "due":
        list_names = _resolve_selected_lists(args.list, config)
        target_day = _parse_user_date(args.date)
        items = _query_day_reminders(list_names, target_day, config)
        _print_text(_format_reminder_block(f"Due for {target_day:%Y-%m-%d}", items))
        return

    if args.command == "add":
        list_name = _resolve_write_list(args.list, config)
        if args.due_datetime and (args.date or args.time):
            raise SystemExit("Use either --due-datetime or --date/--time, not both.")
        due_value = None
        if args.due_datetime:
            due_value = _parse_iso_datetime(args.due_datetime)
        elif args.date:
            due_day = _parse_user_date(args.date)
            due_time = _parse_clock(args.time or config["defaultDueTime"])
            due_value = datetime.combine(due_day, due_time).astimezone()

        if args.remind_datetime and args.remind_minutes_before is not None:
            raise SystemExit(
                "Use either --remind-datetime or --remind-minutes-before, not both."
            )
        remind_value = None
        if args.remind_datetime:
            remind_value = _parse_iso_datetime(args.remind_datetime)
        elif args.remind_minutes_before is not None:
            if due_value is None:
                raise SystemExit("--remind-minutes-before requires a due date/time.")
            remind_value = due_value - timedelta(minutes=args.remind_minutes_before)
        elif due_value is not None and config["defaultReminderLeadMinutes"] is not None:
            remind_value = due_value - timedelta(minutes=int(config["defaultReminderLeadMinutes"]))

        payload = _create_reminder(
            list_name=list_name,
            title=args.title,
            body=args.body,
            due_value=due_value,
            remind_value=remind_value,
            priority=args.priority if args.priority is not None else int(config["defaultAddPriority"]),
            flagged=args.flagged,
            dedupe_by=args.dedupe_by,
            repeat=args.repeat,
            repeat_interval=args.repeat_interval,
            repeat_weekdays=args.repeat_weekdays,
            config=config,
        )
        _print_json(payload)
        return

    if args.command == "done":
        if not args.id and not args.title:
            raise SystemExit("done requires either --id or --title.")
        list_names = [_resolve_write_list(args.list, config)] if args.list else _resolve_selected_lists(None, config)
        items = _query_matching_reminders(
            list_names,
            reminder_id=args.id,
            title=args.title,
            include_completed=args.include_completed,
            config=config,
        )
        matches = _match_items(
            items,
            reminder_id=args.id,
            title=args.title,
            allow_completed=args.include_completed,
        )
        if not matches:
            raise SystemExit("No matching reminder found.")
        if len(matches) > 1:
            raise SystemExit(
                "Multiple reminders match. Refine by --list or use --id.\n"
                + "\n".join(f"{item['id']} | {item['list']} | {item['name']}" for item in matches[:10])
            )
        match = matches[0]
        _print_json(_complete_reminder(match["list"], match["id"], config))
        return

    if args.command in {"update", "delete", "reopen", "move-to-list", "flag", "unflag"}:
        if not getattr(args, "id", None) and not getattr(args, "title", None):
            raise SystemExit(f"{args.command} requires either --id or --title.")
        if args.command == "reopen" and getattr(args, "id", None):
            direct_list = _resolve_write_list(args.list, config) if getattr(args, "list", None) else None
            _print_json(_reopen_reminder(direct_list, args.id, config))
            return
        search_lists = (
            [_resolve_write_list(args.list, config)]
            if getattr(args, "list", None)
            else _resolve_selected_lists(None, config)
        )
        search_include_completed = args.command == "reopen" or getattr(args, "include_completed", False)
        items = _query_matching_reminders(
            search_lists,
            reminder_id=getattr(args, "id", None),
            title=getattr(args, "title", None),
            include_completed=search_include_completed,
            config=config,
        )
        matches = _match_items(
            items,
            reminder_id=getattr(args, "id", None),
            title=getattr(args, "title", None),
            allow_completed=search_include_completed,
        )
        if args.command == "reopen":
            matches = [item for item in items if item.get("completed")]
        if not matches:
            raise SystemExit("No matching reminder found.")
        if len(matches) > 1:
            raise SystemExit(
                "Multiple reminders match. Refine by --list or use --id.\n"
                + "\n".join(f"{item['id']} | {item['list']} | {item['name']}" for item in matches[:10])
            )
        match = matches[0]

        if args.command == "delete":
            _print_json(_delete_reminder(match["list"], match["id"], config))
            return
        if args.command == "reopen":
            _print_json(_reopen_reminder(match["list"], match["id"], config))
            return
        if args.command == "move-to-list":
            _print_json(
                _update_reminder(
                    list_name=match["list"],
                    reminder_id=match["id"],
                    title=None,
                    body=None,
                    due_value=None,
                    remind_value=None,
                clear_due=False,
                clear_remind=False,
                priority=None,
                move_to_list=_resolve_write_list(args.to_list, config),
                repeat=None,
                repeat_interval=1,
                repeat_weekdays=None,
                clear_repeat=False,
                config=config,
            )
            )
            return
        if args.command == "flag":
            _print_json(_set_flagged(match, True, config))
            return
        if args.command == "unflag":
            _print_json(_set_flagged(match, False, config))
            return

        if args.due_datetime and (args.date or args.time):
            raise SystemExit("Use either --due-datetime or --date/--time, not both.")
        due_value = None
        if args.due_datetime:
            due_value = _parse_iso_datetime(args.due_datetime)
        elif args.date:
            due_day = _parse_user_date(args.date)
            due_time = _parse_clock(args.time or config["defaultDueTime"])
            due_value = datetime.combine(due_day, due_time).astimezone()
        if args.remind_datetime and args.remind_minutes_before is not None:
            raise SystemExit(
                "Use either --remind-datetime or --remind-minutes-before, not both."
            )
        remind_value = None
        if args.remind_datetime:
            remind_value = _parse_iso_datetime(args.remind_datetime)
        elif args.remind_minutes_before is not None:
            if due_value is None:
                raise SystemExit("--remind-minutes-before requires a due date/time.")
            remind_value = due_value - timedelta(minutes=args.remind_minutes_before)
        _print_json(
            _update_reminder(
                list_name=match["list"],
                reminder_id=match["id"],
                title=args.set_title,
                body=args.set_body,
                due_value=due_value,
                remind_value=remind_value,
                clear_due=args.clear_due,
                clear_remind=args.clear_remind,
                priority=args.priority,
                move_to_list=_resolve_write_list(args.move_to_list, config) if args.move_to_list else None,
                repeat=args.repeat,
                repeat_interval=args.repeat_interval,
                repeat_weekdays=args.repeat_weekdays,
                clear_repeat=args.clear_repeat,
                config=config,
            )
        )
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
