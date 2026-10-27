import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_run
import model_provider
import route


def codex_scores(tier="judgment", effort="high", tier_score=0.9, effort_score=0.9):
    probabilities = {item["id"]: 0.1 for item in route.BEHAVIORS}
    probabilities["tier_" + tier] = tier_score
    probabilities["effort_" + effort] = effort_score
    return probabilities


class CodexRouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "artifacts" / "routing.jsonl"
        log = patch.object(route, "LOG", self.log)
        log.start()
        self.addCleanup(log.stop)

    def test_cli_codex_json_remains_parseable_on_provider_failure(self):
        output = io.StringIO()
        with patch("route.model_provider.scores", side_effect=model_provider.ScoreError([])), \
                contextlib.redirect_stdout(output):
            status = route.main(["--exclude", "claude", "--json", "Synthetic task"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()),
                         {"model": "gpt-6.1-sol", "effort": "xhigh", "tier": "hard", "harness": "codex"})
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_each_tier_maps_to_its_codex_model_and_logs_target(self):
        for tier, selected in (("judgment", "gpt-6-astra"), ("hard", "gpt-6.1-sol"),
                               ("routine", "gpt-6.1-terra"), ("trivial", "gpt-6-luna")):
            with self.subTest(tier=tier):
                with patch("route.model_provider.scores", return_value=(codex_scores(tier), {})) as score:
                    model, effort, entry = route.route("Synthetic task", "/workspace", "codex", exclude=("claude",))
                self.assertEqual((model, effort, entry["tier"]), (selected, "high", tier))
                self.assertEqual((entry["harness"], entry["exclude"]), ("codex", ["claude"]))
                self.assertEqual(entry["kind"], "codex")
                self.assertEqual(entry["floored"], [])
                score.assert_called_once_with("Synthetic task", route.BEHAVIORS)
                self.assertEqual(json.loads(self.log.read_text().splitlines()[-1]), entry)

    def test_codex_floor_applies_independently_at_the_boundary(self):
        cases = [(0.2, 0.9, ("gpt-6.1-sol", "high"), ["tier"]),
                 (0.9, 0.2, ("gpt-6-astra", "xhigh"), ["effort"]),
                 (0.2, 0.2, ("gpt-6.1-sol", "xhigh"), ["tier", "effort"]),
                 (0.3, 0.3, ("gpt-6-astra", "high"), [])]
        for tier_score, effort_score, expected, floored in cases:
            with self.subTest(tier_score=tier_score, effort_score=effort_score):
                with patch("route.model_provider.scores", return_value=(
                        codex_scores(tier_score=tier_score, effort_score=effort_score), {})):
                    model, effort, entry = route.route("Synthetic task", "", "codex", exclude=("claude",))
                self.assertEqual((model, effort), expected)
                self.assertEqual(entry["floored"], floored)

    def test_codex_provider_failure_uses_codex_fallback(self):
        attempts = [{"provider": "respan", "error": "TimeoutError"}]
        with patch("route.model_provider.scores", side_effect=model_provider.ScoreError(attempts)):
            model, effort, entry = route.route("Synthetic task", "", "codex", exclude=("claude",))
        self.assertEqual((model, effort), ("gpt-6.1-sol", "xhigh"))
        self.assertEqual(entry["error"], "ScoreError")
        self.assertEqual(entry["attempts"], attempts)
        self.assertEqual(entry["harness"], "codex")

    def test_cli_codex_json_contains_model_effort_and_tier(self):
        output = io.StringIO()
        with patch("route.model_provider.scores", return_value=(codex_scores(), {})), \
                contextlib.redirect_stdout(output):
            status = route.main(["--exclude", "claude", "--json", "--", "- Synthetic task from a bullet list"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()),
                         {"model": "gpt-6-astra", "effort": "high", "tier": "judgment", "harness": "codex"})
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_cli_claude_human_output_remains_default(self):
        output = io.StringIO()
        with patch("route.route", return_value=("opus", "high", {"floored": []})), \
                contextlib.redirect_stdout(output):
            self.assertEqual(route.main(["Synthetic task"]), 0)
        self.assertEqual(output.getvalue(), "opus high\n")


