#!/usr/bin/env python3
"""Local helper for Calendar.app via osascript/JXA."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PLUGIN_ROOT / "config.json"
LOCK_PATH = PLUGIN_ROOT / ".calendar.lock"
BACKEND_SOURCE = SCRIPT_DIR / "apple_calendar_backend.swift"
BACKEND_BINARY = SCRIPT_DIR / ".apple_calendar_backend"
DEFAULT_DEDUPE_BY = "title+start+end"
DEFAULT_CONFIG = {
    "defaultWriteCalendar": "Home",
    "defaultReadCalendars": [
        "Home",
        "Work",
        "Family",
        "matej.stipcak@gmail.com",
    ],
    "ignoredCalendars": [
        "Formula 1",
        "FC Barcelona",
        "Scheduled Reminders",
        "Birthdays",
        "Ceské svátky",
        "České svátky",
        "Siri Suggestions",
    ],
    "calendarAliases": {
        "home": "Home",
        "doma": "Home",
        "work": "Work",
        "prace": "Work",
        "family": "Family",
        "rodina": "Family",
        "matej": "matej.stipcak@gmail.com",
        "mail": "matej.stipcak@gmail.com",
    },
    "lockTimeoutSeconds": 12,
    "commandTimeoutSeconds": 20,
    "perCalendarFailureMode": "skip",
    "defaultDayStart": "08:00",
    "defaultDayEnd": "22:00",
    "defaultSlotMinutes": 30,
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
    aliases = payload.get("calendarAliases", {})
    payload["calendarAliases"] = {
        _normalize_name(key): value for key, value in aliases.items() if isinstance(key, str)
    }
    payload["ignoredCalendars"] = list(payload.get("ignoredCalendars", []))
    payload["defaultReadCalendars"] = list(payload.get("defaultReadCalendars", []))
    return payload


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(
            "Invalid datetime. Use ISO 8601 like 2026-03-27T15:00:00."
        ) from exc


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


def _parse_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit("Invalid time. Use HH:MM or HH:MM:SS.") from exc


def _to_iso_local(value: datetime) -> str:
    if value.tzinfo is None:
        return value.astimezone().isoformat(timespec="seconds")
    return value.astimezone().isoformat(timespec="seconds")


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.astimezone()
    return value.astimezone()


def _from_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone()


def _format_event_line(event: dict) -> str:
    start_value = _from_iso_datetime(event["startDate"])
    end_value = _from_iso_datetime(event["endDate"])
    location = event.get("location")
    location_text = f" @ {location}" if location else ""
    return (
        f"{start_value:%H:%M}-{end_value:%H:%M} "
        f"{event['calendar']} | {event['summary']}{location_text}"
    )


def _event_to_text(event: dict) -> str:
    start_value = _from_iso_datetime(event["startDate"])
    end_value = _from_iso_datetime(event["endDate"])
    reminder_text = ""
    reminders = event.get("remindersMinutesBefore") or []
    if reminders:
        reminder_text = f" | reminders: {', '.join(str(item) for item in reminders)}m"
    return (
        f"{event['uid']} | {event['calendar']} | {event['summary']} | "
        f"{start_value:%Y-%m-%d %H:%M}-{end_value:%H:%M}{reminder_text}"
    )


def _format_agenda(events: list[dict], selected_calendars: list[str], day: date) -> str:
    header = f"{day:%A %Y-%m-%d}"
    if not events:
        calendars_text = ", ".join(selected_calendars)
        return f"{header}\nNo events in {calendars_text}."
    lines = [header]
    lines.extend(_format_event_line(event) for event in events)
    return "\n".join(lines)


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for start_value, end_value in intervals[1:]:
        current_start, current_end = merged[-1]
        if start_value <= current_end:
            merged[-1] = (current_start, max(current_end, end_value))
            continue
        merged.append((start_value, end_value))
    return merged


def _format_free_windows(
    events: list[dict],
    selected_calendars: list[str],
    day: date,
    day_start: datetime,
    day_end: datetime,
    slot_minutes: int,
) -> str:
    day_start = _as_local(day_start)
    day_end = _as_local(day_end)
    busy_intervals = []
    for event in events:
        event_start = max(_from_iso_datetime(event["startDate"]), day_start)
        event_end = min(_from_iso_datetime(event["endDate"]), day_end)
        if event_end > event_start:
            busy_intervals.append((event_start, event_end))

    merged_busy = _merge_intervals(busy_intervals)
    free_windows = []
    cursor = day_start
    minimum_gap = timedelta(minutes=slot_minutes)
    for busy_start, busy_end in merged_busy:
        if busy_start - cursor >= minimum_gap:
            free_windows.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if day_end - cursor >= minimum_gap:
        free_windows.append((cursor, day_end))

    header = f"Free windows for {day:%A %Y-%m-%d}"
    calendars_text = ", ".join(selected_calendars)
    if not free_windows:
        return (
            f"{header}\nNo free slots of at least {slot_minutes} minutes in {calendars_text}."
        )

    lines = [header, f"Calendars: {calendars_text}"]
    lines.extend(f"{start_value:%H:%M}-{end_value:%H:%M}" for start_value, end_value in free_windows)
    return "\n".join(lines)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_text(text: str) -> None:
    print(text)


def _calendar_lock(config: dict):
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
                    "Calendar.app is busy. Retry in a few seconds; the helper serializes access."
                )
            time_module.sleep(0.1)


def _run_jxa(script: str, config: dict) -> object:
    lock_handle = _calendar_lock(config)
    timeout_seconds = int(
        config.get("commandTimeoutSeconds", DEFAULT_CONFIG["commandTimeoutSeconds"])
    )
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
            raise SystemExit(
                f"Calendar command timed out after {timeout_seconds}s."
            ) from exc
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown osascript error."
        raise SystemExit(message)
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Unexpected output from osascript: {output}") from exc


def _ensure_backend_binary(config: dict) -> None:
    source_mtime = BACKEND_SOURCE.stat().st_mtime
    binary_mtime = BACKEND_BINARY.stat().st_mtime if BACKEND_BINARY.exists() else 0
    if BACKEND_BINARY.exists() and binary_mtime >= source_mtime:
        return
    compile_timeout = max(60, int(config.get("commandTimeoutSeconds", 20)) * 2)
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
    lock_handle = _calendar_lock(config)
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
            raise SystemExit(f"Calendar backend timed out after {timeout_seconds}s.") from exc
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unknown calendar backend error."
        raise SystemExit(message)
    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Unexpected output from calendar backend: {output}") from exc


def _js_string(value: str) -> str:
    return json.dumps(value)


@lru_cache(maxsize=1)
def _available_calendar_names() -> tuple[str, ...]:
    config = _load_config()
    payload = _run_backend(["list-calendars"], config)
    return tuple(payload or [])


def _resolve_single_calendar_name(value: str, available_names: tuple[str, ...], config: dict) -> str:
    alias_target = config["calendarAliases"].get(_normalize_name(value))
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
        raise SystemExit(f"Ambiguous calendar name '{value}': {', '.join(prefix_matches)}")
    raise SystemExit(f"Calendar not found: {value}")


def _resolve_selected_calendars(
    requested: list[str] | None,
    *,
    include_all: bool,
    config: dict,
) -> list[str]:
    available_names = _available_calendar_names()
    if include_all:
        return list(available_names)
    if requested:
        return [
            _resolve_single_calendar_name(calendar_name, available_names, config)
            for calendar_name in requested
        ]

    selected = [name for name in config["defaultReadCalendars"] if name in available_names]
    if selected:
        return selected

    ignored = {_normalize_name(name) for name in config["ignoredCalendars"]}
    fallback = [
        name for name in available_names if _normalize_name(name) not in ignored
    ]
    return fallback or list(available_names)


def _resolve_write_calendar(calendar_name: str | None, config: dict) -> str:
    target = calendar_name or config["defaultWriteCalendar"]
    return _resolve_single_calendar_name(target, _available_calendar_names(), config)


def _calendar_records(config: dict) -> list[dict]:
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in config["calendarAliases"].items():
        reverse_aliases.setdefault(target, []).append(alias)

    records = []
    available_names = _available_calendar_names()
    for name in available_names:
        records.append(
            {
                "name": name,
                "aliases": sorted(reverse_aliases.get(name, [])),
                "defaultWrite": name == config["defaultWriteCalendar"],
                "preferredRead": name in config["defaultReadCalendars"],
                "ignoredByDefault": name in config["ignoredCalendars"],
            }
        )
    return records


def _list_events_for_calendars(
    calendar_names: list[str],
    start_value: datetime,
    end_value: datetime,
    config: dict,
    *,
    tolerate_failures: bool = False,
) -> list[dict]:
    if end_value <= start_value:
        raise SystemExit("End of range must be after start of range.")
    try:
        payload = _run_backend(
            [
                "list-events",
                "--calendars-json",
                json.dumps(calendar_names),
                "--start-iso",
                _to_iso_local(start_value),
                "--end-iso",
                _to_iso_local(end_value),
            ],
            config,
        )
    except SystemExit:
        if tolerate_failures and config.get("perCalendarFailureMode", "skip") == "skip":
            return []
        raise
    events = payload or []
    events.sort(key=lambda item: item["startDate"])
    return events


def _same_signature(event: dict, title: str, start_value: datetime, end_value: datetime) -> bool:
    if _normalize_name(event.get("summary", "")) != _normalize_name(title):
        return False
    event_start = _from_iso_datetime(event["startDate"])
    event_end = _from_iso_datetime(event["endDate"])
    normalized_start = start_value.astimezone() if start_value.tzinfo else start_value
    normalized_end = end_value.astimezone() if end_value.tzinfo else end_value
    return event_start.replace(tzinfo=None) == normalized_start.replace(tzinfo=None) and event_end.replace(
        tzinfo=None
    ) == normalized_end.replace(tzinfo=None)


def _events_overlap(event: dict, start_value: datetime, end_value: datetime) -> bool:
    event_start = _from_iso_datetime(event["startDate"])
    event_end = _from_iso_datetime(event["endDate"])
    normalized_start = start_value.astimezone() if start_value.tzinfo else start_value
    normalized_end = end_value.astimezone() if end_value.tzinfo else end_value
    return event_start.replace(tzinfo=None) < normalized_end.replace(
        tzinfo=None
    ) and event_end.replace(tzinfo=None) > normalized_start.replace(tzinfo=None)


def _create_event(
    calendar_name: str,
    title: str,
    start_value: datetime,
    end_value: datetime,
    location: str | None,
    notes: str | None,
    *,
    if_free: bool,
    allow_conflict: bool,
    dedupe_by: str,
    repeat: str | None,
    repeat_interval: int,
    repeat_weekdays: str | None,
    config: dict,
) -> object:
    if end_value <= start_value:
        raise SystemExit("--end must be after --start.")

    existing_events = _list_events_for_calendars([calendar_name], start_value, end_value, config)
    duplicates = []
    conflicts = []
    for event in existing_events:
        if dedupe_by == DEFAULT_DEDUPE_BY and _same_signature(event, title, start_value, end_value):
            duplicates.append(event)
        if _events_overlap(event, start_value, end_value):
            conflicts.append(event)

    if duplicates:
        return {
            "created": False,
            "reason": "duplicate",
            "calendar": calendar_name,
            "event": duplicates[0],
        }

    if if_free and conflicts and not allow_conflict:
        return {
            "created": False,
            "reason": "conflict",
            "calendar": calendar_name,
            "conflicts": conflicts,
        }

    args = [
        "add",
        "--calendar",
        calendar_name,
        "--title",
        title,
        "--start-iso",
        _to_iso_local(start_value),
        "--end-iso",
        _to_iso_local(end_value),
    ]
    if location:
        args.extend(["--location", location])
    if notes:
        args.extend(["--notes", notes])
    if repeat:
        args.extend(["--repeat", repeat, "--repeat-interval", str(repeat_interval)])
    if repeat_weekdays:
        args.extend(["--repeat-weekdays", repeat_weekdays])
    payload = _run_backend(args, config)
    if conflicts and not allow_conflict:
        payload["warnings"] = {
            "conflicts": conflicts,
        }
    return payload


def _filter_events_by_title(events: list[dict], title: str) -> list[dict]:
    normalized = _normalize_name(title)
    return [event for event in events if _normalize_name(event.get("summary", "")) == normalized]


def _resolve_event_target(
    *,
    event_id: str | None,
    title: str | None,
    date_value: str | None,
    calendar_names: list[str],
    config: dict,
) -> dict:
    if event_id:
        payload = _run_backend(["get-event", "--id", event_id], config)
        if not payload:
            raise SystemExit("No matching event found.")
        return payload
    else:
        if not title or not date_value:
            raise SystemExit("Target requires --id or both --title and --date.")
        day = _parse_user_date(date_value)
        events = _list_events_for_calendars(
            calendar_names,
            datetime.combine(day, time.min),
            datetime.combine(day + timedelta(days=1), time.min),
            config,
            tolerate_failures=len(calendar_names) > 1,
        )
        matches = _filter_events_by_title(events, title)
    if not matches:
        raise SystemExit("No matching event found.")
    if len(matches) > 1:
        raise SystemExit(
            "Multiple events match. Refine by --calendar or use --id.\n"
            + "\n".join(_event_to_text(event) for event in matches[:10])
        )
    return matches[0]


def _update_event(
    *,
    target_event: dict,
    title: str | None,
    start_value: datetime | None,
    end_value: datetime | None,
    location: str | None,
    notes: str | None,
    move_to_calendar: str | None,
    repeat: str | None,
    repeat_interval: int,
    repeat_weekdays: str | None,
    clear_repeat: bool,
    config: dict,
) -> dict:
    payload = {}
    if title is not None:
        payload["summary"] = title
    if start_value is not None:
        payload["startDate"] = {"__date__": _to_iso_local(start_value)}
    if end_value is not None:
        payload["endDate"] = {"__date__": _to_iso_local(end_value)}
    if location is not None:
        payload["location"] = location
    if notes is not None:
        payload["description"] = notes

    args = ["update", "--id", target_event["uid"]]
    if title is not None:
        args.extend(["--title", title])
    if start_value is not None:
        args.extend(["--start-iso", _to_iso_local(start_value)])
    if end_value is not None:
        args.extend(["--end-iso", _to_iso_local(end_value)])
    if location is not None:
        args.extend(["--location", location])
    if notes is not None:
        args.extend(["--notes", notes])
    if move_to_calendar is not None:
        args.extend(["--move-to-calendar", move_to_calendar])
    if repeat:
        args.extend(["--repeat", repeat, "--repeat-interval", str(repeat_interval)])
    if repeat_weekdays:
        args.extend(["--repeat-weekdays", repeat_weekdays])
    if clear_repeat:
        args.extend(["--clear-repeat", "1"])
    _run_backend(args, config)
    refreshed_title = title if title is not None else target_event["summary"]
    refreshed_calendar = move_to_calendar if move_to_calendar is not None else target_event["calendar"]
    refreshed_day = (
        start_value.astimezone().date().isoformat()
        if start_value is not None
        else _from_iso_datetime(target_event["startDate"]).date().isoformat()
    )
    refreshed = _resolve_event_target(
        event_id=None,
        title=refreshed_title,
        date_value=refreshed_day,
        calendar_names=[refreshed_calendar],
        config=config,
    )
    return {"updated": True, **refreshed}


def _delete_event(target_event: dict, config: dict) -> dict:
    _run_backend(["delete", "--id", target_event["uid"]], config)
    return {
        "deleted": True,
        "calendar": target_event["calendar"],
        "uid": target_event["uid"],
        "summary": target_event["summary"],
    }


def _recreate_event_with_reminders(
    target_event: dict, offsets_minutes: list[int], config: dict
) -> dict:
    payload = _run_backend(
        ["update", "--id", target_event["uid"], "--alarms-json", json.dumps(offsets_minutes)],
        config,
    )
    return {"updated": True, **(payload or {})}


def _clear_event_reminders(target_event: dict, config: dict) -> dict:
    payload = _run_backend(["update", "--id", target_event["uid"], "--clear-alarms", "1"], config)
    return {"updated": True, **(payload or {})}


def _escape_ics_text(value: str | None) -> str:
    if not value:
        return ""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _unescape_ics_text(value: str | None) -> str | None:
    if value is None:
        return None
    return (
        value.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ics_dt(value: str) -> str:
    dt = _from_iso_datetime(value).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _rrule_from_record(event: dict) -> str | None:
    recurrence = event.get("recurrence")
    if not recurrence:
        return None
    parts = [f"FREQ={recurrence['frequency'].upper()}", f"INTERVAL={recurrence.get('interval', 1)}"]
    weekdays = recurrence.get("weekdays") or []
    if weekdays:
        parts.append(f"BYDAY={','.join(weekdays)}")
    return ";".join(parts)


def _build_ics(events: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//matty//apple-calendar//EN",
    ]
    for event in events:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.get('externalId') or event['uid']}",
                f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART:{_ics_dt(event['startDate'])}",
                f"DTEND:{_ics_dt(event['endDate'])}",
                f"SUMMARY:{_escape_ics_text(event.get('summary'))}",
            ]
        )
        if event.get("location"):
            lines.append(f"LOCATION:{_escape_ics_text(event['location'])}")
        if event.get("description"):
            lines.append(f"DESCRIPTION:{_escape_ics_text(event['description'])}")
        rrule = _rrule_from_record(event)
        if rrule:
            lines.append(f"RRULE:{rrule}")
        for minutes in event.get("remindersMinutesBefore") or []:
            lines.extend(
                [
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"TRIGGER:-PT{int(minutes)}M",
                    "END:VALARM",
                ]
            )
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"


def _parse_ics_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
    return datetime.strptime(value, "%Y%m%dT%H%M%S").astimezone()


def _parse_ics_events(path: str) -> list[dict]:
    raw_lines = Path(path).read_text().splitlines()
    unfolded: list[str] = []
    for line in raw_lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    events: list[dict] = []
    current: dict | None = None
    in_alarm = False
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current = {"remindersMinutesBefore": []}
            in_alarm = False
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            in_alarm = False
            continue
        if current is None:
            continue
        if line == "BEGIN:VALARM":
            in_alarm = True
            continue
        if line == "END:VALARM":
            in_alarm = False
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0]
        if in_alarm and key == "TRIGGER" and value.startswith("-PT") and value.endswith("M"):
            current["remindersMinutesBefore"].append(int(value[3:-1]))
            continue
        if key == "SUMMARY":
            current["summary"] = _unescape_ics_text(value)
        elif key == "LOCATION":
            current["location"] = _unescape_ics_text(value)
        elif key == "DESCRIPTION":
            current["description"] = _unescape_ics_text(value)
        elif key == "DTSTART":
            current["startDate"] = _parse_ics_datetime(value)
        elif key == "DTEND":
            current["endDate"] = _parse_ics_datetime(value)
        elif key == "RRULE":
            parts = dict(part.split("=", 1) for part in value.split(";") if "=" in part)
            current["repeat"] = parts.get("FREQ", "").lower() or None
            current["repeat_interval"] = int(parts.get("INTERVAL", "1"))
            current["repeat_weekdays"] = parts.get("BYDAY")
    return events


def _resolve_list_window(
    *,
    date_value: str | None,
    days: int,
    start_value: str | None,
    end_value: str | None,
    default_offset_days: int = 0,
) -> tuple[date, datetime, datetime]:
    using_date_window = date_value is not None or default_offset_days != 0
    using_explicit_window = start_value is not None or end_value is not None
    if using_date_window and using_explicit_window:
        raise SystemExit("Provide either a date-based window or both --start and --end.")

    if using_explicit_window:
        if not start_value or not end_value:
            raise SystemExit("Both --start and --end are required together.")
        parsed_start = _parse_iso_datetime(start_value)
        parsed_end = _parse_iso_datetime(end_value)
        if parsed_end <= parsed_start:
            raise SystemExit("--end must be after --start.")
        return parsed_start.date(), parsed_start, parsed_end

    if days < 1:
        raise SystemExit("--days must be at least 1.")
    day = _parse_user_date(date_value, offset_days=default_offset_days)
    window_start = datetime.combine(day, time.min)
    window_end = datetime.combine(day + timedelta(days=days), time.min)
    return day, window_start, window_end


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and create events in Calendar.app.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-calendars", help="List Calendar.app calendars")

    list_events = subparsers.add_parser("list-events", help="List events in a date window")
    list_events.add_argument("--calendar", action="append", help="Calendar name or alias")
    list_events.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    list_events.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
    list_events.add_argument("--days", type=int, default=1, help="Number of days from --date")
    list_events.add_argument("--start", help="Start datetime in ISO 8601")
    list_events.add_argument("--end", help="End datetime in ISO 8601")

    for command_name, offset_days in (("agenda", 0), ("today", 0), ("tomorrow", 1)):
        agenda_parser = subparsers.add_parser(command_name, help=f"Show {command_name} agenda")
        agenda_parser.add_argument("--calendar", action="append", help="Calendar name or alias")
        agenda_parser.add_argument(
            "--all-calendars", action="store_true", help="Query every calendar"
        )
        if command_name == "agenda":
            agenda_parser.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
            agenda_parser.add_argument(
                "--days", type=int, default=1, help="Number of days from --date"
            )
            agenda_parser.set_defaults(default_offset_days=offset_days)
        else:
            agenda_parser.add_argument(
                "--days", type=int, default=1, help="Number of days from today/tomorrow"
            )
            agenda_parser.set_defaults(default_offset_days=offset_days, date=None)

    free_parser = subparsers.add_parser("free", help="Show free windows across selected calendars")
    free_parser.add_argument("--calendar", action="append", help="Calendar name or alias")
    free_parser.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    free_parser.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
    free_parser.add_argument("--start-time", help="Day start time, default from config")
    free_parser.add_argument("--end-time", help="Day end time, default from config")
    free_parser.add_argument("--slot-minutes", type=int, help="Minimum free slot length")

    create_event = subparsers.add_parser("create-event", help="Create a Calendar.app event")
    create_event.add_argument("--calendar", help="Calendar name or alias")
    create_event.add_argument("--title", required=True, help="Event title")
    create_event.add_argument("--start", required=True, help="Start datetime in ISO 8601")
    create_event.add_argument("--end", required=True, help="End datetime in ISO 8601")
    create_event.add_argument("--location", help="Event location")
    create_event.add_argument("--notes", help="Event notes/description")
    create_event.add_argument("--repeat", choices=["daily", "weekly"], help="Repeat frequency")
    create_event.add_argument("--repeat-interval", type=int, default=1, help="Repeat interval")
    create_event.add_argument("--repeat-weekdays", help="Comma-separated weekdays like MO,WE,FR")
    create_event.add_argument(
        "--if-free", action="store_true", help="Skip create when the slot already conflicts"
    )
    create_event.add_argument(
        "--allow-conflict", action="store_true", help="Allow create even when conflicts exist"
    )
    create_event.add_argument(
        "--dedupe-by",
        choices=[DEFAULT_DEDUPE_BY, "none"],
        default=DEFAULT_DEDUPE_BY,
        help="Duplicate detection rule before create",
    )

    add_event = subparsers.add_parser("add", help="Quick add command with local date and time")
    add_event.add_argument("--calendar", help="Calendar name or alias")
    add_event.add_argument("--title", required=True, help="Event title")
    add_event.add_argument("--date", required=True, help="Date in YYYY-MM-DD, today, or tomorrow")
    add_event.add_argument("--start-time", required=True, help="Start time in HH:MM")
    add_event.add_argument("--end-time", help="End time in HH:MM")
    add_event.add_argument(
        "--duration-minutes", type=int, default=60, help="Duration when --end-time is omitted"
    )
    add_event.add_argument("--location", help="Event location")
    add_event.add_argument("--notes", help="Event notes/description")
    add_event.add_argument("--repeat", choices=["daily", "weekly"], help="Repeat frequency")
    add_event.add_argument("--repeat-interval", type=int, default=1, help="Repeat interval")
    add_event.add_argument("--repeat-weekdays", help="Comma-separated weekdays like MO,WE,FR")
    add_event.add_argument(
        "--if-free", action="store_true", help="Skip create when the slot already conflicts"
    )
    add_event.add_argument(
        "--allow-conflict", action="store_true", help="Allow create even when conflicts exist"
    )
    add_event.add_argument(
        "--dedupe-by",
        choices=[DEFAULT_DEDUPE_BY, "none"],
        default=DEFAULT_DEDUPE_BY,
        help="Duplicate detection rule before create",
    )

    find_events = subparsers.add_parser("find-events", help="Find events by title on a given day")
    find_events.add_argument("--calendar", action="append", help="Calendar name or alias")
    find_events.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    find_events.add_argument("--title", required=True, help="Exact event title")
    find_events.add_argument("--date", required=True, help="Date in YYYY-MM-DD, today, or tomorrow")

    update_event = subparsers.add_parser("update-event", help="Update an existing event")
    update_event.add_argument("--calendar", action="append", help="Calendar name or alias for search scope")
    update_event.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    update_event.add_argument("--id", help="Exact event uid")
    update_event.add_argument("--title", help="Exact existing event title")
    update_event.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
    update_event.add_argument("--set-title", help="New event title")
    update_event.add_argument("--set-location", help="New event location")
    update_event.add_argument("--set-notes", help="New event notes/description")
    update_event.add_argument("--start", help="New start datetime in ISO 8601")
    update_event.add_argument("--end", help="New end datetime in ISO 8601")
    update_event.add_argument("--move-to-calendar", help="Move the event to another calendar")
    update_event.add_argument("--repeat", choices=["daily", "weekly"], help="Repeat frequency")
    update_event.add_argument("--repeat-interval", type=int, default=1, help="Repeat interval")
    update_event.add_argument("--repeat-weekdays", help="Comma-separated weekdays like MO,WE,FR")
    update_event.add_argument("--clear-repeat", action="store_true", help="Clear recurrence")

    delete_event = subparsers.add_parser("delete-event", help="Delete an existing event")
    delete_event.add_argument("--calendar", action="append", help="Calendar name or alias for search scope")
    delete_event.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    delete_event.add_argument("--id", help="Exact event uid")
    delete_event.add_argument("--title", help="Exact existing event title")
    delete_event.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")

    set_reminder = subparsers.add_parser("set-reminder", help="Replace event reminders")
    set_reminder.add_argument("--calendar", action="append", help="Calendar name or alias for search scope")
    set_reminder.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    set_reminder.add_argument("--id", help="Exact event uid")
    set_reminder.add_argument("--title", help="Exact existing event title")
    set_reminder.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
    set_reminder.add_argument(
        "--minutes-before",
        action="append",
        type=int,
        required=True,
        help="Reminder offset in minutes before start; may be repeated",
    )

    clear_reminder = subparsers.add_parser("clear-reminders", help="Remove all event reminders")
    clear_reminder.add_argument("--calendar", action="append", help="Calendar name or alias for search scope")
    clear_reminder.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    clear_reminder.add_argument("--id", help="Exact event uid")
    clear_reminder.add_argument("--title", help="Exact existing event title")
    clear_reminder.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")

    export_ics = subparsers.add_parser("export-ics", help="Export events in a date window to .ics")
    export_ics.add_argument("--calendar", action="append", help="Calendar name or alias")
    export_ics.add_argument("--all-calendars", action="store_true", help="Query every calendar")
    export_ics.add_argument("--date", help="Date in YYYY-MM-DD, today, or tomorrow")
    export_ics.add_argument("--days", type=int, default=1, help="Number of days from --date")
    export_ics.add_argument("--start", help="Start datetime in ISO 8601")
    export_ics.add_argument("--end", help="End datetime in ISO 8601")
    export_ics.add_argument("--output", required=True, help="Path to output .ics file")

    import_ics = subparsers.add_parser("import-ics", help="Import VEVENTs from .ics")
    import_ics.add_argument("--calendar", help="Target calendar name or alias")
    import_ics.add_argument("--input", required=True, help="Path to input .ics file")
    import_ics.add_argument("--if-free", action="store_true", help="Skip conflicting events")
    import_ics.add_argument("--allow-conflict", action="store_true", help="Allow overlapping imports")
    import_ics.add_argument(
        "--dedupe-by",
        choices=[DEFAULT_DEDUPE_BY, "none"],
        default=DEFAULT_DEDUPE_BY,
        help="Duplicate detection rule before import",
    )

    return parser


def main() -> None:
    _require_macos()
    config = _load_config()
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "list-calendars":
        _print_json(_calendar_records(config))
        return

    if args.command == "list-events":
        _, start_value, end_value = _resolve_list_window(
            date_value=args.date,
            days=args.days,
            start_value=args.start,
            end_value=args.end,
        )
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        _print_json(
            _list_events_for_calendars(
                selected_calendars,
                start_value,
                end_value,
                config,
                tolerate_failures=len(selected_calendars) > 1,
            )
        )
        return

    if args.command in {"agenda", "today", "tomorrow"}:
        date_value = getattr(args, "date", None)
        _, start_value, end_value = _resolve_list_window(
            date_value=date_value,
            days=args.days,
            start_value=None,
            end_value=None,
            default_offset_days=getattr(args, "default_offset_days", 0),
        )
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        events = _list_events_for_calendars(
            selected_calendars,
            start_value,
            end_value,
            config,
            tolerate_failures=len(selected_calendars) > 1,
        )
        _print_text(_format_agenda(events, selected_calendars, start_value.date()))
        return

    if args.command == "free":
        day = _parse_user_date(args.date)
        day_start = _as_local(
            datetime.combine(day, _parse_clock(args.start_time or config["defaultDayStart"]))
        )
        day_end = _as_local(
            datetime.combine(day, _parse_clock(args.end_time or config["defaultDayEnd"]))
        )
        if day_end <= day_start:
            raise SystemExit("--end-time must be after --start-time.")
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        events = _list_events_for_calendars(
            selected_calendars,
            day_start,
            day_end,
            config,
            tolerate_failures=len(selected_calendars) > 1,
        )
        _print_text(
            _format_free_windows(
                events,
                selected_calendars,
                day,
                day_start,
                day_end,
                args.slot_minutes or int(config["defaultSlotMinutes"]),
            )
        )
        return

    if args.command == "create-event":
        calendar_name = _resolve_write_calendar(args.calendar, config)
        start_value = _parse_iso_datetime(args.start)
        end_value = _parse_iso_datetime(args.end)
        _print_json(
            _create_event(
                calendar_name=calendar_name,
                title=args.title,
                start_value=start_value,
                end_value=end_value,
                location=args.location,
                notes=args.notes,
                if_free=args.if_free,
                allow_conflict=args.allow_conflict,
                dedupe_by=args.dedupe_by,
                repeat=args.repeat,
                repeat_interval=args.repeat_interval,
                repeat_weekdays=args.repeat_weekdays,
                config=config,
            )
        )
        return

    if args.command == "add":
        calendar_name = _resolve_write_calendar(args.calendar, config)
        day = _parse_user_date(args.date)
        start_clock = _parse_clock(args.start_time)
        start_value = datetime.combine(day, start_clock)
        if args.end_time:
            end_value = datetime.combine(day, _parse_clock(args.end_time))
        else:
            if args.duration_minutes < 1:
                raise SystemExit("--duration-minutes must be at least 1.")
            end_value = start_value + timedelta(minutes=args.duration_minutes)
        _print_json(
            _create_event(
                calendar_name=calendar_name,
                title=args.title,
                start_value=start_value,
                end_value=end_value,
                location=args.location,
                notes=args.notes,
                if_free=args.if_free,
                allow_conflict=args.allow_conflict,
                dedupe_by=args.dedupe_by,
                repeat=args.repeat,
                repeat_interval=args.repeat_interval,
                repeat_weekdays=args.repeat_weekdays,
                config=config,
            )
        )
        return

    if args.command == "find-events":
        day = _parse_user_date(args.date)
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        events = _list_events_for_calendars(
            selected_calendars,
            datetime.combine(day, time.min),
            datetime.combine(day + timedelta(days=1), time.min),
            config,
            tolerate_failures=len(selected_calendars) > 1,
        )
        _print_json(_filter_events_by_title(events, args.title))
        return

    if args.command in {"update-event", "delete-event", "set-reminder", "clear-reminders"}:
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        target = _resolve_event_target(
            event_id=getattr(args, "id", None),
            title=getattr(args, "title", None),
            date_value=getattr(args, "date", None),
            calendar_names=selected_calendars,
            config=config,
        )
        if args.command == "delete-event":
            _print_json(_delete_event(target, config))
            return
        if args.command == "set-reminder":
            offsets = [value for value in args.minutes_before if value >= 0]
            if not offsets:
                raise SystemExit("Provide at least one non-negative --minutes-before value.")
            _print_json(_recreate_event_with_reminders(target, offsets, config))
            return
        if args.command == "clear-reminders":
            _print_json(_clear_event_reminders(target, config))
            return

        start_value = _parse_iso_datetime(args.start) if args.start else None
        end_value = _parse_iso_datetime(args.end) if args.end else None
        if (start_value is None) != (end_value is None):
            raise SystemExit("Provide both --start and --end together when changing event timing.")
        if start_value and end_value and end_value <= start_value:
            raise SystemExit("--end must be after --start.")
        move_to_calendar = (
            _resolve_write_calendar(args.move_to_calendar, config) if args.move_to_calendar else None
        )
        _print_json(
            _update_event(
                target_event=target,
                title=args.set_title,
                start_value=start_value,
                end_value=end_value,
                location=args.set_location,
                notes=args.set_notes,
                move_to_calendar=move_to_calendar,
                repeat=args.repeat,
                repeat_interval=args.repeat_interval,
                repeat_weekdays=args.repeat_weekdays,
                clear_repeat=args.clear_repeat,
                config=config,
            )
        )
        return

    if args.command == "export-ics":
        _, start_value, end_value = _resolve_list_window(
            date_value=args.date,
            days=args.days,
            start_value=args.start,
            end_value=args.end,
        )
        selected_calendars = _resolve_selected_calendars(
            args.calendar, include_all=args.all_calendars, config=config
        )
        events = _list_events_for_calendars(
            selected_calendars,
            start_value,
            end_value,
            config,
            tolerate_failures=len(selected_calendars) > 1,
        )
        Path(args.output).write_text(_build_ics(events))
        _print_json({"exported": True, "count": len(events), "output": args.output})
        return

    if args.command == "import-ics":
        calendar_name = _resolve_write_calendar(args.calendar, config)
        imported = []
        for event in _parse_ics_events(args.input):
            payload = _create_event(
                calendar_name=calendar_name,
                title=event["summary"],
                start_value=event["startDate"],
                end_value=event["endDate"],
                location=event.get("location"),
                notes=event.get("description"),
                if_free=args.if_free,
                allow_conflict=args.allow_conflict,
                dedupe_by=args.dedupe_by,
                repeat=event.get("repeat"),
                repeat_interval=event.get("repeat_interval", 1),
                repeat_weekdays=event.get("repeat_weekdays"),
                config=config,
            )
            reminder_offsets = event.get("remindersMinutesBefore") or []
            if payload.get("created") and reminder_offsets:
                payload = _run_backend(
                    ["update", "--id", payload["uid"], "--alarms-json", json.dumps(reminder_offsets)],
                    config,
                )
            imported.append(payload)
        _print_json({"imported": True, "count": len(imported), "events": imported})
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
