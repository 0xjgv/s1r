# s1r

Choose a model and effort for Claude Code subagents or whole Codex tasks with a
small decision model. A Claude `PreToolUse` hook updates Agent calls, and its
`PostToolUse` side logs how each call turned out; the Codex launcher selects a model
and effort before starting `codex exec`.
Supports Respan Lite, Respan Pro, direct TypeSafe Jev, and OpenRouter System One models.

Python 3.9+ and the standard library are sufficient at runtime. To execute tasks, install
[Claude Code](https://code.claude.com/docs/en/overview) or
[Codex CLI](https://developers.openai.com/codex/cli) and sign in with an account
that has access to the configured execution models.

## Install

The installer needs [uv](https://docs.astral.sh/uv/) and a POSIX shell (macOS, Linux, WSL).
Install or update the latest release:

```sh
curl -fsSL https://raw.githubusercontent.com/0xjgv/s1r/main/install.sh | sh
```

It downloads `s1r-<version>-py3-none-any.whl` and `checksums.txt` from
[GitHub Releases](https://github.com/0xjgv/s1r/releases), verifies the
wheel's SHA-256, keeps it under `~/.local/share/s1r/releases`, and installs
it with `uv tool install`. The `s1r` command lands in uv's tool bin directory
(`uv tool dir --bin`, usually `~/.local/bin`); put that directory on your `PATH`. Set
`SYSTEM_ONE_ROUTER_VERSION=v0.1.2` to pin a release. Once installed, `s1r update` runs the
latest release's installer, and `s1r update v0.1.2` installs that release. To uninstall, run
`uv tool uninstall s1r`.

From a source checkout, `make install` builds the wheel, packages it as a release does,
and installs that package. `make release [BUMP=patch|minor|major]` tags the next version
on `main` and pushes it; `.github/workflows/release.yml` tests, smoke-tests the installer,
and publishes the assets.

## Configure

Configuration and logs live in your home directory, not the repository:

| Path | Contents |
|---|---|
| `~/.config/s1r/.env` | Credentials and settings; the installer creates it with mode `600` |
| `~/.local/state/s1r/beta_log.jsonl` | Routing records |

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` move them. Set credentials in your environment or
in that `.env`:

```dotenv
RESPAN_API_KEY=your-respan-key
TYPESAFE_API_KEY=your-typesafe-key
OPENROUTER_API_KEY=your-openrouter-key
```

Environment values take precedence. The `.env` reader accepts `NAME=value` lines
with optional quotes; it does not execute shell expressions. Keep this file private.

```sh
s1r status
```

This shows the config file, `ROUTER_PROVIDER` and `ROUTER_MODEL` with where each came from
(environment, `.env`, or default), and every provider in the order it is tried: the
configured primary, then the fallbacks direct Jev, OpenRouter Jev, and Respan. For each it
says whether the key is set and from where. It prints key names, never values, and exits
nonzero if a required key is missing. It does not contact providers or validate credentials.

## Choose a scoring provider

Set `ROUTER_PROVIDER` and `ROUTER_MODEL` in the environment or `.env`:

| Provider | `ROUTER_PROVIDER` | `ROUTER_MODEL` | Credential |
|---|---|---|---|
| TypeSafe Jev (default) | `typesafe` | `jev-1.13.0` | `TYPESAFE_API_KEY` |
| Respan Lite | `respan` | `span-01-free` | `RESPAN_API_KEY` |
| Respan Pro | `respan` | `span-01-pro` | `RESPAN_API_KEY` |
| OpenRouter | `openrouter` | `typesafe/jev-1.13`, or another compatible System One model ID | `OPENROUTER_API_KEY` |

Omitting `ROUTER_MODEL` selects the provider's default from `system_one_router/model_provider.py`.
Set both variables when switching providers if a model is already configured.
The `PRIMARY` and `FALLBACKS` constants in that file define the default provider chain:
direct TypeSafe Jev → OpenRouter Jev → Respan Lite. A configured primary goes first, then
the rest in that order. Jev replaced Respan Lite as the default on 2026-10-29 after the
live evals: on accepted labels, tier 0.92 vs 0.78 (paired exact McNemar p = 0.02),
effort 0.90 vs 0.62, and p50 latency 180 vs 863 ms.

```sh
# Respan Pro
ROUTER_PROVIDER=respan ROUTER_MODEL=span-01-pro \
  s1r route "Fix the flaky login test"

# Direct Jev
ROUTER_PROVIDER=typesafe ROUTER_MODEL=jev-1.13.0 \
  s1r route "Fix the flaky login test"

# Jev through OpenRouter; replace the model ID to try another System One model
ROUTER_PROVIDER=openrouter ROUTER_MODEL=typesafe/jev-1.13 \
  s1r route "Fix the flaky login test"
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
| `judgment`: deciding, comparing, weighing trade-offs | `opus` | `gpt-6-astra` |
| `hard`: engineering with an unknown cause or solution | `opus` | `gpt-6.1-sol` |
| `routine`: engineering with a known approach, from a one-line edit up | `sonnet` | `gpt-6.1-terra` |
| Fallback tier/effort for `route` and `codex` | `routine` / `high` | `hard` / `xhigh` |

The Claude hook excludes `codex`, and `s1r codex` excludes `claude`. With no
exclusion, every tier picks Claude, because `claude` comes first in each row. Tier
definitions, the tier table, and the fallbacks live in `system_one_router/route.py` (`TIERS`, `MODELS`,
`FALLBACKS`). A model rename, a new harness, or a reordered row changes only the tables.
These are initial routing rules, not validated rankings; Terra's place under `routine`
follows the Codex model cache's description of its predecessor, gpt-5.6-terra, as a
"balanced model for straightforward work"; gpt-6.1-terra's own entry says only
"GPT-6.1 Terra Codex model." Haiku and Luna are not routed: lookups and one-line edits
go to `routine` at `medium`, the lowest effort the scorer picks. Fable is not routed
either: a caller who writes `model: fable` still gets it, with a routed effort (below).
Both harnesses accept `medium`, `high`, and `xhigh`. Account access may differ.

1. Score the definitions in `system_one_router/route.py`: three tiers and three efforts (`xhigh`, `high`,
   `medium`), the same six definitions for every caller. Respan returns
   `p_present`; TypeSafe and OpenRouter return one `noul` probability per definition.
2. Take the highest tier score, then map the tier to the first model whose harness is
   not excluded. Effort is the highest level scoring at least `EFFORT_BAR` (`0.5`): the
   effort definitions describe what a mistake would cost, and a costlier mistake also
   fits the levels below. A tier winner below `FLOOR` (`0.3`), or no effort at the bar,
   uses that group's default from the first remaining harness's fallback. Low scores do
   not trigger a provider retry. The stakes-based efforts and this rule replaced argmax
   over "how careful" definitions on 2026-10-29. On the live evals, Jev's accepted effort
   rose from 0.67 to 0.83 and efforts below every accepted label fell from 15% to 3%,
   while xhigh picks rose from 21 to 34 of 60 cases.

The `hard` and `routine` tier text and the `xhigh` effort text were rewritten on 2026-10-29
by a hill-climb on the 100 train cases in `evals/cases.jsonl`: one definition per candidate,
accepted only on a gain of at least two cases. The four kept rewrites were scored once on
the 50 held-out cases. On Jev, accepted effort rose from 0.76 to 0.84, effort above every
accepted label fell from 24% to 14%, xhigh picks fell from 58% to 42%, tier rose from 0.94
to 0.96, and effort below the label rose by one case. Respan, which was not tuned, moved the
same way. At n = 50 the effort gain is not significant (6 cases gained vs 2 lost, paired
exact p = 0.29). Asking tier as one Jev `choice` and effort as one `score` instead of
independent `noul` questions gained nothing beyond noise on the same cases.
3. Missing credentials, request errors, or malformed scores trigger the next provider
   in the chain. If all attempts fail, `route` and `codex` use the first remaining harness's
   fallback pair; the Claude hook leaves the Agent call unrouted (below).
   Duplicate provider/model pairs are attempted only once, including when a fallback
   is selected as the primary.

Requests use an 8-second socket timeout per attempt with no retries for the same
provider/model pair. The shell example gives the whole hook 30 seconds. Invalid configuration
counts as a scoring failure; use `s1r status` to catch it before starting a session.

The Claude hook sets the Agent call's `model` and `effort` fields where the driver left
them out and keeps every other input, including the agent type and its instructions.
It never replaces a `model` or `effort` the driver wrote on the call, which usually
carries a user's instruction. It routes only the other field and says so:

| The call sets | The hook sets | Description prefix | System message |
|---|---|---|---|
| neither | `model`, `effort` | `sonnet-medium` | `routed-sonnet-medium` |
| `model: opus` | `effort` | `opus(kept)-medium` | `routed-medium kept-opus` |
| `effort: lower` | `model` | `sonnet-lower(kept)` | `routed-sonnet kept-lower` |
| both, or a `model` outside the tier table other than `fable` | nothing | unchanged | none |

An effort is kept as written, including the relative `lower` and `higher`, which Claude
Code resolves. A model the hook does not route to (`haiku`, a full model ID) passes
through unscored, like a call that sets both fields: the hook pairs its effort only with
the models in the tier table and `fable`. The log records these with `"applied": false`.

Per-call `effort` needs Claude Code 2.1.292 or later; forks ignore both fields, and
`CLAUDE_CODE_EFFORT_LEVEL` overrides the routed effort. Non-Agent calls and empty prompts
pass through. When every
provider fails, the hook prints nothing, so the Agent call runs with the model and effort the
driver wrote; the log records the fallback it did not apply with `"applied": false`. A floored
tier or effort still applies the fallback default. Log-write failures do not block spawning.
An agent type whose definition pins a model also passes through unscored, so the pin and the
session's effort apply: the hook matches `subagent_type` to the frontmatter `name:` in
`<cwd>/.claude/agents`, then `${CLAUDE_CONFIG_DIR:-~/.claude}/agents`, and skips any `model:`
other than `inherit`. The log records the pin with `"applied": false`. Built-in and plugin
agents, and files without a pin, are routed.

The scoring model chooses the execution model; these are separate settings.
The shared probability format does not establish equal calibration across providers.
Treat routing quality and the current threshold as experimental.

## Try Claude Code with a shell function

Paste this function into a POSIX-compatible shell, Bash, or Zsh, or add it to your
shell configuration. The hook runs the installed command, so it works from any project.

```sh
beta() (
  unset CLAUDE_CODE_SUBAGENT_MODEL_FORCE CLAUDE_CODE_EFFORT_LEVEL
  s1r status || exit
  claude --model opus \
    --settings '{"hooks":{"PreToolUse":[{"matcher":"Agent","hooks":[{"type":"command","command":"s1r hook","timeout":30}]}],"PostToolUse":[{"matcher":"Agent","hooks":[{"type":"command","command":"s1r hook"}]}],"PostToolUseFailure":[{"matcher":"Agent","hooks":[{"type":"command","command":"s1r hook"}]}],"SubagentStop":[{"hooks":[{"type":"command","command":"s1r hook"}]}]}}' \
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
s1r route --exclude claude --json -- "Explain what a Python list is"
# Example: {"model": "gpt-6.1-terra", "effort": "medium", "tier": "routine", "harness": "codex"}
```

`--json` emits one model/effort/tier/harness object, including when scoring fails and
the fallback is used. `--exclude codex` picks Claude models instead. Excluding every harness
is an error. Put `--` before the
prompt when it may start with `-`, as a task file's bullet list does.

Launch one routed Codex task:

```sh
s1r codex "Summarize this project's README. Do not modify files." \
  --sandbox read-only
```

The prompt is the first argument; remaining arguments are passed to `codex exec`.
The launcher supplies `--model` and `-c model_reasoning_effort=...`; omit competing
model or effort overrides. It forwards Codex's output and exit status, prints the
routing decision to stderr, and preserves Codex's normal permission handling.

Run from the project you want to work on, optionally with another scoring provider:

```sh
ROUTER_PROVIDER=respan ROUTER_MODEL=span-01-pro \
  s1r codex "Explain this project's test structure." --sandbox read-only
```

This routes the initial task, not individual Codex subagents or later turns. The
Claude hook is not a Codex hook. Native Codex subagent routing requires
separate validation of spawn arguments, fork behavior, and model overrides. See
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
for model and effort settings.

## Tests and logs

```sh
python3 -m unittest discover -s tests -v        # Offline tests; no API keys needed (or: make test)
printf '%s\n' '{"tool_name":"Read","tool_input":{}}' | s1r hook
```

The last command should exit successfully without output.

Live evals send every labeled synthetic case in `evals/cases.jsonl` to Respan and to
direct TypeSafe Jev through the production selection, print per-provider metrics (tier
and effort accuracy, under- and over-routing, margins, latency, a confusion matrix), and
write per-case scores with the definitions used to the ignored `artifacts/evals/`. The 150
cases carry a fixed 100/50 train/held-out `split`. Tier labels agree 149/150 with a blind
`gpt-6-astra` relabel; effort labels come from two blind labelers on the stakes rubric
(Cohen's kappa 0.74 and 0.85). Gates sit three cases below the 2026-10-29 baseline:

```sh
python3 -m unittest discover -s evals -v        # Live; needs RESPAN_API_KEY and TYPESAFE_API_KEY
```

To test an Agent call directly, use a synthetic prompt; this makes a live scoring request:

```sh
printf '%s\n' '{"tool_name":"Agent","tool_input":{"prompt":"Explain what a Python list is","subagent_type":"general-purpose"}}' \
  | s1r hook
tail -n 1 ~/.local/state/s1r/beta_log.jsonl
```

Each routing record includes the prompt, working directory, excluded harnesses, selected tier/harness/model/effort,
scores, and provider attempts with timing and error categories. Successful scoring
also records `scoring_provider` and `scoring_model`. Agent records carry `explicit`, the
`model` and `effort` the driver wrote on the call (`{}` for neither; records from before
this field do not say). `model` and `effort` remain the router's pick, so a record whose
`explicit.model` differs from its `model` counts a pick the driver overruled. Logs stay in
`~/.local/state/s1r/beta_log.jsonl`; logging failure does not discard a decision.

Agent records also carry the call's `tool_use_id` and `session_id` from the hook input.
Registered for `PostToolUse`, `PostToolUseFailure`, and `SubagentStop` (as in `beta()`
above), `s1r hook` appends `"kind": "outcome"` records: one per Agent call with the same
`tool_use_id`, and one per subagent stop with its `agent_id`. It prints nothing, never
blocks the call, and stores no prompt or result text:

| Event | Record |
|---|---|
| `PostToolUse`, foreground call | `status: "completed"`, `agent_id`, `resolved_model`, `duration_ms`, `total_tokens`, `tool_uses` |
| `PostToolUse`, background call | `status: "async_launched"`, `agent_id`, `resolved_model`; no totals, since the hook fires at launch |
| `PostToolUseFailure` | `status: "failed"`, `interrupted`, `duration_ms` |
| `SubagentStop` | `status: "stopped"`, `agent_id`, `transcript` (the agent's transcript path) |

The fields come from `tool_response` (`totalDurationMs`, `totalTokens`, `totalToolUseCount`)
and the failure event's `is_interrupt` and `duration_ms`, as Claude Code 2.1.323 sends them.
Non-Agent events pass through without output or a record.

A background call is finished by its first stop record with the same `agent_id`: its
duration is the time between the launch and stop records, to the second. Its tokens are
read from the transcript when `report` runs, not by the hook, because `SubagentStop` fires
before the agent's final message reaches the transcript. They are the last assistant
message's input, cache, and output tokens, the same measure as a foreground call's
`totalTokens`, and stay blank once Claude Code deletes the transcript
(`cleanupPeriodDays`). Foreground calls also stop; their `PostToolUse` totals win. Whether
a background agent that errors still logs a stop, and so counts as completed, is unverified.

```sh
s1r report [--since 2026-11-01] [--json]
```

`report` reads the log and prints one row per picked model and effort: Agent picks, the
share applied, the share the caller overrode (it set a `model` other than the scored
tier's, counted over records that have `explicit`), and, for picks joined to outcomes of
calls that ran as picked (applied, with no caller `model` or `effort`), completed counts
(foreground, and background with a stop), background launches with no stop yet, failed
counts, the error rate, and median duration and tokens of completed calls. Pass-through records with no pick (a pinned agent, a call that set
both fields) count under `-`. Older records without the new fields count where they can.

Prompts are sent to the selected scoring service and, on failure, the next ones in the chain. Keep
credentials and logs out of commits and use synthetic prompts for integration tests.

## Files

| File | Purpose |
|---|---|
| `system_one_router/cli.py` | The `s1r` command: `route`, `codex`, `hook`, `report`, `status`, `update`, `version` |
| `system_one_router/model_provider.py` | Provider configuration, credentials, API adapters, and fallback |
| `system_one_router/route.py` | Tier and effort definitions, the tier table, fallbacks, selection, and logs |
| `system_one_router/agent_hook.py` | Claude Code `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, and `SubagentStop` JSON interface |
| `system_one_router/report.py` | Summarize Agent picks and outcomes from the log |
| `system_one_router/codex_run.py` | Launch a whole Codex task with the selected model and effort |
| `pyproject.toml`, `install.sh`, `Makefile`, `scripts/`, `.github/workflows/release.yml` | Packaging, the release installer, and the release pipeline |
| `tests/test_model_provider.py`, `tests/test_route.py`, `tests/test_codex.py`, `tests/test_report.py` | Offline provider, routing, hook, launcher, and report regression tests |
| `evals/cases.jsonl`, `evals/test_live_routing.py` | Labeled synthetic routing cases and the live evals that score them |
