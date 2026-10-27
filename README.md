# system-one-router

Choose a model and effort for Claude Code subagents or whole Codex tasks with a
small decision model. A Claude `PreToolUse` hook updates Agent calls; the Codex
launcher selects a model and effort before starting `codex exec`.
Supports Respan Lite, Respan Pro, direct TypeSafe Jev, and OpenRouter System One models.

Python 3.9+ and the standard library are sufficient. To execute tasks, install
[Claude Code](https://code.claude.com/docs/en/overview) or
[Codex CLI](https://developers.openai.com/codex/cli) and sign in with an account
that has access to the configured execution models.

## Setup

```sh
git clone https://github.com/0xjgv/system-one-router.git
cd system-one-router
export EFFORT_ROUTER_DIR="$(pwd)"
```

Set credentials in your environment or create a `.env` file in the repository:

```dotenv
RESPAN_API_KEY=your-respan-key
TYPESAFE_API_KEY=your-typesafe-key
OPENROUTER_API_KEY=your-openrouter-key
```

Environment values take precedence. The `.env` reader accepts `NAME=value` lines
with optional quotes; it does not execute shell expressions. Keep this file private;
Git ignores it.

```sh
python3 model_provider.py check
```

This checks that keys are present for the primary provider and both Jev fallbacks.
It prints key names, never values, and exits nonzero if a required key is missing.
It does not contact providers or validate credentials. A direct TypeSafe setup also
needs `OPENROUTER_API_KEY` for fallback.

## Choose a scoring provider

Set `ROUTER_PROVIDER` and `ROUTER_MODEL` in the environment or `.env`:

| Provider | `ROUTER_PROVIDER` | `ROUTER_MODEL` | Credential |
|---|---|---|---|
| Respan Lite (default) | `respan` | `span-01-free` | `RESPAN_API_KEY` |
| Respan Pro | `respan` | `span-01-pro` | `RESPAN_API_KEY` |
| TypeSafe Jev | `typesafe` | `jev-1.13.0` | `TYPESAFE_API_KEY` |
| OpenRouter | `openrouter` | `typesafe/jev-1.13`, or another compatible System One model ID | `OPENROUTER_API_KEY` |

Omitting `ROUTER_MODEL` selects the provider's default from `model_provider.py`.
Set both variables when switching providers if a model is already configured.
The `PRIMARY` and `FALLBACKS` constants in that file define the default provider chain:
Respan → direct TypeSafe Jev → OpenRouter Jev.

```sh
# Respan Pro
ROUTER_PROVIDER=respan ROUTER_MODEL=span-01-pro \
  python3 route.py "Fix the flaky login test"

# Direct Jev
ROUTER_PROVIDER=typesafe ROUTER_MODEL=jev-1.13.0 \
  python3 route.py "Fix the flaky login test"

# Jev through OpenRouter; replace the model ID to try another System One model
ROUTER_PROVIDER=openrouter ROUTER_MODEL=typesafe/jev-1.13 \
  python3 route.py "Fix the flaky login test"
```

Respan Pro requires Respan credits. OpenRouter uses its
`/api/v1/systemone` endpoint and models that support `noul` questions, rather than
the chat-completions API. See [Respan's models](https://www.respan.ai/docs/documentation/span-01/concept#models-and-pricing),
[TypeSafe's API](https://docs.typesafe.ai/api), and
[OpenRouter's System One API](https://openrouter.ai/docs/guides/community/typesafe-sdk).

## How routing works

The scorer picks a tier, which says what makes the task hard. Each tier has one model
per harness. A caller excludes the harnesses it cannot run, and the first remaining
harness in the tier's row wins. The harness is independent of the scoring provider:

| Tier | `claude` | `codex` |
|---|---|---|
| `judgment`: deciding, comparing, weighing trade-offs | `fable` | `gpt-6-astra` |
| `hard`: engineering with an unknown cause or solution | `opus` | `gpt-6.1-sol` |
| `routine`: engineering with a known approach | `sonnet` | `gpt-6.1-terra` |
| `trivial`: a lookup, a one-line edit, a short answer | `haiku` | `gpt-6-luna` |
| Fallback tier/effort | `routine` / `xhigh` | `hard` / `xhigh` |

The Claude hook excludes `codex`, and `codex_run.py` excludes `claude`. With no
exclusion, every tier picks Claude, because `claude` comes first in each row. Tier
definitions, the tier table, and the fallbacks live in `route.py` (`TIERS`, `MODELS`,
`FALLBACKS`). A model rename, a new harness, or a reordered row changes only the tables.
These are initial routing rules, not validated rankings; Terra's place under `routine`
follows the Codex model cache's description of its predecessor, gpt-5.6-terra, as a
"balanced model for straightforward work".
Both harnesses use `low`, `medium`, `high`, and `xhigh`; the listed Codex models
support all four. Account access may differ.

1. Score the definitions in `route.py`: four tiers and four efforts (`xhigh`, `high`,
   `medium`, `low`), the same eight definitions for every caller. Respan returns
   `p_present`; TypeSafe and OpenRouter return one `noul` probability per definition.
2. Take the highest score in each group, then map the tier to the first model whose
   harness is not excluded. A winner below `FLOOR` (`0.3`) uses that group's default from
   the first remaining harness's fallback. Low scores do not trigger a provider retry.
3. Missing credentials, request errors, or malformed scores trigger direct TypeSafe
   Jev, then OpenRouter Jev. If all attempts fail, use the first remaining harness's
   fallback pair.
   Duplicate provider/model pairs are attempted only once, including when a fallback
   is selected as the primary.

Requests use an 8-second socket timeout per attempt with no retries for the same
provider/model pair. The shell example gives the whole hook 30 seconds. Invalid configuration
also produces the static default; use `check` to catch it before starting a session.

The Claude hook sets the Agent call's `model` and `effort` fields and keeps every
other input, including the agent type and its instructions. Per-call `effort` needs
Claude Code 2.1.292 or later; forks ignore both fields, and `CLAUDE_CODE_EFFORT_LEVEL`
overrides the routed effort. Non-Agent calls and empty prompts pass through.
Provider and log-write failures do not block spawning.

The scoring model chooses the execution model; these are separate settings.
The shared probability format does not establish equal calibration across providers.
Treat routing quality and the current threshold as experimental.

## Try Claude Code with a shell function

Paste this function into a POSIX-compatible shell, Bash, or Zsh, or add it to your
shell configuration. It uses the exported path so hooks work from other projects.

```sh
beta() (
  : "${EFFORT_ROUTER_DIR:?Set EFFORT_ROUTER_DIR to the cloned repository}"
  export EFFORT_ROUTER_DIR
  unset CLAUDE_CODE_SUBAGENT_MODEL_FORCE CLAUDE_CODE_EFFORT_LEVEL
  python3 "$EFFORT_ROUTER_DIR/model_provider.py" check || exit
  claude --model opus \
    --settings '{"hooks":{"PreToolUse":[{"matcher":"Agent","hooks":[{"type":"command","command":"python3 \"$EFFORT_ROUTER_DIR/agent_hook.py\"","timeout":30}]}]}}' \
    "$@"
)
```

Run a session from the project you want to work on:

```sh
beta

# Request a small read-only delegation to exercise the hook
beta -p "Use a general-purpose subagent to summarize this project's README. Do not modify files."

# Test the same flow using Respan Pro
ROUTER_PROVIDER=respan ROUTER_MODEL=span-01-pro \
  beta -p "Use a general-purpose subagent to summarize this project's README. Do not modify files."
```

Avoid forcing a global subagent model or effort override when testing routing.
The function preserves normal Claude Code permission checks. See the
[subagent](https://code.claude.com/docs/en/sub-agents) and
[hook](https://code.claude.com/docs/en/hooks) documentation for integration details.

## Try Codex

Select a Codex model and effort without launching it:

```sh
python3 route.py --exclude claude --json -- "Explain what a Python list is"
# Example: {"model": "gpt-6-luna", "effort": "low", "tier": "trivial", "harness": "codex"}
```

`--json` emits one model/effort/tier/harness object, including when scoring fails and
the fallback is used. `--exclude codex` picks Claude models instead. Excluding every harness
is an error. Put `--` before the
prompt when it may start with `-`, as a task file's bullet list does.

Launch one routed Codex task:

```sh
python3 codex_run.py "Summarize this project's README. Do not modify files." \
  --sandbox read-only
```

The prompt is the first argument; remaining arguments are passed to `codex exec`.
The launcher supplies `--model` and `-c model_reasoning_effort=...`; omit competing
model or effort overrides. It forwards Codex's output and exit status, prints the
routing decision to stderr, and preserves Codex's normal permission handling.

With `EFFORT_ROUTER_DIR` exported by the setup command, add this shell function:

```sh
codex_routed() {
  : "${EFFORT_ROUTER_DIR:?Set EFFORT_ROUTER_DIR to the cloned repository}"
  python3 "$EFFORT_ROUTER_DIR/codex_run.py" "$@"
}
```

Run from the project you want to work on:

```sh
codex_routed "Summarize this project's README. Do not modify files." --sandbox read-only

# Change the scoring provider while still executing with Codex
ROUTER_PROVIDER=respan ROUTER_MODEL=span-01-pro \
  codex_routed "Explain this project's test structure." --sandbox read-only
```

This routes the initial task, not individual Codex subagents or later turns. The
Claude `agent_hook.py` is not a Codex hook. Native Codex subagent routing requires
separate validation of spawn arguments, fork behavior, and model overrides. See
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
for model and effort settings.

## Tests and logs

```sh
python3 -m unittest discover -s tests -v        # Offline tests; no API keys needed
printf '%s\n' '{"tool_name":"Read","tool_input":{}}' | python3 agent_hook.py
```

The last command should exit successfully without output. To test an Agent call
directly, use a synthetic prompt; this makes a live scoring request:

```sh
printf '%s\n' '{"tool_name":"Agent","tool_input":{"prompt":"Explain what a Python list is","subagent_type":"general-purpose"}}' \
  | python3 agent_hook.py
tail -n 1 "$EFFORT_ROUTER_DIR/artifacts/beta_log.jsonl"
```

Each routing record includes the prompt, working directory, excluded harnesses, selected tier/harness/model/effort,
scores, and provider attempts with timing and error categories. Successful scoring
also records `scoring_provider` and `scoring_model`. Logs stay in the repository's
ignored `artifacts/beta_log.jsonl`; logging failure does not discard a decision.

Prompts are sent to the selected scoring service and, on failure, TypeSafe. Keep
credentials and logs out of commits and use synthetic prompts for integration tests.

## Files

| File | Purpose |
|---|---|
| `model_provider.py` | Provider configuration, credentials, API adapters, and fallback |
| `route.py` | Tier and effort definitions, the tier-to-model table, fallbacks, selection, and logs |
| `agent_hook.py` | Claude Code `PreToolUse` JSON interface |
| `codex_run.py` | Launch a whole Codex task with the selected model and effort |
| `tests/test_model_provider.py`, `tests/test_route.py`, `tests/test_codex.py` | Offline provider, routing, hook, and launcher regression tests |
