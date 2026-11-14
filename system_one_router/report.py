"""Summarize the Agent hook's picks and outcomes from the routing log, per picked model and effort.

    s1r report [--since YYYY-MM-DD] [--json]

picks: Agent pick records. applied: the share the hook applied (applied=False marks a pass-through;
records with no pick, such as a pinned agent, count under model and effort "-"). overrode: of the
records that log `explicit` and a tier, the share whose caller set a model other than the tier's.
Outcome records join picks by tool_use_id and count only for calls that ran as picked: applied, with
no model or effort of the caller's. A background launch joins its first SubagentStop by agent_id and
counts as completed: its duration is the time between the two records (second resolution), its tokens
come from the agent transcript the stop names, blank once Claude Code has deleted it. done, bg, and
failed count completed calls, background launches with no stop logged yet, and failures; error is
failed over done plus failed. The medians cover completed calls. Records from before a field existed
count only where they can.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, datetime

from system_one_router import route

COLUMNS = ("model", "effort", "picks", "applied", "overrode", "done", "bg", "failed", "error",
           "median_ms", "median_tokens")


def read(since: str = "") -> list[dict]:
    """The log's records at or after since (an ISO date), skipping lines that are not JSON objects."""
    records = []
    try:
        lines = route.LOG.read_text().splitlines()
    except FileNotFoundError:
        return records
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and str(record.get("ts", "")) >= since:
            records.append(record)
    return records


def summarize(records: list[dict]) -> list[dict]:
    """One row per picked model and effort, most picks first, then by name."""
    stops: dict = {}
    for r in records:
        if r.get("kind") == "outcome" and r.get("status") == "stopped" and r.get("agent_id"):
            stops.setdefault(r["agent_id"], r)
    outcomes = {r["tool_use_id"]: r for r in records if r.get("kind") == "outcome" and r.get("tool_use_id")}
    groups: dict = {}
    for record in records:
        if record.get("kind") != "agent":
            continue
        key = (record.get("model") or "-", record.get("effort") or "-")
        group = groups.setdefault(key, {"picks": 0, "applied": 0, "known": 0, "overrode": 0, "outcomes": []})
        group["picks"] += 1
        group["applied"] += record.get("applied") is not False
        explicit, tier = record.get("explicit"), record.get("tier")
        if isinstance(explicit, dict) and tier in route.MODELS and record.get("model"):
            group["known"] += 1
            group["overrode"] += bool(explicit.get("model")) and explicit["model"] != record["model"]
        ran_as_picked = record.get("applied") is not False and not explicit
        if ran_as_picked and record.get("tool_use_id") in outcomes:
            joined = outcomes[record["tool_use_id"]]
            group["outcomes"].append(finished(joined, stops.get(joined.get("agent_id"))))
    rows = []
    for (model, effort), group in sorted(groups.items(), key=lambda item: (-item[1]["picks"], item[0])):
        statuses = [o.get("status") for o in group["outcomes"]]
        completed = [o for o in group["outcomes"] if o.get("status") == "completed"]
        done, failed = statuses.count("completed"), statuses.count("failed")
        rows.append({
            "model": model, "effort": effort, "picks": group["picks"],
            "applied": ratio(group["applied"], group["picks"]), "overrode": ratio(group["overrode"], group["known"]),
            "done": done, "bg": statuses.count("async_launched"), "failed": failed, "error": ratio(failed, done + failed),
            "median_ms": median(o.get("duration_ms") for o in completed),
            "median_tokens": median(o.get("total_tokens") for o in completed),
        })
    return rows


def finished(outcome: dict, stop: dict | None) -> dict:
    """A background launch completed by its SubagentStop; any other outcome as logged."""
    if outcome.get("status") != "async_launched" or not stop:
        return outcome
    try:
        start, end = (datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ") for r in (outcome, stop))
        duration = (end - start).total_seconds() * 1000
    except (KeyError, TypeError, ValueError):
        duration = None
    return {**outcome, "status": "completed", "duration_ms": duration,
            "total_tokens": final_tokens(stop.get("transcript"))}


def final_tokens(transcript) -> int | None:
    """Input, cache, and output tokens of the transcript's last assistant message, which is what a
    foreground call's PostToolUse reports as totalTokens; None when the transcript cannot say."""
    usage = None
    try:
        with open(transcript) as lines:
            for line in lines:
                try:
                    record = json.loads(line)
                    if record.get("type") == "assistant" and isinstance(record["message"].get("usage"), dict):
                        usage = record["message"]["usage"]
                except (ValueError, AttributeError, KeyError, TypeError):
                    continue
    except (OSError, TypeError):
        return None
    if usage is None:
        return None
    return sum(usage.get(field) or 0 for field in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))


def ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def median(values) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return statistics.median(numbers) if numbers else None


def table(rows: list[dict]) -> str:
    """Rows as an aligned text table, with "-" for values the log cannot give."""
    def cell(column, value):
        if value is None:
            return "-"
        if column in ("applied", "overrode", "error"):
            return f"{value:.0%}"
        return str(round(value)) if isinstance(value, float) else str(value)
    cells = [list(COLUMNS)] + [[cell(column, row[column]) for column in COLUMNS] for row in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(COLUMNS))]
    return "\n".join("  ".join(value.ljust(width) if i < 2 else value.rjust(width)
                               for i, (value, width) in enumerate(zip(line, widths))).rstrip() for line in cells)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="s1r report", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=date.fromisoformat, help="Only records on or after this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Print the rows as a JSON array")
    args = parser.parse_args(argv)
    rows = summarize(read(args.since.isoformat() if args.since else ""))
    print(json.dumps(rows) if args.json else table(rows))
    return 0
