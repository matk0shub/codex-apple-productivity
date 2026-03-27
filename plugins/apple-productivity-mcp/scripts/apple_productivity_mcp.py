#!/usr/bin/env python3
"""Local stdio MCP server for Apple Calendar and Reminders."""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Callable


ROOT = Path("/Users/matty/Documents/ai_projects/pinescript")
CAL_PATH = ROOT / "plugins" / "apple-calendar" / "scripts" / "apple_calendar.py"
REM_PATH = ROOT / "plugins" / "apple-reminders" / "scripts" / "apple_reminders.py"
PROTOCOL_VERSION = "2024-11-05"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAL = load_module("apple_calendar_cli", CAL_PATH)
REM = load_module("apple_reminders_cli", REM_PATH)


def iso_local(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def parse_date_or_today(value: str | None) -> datetime.date:
    return CAL._parse_user_date(value)


def now_local() -> datetime:
    return datetime.now().astimezone()


def calendar_json_result(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
    }


def error_result(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def ensure_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


def calendar_selected_calendars(args: dict[str, Any], config: dict) -> list[str]:
    return CAL._resolve_selected_calendars(
        ensure_list(args.get("calendars")),
        include_all=bool(args.get("all_calendars", False)),
        config=config,
    )


def reminder_selected_lists(args: dict[str, Any], config: dict) -> list[str]:
    return REM._resolve_selected_lists(ensure_list(args.get("lists")), config)


def build_calendar_window(args: dict[str, Any]) -> tuple[datetime, datetime]:
    if args.get("start") or args.get("end"):
        if not args.get("start") or not args.get("end"):
            raise SystemExit("Provide both start and end.")
        return CAL._parse_iso_datetime(args["start"]), CAL._parse_iso_datetime(args["end"])
    day = parse_date_or_today(args.get("date"))
    days = int(args.get("days", 1))
    return (
        datetime.combine(day, dt_time.min).astimezone(),
        datetime.combine(day + timedelta(days=days), dt_time.min).astimezone(),
    )


def handle_calendar_list_calendars(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    return CAL._calendar_records(config)


def handle_calendar_list_events(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    calendars = calendar_selected_calendars(args, config)
    start_value, end_value = build_calendar_window(args)
    return CAL._list_events_for_calendars(
        calendars,
        start_value,
        end_value,
        config,
        tolerate_failures=len(calendars) > 1,
    )


def handle_calendar_find_events(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    calendars = calendar_selected_calendars(args, config)
    day = parse_date_or_today(args.get("date"))
    events = CAL._list_events_for_calendars(
        calendars,
        datetime.combine(day, dt_time.min).astimezone(),
        datetime.combine(day + timedelta(days=1), dt_time.min).astimezone(),
        config,
        tolerate_failures=len(calendars) > 1,
    )
    return CAL._filter_events_by_title(events, args["title"])


def handle_calendar_add_event(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    calendar_name = CAL._resolve_write_calendar(args.get("calendar"), config)
    if args.get("start") and args.get("end"):
        start_value = CAL._parse_iso_datetime(args["start"])
        end_value = CAL._parse_iso_datetime(args["end"])
    else:
        day = parse_date_or_today(args["date"])
        start_value = datetime.combine(day, CAL._parse_clock(args["start_time"]))
        if args.get("end_time"):
            end_value = datetime.combine(day, CAL._parse_clock(args["end_time"]))
        else:
            end_value = start_value + timedelta(minutes=int(args.get("duration_minutes", 60)))
    return CAL._create_event(
        calendar_name=calendar_name,
        title=args["title"],
        start_value=start_value,
        end_value=end_value,
        location=args.get("location"),
        notes=args.get("notes"),
        if_free=bool(args.get("if_free", False)),
        allow_conflict=bool(args.get("allow_conflict", False)),
        dedupe_by=args.get("dedupe_by", CAL.DEFAULT_DEDUPE_BY),
        repeat=args.get("repeat"),
        repeat_interval=int(args.get("repeat_interval", 1)),
        repeat_weekdays=args.get("repeat_weekdays"),
        config=config,
    )


def resolve_calendar_target(args: dict[str, Any], config: dict) -> dict[str, Any]:
    calendars = calendar_selected_calendars(args, config)
    return CAL._resolve_event_target(
        event_id=args.get("id"),
        title=args.get("title"),
        date_value=args.get("date"),
        calendar_names=calendars,
        config=config,
    )


def handle_calendar_update_event(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    target = resolve_calendar_target(args, config)
    start_value = CAL._parse_iso_datetime(args["start"]) if args.get("start") else None
    end_value = CAL._parse_iso_datetime(args["end"]) if args.get("end") else None
    move_to = CAL._resolve_write_calendar(args["move_to_calendar"], config) if args.get("move_to_calendar") else None
    return CAL._update_event(
        target_event=target,
        title=args.get("set_title"),
        start_value=start_value,
        end_value=end_value,
        location=args.get("set_location"),
        notes=args.get("set_notes"),
        move_to_calendar=move_to,
        repeat=args.get("repeat"),
        repeat_interval=int(args.get("repeat_interval", 1)),
        repeat_weekdays=args.get("repeat_weekdays"),
        clear_repeat=bool(args.get("clear_repeat", False)),
        config=config,
    )


def handle_calendar_delete_event(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    target = resolve_calendar_target(args, config)
    return CAL._delete_event(target, config)


def handle_calendar_set_reminders(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    target = resolve_calendar_target(args, config)
    offsets = [int(item) for item in args.get("minutes_before", [])]
    return CAL._recreate_event_with_reminders(target, offsets, config)


def handle_calendar_clear_reminders(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    target = resolve_calendar_target(args, config)
    return CAL._clear_event_reminders(target, config)


def handle_calendar_export_ics(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    calendars = calendar_selected_calendars(args, config)
    start_value, end_value = build_calendar_window(args)
    events = CAL._list_events_for_calendars(
        calendars,
        start_value,
        end_value,
        config,
        tolerate_failures=len(calendars) > 1,
    )
    ics = CAL._build_ics(events)
    if args.get("output_path"):
        Path(args["output_path"]).write_text(ics)
    return {"count": len(events), "ics": ics, "output_path": args.get("output_path")}


def handle_calendar_import_ics(args: dict[str, Any]) -> Any:
    config = CAL._load_config()
    calendar_name = CAL._resolve_write_calendar(args.get("calendar"), config)
    imported = []
    for event in CAL._parse_ics_events(args["input_path"]):
        payload = CAL._create_event(
            calendar_name=calendar_name,
            title=event["summary"],
            start_value=event["startDate"],
            end_value=event["endDate"],
            location=event.get("location"),
            notes=event.get("description"),
            if_free=bool(args.get("if_free", False)),
            allow_conflict=bool(args.get("allow_conflict", False)),
            dedupe_by=args.get("dedupe_by", CAL.DEFAULT_DEDUPE_BY),
            repeat=event.get("repeat"),
            repeat_interval=event.get("repeat_interval", 1),
            repeat_weekdays=event.get("repeat_weekdays"),
            config=config,
        )
        reminder_offsets = event.get("remindersMinutesBefore") or []
        if payload.get("created") and reminder_offsets:
            payload = CAL._run_backend(
                ["update", "--id", payload["uid"], "--alarms-json", json.dumps(reminder_offsets)],
                config,
            )
        imported.append(payload)
    return {"count": len(imported), "events": imported}


def handle_reminders_list_lists(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    return REM._list_records(config)


def handle_reminders_list(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    lists = reminder_selected_lists(args, config)
    return REM._fetch_list_reminders(
        lists,
        config,
        include_completed=bool(args.get("include_completed", False)),
        search_limit=int(args.get("limit", config["defaultSearchLimit"])),
    )


def handle_reminders_today(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    lists = reminder_selected_lists(args, config)
    due_items = REM._query_day_reminders(lists, now_local().date(), config)
    alarm_items = REM._query_alarm_day_reminders(lists, now_local().date(), config)
    combined = {item["id"]: item for item in due_items}
    for item in alarm_items:
        combined.setdefault(item["id"], item)
    return list(combined.values())


def handle_reminders_overdue(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    lists = reminder_selected_lists(args, config)
    return REM._query_overdue_reminders(lists, now_local(), config)


def handle_reminders_alarms_today(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    lists = reminder_selected_lists(args, config)
    return REM._query_alarm_day_reminders(lists, now_local().date(), config)


def handle_reminders_find(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    lists = reminder_selected_lists(args, config)
    return REM._query_matching_reminders(
        lists,
        reminder_id=args.get("id"),
        title=args.get("title"),
        include_completed=bool(args.get("include_completed", False)),
        config=config,
    )


def handle_reminders_add(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    list_name = REM._resolve_write_list(args.get("list"), config)
    due_value = None
    if args.get("due_datetime"):
        due_value = REM._parse_iso_datetime(args["due_datetime"])
    elif args.get("date"):
        due_day = REM._parse_user_date(args["date"])
        due_time = REM._parse_clock(args.get("time") or config["defaultDueTime"])
        due_value = datetime.combine(due_day, due_time).astimezone()
    remind_value = None
    if args.get("remind_datetime"):
        remind_value = REM._parse_iso_datetime(args["remind_datetime"])
    elif args.get("remind_minutes_before") is not None:
        if due_value is None:
            raise SystemExit("remind_minutes_before requires a due date/time.")
        remind_value = due_value - timedelta(minutes=int(args["remind_minutes_before"]))
    elif due_value is not None and config["defaultReminderLeadMinutes"] is not None:
        remind_value = due_value - timedelta(minutes=int(config["defaultReminderLeadMinutes"]))
    return REM._create_reminder(
        list_name=list_name,
        title=args["title"],
        body=args.get("body"),
        due_value=due_value,
        remind_value=remind_value,
        priority=int(args.get("priority", config["defaultAddPriority"])),
        flagged=bool(args.get("flagged", False)),
        dedupe_by=args.get("dedupe_by", REM.DEFAULT_DEDUPE_BY),
        repeat=args.get("repeat"),
        repeat_interval=int(args.get("repeat_interval", 1)),
        repeat_weekdays=args.get("repeat_weekdays"),
        config=config,
    )


def resolve_reminder_match(args: dict[str, Any], include_completed: bool) -> tuple[dict[str, Any], dict]:
    config = REM._load_config()
    lists = reminder_selected_lists({"lists": args.get("lists") or args.get("list")}, config) if args.get("lists") else (
        [_resolve_write_list(args.get("list"), config)] if args.get("list") else REM._resolve_selected_lists(None, config)
    )
    items = REM._query_matching_reminders(
        lists,
        reminder_id=args.get("id"),
        title=args.get("title"),
        include_completed=include_completed,
        config=config,
    )
    matches = REM._match_items(
        items,
        reminder_id=args.get("id"),
        title=args.get("title"),
        allow_completed=include_completed,
    )
    if include_completed and args.get("operation") == "reopen":
        matches = [item for item in items if item.get("completed")]
    if not matches:
        raise SystemExit("No matching reminder found.")
    if len(matches) > 1:
        raise SystemExit(
            "Multiple reminders match. Refine by list or use id.\n"
            + "\n".join(f"{item['id']} | {item['list']} | {item['name']}" for item in matches[:10])
        )
    return config, matches[0]


def _resolve_write_list(list_name: str | None, config: dict) -> str:
    return REM._resolve_write_list(list_name, config)


def handle_reminders_update(args: dict[str, Any]) -> Any:
    config, match = resolve_reminder_match(args, include_completed=bool(args.get("include_completed", False)))
    due_value = None
    if args.get("due_datetime"):
        due_value = REM._parse_iso_datetime(args["due_datetime"])
    elif args.get("date"):
        due_day = REM._parse_user_date(args["date"])
        due_time = REM._parse_clock(args.get("time") or config["defaultDueTime"])
        due_value = datetime.combine(due_day, due_time).astimezone()
    remind_value = None
    if args.get("remind_datetime"):
        remind_value = REM._parse_iso_datetime(args["remind_datetime"])
    elif args.get("remind_minutes_before") is not None:
        if due_value is None:
            raise SystemExit("remind_minutes_before requires a due date/time.")
        remind_value = due_value - timedelta(minutes=int(args["remind_minutes_before"]))
    move_to = _resolve_write_list(args["move_to_list"], config) if args.get("move_to_list") else None
    return REM._update_reminder(
        list_name=match["list"],
        reminder_id=match["id"],
        title=args.get("set_title"),
        body=args.get("set_body"),
        due_value=due_value,
        remind_value=remind_value,
        clear_due=bool(args.get("clear_due", False)),
        clear_remind=bool(args.get("clear_remind", False)),
        priority=args.get("priority"),
        move_to_list=move_to,
        repeat=args.get("repeat"),
        repeat_interval=int(args.get("repeat_interval", 1)),
        repeat_weekdays=args.get("repeat_weekdays"),
        clear_repeat=bool(args.get("clear_repeat", False)),
        config=config,
    )


def handle_reminders_done(args: dict[str, Any]) -> Any:
    config, match = resolve_reminder_match(args, include_completed=bool(args.get("include_completed", False)))
    return REM._complete_reminder(match["list"], match["id"], config)


def handle_reminders_reopen(args: dict[str, Any]) -> Any:
    config = REM._load_config()
    if args.get("id"):
        direct_list = _resolve_write_list(args.get("list"), config) if args.get("list") else None
        return REM._reopen_reminder(direct_list, args["id"], config)
    _, match = resolve_reminder_match({**args, "operation": "reopen"}, include_completed=True)
    return REM._reopen_reminder(match["list"], match["id"], config)


def handle_reminders_delete(args: dict[str, Any]) -> Any:
    config, match = resolve_reminder_match(args, include_completed=bool(args.get("include_completed", False)))
    return REM._delete_reminder(match["list"], match["id"], config)


def handle_reminders_move(args: dict[str, Any]) -> Any:
    return handle_reminders_update({**args, "move_to_list": args["to_list"]})


def handle_reminders_flag(args: dict[str, Any]) -> Any:
    _, match = resolve_reminder_match(args, include_completed=bool(args.get("include_completed", False)))
    config = REM._load_config()
    return REM._set_flagged(match, True, config)


def handle_reminders_unflag(args: dict[str, Any]) -> Any:
    _, match = resolve_reminder_match(args, include_completed=bool(args.get("include_completed", False)))
    config = REM._load_config()
    return REM._set_flagged(match, False, config)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


TOOLS: dict[str, Tool] = {
    "calendar_list_calendars": Tool(
        "calendar_list_calendars",
        "List available Apple Calendar calendars with aliases and defaults.",
        schema({}),
        handle_calendar_list_calendars,
    ),
    "calendar_list_events": Tool(
        "calendar_list_events",
        "List Apple Calendar events in a date window.",
        schema(
            {
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
                "date": {"type": "string"},
                "days": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            }
        ),
        handle_calendar_list_events,
    ),
    "calendar_find_events": Tool(
        "calendar_find_events",
        "Find Apple Calendar events by exact title on a given day.",
        schema(
            {
                "title": {"type": "string"},
                "date": {"type": "string"},
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
            },
            ["title", "date"],
        ),
        handle_calendar_find_events,
    ),
    "calendar_add_event": Tool(
        "calendar_add_event",
        "Create an Apple Calendar event with optional reminders and recurrence.",
        schema(
            {
                "calendar": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "duration_minutes": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "location": {"type": "string"},
                "notes": {"type": "string"},
                "if_free": {"type": "boolean"},
                "allow_conflict": {"type": "boolean"},
                "dedupe_by": {"type": "string"},
                "repeat": {"type": "string", "enum": ["daily", "weekly"]},
                "repeat_interval": {"type": "integer"},
                "repeat_weekdays": {"type": "string"},
            },
            ["title"],
        ),
        handle_calendar_add_event,
    ),
    "calendar_update_event": Tool(
        "calendar_update_event",
        "Update an Apple Calendar event by id or title and day.",
        schema(
            {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
                "set_title": {"type": "string"},
                "set_location": {"type": "string"},
                "set_notes": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "move_to_calendar": {"type": "string"},
                "repeat": {"type": "string", "enum": ["daily", "weekly"]},
                "repeat_interval": {"type": "integer"},
                "repeat_weekdays": {"type": "string"},
                "clear_repeat": {"type": "boolean"},
            }
        ),
        handle_calendar_update_event,
    ),
    "calendar_delete_event": Tool(
        "calendar_delete_event",
        "Delete an Apple Calendar event by id or title and day.",
        schema(
            {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
            }
        ),
        handle_calendar_delete_event,
    ),
    "calendar_set_reminders": Tool(
        "calendar_set_reminders",
        "Replace all reminders on an Apple Calendar event.",
        schema(
            {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
                "minutes_before": {"type": "array", "items": {"type": "integer"}},
            },
            ["minutes_before"],
        ),
        handle_calendar_set_reminders,
    ),
    "calendar_clear_reminders": Tool(
        "calendar_clear_reminders",
        "Remove all reminders from an Apple Calendar event.",
        schema(
            {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
            }
        ),
        handle_calendar_clear_reminders,
    ),
    "calendar_export_ics": Tool(
        "calendar_export_ics",
        "Export Apple Calendar events in a date window to ICS text or a file.",
        schema(
            {
                "calendars": {"type": "array", "items": {"type": "string"}},
                "all_calendars": {"type": "boolean"},
                "date": {"type": "string"},
                "days": {"type": "integer"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "output_path": {"type": "string"},
            }
        ),
        handle_calendar_export_ics,
    ),
    "calendar_import_ics": Tool(
        "calendar_import_ics",
        "Import VEVENTs from an ICS file into Apple Calendar.",
        schema(
            {
                "calendar": {"type": "string"},
                "input_path": {"type": "string"},
                "if_free": {"type": "boolean"},
                "allow_conflict": {"type": "boolean"},
                "dedupe_by": {"type": "string"},
            },
            ["input_path"],
        ),
        handle_calendar_import_ics,
    ),
    "reminders_list_lists": Tool(
        "reminders_list_lists",
        "List Apple Reminders lists with aliases and defaults.",
        schema({}),
        handle_reminders_list_lists,
    ),
    "reminders_list": Tool(
        "reminders_list",
        "List reminders from one or more lists.",
        schema(
            {
                "lists": {"type": "array", "items": {"type": "string"}},
                "include_completed": {"type": "boolean"},
                "limit": {"type": "integer"},
            }
        ),
        handle_reminders_list,
    ),
    "reminders_today": Tool(
        "reminders_today",
        "List reminders due today or with an alarm today.",
        schema({"lists": {"type": "array", "items": {"type": "string"}}}),
        handle_reminders_today,
    ),
    "reminders_overdue": Tool(
        "reminders_overdue",
        "List overdue reminders.",
        schema({"lists": {"type": "array", "items": {"type": "string"}}}),
        handle_reminders_overdue,
    ),
    "reminders_alarms_today": Tool(
        "reminders_alarms_today",
        "List reminders whose alarm fires today.",
        schema({"lists": {"type": "array", "items": {"type": "string"}}}),
        handle_reminders_alarms_today,
    ),
    "reminders_find": Tool(
        "reminders_find",
        "Find reminders by id or exact title.",
        schema(
            {
                "lists": {"type": "array", "items": {"type": "string"}},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "include_completed": {"type": "boolean"},
            }
        ),
        handle_reminders_find,
    ),
    "reminders_add": Tool(
        "reminders_add",
        "Create an Apple Reminder with optional alarm and recurrence.",
        schema(
            {
                "list": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "due_datetime": {"type": "string"},
                "remind_datetime": {"type": "string"},
                "remind_minutes_before": {"type": "integer"},
                "priority": {"type": "integer"},
                "flagged": {"type": "boolean"},
                "dedupe_by": {"type": "string"},
                "repeat": {"type": "string", "enum": ["daily", "weekly"]},
                "repeat_interval": {"type": "integer"},
                "repeat_weekdays": {"type": "string"},
            },
            ["title"],
        ),
        handle_reminders_add,
    ),
    "reminders_update": Tool(
        "reminders_update",
        "Update an Apple Reminder by id or exact title.",
        schema(
            {
                "list": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "include_completed": {"type": "boolean"},
                "set_title": {"type": "string"},
                "set_body": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "due_datetime": {"type": "string"},
                "clear_due": {"type": "boolean"},
                "remind_datetime": {"type": "string"},
                "remind_minutes_before": {"type": "integer"},
                "clear_remind": {"type": "boolean"},
                "priority": {"type": "integer"},
                "move_to_list": {"type": "string"},
                "repeat": {"type": "string", "enum": ["daily", "weekly"]},
                "repeat_interval": {"type": "integer"},
                "repeat_weekdays": {"type": "string"},
                "clear_repeat": {"type": "boolean"},
            }
        ),
        handle_reminders_update,
    ),
    "reminders_done": Tool(
        "reminders_done",
        "Mark an Apple Reminder complete.",
        schema(
            {
                "list": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "include_completed": {"type": "boolean"},
            }
        ),
        handle_reminders_done,
    ),
    "reminders_reopen": Tool(
        "reminders_reopen",
        "Reopen a completed Apple Reminder.",
        schema(
            {
                "list": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "include_completed": {"type": "boolean"},
            }
        ),
        handle_reminders_reopen,
    ),
    "reminders_delete": Tool(
        "reminders_delete",
        "Delete an Apple Reminder.",
        schema(
            {
                "list": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "include_completed": {"type": "boolean"},
            }
        ),
        handle_reminders_delete,
    ),
    "reminders_move_to_list": Tool(
        "reminders_move_to_list",
        "Move an Apple Reminder to another list.",
        schema(
            {
                "list": {"type": "string"},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "to_list": {"type": "string"},
            },
            ["to_list"],
        ),
        handle_reminders_move,
    ),
    "reminders_flag": Tool(
        "reminders_flag",
        "Flag an Apple Reminder.",
        schema({"list": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}}),
        handle_reminders_flag,
    ),
    "reminders_unflag": Tool(
        "reminders_unflag",
        "Unflag an Apple Reminder.",
        schema({"list": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}}),
        handle_reminders_unflag,
    ),
}


def tool_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        for tool in TOOLS.values()
    ]


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "apple-productivity-mcp", "version": "0.1.0"},
            },
        }

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tool_descriptors()}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        tool = TOOLS.get(name)
        if tool is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }
        try:
            payload = tool.handler(arguments)
            result = calendar_json_result(payload)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            result = error_result(str(exc))
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Unsupported method: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle_request(message)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(exc)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
