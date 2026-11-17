"""Summarize the Agent hook's picks and outcomes from the routing log, per picked model and effort.

    s1r report [--since YYYY-MM-DD] [--json] [--outcomes]

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

--outcomes joins the same calls to lead tasks instead: a call belongs to the task in a cos ledger
(every directory under ~/.local/state/cos, read through `ledger board --all --json` and `ledger stats
--json`) open at the call (created at or before it, not ended before it) whose `worktree` field holds
the call's cwd: the deepest worktree, then the task created last. Per routed model and effort it
prints calls, the calls matched to a task, tasks touched, how many of those tasks are done, their
median cycle time in minutes (done tasks only: an open task's cycle still grows and a dropped one
did not ship), and their reworks and blocks summed. A task touched by two picks counts under both.
The matched and unmatched call totals follow the table.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from system_one_router import route

EFFORT_LABELS = {"low": "medium", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}  # clamped to EFFORTS
COS = route.LOG.parent.parent / "cos"
OUTCOME_COLUMNS = ("model", "effort", "calls", "matched", "tasks", "done", "median_cycle_min", "reworks", "blocks")
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
        if ran_as_picked(record) and record.get("tool_use_id") in outcomes:
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


def ran_as_picked(record: dict) -> bool:
    """An applied Agent pick whose caller set neither model nor effort."""
    return record.get("kind") == "agent" and record.get("applied") is not False and not record.get("explicit")


def ledger_json(directory: Path, *args: str):
    """The parsed output of `ledger <args> --json` against one ledger directory; None, with a note on
    stderr, when ledger is missing, fails, or prints something other than JSON."""
    try:
        done = subprocess.run(["ledger", *args, "--json"], env={**os.environ, "LEDGER_DIR": str(directory)},
                              capture_output=True, text=True, timeout=30)
        if done.returncode == 0:
            return json.loads(done.stdout)
        note = done.stderr.strip()
    except (OSError, subprocess.TimeoutExpired, ValueError) as err:
        note = str(err)
    print(f"s1r report: skipped ledger {directory}: {note}", file=sys.stderr)
    return None


def ledger_tasks() -> list[dict]:
    """Every task with a worktree in every ledger under COS, with its stats merged in."""
    tasks = []
    if not COS.is_dir():
        print(f"s1r report: no ledgers: {COS} is not a directory", file=sys.stderr)
    for directory in sorted(COS.glob("*/")) if COS.is_dir() else []:
        if not (directory / "events.jsonl").exists():
            continue
        board = ledger_json(directory, "board", "--all")
        stats = ledger_json(directory, "stats") if isinstance(board, dict) else None
        if not isinstance(stats, dict):
            continue
        by_id = {task.get("id"): task for task in stats.get("tasks", [])}
        for task in board.get("tasks", []):
            worktree = (task.get("fields") or {}).get("worktree")
            if worktree:
                tasks.append({**by_id.get(task.get("id"), {}), "key": (directory.name, task.get("id")),
                              "created": task.get("created") or "", "worktree": os.path.normpath(worktree)})
    return tasks


def task_for(cwd, ts: str, tasks: list[dict]) -> dict | None:
    """Of the tasks open at ts (created at or before it, not ended before it) whose worktree holds cwd,
    the deepest worktree's latest task."""
    if not cwd:
        return None
    cwd = os.path.normpath(cwd)
    holding = [task for task in tasks if task["created"] <= ts <= (task.get("ended") or ts)
               and (cwd == task["worktree"] or cwd.startswith(task["worktree"].rstrip(os.sep) + os.sep))]
    return max(holding, key=lambda task: (len(task["worktree"]), task["created"]), default=None)


def task_outcomes(records: list[dict], tasks: list[dict]) -> dict:
    """One row per routed model and effort over the calls that ran as picked, joined to ledger tasks."""
    groups: dict = {}
    matched = unmatched = 0
    for record in filter(ran_as_picked, records):
        group = groups.setdefault((record.get("model") or "-", record.get("effort") or "-"), {"calls": 0, "matched": 0, "tasks": {}})
        group["calls"] += 1
        task = task_for(record.get("cwd"), str(record.get("ts", "")), tasks)
        if task is None:
            unmatched += 1
            continue
        matched += 1
        group["matched"] += 1
        group["tasks"][task["key"]] = task
    rows = []
    for (model, effort), group in sorted(groups.items(), key=lambda item: (-item[1]["calls"], item[0])):
        touched = list(group["tasks"].values())
        done = [task for task in touched if task.get("state") == "done"]
        cycle = median(task.get("cycle_s") for task in done)
        rows.append({"model": model, "effort": effort, "calls": group["calls"], "matched": group["matched"], "tasks": len(touched),
                     "done": len(done), "median_cycle_min": None if cycle is None else cycle / 60,
                     "reworks": sum(task.get("reworks") or 0 for task in touched),
                     "blocks": sum(task.get("blocks") or 0 for task in touched)})
    return {"rows": rows, "matched": matched, "unmatched": unmatched}


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


