import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_one_router import cli, report, route

FAKE_LEDGER = """#!{python}
import os, sys
command = sys.argv[1]
if command not in ("board", "stats") or sys.argv[-1] != "--json" or (command == "board") != ("--all" in sys.argv):
    sys.exit("unexpected arguments: " + " ".join(sys.argv[1:]))
sys.stdout.write(open(os.path.join(os.environ["LEDGER_DIR"], command + ".json")).read())
"""
WORKTREE = "/work/repo with space/.worktrees/a"


def call(ts, cwd, model="sonnet", effort="medium", **extra):
    return {"ts": ts, "kind": "agent", "model": model, "effort": effort, "explicit": {}, "cwd": cwd, **extra}


RECORDS = [
    call("2026-11-17T10:00:00Z", WORKTREE),
    call("2026-11-17T10:00:01Z", WORKTREE + "/sub/dir/"),  # nested, trailing slash
    call("2026-11-17T10:00:02Z", "/work/repo with space/.worktrees/a-other"),  # a sibling prefix, not inside
    call("2026-11-17T10:00:03Z", "/work/repo with space/.worktrees/b/", model="opus", effort="high"),
    call("2026-11-17T08:00:00Z", WORKTREE, model="opus", effort="high"),  # before any task in the worktree
    call("2026-11-17T12:00:00Z", WORKTREE, model="opus", effort="high"),  # after the worktree's tasks ended
    call("2026-11-17T10:00:04Z", ""),
    call("2026-11-17T10:00:05Z", WORKTREE, explicit={"model": "opus"}),  # not run as picked
    call("2026-11-17T10:00:06Z", WORKTREE, applied=False),
    {"ts": "2026-11-17T10:00:07Z", "kind": "manual", "model": "opus", "effort": "xhigh", "cwd": WORKTREE},
]
BOARDS = {
    "cos-one": [
        {"id": "a-old", "created": "2026-11-17T09:00:00Z", "fields": {"worktree": WORKTREE + "/"}},
        {"id": "a", "created": "2026-11-17T09:30:00Z", "fields": {"worktree": WORKTREE}},
        {"id": "no-worktree", "created": "2026-11-17T09:00:00Z", "fields": {}},
    ],
    "cos-two": [{"id": "b", "created": "2026-11-17T09:00:00Z", "fields": {"worktree": "/work/repo with space/.worktrees/b"}}],
}
STATS = {
    "cos-one": [{"id": "a-old", "state": "dropped", "ended": "2026-11-17T09:50:00Z", "cycle_s": 1200, "reworks": 5, "blocks": 5},
                {"id": "a", "state": "done", "ended": "2026-11-17T11:30:00Z", "cycle_s": 7200, "reworks": 2, "blocks": 1}],
    "cos-two": [{"id": "b", "state": "review", "ended": None, "cycle_s": 600, "reworks": 1, "blocks": 0}],
}


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        log = root / "beta_log.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in RECORDS))
        self.cos = root / "cos"
        for name in BOARDS:
            (self.cos / name).mkdir(parents=True)
            (self.cos / name / "events.jsonl").write_text("")
            (self.cos / name / "board.json").write_text(json.dumps({"tasks": BOARDS[name], "inbox": []}))
            (self.cos / name / "stats.json").write_text(json.dumps({"tasks": STATS[name]}))
        (self.cos / "distill").mkdir()  # no events.jsonl: not a ledger
        self.bin = root / "bin"
        self.bin.mkdir()
        fake = self.bin / "ledger"
        fake.write_text(FAKE_LEDGER.format(python=sys.executable))
        fake.chmod(0o755)
        for patcher in (patch.object(route, "LOG", log), patch.object(report, "COS", self.cos),
                        patch.dict(os.environ, {"PATH": str(self.bin)})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(cli.main(["report", "--outcomes", *argv]), 0)
        return output.getvalue(), errors.getvalue()

    def test_calls_join_the_latest_task_whose_worktree_holds_their_cwd(self):
        joined = json.loads(self.run_cli("--json")[0])
        self.assertEqual(joined, {"matched": 3, "unmatched": 4, "rows": [
            {"model": "sonnet", "effort": "medium", "calls": 4, "matched": 2, "tasks": 1, "done": 1, "median_cycle_min": 120,
             "reworks": 2, "blocks": 1},
            {"model": "opus", "effort": "high", "calls": 3, "matched": 1, "tasks": 1, "done": 0, "median_cycle_min": None,
             "reworks": 1, "blocks": 0},
        ]})

    def test_table_ends_with_the_matched_and_unmatched_counts(self):
        lines = self.run_cli()[0].splitlines()
        self.assertEqual(lines[0].split(), list(report.OUTCOME_COLUMNS))
        self.assertEqual(lines[1].split(), ["sonnet", "medium", "4", "2", "1", "1", "120", "2", "1"])
        self.assertEqual(lines[2].split(), ["opus", "high", "3", "1", "1", "0", "-", "1", "0"])
        self.assertEqual(lines[-1], "matched 3 calls, unmatched 4")

    def test_missing_ledger_command_leaves_every_call_unmatched(self):
        (self.bin / "ledger").unlink()
        out, err = self.run_cli("--json")
        self.assertEqual((json.loads(out)["matched"], json.loads(out)["unmatched"]), (0, 7))
        self.assertIn("s1r report: skipped ledger", err)

    def test_failing_ledger_skips_only_that_ledger(self):
        (self.cos / "cos-two" / "stats.json").unlink()
        out, err = self.run_cli("--json")
        self.assertEqual((json.loads(out)["matched"], json.loads(out)["unmatched"]), (2, 5))
        self.assertIn("cos-two", err)

    def test_missing_cos_directory_leaves_every_call_unmatched(self):
        with patch.object(report, "COS", self.cos / "gone"):
            out, err = self.run_cli("--json")
        self.assertEqual((json.loads(out)["matched"], json.loads(out)["unmatched"]), (0, 7))
        self.assertIn("no ledgers", err)


if __name__ == "__main__":
    unittest.main()
