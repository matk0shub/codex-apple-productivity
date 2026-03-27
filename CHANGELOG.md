# Changelog

All notable changes to this repository will be documented in this file.

## 0.1.0 - 2026-03-27

### Added

- `apple-calendar` plugin with:
  - EventKit backend
  - agenda, free-window, search, update, delete
  - reminders management
  - recurring events
  - `.ics` export and import

- `apple-reminders` plugin with:
  - EventKit backend
  - due, overdue, alarms-today
  - add, update, done, reopen, delete
  - move between lists
  - recurring reminders
  - AppleScript fallback for flag and unflag

- `apple-productivity-mcp` local stdio MCP server exposing both domains

- integration smoke tests for:
  - CLI workflows
  - MCP workflows

- install helper for local path rewriting and marketplace setup
