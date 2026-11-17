import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_one_router import cli, route

RECORDS = [
    {"ts": "t0", "kind": "agent", "text": "Synthetic A", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {"model": "opus"}, "tool_use_id": "a"},  # overrode routine: opus accepts judgment or hard
    {"ts": "t1", "kind": "agent", "text": "Synthetic B", "tier": "routine", "model": "sonnet", "effort": "high",
     "explicit": {"model": "sonnet", "effort": "low"}, "applied": False},  # low clamps to medium
    {"ts": "t2", "kind": "agent", "text": "Synthetic C", "tier": "hard", "model": "opus", "effort": "high",
     "explicit": {"effort": "lower"}},  # relative effort: no label
    {"ts": "t3", "kind": "agent", "text": "Synthetic D", "applied": False, "pinned": "haiku",
     "explicit": {"effort": "xhigh", "model": "haiku"}},  # pass-through: effort label, no pick to compare
    {"ts": "t4", "kind": "agent", "text": "Synthetic E", "tier": "routine", "model": "sonnet", "effort": "xhigh",
     "error": "ScoreError", "applied": False, "explicit": {"model": "sonnet"}},  # fallback pick is not compared
    {"ts": "t5", "kind": "agent", "text": "Synthetic F", "tier": "routine", "explicit": {}},
    {"ts": "t6", "kind": "manual", "text": "Synthetic G", "explicit": {"model": "opus"}},
]


class LearnTests(unittest.TestCase):
    def test_learn_writes_caller_labeled_cases_and_counts_agreement(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "beta_log.jsonl"
            log.write_text("\n".join(map(json.dumps, RECORDS)) + "\nnot json\n")
            output = io.StringIO()
            with patch.object(route, "LOG", log), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["learn"]), 0)
            cases = [json.loads(line) for line in (Path(directory) / "learned_cases.jsonl").read_text().splitlines()]
        self.assertEqual([(c["id"], c.get("tier"), c.get("also_tier"), c.get("effort"), c["picked"]) for c in cases], [
            ("a", "judgment", ["hard"], None, {"tier": "routine", "effort": "medium"}),
            ("t1", "routine", None, "medium", {"tier": "routine", "effort": "high"}),
            ("t3", None, None, "xhigh", {}),
            ("t4", "routine", None, None, {}),
        ])
        self.assertEqual(cases[0]["text"], "Synthetic A")
        self.assertEqual(output.getvalue().splitlines()[1:], [
            "compared with the logged pick: tier 2, effort 1; 2 disagreed:",
            "  a: picked tier routine, caller wrote {'model': 'opus'}",
            "  t1: picked effort high, caller wrote {'model': 'sonnet', 'effort': 'low'}",
            "skipped 1: relative effort or a model outside the tier table",
        ])

    def test_learn_without_a_log_writes_no_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "s1r" / "beta_log.jsonl"
            with patch.object(route, "LOG", log), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["learn"]), 0)
            self.assertEqual(log.with_name("learned_cases.jsonl").read_text(), "")
        self.assertIn("0 cases", output.getvalue())


if __name__ == "__main__":
    unittest.main()
