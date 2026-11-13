import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_one_router import cli, report, route

OLD = [  # before explicit and the call ids existed
    {"ts": "2026-10-30T10:00:00Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium"},
    {"ts": "2026-10-30T10:01:00Z", "kind": "agent", "applied": False, "pinned": "claude-opus-5-5"},
    {"ts": "2026-10-30T10:02:00Z", "kind": "manual", "role": "plan", "model": "opus", "effort": "xhigh"},
]
NEW = [
    {"ts": "2026-11-13T10:00:00Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {}, "tool_use_id": "t1", "session_id": "s"},
    {"ts": "2026-11-13T10:00:01Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {"model": "opus"}, "tool_use_id": "t2", "session_id": "s"},
    {"ts": "2026-11-13T10:00:02Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {"effort": "high"}, "tool_use_id": "t3", "session_id": "s"},
    {"ts": "2026-11-13T10:00:03Z", "kind": "agent", "tier": "hard", "model": "opus", "effort": "xhigh",
     "explicit": {"model": "opus"}, "tool_use_id": "t4", "session_id": "s"},
    {"ts": "2026-11-13T10:00:04Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {}, "tool_use_id": "t5", "session_id": "s"},
    {"ts": "2026-11-13T10:00:05Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "medium",
     "explicit": {}, "tool_use_id": "t6", "session_id": "s"},
    {"ts": "2026-11-13T10:00:06Z", "kind": "agent", "tier": "routine", "model": "sonnet", "effort": "xhigh",
     "explicit": {}, "error": "ScoreError", "applied": False, "tool_use_id": "t7", "session_id": "s"},
    {"ts": "2026-11-13T10:01:00Z", "kind": "outcome", "tool_use_id": "t1", "status": "completed",
     "duration_ms": 1000, "total_tokens": 100},
    {"ts": "2026-11-13T10:01:01Z", "kind": "outcome", "tool_use_id": "t2", "status": "completed",
     "duration_ms": 3000, "total_tokens": 300},
    {"ts": "2026-11-13T10:01:02Z", "kind": "outcome", "tool_use_id": "t3", "status": "failed", "interrupted": False,
     "duration_ms": 1},
    {"ts": "2026-11-13T10:01:03Z", "kind": "outcome", "tool_use_id": "t5", "status": "async_launched"},
    {"ts": "2026-11-13T10:01:05Z", "kind": "outcome", "tool_use_id": "t6", "status": "failed", "interrupted": True},
    {"ts": "2026-11-13T10:01:06Z", "kind": "outcome", "tool_use_id": "t7", "status": "completed",
     "duration_ms": 5, "total_tokens": 5},
    {"ts": "2026-11-13T10:01:04Z", "kind": "outcome", "tool_use_id": "orphan", "status": "completed",
     "duration_ms": 99999, "total_tokens": 99999},
]


class ReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "beta_log.jsonl"
        self.log.write_text("\n".join([json.dumps(r) for r in OLD] + ["not json", "[]"]
                                      + [json.dumps(r) for r in NEW]) + "\n")
        patcher = patch.object(route, "LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["report", *argv]), 0)
        return output.getvalue()

    def test_old_and_new_rows_group_by_pick_and_join_outcomes_of_calls_run_as_picked(self):
        rows = {(row["model"], row["effort"]): row for row in json.loads(self.run_cli("--json"))}
        self.assertEqual(rows[("sonnet", "medium")], {  # t2 and t3 ran with the caller's model or effort
            "model": "sonnet", "effort": "medium", "picks": 6, "applied": 1.0,
            "overrode": 0.2,  # of the 5 rows with explicit; t2 kept opus over routine's sonnet
            "done": 1, "bg": 1, "failed": 1, "error": 0.5, "median_ms": 1000, "median_tokens": 100})
        self.assertEqual(rows[("sonnet", "xhigh")], {  # the unapplied fallback pick did not run as picked
            "model": "sonnet", "effort": "xhigh", "picks": 1, "applied": 0.0, "overrode": 0.0,
            "done": 0, "bg": 0, "failed": 0, "error": None, "median_ms": None, "median_tokens": None})
        self.assertEqual(rows[("opus", "xhigh")], {  # explicit opus matches hard's model: no override
            "model": "opus", "effort": "xhigh", "picks": 1, "applied": 1.0, "overrode": 0.0,
            "done": 0, "bg": 0, "failed": 0, "error": None, "median_ms": None, "median_tokens": None})
        self.assertEqual((rows[("-", "-")]["picks"], rows[("-", "-")]["applied"], rows[("-", "-")]["overrode"]),
                         (1, 0.0, None))
        self.assertEqual(len(rows), 4)  # the manual row is not an Agent pick

    def test_since_drops_older_records(self):
        rows = json.loads(self.run_cli("--since", "2026-11-01", "--json"))
        self.assertEqual([(row["model"], row["picks"]) for row in rows], [("sonnet", 5), ("opus", 1), ("sonnet", 1)])

    def test_table_prints_a_header_and_dashes_for_missing_values(self):
        lines = self.run_cli().splitlines()
        self.assertEqual(lines[0].split(), list(report.COLUMNS))
        self.assertEqual(lines[1].split(), ["sonnet", "medium", "6", "100%", "20%", "1", "1", "1", "50%", "1000", "100"])
        self.assertEqual(lines[2].split(), ["-", "-", "1", "0%", "-", "0", "0", "0", "-", "-", "-"])

    def test_missing_log_prints_only_the_header(self):
        self.log.unlink()
        self.assertEqual(self.run_cli().split(), list(report.COLUMNS))

    def test_bad_since_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            cli.main(["report", "--since", "yesterday"])
        self.assertEqual(exit.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
