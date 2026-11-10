"""PreToolUse hook: a System One scorer picks each subagent's model and effort from its prompt.

The pick goes on the Agent call's `model` and `effort` fields (Claude Code 2.1.292+); every other
input, including the agent type, is kept. The route label <model>-<effort> is prefixed to
`description` so the UI shows it. systemMessage shows routed-<model>-<effort> in the session UI
only; the model never sees it. Agent calls run Claude models, so Codex is excluded. When scoring
fails, the hook prints nothing and the call runs as written; the log marks the pick applied=False.
An agent type whose definition file pins a model (anything but `inherit`) also passes through
unscored, so the pin and the session's effort apply; the log records the pin with applied=False.
Non-Agent calls pass through unchanged.
"""

import json
import os
import sys
from pathlib import Path

from system_one_router import route

EXCLUDE = ("codex",)


def pinned_model(agent_type: str, cwd: str) -> str:
    """The model an agent definition pins, or "" when none does. Project agents (<cwd>/.claude/agents)
    take precedence over user agents (${CLAUDE_CONFIG_DIR:-~/.claude}/agents); the frontmatter
    `name:` identifies the agent, not the file name."""
    user = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for directory in ([Path(cwd) / ".claude"] if cwd else []) + [user]:
        for path in sorted((directory / "agents").rglob("*.md")):
            try:
                lines = path.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            if lines[:1] != ["---"] or "---" not in lines[1:]:
                continue
            fields = {}
            for line in lines[1:lines.index("---", 1)]:
                key, separator, value = line.partition(":")
                if separator and not line[:1].isspace():
                    fields[key.strip()] = value.strip().strip("\"'")
            if fields.get("name") == agent_type:
                model = fields.get("model", "")
                return "" if model == "inherit" else model
    return ""


def main() -> int:
    event = json.load(sys.stdin)
    call = event.get("tool_input") or {}
    if event.get("tool_name") != "Agent" or not call.get("prompt"):
        return 0
    cwd, agent_type = event.get("cwd", ""), call.get("subagent_type")
    pinned = pinned_model(agent_type, cwd) if agent_type else ""
    if pinned:
        entry = route.record("agent", cwd, call["prompt"], EXCLUDE)
        entry.update(subagent_type=agent_type, pinned=pinned, applied=False)
        route.log(entry)
        return 0
    model, effort, entry = route.route(call["prompt"], cwd, "agent", exclude=EXCLUDE, fallback=False)
    if entry.get("applied") is False:
        return 0
    label = f"{model}-{effort}"
    description = call.get("description")
    routed = {**call, "model": model, "effort": effort,
              "description": f"{label} · {description}" if description else label}
    print(json.dumps({"systemMessage": f"routed-{model}-{effort}",
                      "hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": routed}}))
    return 0

