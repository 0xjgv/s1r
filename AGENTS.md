# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.9, standard-library-only router for Claude Code subagents.

- `system_one_router/cli.py`: the installed `s1r` command (`route`, `codex`, `hook`, `status`, `update`, `version`).
- `system_one_router/route.py`: tier and role selection, scoring through `model_provider.py`, and logging.
- `system_one_router/agent_hook.py`: `PreToolUse` hook that reads JSON from stdin and emits updated Agent input.
- `pyproject.toml`, `install.sh`, `Makefile`, `scripts/`, `.github/workflows/release.yml`: packaging and releases.
- `README.md`: setup, routing behavior, and experiment history.
- `~/.config/s1r/.env`: credentials; `~/.local/state/s1r/beta_log.jsonl`: routing records.

Offline `unittest` tests live in `tests/`. Keep changes within the existing modules unless a concrete need warrants expansion.

## Build, Test, and Development Commands

No runtime dependencies. `make install` builds a wheel with uv and installs it as a uv tool.

- `python3 -m system_one_router route "Fix the flaky login test"`: score a prompt, print the selection, and append a log record. This sends the prompt to the scoring chain (TypeSafe Jev first).
- `printf '%s\n' '{"tool_name":"Read","tool_input":{}}' | python3 -m system_one_router hook`: check pass-through behavior; expect successful exit with no output.
- `make test`: run the offline tests.

See `README.md` for the external `beta()` shell integration.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and variables, and uppercase module constants. Match existing type annotations and concise docstrings. Maintain Python 3.9 compatibility and prefer standard-library tools. No formatter or linter is configured. Keep hook stdout reserved for its JSON protocol.

## Testing Guidelines

No coverage threshold exists. For routing changes, add focused standard-library `unittest` tests named `tests/test_*.py`; run them with `python3 -m unittest discover -s tests`.

Mock scoring and redirect logs to temporary paths. Cover low-score and API-failure fallbacks, model and effort on every Agent call that leaves them out with its type preserved, a caller's own model or effort kept, and empty/non-Agent pass-through. Validate emitted JSON through the hook entry point without sending private prompts to a scoring provider.

## Commit & Pull Request Guidelines

Recent commits use concise imperative subjects such as `Reduce effort-router to the beta router`; historical experiment records use `DESIGN:`. Follow the relevant pattern.

PRs should explain the behavior change, list validation commands and outcomes, and link relevant issues. Include sample input/output for routing or hook changes. Update `README.md` when setup or behavior changes.

## Security & Configuration

Read `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, and `RESPAN_API_KEY` from the environment or `~/.config/s1r/.env`. Never commit credentials or generated logs. Prompts are sent to `api.typesafe.ai` (then OpenRouter and `api.respan.ai` on failure) and recorded locally. Live evals in `evals/` send only the synthetic cases. Use synthetic prompts for checks. Treat routing quality as experimental, as documented in `README.md`.