def table(rows: list[dict], columns: tuple = COLUMNS) -> str:
    """Rows as an aligned text table, with "-" for values the log cannot give."""
    def cell(column, value):
        if value is None:
            return "-"
        if column in ("applied", "overrode", "error"):
            return f"{value:.0%}"
        return str(round(value)) if isinstance(value, float) else str(value)
    cells = [list(columns)] + [[cell(column, row[column]) for column in columns] for row in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(columns))]
    return "\n".join("  ".join(value.ljust(width) if i < 2 else value.rjust(width)
                               for i, (value, width) in enumerate(zip(line, widths))).rstrip() for line in cells)


def labels(explicit: dict) -> dict:
    """Eval case labels from a caller's explicit model and effort: the tiers whose Claude model it named,
    first one as tier and the rest as also_tier, and its effort clamped to EFFORTS. Relative efforts
    (lower, higher) and models outside the tier table give no label."""
    case = {}
    tiers = [tier for tier, models in route.MODELS.items() if models["claude"] == explicit.get("model")]
    if tiers:
        case.update(tier=tiers[0], **({"also_tier": tiers[1:]} if tiers[1:] else {}))
    effort = explicit.get("effort")
    if isinstance(effort, str) and effort in EFFORT_LABELS:  # the hook keeps any value the caller wrote
        case["effort"] = EFFORT_LABELS[effort]
    return case


def learn() -> int:
    """s1r learn: write the log's Agent prompts whose caller set a model or effort to learned_cases.jsonl
    beside the log, as private eval cases labeled by the caller (split "learned", the router's logged pick
    in picked), and print how often the pick agreed with the caller, listing each disagreement by case id.
    Rewrites the file on every run."""
    cases, skipped, compared, disagreements = [], 0, {"tier": 0, "effort": 0}, []
    for record in read():
        explicit = record.get("explicit")
        if record.get("kind") != "agent" or not isinstance(explicit, dict) or not explicit:
            continue
        case = labels(explicit)
        if not case or not record.get("text"):
            skipped += 1
            continue
        picked = {key: record[key] for key in ("tier", "effort") if record.get(key) and "error" not in record}
        case_id = record.get("tool_use_id") or record.get("ts")
        accepts = {"tier": [case.get("tier"), *case.get("also_tier", [])], "effort": [case.get("effort")]}
        for key, accepted in accepts.items():
            if case.get(key) and picked.get(key):
                compared[key] += 1
                if picked[key] not in accepted:
                    disagreements.append(f"  {case_id}: picked {key} {picked[key]}, caller wrote {explicit}")
        cases.append({"id": case_id, "ts": record.get("ts"), "text": record["text"], **case,
                      "split": "learned", "picked": picked, "explicit": explicit})
    path = route.LOG.with_name("learned_cases.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(case) + "\n" for case in cases))
    except OSError as err:
        print(f"s1r learn: cannot write {path}: {err}", file=sys.stderr)
        return 1
    print(f"{len(cases)} cases -> {path}")
    print(f"compared with the logged pick: tier {compared['tier']}, effort {compared['effort']}; "
          f"{len(disagreements)} disagreed" + (":" if disagreements else ""))
    print("\n".join(disagreements + [f"skipped {skipped}: relative effort or a model outside the tier table"]))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="s1r report", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", type=date.fromisoformat, help="Only records on or after this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Print the rows as a JSON array (with --outcomes, an object of rows and counts)")
    parser.add_argument("--outcomes", action="store_true", help="Join calls to cos ledger tasks by worktree")
    args = parser.parse_args(argv)
    records = read(args.since.isoformat() if args.since else "")
    if args.outcomes:
        joined = task_outcomes(records, ledger_tasks())
        print(json.dumps(joined) if args.json else table(joined["rows"], OUTCOME_COLUMNS)
              + f"\nmatched {joined['matched']} calls, unmatched {joined['unmatched']}")
        return 0
    rows = summarize(records)
    print(json.dumps(rows) if args.json else table(rows))
    return 0
