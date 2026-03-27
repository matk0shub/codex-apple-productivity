# Codex Apple Productivity

Polished local Apple productivity tooling for Codex on macOS.

This repository contains:

- `apple-calendar`
- `apple-reminders`
- `apple-productivity-mcp`

Together they give you:

- native Apple Calendar automation through EventKit
- native Apple Reminders automation through EventKit
- a shared local MCP server for both domains
- Codex plugin manifests, skills, smoke tests, and installation helpers

## Included

| Component | Purpose |
| --- | --- |
| `plugins/apple-calendar` | Calendar plugin, skill, CLI wrapper, EventKit backend |
| `plugins/apple-reminders` | Reminders plugin, skill, CLI wrapper, EventKit backend |
| `plugins/apple-productivity-mcp` | Shared local stdio MCP server for both |
| `scripts/smoke_test_apple_cli.py` | End-to-end smoke test for both CLI tools |
| `scripts/smoke_test_apple_mcp.py` | End-to-end smoke test for the MCP server |
| `scripts/install_local_plugins.py` | Local install helper that rewrites `.mcp.json` paths for your clone |

## Why This Repo Exists

Codex plugins are excellent for local macOS automation, but once you want one shared backend for Calendar and Reminders, a local MCP server becomes the cleaner architecture.

This repo gives you both:

- human-friendly Codex plugins and skills
- one reusable MCP layer underneath

## Quick Start

1. Clone the repo anywhere on your Mac.
2. Run the installer:

```bash
/usr/bin/python3 scripts/install_local_plugins.py --repo-root "$(pwd)"
```

3. Make sure macOS permissions are enabled for:
   - Calendar
   - Reminders

   for the app that runs Codex.

4. Open the repo in Codex.
5. Use either:
   - the `apple-calendar` and `apple-reminders` plugin skills
   - or the `apple-productivity` MCP tools

## Install Notes

The installer does two useful things:

- rewrites all `.mcp.json` files to the real absolute path of your clone
- writes a ready-to-use local marketplace file under `.agents/plugins/marketplace.json`

It does not force-copy anything into your global Codex directories. That keeps install reversible and transparent.

## Smoke Tests

CLI smoke test:

```bash
/usr/bin/python3 scripts/smoke_test_apple_cli.py
```

MCP smoke test:

```bash
/usr/bin/python3 scripts/smoke_test_apple_mcp.py
```

Both tests create temporary items and clean them up afterward.

## Repository Design

The repo is intentionally split:

- plugin UX stays in each plugin folder
- shared backend integration goes through `apple-productivity-mcp`
- Calendar and Reminders keep their own configs and skills

That gives you a clean local UX today and a future path to a ChatGPT custom app later if needed.

## License

MIT. See [LICENSE](./LICENSE).

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).
