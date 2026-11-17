"""s1r: pick a model and effort for Claude Code subagents and Codex tasks.

    s1r route [--exclude H] [--json] [--] <prompt>   print and log a pick
    s1r codex <prompt> [codex exec options]   run one routed Codex task
    s1r hook               Claude Code Agent hook: route on PreToolUse, log outcomes on PostToolUse(Failure) and SubagentStop
    s1r report [--since YYYY-MM-DD] [--json]   summarize Agent picks and outcomes from the log
    s1r status             show settings, their sources, and the provider chain's keys
    s1r update [VERSION]   install the latest release, or VERSION such as v0.2.0
    s1r version            print the installed version

Configuration: environment variables, then ~/.config/s1r/.env.
Routing log: ~/.local/state/s1r/beta_log.jsonl.
"""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from system_one_router import agent_hook, codex_run, model_provider, report, route, signature


def update(target: str) -> int:
    """Run the installer bundled with this version. It verifies the release's signature by
    calling back `update --verify-signature` with this interpreter, then installs the wheel."""
    environment = {**os.environ, "SYSTEM_ONE_ROUTER_VERIFIER": sys.executable}
    if target:
        environment["SYSTEM_ONE_ROUTER_VERSION"] = "v" + target.lstrip("v")
    return subprocess.run(["sh", str(Path(__file__).with_name("install.sh"))], env=environment).returncode


def verify_signature(public_key: str, message: str, signature_file: str) -> int:
    try:
        signature.verify(public_key, Path(message).read_bytes(), Path(signature_file).read_text())
    except (OSError, ValueError) as err:
        print(f"s1r update: {signature_file}: {err}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    command, *rest = (sys.argv[1:] if argv is None else argv) or [""]
    if command == "route":
        return route.main(rest)
    if command == "codex":
        return codex_run.main(rest)
    if command == "report":
        return report.main(rest)
    if command == "hook" and not rest:
        return agent_hook.main()
    if command == "status" and not rest:
        return model_provider.status()
    if command == "update" and len(rest) == 4 and rest[0] == "--verify-signature":
        return verify_signature(*rest[1:])
    if command == "update" and len(rest) <= 1 and rest[:1] != ["--verify-signature"]:
        return update(rest[0] if rest else "")
    if command == "version" and not rest:
        try:
            print(version("s1r"))
        except PackageNotFoundError:
            print("unknown (not installed)")
        return 0
    if command in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2
