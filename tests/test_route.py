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

import agent_hook
import model_provider
import route


def scores(tier="hard", effort="high", tier_score=0.9, effort_score=0.9):
    probabilities = {item["id"]: 0.1 for item in route.BEHAVIORS}
    probabilities["tier_" + tier] = tier_score
    probabilities["effort_" + effort] = effort_score
    return probabilities


class RouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "artifacts" / "routing.jsonl"
        log = patch.object(route, "LOG", self.log)
        log.start()
        self.addCleanup(log.stop)

    def test_each_tier_maps_to_its_claude_model(self):
        cases = (("judgment", "fable"), ("hard", "opus"), ("routine", "sonnet"))
        self.assertEqual(set(route.MODELS), {tier for tier, _ in cases})
        self.assertEqual(list(route.EFFORTS), ["xhigh", "high", "medium"])
        for tier, expected in cases:
            with self.subTest(tier=tier), patch("route.model_provider.scores", return_value=(scores(tier, "medium"), {})):
                model, effort, entry = route.route("Synthetic task", "", "manual", exclude=("codex",))
            self.assertEqual((model, effort, entry["tier"], entry["harness"]), (expected, "medium", tier, "claude"))
            self.assertEqual(entry["floored"], [])

    def test_each_role_picks_its_model_and_effort_without_scoring(self):
        cases = {"implement": ("gpt-6.1-sol", "xhigh"), "validate": ("opus", "xhigh"), "plan": ("opus", "xhigh"),
                 "advise": ("gpt-6-astra", "high"), "research": ("gpt-6.1-terra", "high"),
                 "commit": ("gpt-6.1-terra", "medium")}
        self.assertEqual(set(route.ROLES), set(cases))
        for role, expected in cases.items():
            with self.subTest(role=role), patch("route.model_provider.scores") as score:
                model, effort, entry = route.route("Synthetic task", "/workspace", "manual", role=role)
            score.assert_not_called()
            self.assertEqual((model, effort, entry["role"]), (*expected, role))
            self.assertNotIn("tier", entry)
            self.assertEqual(json.loads(self.log.read_text().splitlines()[-1]), entry)

    def test_every_role_resolves_under_either_exclusion(self):
        for role, (models, _) in route.ROLES.items():
            with self.subTest(role=role):
                self.assertEqual(set(models), set(route.FALLBACKS))

    def test_advise_goes_to_the_harness_the_driver_excludes_not(self):
        for exclude, expected in ((("claude",), ("gpt-6-astra", "codex")), (("codex",), ("fable", "claude"))):
            with self.subTest(exclude=exclude):
                model, _, entry = route.route("Synthetic task", "", "manual", exclude=exclude, role="advise")
            self.assertEqual((model, entry["harness"]), expected)

    def test_cli_role_json_names_the_role_instead_of_a_tier(self):
        output = io.StringIO()
        with patch("route.model_provider.scores") as score, contextlib.redirect_stdout(output):
            status = route.main(["--role", "plan", "--json", "--", "- Synthetic plan task"])
        self.assertEqual(status, 0)
        score.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()),
                         {"model": "opus", "effort": "xhigh", "role": "plan", "harness": "claude"})

    def test_without_exclusions_each_tier_takes_its_first_listed_harness(self):
        for tier, models in route.MODELS.items():
            harness = next(iter(models))
            with self.subTest(tier=tier), patch("route.model_provider.scores", return_value=(scores(tier), {})):
                model, _, entry = route.route("Synthetic task", "", "manual")
            self.assertEqual((model, entry["harness"], entry["exclude"]), (models[harness], harness, []))

    def test_excluding_an_unknown_or_every_harness_is_refused_before_scoring(self):
        for exclude in (("claude", "codex"), ("gemini",), "codex"):
            with self.subTest(exclude=exclude), patch("route.model_provider.scores") as score:
                with self.assertRaises(ValueError):
                    route.route("Synthetic task", "", "manual", exclude=exclude)
                score.assert_not_called()

    def test_cli_refuses_to_exclude_every_harness(self):
        with patch("route.model_provider.scores") as score, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as error:
            route.main(["--exclude", "claude", "--exclude", "codex", "Synthetic task"])
        self.assertEqual(error.exception.code, 2)
        score.assert_not_called()

    def test_every_exclusion_scores_the_same_tiers_and_efforts(self):
        for exclude in ((), ("codex",), ("claude",)):
            with self.subTest(exclude=exclude), \
                    patch("route.model_provider.scores", return_value=(scores(), {})) as score:
                route.route("Synthetic task", "", "manual", exclude=exclude)
            score.assert_called_once_with("Synthetic task", route.BEHAVIORS)
        self.assertEqual({item["id"] for item in route.BEHAVIORS},
                         {f"tier_{t}" for t in route.TIERS} | {f"effort_{e}" for e in route.EFFORTS})

    def test_selection_and_provider_metadata_are_logged(self):
        metadata = {"scoring_provider": "openrouter", "scoring_model": "typesafe/jev-1.13",
                    "attempts": [{"provider": "openrouter", "model": "typesafe/jev-1.13", "elapsed_ms": 1}]}
        with patch("route.model_provider.scores", return_value=(scores(), metadata)) as score:
            model, effort, entry = route.route("Synthetic task", "/workspace", "manual")
        score.assert_called_once_with("Synthetic task", route.BEHAVIORS)
        self.assertEqual((model, effort), ("opus", "high"))
        self.assertEqual(entry["floored"], [])
        for key, value in metadata.items():
            self.assertEqual(entry[key], value)
        self.assertEqual(json.loads(self.log.read_text()), entry)

    def test_tier_floor_and_effort_bar_apply_independently(self):
        cases = [(0.2, 0.9, ("sonnet", "high"), ["tier"]),
                 (0.9, 0.4, ("opus", "xhigh"), ["effort"]),
                 (0.3, 0.5, ("opus", "high"), [])]
        for tier_score, effort_score, expected, floored in cases:
            with self.subTest(tier_score=tier_score, effort_score=effort_score):
                with patch("route.model_provider.scores", return_value=(
                        scores(tier_score=tier_score, effort_score=effort_score), {})):
                    model, effort, entry = route.route("Synthetic task", "", "agent")
                self.assertEqual((model, effort), expected)
                self.assertEqual(entry["floored"], floored)

    def test_effort_is_the_highest_level_at_the_bar(self):
        for levels, expected in (({"xhigh": 0.6, "high": 0.9, "medium": 0.9}, "xhigh"),
                                 ({"xhigh": 0.4, "high": 0.5, "medium": 0.9}, "high"),
                                 ({"xhigh": 0.1, "high": 0.2, "medium": 0.5}, "medium")):
            probabilities = scores()
            probabilities.update({f"effort_{e}": value for e, value in levels.items()})
            with self.subTest(levels=levels), patch("route.model_provider.scores", return_value=(probabilities, {})):
                _, effort, entry = route.route("Synthetic task", "", "agent")
            self.assertEqual((effort, entry["floored"]), (expected, []))

    def test_all_provider_failures_keep_spawn_fallback_and_attempts(self):
        attempts = [{"provider": "respan", "model": "span-01-free", "error": "HTTP 503", "elapsed_ms": 1},
                    {"provider": "typesafe", "model": "jev-1.13.0", "error": "TimeoutError", "elapsed_ms": 2}]
        failure = model_provider.ScoreError(attempts)
        with patch("route.model_provider.scores", side_effect=failure):
            model, effort, entry = route.route("Synthetic task", "", "agent")
        self.assertEqual((model, effort, entry["tier"]), ("sonnet", "xhigh", "routine"))
        self.assertIn("error", entry)
        self.assertEqual(entry["attempts"], attempts)

    def test_log_failure_does_not_block_the_selected_spawn(self):
        with patch("route.model_provider.scores", return_value=(scores(), {})), \
                patch.object(Path, "open", side_effect=PermissionError("unwritable log")):
            model, effort, entry = route.route("Synthetic task", "", "agent")
        self.assertEqual((model, effort), ("opus", "high"))
        self.assertTrue(entry["log_error"])


