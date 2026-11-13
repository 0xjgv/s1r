"""Agent hook: on PreToolUse a System One scorer picks each subagent's model and effort from its prompt;
on PostToolUse and PostToolUseFailure the hook logs how the call turned out.

The pick goes on the Agent call's `model` and `effort` fields (Claude Code 2.1.292+); every other
input, including the agent type, is kept. The hook never replaces a `model` or `effort` the caller
wrote on the call: it routes only the field left out, and the log's `explicit` holds what the caller
set. The label <model>-<effort> is prefixed to `description` so the UI shows what the agent runs as,
with `(kept)` after a caller's value: opus(kept)-high. systemMessage names who chose what, as
routed-<model>-<effort> or routed-<effort> kept-<model>, in the session UI only; the model never
sees it. Agent calls run Claude models, so Codex is excluded.

The hook prints nothing, the call runs as written, and the log says why with applied=False when:
scoring fails; the agent type's definition file pins a model (anything but `inherit`), so the pin and
the session's effort apply; the caller set both fields; or the caller's `model` is not one the hook
routes to, so no routed effort is paired with it. Only the first of these scores the prompt.
Non-Agent calls pass through unchanged. Every pick record carries the call's tool_use_id and
session_id, which join it to its outcome record. An outcome prints nothing and never blocks the call.
"""

import json
import os
import sys
from pathlib import Path

from system_one_router import route

EXCLUDE = ("codex",)
MODELS = [models["claude"] for models in route.MODELS.values()]  # the models the hook routes to


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


def outcome(event: dict) -> dict:
    """The outcome record for a PostToolUse or PostToolUseFailure Agent event. A background launch
    reports status async_launched and no totals: its PostToolUse fires when the agent starts."""
    entry = {"ts": route.timestamp(), "kind": "outcome", "session_id": event.get("session_id"),
             "tool_use_id": event.get("tool_use_id")}
    if event.get("hook_event_name") == "PostToolUseFailure":
        entry.update(status="failed", interrupted=bool(event.get("is_interrupt")), duration_ms=event.get("duration_ms"))
    else:
        response = event.get("tool_response")
        response = response if isinstance(response, dict) else {}
        entry.update(status=response.get("status"), agent_id=response.get("agentId"),
                     resolved_model=response.get("resolvedModel"), duration_ms=response.get("totalDurationMs"),
                     total_tokens=response.get("totalTokens"), tool_uses=response.get("totalToolUseCount"))
    return {key: value for key, value in entry.items() if value is not None}


def main() -> int:
    event = json.load(sys.stdin)
    call = event.get("tool_input") or {}
    if event.get("tool_name") != "Agent":
        return 0
    if event.get("hook_event_name") in ("PostToolUse", "PostToolUseFailure"):
        route.log(outcome(event))
        return 0
    if not call.get("prompt"):
        return 0
    cwd, agent_type = event.get("cwd", ""), call.get("subagent_type")
    ids = {key: event[key] for key in ("tool_use_id", "session_id") if event.get(key)}
    explicit = {field: call[field] for field in ("model", "effort") if call.get(field)}
    pinned = pinned_model(agent_type, cwd) if agent_type else ""
    unrouted_model = "model" in explicit and explicit["model"] not in MODELS
    if pinned or unrouted_model or len(explicit) == 2:
        entry = route.record("agent", cwd, call["prompt"], EXCLUDE)
        entry.update(subagent_type=agent_type, explicit=explicit, applied=False, **ids)
        if pinned:
            entry["pinned"] = pinned
        route.log(entry)
        return 0
    model, effort, entry = route.route(call["prompt"], cwd, "agent", exclude=EXCLUDE, fallback=False,
                                       context={"explicit": explicit, **ids})
    if entry.get("applied") is False:
        return 0
    run = {"model": model, "effort": effort, **explicit}
    label = "-".join(f"{value}(kept)" if field in explicit else value for field, value in run.items())
    message = "-".join(["routed"] + [value for field, value in run.items() if field not in explicit])
    description = call.get("description")
    routed = {**call, **run, "description": f"{label} · {description}" if description else label}
    print(json.dumps({"systemMessage": message + "".join(f" kept-{value}" for value in explicit.values()),
                      "hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": routed}}))
    return 0

