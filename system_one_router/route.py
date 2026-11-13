"""Pick the model and effort for a task or subagent with a System One scorer.

    s1r route "<prompt>"   print the pick for one prompt and log it
    s1r route --exclude claude --json -- "<prompt>"   print a Codex model, effort, tier, and harness

One scoring call evaluates every tier and effort option. The tier takes the highest probability; the
effort takes the highest level that reaches EFFORT_BAR. MODELS maps the tier to the first listed model
whose harness the caller did not exclude. Scoring failures, a tier under FLOOR, and no effort at
EFFORT_BAR use the first remaining harness's fallback. Standard library only; Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from system_one_router import model_provider

LOG = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "s1r" / "beta_log.jsonl"
FLOOR = 0.3  # a winning tier p_present under this means no tier fit
EFFORT_BAR = 0.5  # effort is the highest level at or above this; none means no level fit
TIERS = {  # What makes the task hard; MODELS maps a tier to a model.
    "judgment": "The main difficulty of the task request is judgment, not execution: it asks to decide, compare options, weigh trade-offs, or work out what is really wanted, and following instructions alone would not produce the right answer.",
    "hard": "The task request is hard engineering: the real cause or right fix for the reported problem is still unproven, even when the request proposes a quick change, so a competent engineer must investigate first and could easily get it wrong.",
    "routine": "The task request is standard engineering whose requested steps are known and clearly solve the problem it describes, from a one-line edit or lookup to several steps, so a competent engineer finishes correctly on the first attempt, unlike a problem whose cause is unknown.",
}
MODELS = {  # Each tier's model per harness; the first harness not excluded wins.
    "judgment": {"claude": "opus", "codex": "gpt-6-astra"},
    "hard": {"claude": "opus", "codex": "gpt-6.1-sol"},
    "routine": {"claude": "sonnet", "codex": "gpt-6.1-terra"},
}
FALLBACKS = {  # Per harness, in order: the tier and effort used when scoring fails or no option fits.
    "claude": ("routine", "high"),
    "codex": ("hard", "xhigh"),
}
EFFORTS = {  # What a mistake would cost, highest first; a costlier mistake also fits the levels below.
    "xhigh": "A mistake in the task request's work would do lasting harm to customers or the business: lost or corrupted data, exposed private data, a security hole, wrong money movement, or users cut off by an outage, harm that outlasts reworking the code.",
    "high": "A mistake in the task request's work would cost real rework: it changes shared code, interfaces, or behavior that other code or people depend on.",
    "medium": "A mistake in the task request's work would be cheap to notice and undo: it is read-only, local to one place, or easily reverted.",
}


BEHAVIORS = ([{"id": f"tier_{k}", "definition": v} for k, v in TIERS.items()]
             + [{"id": f"effort_{k}", "definition": v} for k, v in EFFORTS.items()])


def route(text: str, cwd: str, kind: str, exclude: tuple[str, ...] = (),
          fallback: bool = True, context: dict | None = None) -> tuple[str, str, dict]:
    """Score, pick, and log one prompt among the harnesses not excluded.
    Without fallback, a scoring failure logs the fallback pick with applied=False for the caller to skip.
    context holds caller fields logged beside the pick, such as the hook's explicit fields and call ids."""
    allowed = [harness for harness in FALLBACKS if harness not in exclude]
    if set(exclude) - set(FALLBACKS) or not allowed:
        raise ValueError(f"exclude must name harnesses from {list(FALLBACKS)} and leave one; got {list(exclude)}")
    entry = record(kind, cwd, text, exclude)
    entry.update(context or {})
    tier, effort = score(text, FALLBACKS[allowed[0]], entry)
    models = MODELS[tier]
    entry["tier"] = tier
    harness = next(h for h in models if h in allowed)
    entry.update(harness=harness, model=models[harness], effort=effort)
    if "error" in entry and not fallback:
        entry["applied"] = False
    log(entry)
    return entry["model"], effort, entry


def record(kind: str, cwd: str, text: str, exclude: tuple[str, ...] = ()) -> dict:
    """Start a log entry for one prompt."""
    return {"ts": timestamp(), "kind": kind, "cwd": cwd, "text": text, "exclude": list(exclude)}


def timestamp() -> str:
    """The current UTC time as the log writes it."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(entry: dict) -> None:
    """Append entry to LOG; a write failure marks the entry instead of raising."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        entry["log_error"] = True


def score(text: str, fallback: tuple[str, str], entry: dict) -> tuple[str, str]:
    """Score the tiers and efforts into entry; failures and floored groups keep the fallback."""
    tier, effort = fallback
    try:
        p, metadata = model_provider.scores(text, BEHAVIORS)
        entry.update(metadata)
        best_tier = max(TIERS, key=lambda t: p[f"tier_{t}"])
        fitting_effort = next((e for e in EFFORTS if p[f"effort_{e}"] >= EFFORT_BAR), None)
        floored = (["tier"] if p[f"tier_{best_tier}"] < FLOOR else []) + ([] if fitting_effort else ["effort"])
        tier = tier if "tier" in floored else best_tier
        effort = fitting_effort or effort
        entry.update(p_present=p, floored=floored)
    except Exception as err:  # network, HTTP, key, or response shape: never block the spawn
        entry["error"] = type(err).__name__
        if isinstance(err, model_provider.ScoreError):
            entry["attempts"] = err.attempts
    return tier, effort


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="s1r route", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exclude", action="append", default=[], choices=FALLBACKS,
                        help="Skip a harness's models; repeat to skip several (default: none)")
    parser.add_argument("--json", action="store_true",
                        help="Print a model/effort/tier/harness JSON object")
    parser.add_argument("prompt", help="Task prompt")
    args = parser.parse_args(argv)
    if not args.prompt.strip():
        parser.error("prompt must not be empty")
    try:
        model, effort, entry = route(args.prompt, os.getcwd(), "manual", tuple(args.exclude))
    except ValueError as err:
        parser.error(str(err))
    if args.json:
        print(json.dumps({key: entry[key] for key in ("model", "effort", "tier", "harness") if key in entry}))
    else:
        note = "Scoring failed" if "error" in entry else ",".join(entry.get("floored", []))
        print(f"{model} {effort}" + (f"  ({note})" if note else ""))
    return 0

