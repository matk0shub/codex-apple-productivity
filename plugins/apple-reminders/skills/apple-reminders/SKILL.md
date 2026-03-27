---
name: apple-reminders
description: Read and manage Apple Reminders through a local macOS helper. Use when the user wants to inspect reminder lists, review due or overdue reminders, add a new reminder, or mark an existing reminder complete from Codex.
---

# Apple Reminders

## Overview

Use this skill to work with the local macOS Reminders app through the bundled helper at `../../scripts/apple_reminders.py`. The helper uses a Swift/EventKit backend for core operations and AppleScript only for `flag`/`unflag`. Prefer the quick commands `today`, `overdue`, `due`, `alarms-today`, `add`, and `done` over raw JSON unless you need exact reminder fields.

## Workflow

1. Confirm the task is meant for Apple Reminders on macOS, not calendar events or another task app.
2. Never run multiple Reminders helper commands in parallel. The helper serializes access to Reminders.app with a lock and timeout.
3. For triage, start with `today` or `overdue`. These default to the configured lists in `../../config.json`.
4. Use `list-lists` to ground list names and aliases before writing if the target list is not obvious.
5. Prefer `add` for new reminders and `done` for completion. Use `list` only when you need a fuller list or raw JSON output.
6. Use `update`, `move-to-list`, `flag`, `unflag`, `reopen`, and `delete` for lifecycle changes to an existing reminder.
7. If there are multiple similarly named reminders, identify the right one explicitly before mutating it.

## Commands

List reminder lists and aliases:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py list-lists
```

Show reminders due today:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py today
```

Show overdue reminders:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py overdue
```

Show reminders whose alarm fires today:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py alarms-today
```

List reminders from one list alias:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py list \
  --list todo
```

Add a new reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py add \
  --list todo \
  --title "Zavolat Petrovi" \
  --date tomorrow \
  --time 09:00 \
  --remind-minutes-before 30
```

Add a recurring reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py add \
  --list todo \
  --title "Týdenní report" \
  --date tomorrow \
  --time 09:00 \
  --repeat weekly \
  --repeat-weekdays MO,WE,FR
```

Mark a reminder complete:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py done \
  --list todo \
  --title "Zavolat Petrovi"
```

Update a reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py update \
  --list todo \
  --title "Zavolat Petrovi" \
  --set-body "Po obědě" \
  --priority 5
```

Move a reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py move-to-list \
  --list todo \
  --title "Zavolat Petrovi" \
  --to-list dovolena
```

Flag a reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py flag \
  --list todo \
  --title "Zavolat Petrovi"
```

Delete a reminder:

```bash
/usr/bin/python3 ../../scripts/apple_reminders.py delete \
  --list todo \
  --title "Zavolat Petrovi"
```

## Safety

- Treat `add` and `done` as write operations.
- Do not invent list names. Use the exact list or alias returned by `list-lists`.
- Duplicate detection defaults to `title+list+dueDate`. Set `--dedupe-by none` only when a true duplicate is intended.
- `done` requires a unique target. If several reminders match, refine by list or use the reminder `id`.
- `flag` and `unflag` are implemented through AppleScript because EventKit does not expose the flagged property.
- macOS may prompt for Reminders automation access the first time the helper runs.

## Output Conventions

- Use exact local dates and times when summarizing due reminders.
- Prefer compact human-readable output for `today`, `overdue`, and `due`.
- When creating a reminder, echo the final list, title, due date, and remind time if present.
- When completing a reminder, echo the matched reminder `id` and title.