class HookTests(unittest.TestCase):
    def invoke(self, event):
        output = io.StringIO()
        with patch("agent_hook.sys.stdin", io.StringIO(json.dumps(event))), contextlib.redirect_stdout(output):
            status = agent_hook.main()
        self.assertEqual(status, 0)
        return output.getvalue()

    def test_every_agent_keeps_its_type_and_receives_model_and_effort(self):
        for agent_type in (None, "general-purpose", "security-reviewer"):
            with self.subTest(agent_type=agent_type):
                call = {"prompt": "Synthetic task", "description": "Example", "subagent_type": agent_type}
                with patch("agent_hook.route.route", return_value=("opus", "high", {})) as pick:
                    output = json.loads(self.invoke({"tool_name": "Agent", "cwd": "/workspace", "tool_input": call}))
                pick.assert_called_once_with("Synthetic task", "/workspace", "agent", exclude=("codex",))
                self.assertEqual(output, {"systemMessage": "routed-opus-high", "hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "updatedInput": {
                        **call, "model": "opus", "effort": "high", "description": "opus-high · Example"}}})

    def test_selection_replaces_caller_model_and_effort_and_keeps_other_inputs(self):
        call = {"prompt": "Synthetic task", "subagent_type": "security-reviewer", "model": "sonnet",
                "effort": "low", "description": "Example", "run_in_background": True}
        with patch("agent_hook.route.route", return_value=("opus", "xhigh", {})):
            output = json.loads(self.invoke({"tool_name": "Agent", "tool_input": call}))
        self.assertEqual(output["hookSpecificOutput"]["updatedInput"], {
            **call, "model": "opus", "effort": "xhigh", "description": "opus-xhigh · Example"})

    def test_description_gets_route_label_prefix_with_or_without_incoming_description(self):
        for incoming, expected in (("Fix login", "opus-high · Fix login"), ("", "opus-high"), (None, "opus-high")):
            call = {"prompt": "Synthetic task"}
            if incoming is not None:
                call["description"] = incoming
            with self.subTest(description=incoming), patch("agent_hook.route.route", return_value=("opus", "high", {})):
                output = json.loads(self.invoke({"tool_name": "Agent", "tool_input": call}))
            self.assertEqual(output["hookSpecificOutput"]["updatedInput"]["description"], expected)

    def test_non_agent_and_empty_prompts_emit_nothing(self):
        for event in ({}, {"tool_name": "Read", "tool_input": {"prompt": "Synthetic task"}},
                      {"tool_name": "Agent", "tool_input": {}},
                      {"tool_name": "Agent", "tool_input": {"prompt": ""}}):
            with self.subTest(event=event), patch("agent_hook.route.route") as pick:
                self.assertEqual(self.invoke(event), "")
                pick.assert_not_called()

    def test_hook_subprocess_without_keys_routes_and_logs_provider_failures(self):
        call = {"prompt": "Synthetic task", "description": "Example", "subagent_type": "general-purpose"}
        environment = os.environ.copy()
        for name in ("ROUTER_PROVIDER", "ROUTER_MODEL", "RESPAN_API_KEY",
                     "TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).resolve().parent.parent
            for name in ("route.py", "model_provider.py", "agent_hook.py"):
                shutil.copyfile(source / name, root / name)
            result = subprocess.run(
                [sys.executable, str(root / "agent_hook.py")],
                input=json.dumps({"tool_name": "Agent", "cwd": str(root), "tool_input": call}),
                text=True, capture_output=True, cwd=root, env=environment, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["hookSpecificOutput"]["updatedInput"], {
                **call, "model": "sonnet", "effort": "xhigh", "description": "sonnet-xhigh · Example"})
            entry = json.loads((root / "artifacts" / "beta_log.jsonl").read_text())
        self.assertIn("ScoreError", entry["error"])
        self.assertEqual([attempt["provider"] for attempt in entry["attempts"]], ["typesafe", "openrouter", "respan"])
        self.assertTrue(all(attempt["error"] == "ValueError" for attempt in entry["attempts"]))


if __name__ == "__main__":
    unittest.main()
