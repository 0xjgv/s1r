"""Live routing evals: score the labeled synthetic cases in cases.jsonl with each scoring provider.

    python3 -m unittest discover -s evals -v

Every case goes to each provider in PROVIDERS through route.score, the production selection
(tier argmax and FLOOR, effort EFFORT_BAR, Claude fallback). Prints per-provider metrics, writes
per-case scores with the definitions used to artifacts/evals/, and fails when a provider misses a
gate. Cases are synthetic; the offline suite in tests/ never runs these.

Labels: a Claude agent wrote each case with a tier; a blind gpt-6-astra relabel agreed on 149/150
(the other goes in also_tier). Effort comes from two blind labelers (Claude, gpt-6-astra) on the
stakes rubric in route.EFFORTS, Cohen's kappa 0.74 on the first 60 cases and 0.85 on the next 90:
the lower level is the label and the higher one goes in also_effort. Each case's note is the
writer's original rationale. `split` fixes 100 train and 50 held-out cases, stratified by tier,
effort, and tricky: tune definitions on train only and score held-out once per candidate.
"""

import json
import math
import statistics
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import route  # noqa: E402

CASES = [json.loads(line) for line in (Path(__file__).parent / "cases.jsonl").read_text().splitlines() if line.strip()]
PROVIDERS = (("respan", "span-01-free"), ("typesafe", "jev-1.13.0"))
OUT = ROOT / "artifacts" / "evals"
SHORT = 300  # characters; shorter cases are one-liners, longer ones are briefs
EFFORT_RANK = {effort: rank for rank, effort in enumerate(("medium", "high", "xhigh"))}
GATES = {  # Per provider: metric -> (">=" or "<=", bound). Regression gates, three cases (0.02) under
    # the 2026-10-29 run of the shipped definitions. The definitions were tuned on the 100 train cases,
    # so these totals are optimistic; quote held-out numbers for quality. Identical runs flip 1-3 picks.
    "respan": {"tier_accuracy": (">=", 0.92), "under_route": ("<=", 0.067), "effort_accepted": (">=", 0.733),
               "effort_under": ("<=", 0.08), "errors": ("<=", 0.0)},
    "typesafe": {"tier_accuracy": (">=", 0.967), "under_route": ("<=", 0.027), "effort_accepted": (">=", 0.793),
                 "effort_under": ("<=", 0.053), "errors": ("<=", 0.0)},
}


def accepted(case: dict, key: str) -> list:
    """The labeled answer first, then any alternative a second labeler accepted."""
    return [case[key], *case.get("also_" + key, [])]


def wilson(hits: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson interval for a proportion."""
    p, center = hits / n, z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((p + center - spread) / (1 + 2 * center), 3), round((p + center + spread) / (1 + 2 * center), 3)


def score_all(provider: str, model: str) -> list:
    """Score every case with one provider only, as route.route would, without writing the routing log."""
    def one(case: dict) -> dict:
        entry: dict = {}
        tier, effort = route.score(case["text"], route.FALLBACKS["claude"], entry)
        return {"id": case["id"], "tier": tier, "effort": effort,
                **{k: entry[k] for k in ("p_present", "floored", "error", "attempts") if k in entry}}
    with patch("model_provider.provider_chain", return_value=[(provider, model)]), ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, CASES))


def metrics(results: list) -> dict:
    cases = {case["id"]: case for case in CASES}
    pairs = [(cases[r["id"]], r) for r in results]
    n = len(pairs)

    def rate(predicate, subset=pairs) -> float:
        return round(sum(1 for c, r in subset if predicate(c, r)) / len(subset), 3) if subset else float("nan")

    tier_hits = sum(1 for c, r in pairs if r["tier"] == c["tier"])
    margins = []
    for _, r in pairs:
        if "p_present" in r:
            top = sorted((r["p_present"][f"tier_{t}"] for t in route.TIERS), reverse=True)
            margins.append(top[0] - top[1])
    latencies = sorted(a["elapsed_ms"] for _, r in pairs for a in r.get("attempts", []) if "error" not in a)
    confusion = {label: {pick: sum(1 for c, r in pairs if c["tier"] == label and r["tier"] == pick)
                         for pick in route.TIERS} for label in route.TIERS}
    return {
        "n": n,
        "tier_accuracy": round(tier_hits / n, 3),
        "tier_accuracy_ci95": wilson(tier_hits, n),
        "tier_accepted": rate(lambda c, r: r["tier"] in accepted(c, "tier")),
        "under_route": rate(lambda c, r: c["tier"] != "routine" and r["tier"] == "routine"),
        "over_route": rate(lambda c, r: c["tier"] == "routine" and r["tier"] != "routine"),
        "effort_accuracy": rate(lambda c, r: r["effort"] == c["effort"]),
        "effort_accepted": rate(lambda c, r: r["effort"] in accepted(c, "effort")),
        "effort_within_one": rate(lambda c, r: abs(EFFORT_RANK[r["effort"]] - EFFORT_RANK[c["effort"]]) <= 1),
        "effort_under": rate(lambda c, r: EFFORT_RANK[r["effort"]] < EFFORT_RANK[c["effort"]]),
        "tricky_tier_accuracy": rate(lambda c, r: r["tier"] == c["tier"], [p for p in pairs if p[0].get("tricky")]),
        "short_tier_accuracy": rate(lambda c, r: r["tier"] == c["tier"], [p for p in pairs if len(p[0]["text"]) < SHORT]),
        "long_tier_accuracy": rate(lambda c, r: r["tier"] == c["tier"], [p for p in pairs if len(p[0]["text"]) >= SHORT]),
        "floored": rate(lambda c, r: bool(r.get("floored"))),
        "errors": rate(lambda c, r: "error" in r),
        "median_tier_margin": round(statistics.median(margins), 3) if margins else None,
        "latency_ms_p50_p95": (latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95)]) if latencies else None,
        "tier_confusion": confusion,  # label -> pick -> count
    }


class LiveRoutingEval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metrics = {}
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        OUT.mkdir(parents=True, exist_ok=True)
        for provider, model in PROVIDERS:
            results = score_all(provider, model)
            cls.metrics[provider] = metrics(results)
            record = {"provider": provider, "model": model, "behaviors": route.BEHAVIORS, "floor": route.FLOOR,
                      "metrics": cls.metrics[provider], "results": results}
            (OUT / f"{stamp}-{provider}.json").write_text(json.dumps(record, indent=1))
            print(f"\n{provider}/{model}: " + json.dumps(cls.metrics[provider], indent=1), file=sys.stderr)

    def test_dataset_is_labeled_with_routed_tiers_and_efforts(self):
        self.assertEqual(len({case["id"] for case in CASES}), len(CASES))
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertTrue(set(accepted(case, "tier")) <= set(route.TIERS))
                self.assertTrue(set(accepted(case, "effort")) <= set(route.EFFORTS))

    def test_each_provider_meets_its_gates(self):
        for provider, gates in GATES.items():
            for metric, (op, bound) in gates.items():
                value = self.metrics[provider][metric]
                with self.subTest(provider=provider, metric=metric, value=value):
                    self.assertTrue(value >= bound if op == ">=" else value <= bound, f"{metric} {op} {bound}")


if __name__ == "__main__":
    unittest.main()
