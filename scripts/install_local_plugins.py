#!/usr/bin/env python3
"""Rewrite local MCP paths for this clone and verify the repo layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = ROOT / "plugins" / "apple-productivity-mcp" / "scripts" / "apple_productivity_mcp.py"


def rewrite_mcp_json(path: Path) -> None:
    payload = json.loads(path.read_text())
    for server in payload.get("mcpServers", {}).values():
        args = server.get("args", [])
        rewritten = []
        for arg in args:
            if isinstance(arg, str) and "apple_productivity_mcp.py" in arg:
                rewritten.append(str(SERVER_PATH))
            else:
                rewritten.append(arg)
        server["args"] = rewritten
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare local MCP paths for this clone.")
    parser.add_argument("--repo-root", help="Optional explicit repo root for validation")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else ROOT
    if repo_root != ROOT:
        raise SystemExit(f"Run this script from the cloned repository. Expected {ROOT}, got {repo_root}")

    if not SERVER_PATH.exists():
        raise SystemExit(f"Server script not found: {SERVER_PATH}")

    mcp_files = [
        ROOT / "plugins" / "apple-calendar" / ".mcp.json",
        ROOT / "plugins" / "apple-reminders" / ".mcp.json",
        ROOT / "plugins" / "apple-productivity-mcp" / ".mcp.json",
    ]
    for path in mcp_files:
        rewrite_mcp_json(path)

    print("Updated MCP config files:")
    for path in mcp_files:
        print(f"- {path}")
    print("\nNext steps:")
    print("1. Ensure Calendar and Reminders permissions are enabled for the app running Codex.")
    print("2. Open this repo in Codex.")
    print("3. Run smoke tests if desired:")
    print("   /usr/bin/python3 scripts/smoke_test_apple_cli.py")
    print("   /usr/bin/python3 scripts/smoke_test_apple_mcp.py")


if __name__ == "__main__":
    main()