class CodexLauncherTests(unittest.TestCase):
    def test_launcher_preserves_prompt_options_and_child_exit_status(self):
        prompt = 'Synthetic task with spaces; $(echo unsafe) "quotes"\nsecond line'
        options = ["--sandbox", "workspace-write", "--cd", "/workspace with spaces",
                   "-c", 'notice="value with spaces"']
        for separator, child_status, expected_status in (([], 23, 23), (["--"], 23, 23), ([], -15, 143)):
            with self.subTest(separator=separator, child_status=child_status):
                output, errors = io.StringIO(), io.StringIO()
                with patch("codex_run.route.route", return_value=("gpt-6-luna", "medium", {})) as pick, \
                        patch("codex_run.subprocess.run", return_value=subprocess.CompletedProcess([], child_status)) as run, \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = codex_run.main([prompt] + separator + options)
                self.assertEqual(status, expected_status)
                pick.assert_called_once_with(prompt, os.getcwd(), "codex", exclude=("claude",))
                run.assert_called_once_with([
                    "codex", "exec", *options, "--model", "gpt-6-luna", "-c",
                    'model_reasoning_effort="medium"', "--", prompt,
                ])
                self.assertEqual(output.getvalue(), "")
                self.assertIn("gpt-6-luna", errors.getvalue())
                self.assertIn("medium", errors.getvalue())

    def test_missing_codex_returns_127_and_reports_stderr(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("codex_run.route.route", return_value=("gpt-6.1-sol", "xhigh", {})), \
                patch("codex_run.subprocess.run", side_effect=FileNotFoundError("codex")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = codex_run.main(["Synthetic task"])
        self.assertEqual(status, 127)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("codex", errors.getvalue().lower())

    def test_blank_prompt_is_rejected_before_scoring_or_launching(self):
        for arguments in ([], [""], [" \t\n"]):
            with self.subTest(arguments=arguments), patch("codex_run.route.route") as pick, \
                    patch("codex_run.subprocess.run") as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    codex_run.main(arguments)
                self.assertNotEqual(error.exception.code, 0)
                pick.assert_not_called()
                run.assert_not_called()

    def test_launcher_subprocess_falls_back_and_propagates_output_and_exit(self):
        prompt = 'Synthetic task; $(echo unsafe) "quotes"'
        options = ["--sandbox", "read-only"]
        expected = ["exec", *options, "--model", "gpt-6.1-sol", "-c",
                    'model_reasoning_effort="xhigh"', "--", prompt]
        environment = os.environ.copy()
        for name in ("ROUTER_PROVIDER", "ROUTER_MODEL", "RESPAN_API_KEY",
                     "TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).resolve().parent.parent
            for name in ("route.py", "model_provider.py", "codex_run.py"):
                shutil.copyfile(source / name, root / name)
            executable = root / "codex"
            executable.write_text(
                "#!" + sys.executable + "\nimport sys\n"
                + "expected = " + repr(expected) + "\n"
                + "if sys.argv[1:] != expected:\n"
                + "    print(repr(sys.argv[1:]), file=sys.stderr)\n    sys.exit(99)\n"
                + "print('child output')\nsys.exit(17)\n")
            executable.chmod(0o755)
            environment["PATH"] = str(root) + os.pathsep + environment.get("PATH", "")
            result = subprocess.run(
                [sys.executable, str(root / "codex_run.py"), prompt, "--", *options],
                capture_output=True, text=True, cwd=root, env=environment, timeout=5,
            )
            entry = json.loads((root / "artifacts" / "beta_log.jsonl").read_text())
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(result.stdout, "child output\n")
        self.assertIn("gpt-6.1-sol", result.stderr)
        self.assertIn("xhigh", result.stderr)
        self.assertEqual(entry["harness"], "codex")
        self.assertEqual(entry["error"], "ScoreError")


if __name__ == "__main__":
    unittest.main()
