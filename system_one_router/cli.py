"""s1r: pick a model and effort for Claude Code subagents and Codex tasks.

    s1r route [--exclude H] [--role R] [--json] [--] <prompt>   print and log a pick
    s1r codex <prompt> [codex exec options]   run one routed Codex task
    s1r hook               Claude Code PreToolUse hook: route the Agent call on stdin
    s1r status             show settings, their sources, and the provider chain's keys
    s1r update [VERSION]   install the latest release, or VERSION such as v0.2.0
    s1r version            print the installed version

Configuration: environment variables, then ~/.config/s1r/.env.
Routing log: ~/.local/state/s1r/beta_log.jsonl.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from system_one_router import agent_hook, codex_run, model_provider, route

RELEASES = "https://github.com/0xjgv/s1r/releases"


def update(target: str) -> int:
    """Fetch the release's install.sh and run it; it verifies and installs the wheel.
    SYSTEM_ONE_ROUTER_DOWNLOAD_BASE may name another release URL or a local directory, as for install.sh."""
    base = os.environ.get("SYSTEM_ONE_ROUTER_DOWNLOAD_BASE", RELEASES).rstrip("/")
    tag = "v" + target.lstrip("v") if target else ""
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "install.sh"
        try:
            if base.startswith("/"):
                shutil.copyfile(Path(base) / "install.sh", script)
            else:
                url = f"{base}/download/{tag}/install.sh" if tag else f"{base}/latest/download/install.sh"
                with urllib.request.urlopen(url, timeout=30) as response:
                    script.write_bytes(response.read())
        except OSError as err:
            print(f"s1r update: cannot fetch install.sh from {base}: {err}", file=sys.stderr)
            return 1
        environment = {**os.environ, "SYSTEM_ONE_ROUTER_VERSION": tag} if tag else None
        return subprocess.run(["sh", str(script)], env=environment).returncode


def main(argv: list[str] | None = None) -> int:
    command, *rest = (sys.argv[1:] if argv is None else argv) or [""]
    if command == "route":
        return route.main(rest)
    if command == "codex":
        return codex_run.main(rest)
    if command == "hook" and not rest:
        return agent_hook.main()
    if command == "status" and not rest:
        return model_provider.status()
    if command == "update" and len(rest) <= 1:
        return update(rest[0] if rest else "")
    if command == "version" and not rest:
        try:
            print(version("system-one-router"))
        except PackageNotFoundError:
            print("unknown (not installed)")
        return 0
    if command in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2
