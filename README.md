# Design Doc Evolution Harness

`auto_design_doc` is an autonomous, multi-hour orchestration loop that takes a
technical design document and evolves it — grounding every claim in cited
evidence, scoring each revision across multiple quality dimensions, and keeping
only the rounds that demonstrably improve the doc.

You hand it a goal and a starting doc (empty, a stub, or a full draft). It runs
unattended — overnight, typically — spawning specialized agents (planner,
designer, reviewer, cross-model verifier) round after round against a set of
competing variants. Every terminal outcome is one git commit with a structured
trailer, so the entire run is auditable from `git log` alone. In the morning you
read `morning_brief.md`, drop a pivot if the direction needs to change, and let
it keep going.

The harness is **stdlib-only Python** (no third-party runtime dependencies). It
shells out to whatever model CLIs you have installed — `claude`, `codex`,
`gemini` — so you can run it against a single model or mix several.

---

## How it works

The loop is built on a strict division of labor:

- **The harness owns** scheduling, validation, git, derived data, crash
  recovery, and the mechanical evidence checks (Verifier A and B are pure
  Python — no model calls).
- **The agents own** judgment, exploration, and writing.

Each round runs a small state machine:

1. **Planner** proposes what to change this round, checked against the goal,
   scope, and the rejection log.
2. **Designer** writes a patch to the doc plus evidence claims, in a per-round
   stance (entry-point-first, leaf-first, and so on) to vary exploration.
3. **Verifier A / B** (pure Python) confirm every citation resolves and every
   excerpt matches its source. A failure ends the round immediately.
4. **Reviewer** independently judges the patch. Its context never contains the
   designer's reasoning — independence is enforced in the filesystem.
5. **Verifier C** (a different model from the designer) cross-checks claims on a
   configurable cadence.
6. **Scorecard merge gate** admits the round only if it improves at least one
   dimension and regresses none beyond tolerance.

A round produces exactly one commit per terminal outcome (`merge`, `reject`,
`fail`, ...). A crash leaves no half-state: recovery is `git checkout -- . &&
git clean -fd` back to the last clean round.

Scored dimensions: groundedness, goal alignment, technical correctness,
completeness, coherence, and constitution compliance.

---

## Requirements

- **Python 3.11+** (the harness uses the stdlib `tomllib` parser).
- **Git** on `PATH`.
- At least one model CLI on `PATH`, matching the tools you configure:
  - `claude` — invoked as `claude -p --output-format json --model <id>`
  - `codex` — invoked as `codex exec --model <id> --json`
  - `gemini` — invoked as `gemini --model <id> --output json`

Each CLI must be independently authenticated before you run the harness. The
harness never handles credentials itself; it only spawns the tools.

---

## Installation

```bash
git clone <repo-url> auto_design_doc
cd auto_design_doc

python -m venv .venv
source .venv/bin/activate

pip install -e .
```

The package installs as `auto-design-doc` and exposes a `harness` console
script. Both of these are equivalent:

```bash
harness --help
python -m harness --help
```

The examples below use the `harness` command; substitute `python -m harness` if
you prefer not to rely on the installed script being on `PATH`.

---

## Quick start

### 1. Scaffold a workspace

```bash
harness init ./my-design
```

This copies the workspace template into `./my-design`, initializes a git
repository there, and wires up the pre-commit and commit-msg hooks. The target
must not exist or must be an empty directory.

The scaffold contains:

| File | Purpose |
|---|---|
| `harness.toml` | Orchestrator configuration (models, run bounds, gates). |
| `goal.toml` | The goal and the registry of decisions the doc must address. |
| `constitution.md` | Judgment rules loaded into every agent's context. |
| `seed_doc.md` | The starting document (empty, stub, or full draft). |
| `hooks/` | `pre-commit` and `commit-msg` validators. |

### 2. Edit the goal and seed

Open `my-design/goal.toml` and replace the example with your real goal and the
list of decisions the design must resolve. Then choose a starting state for
`my-design/seed_doc.md`:

- **Empty** — leave it blank; early rounds build structure from the goal.
- **Stub** — add an outline or a few notes to anchor early rounds.
- **Drafted** — paste a complete human-reviewed doc; the harness refines and
  extends it.

All three flow through the same pipeline.

#### Grounding in a codebase (optional)

Drop a read-only copy of the relevant code into `my-design/repo/`. When `repo/`
is present, each round runs a **repo-query pass**: the designer names the
specific facts it needs from the code, a `repo_adapter` reads the real files and
returns cited evidence, and the designer authors against that evidence. Results
are cached by `(repo HEAD sha, question)` in `derived/` so unchanged questions
aren't re-asked. It's best-effort — if the adapter can't run, the round proceeds
without repo evidence. The adapter reads files, so the model you assign to
`[models.repo_adapter]` must have filesystem read access in your environment.

### 3. Run the loop

```bash
harness run --workspace ./my-design --hours 8 --variants 3
```

At least one of `--rounds` or `--hours` is required — whichever cap is reached
first ends the run.

| Flag | Default | Meaning |
|---|---|---|
| `--workspace` | current directory | The scaffolded workspace to run in. |
| `--rounds` | none | Maximum number of rounds. |
| `--hours` | none | Maximum wall-clock hours. |
| `--variants` | `2` | Number of competing variants to rotate across. |

When the run ends, the harness writes `morning_brief.md` into the workspace
summarizing trajectory, still-weak areas, rejected rounds, and what to look at
first.

### 4. Review and pivot

Read `my-design/morning_brief.md`. The current state of each variant lives under
`variants/nodes/v-NNN/`. To change direction between runs, drop a pivot file in
`pivots/` and start another run — the harness reconciles it before scheduling
the next round. The full audit trail is in `git log` (each round is one commit
with a structured trailer) and in `actions.jsonl`.

To resume in a fresh clone of a workspace, re-wire the hooks first:

```bash
harness init ./my-design --reactivate
```

### Editing config between runs

`goal.toml` and `harness.toml` are tracked, and every commit must carry an
`Action` trailer that the commit-msg hook validates — so a hand-made `git
commit` of a config tweak is fiddly. Use the helper, which stages only the
config file(s) and attaches the right trailer:

```bash
harness commit-config -m "raise max_rounds to 300" --workspace ./my-design
```

### Preflight check (before a long run)

`harness doctor` verifies a workspace is ready before you commit to an 8-hour
run — it checks each role's CLI is installed, lists MCP server health, and
**spawns each tool-enabled role once** with a sentinel-file probe to confirm its
tools actually execute (not just that the CLI launched).

```bash
harness doctor --workspace ./my-design          # full check (spawns probes)
harness doctor --workspace ./my-design --no-probe  # static only, no spawns
```

It reports CLIs found, surfaces MCP servers that need auth/connection as
warnings, and marks a tool probe `FAIL` if the role couldn't read the planted
file (i.e. its tools are denied in your environment). Exit code is non-zero only
for hard problems (missing CLI, `mcp list` error, failed probe), so it's usable
in a pre-run script. Run it after configuring `allowed_tools`/`mcp_config` and
authenticating MCP servers.

### Resetting a workspace

Roll a workspace back to a clean starting point. Round numbering is derived from
the `rounds/` directory, so this also clears it to restart at round 1.

```bash
# Keep the seeded docs + round-0 baseline; just discard the rounds since then:
harness reset --to seed --workspace ./my-design

# Bare workspace — re-seeds and re-scores on the next run (use after editing
# seed_doc.md or goal.toml):
harness reset --to scaffold --workspace ./my-design
```

Both prompt for confirmation (they hard-reset git and delete round artifacts);
pass `--yes` to skip the prompt.

---

## Model configuration

Model assignment is per role, set in `harness.toml`. Each role names a `tool`
(`claude`, `codex`, or `gemini`) and a pinned `model` snapshot:

```toml
[models.planner]
tool  = "claude"
model = "claude-haiku-4-5-20251001"

[models.designer]
tool  = "claude"
model = "claude-opus-4-7-20260315"

[models.reviewer]
tool  = "claude"
model = "claude-opus-4-7-20260315"

[models.verifier_c]
tool  = "codex"
model = "gpt-5-20260301"
```

### Multiple models (recommended)

The default configuration mixes models on purpose. The designer and reviewer run
on a strong model, the planner on a cheaper one, and **Verifier C runs on a
different model family** so cross-model verification is genuinely independent —
same-model self-verification is degenerate and catches far less. Use this when
you have more than one CLI authenticated and want the strongest grounding.

### Single model

If you only have one CLI available, point every role at the same tool and model:

```toml
[models.planner]
tool  = "claude"
model = "claude-opus-4-7-20260315"

[models.designer]
tool  = "claude"
model = "claude-opus-4-7-20260315"

[models.reviewer]
tool  = "claude"
model = "claude-opus-4-7-20260315"

[models.verifier_c]
tool  = "claude"
model = "claude-sonnet-4-6"
```

Even in single-vendor mode, prefer a *different* model for Verifier C (for
example a smaller Claude model) so the cross-check is not the designer grading
its own work. A weaker independent check is still more useful than none.

### Tools and MCP (gh, gdrive, glean, …)

A role spawn has **no tool access by default** — it only reasons over the
context it is given. To let a role read files, shell out, or reach a workspace
endpoint over MCP, add tool keys to its `[models.<role>]` block. This is mainly
for `repo_adapter` (which must read `repo/`) and any source-fetching role.

```toml
[models.repo_adapter]
tool          = "claude"
model         = "claude-opus-4-7-20260315"
allowed_tools = ["Read", "Grep", "Glob", "Bash(gh:*)", "mcp__gdrive"]
mcp_config    = [".mcp.json"]      # Claude's native MCP convention
# strict_mcp_config = true          # use only these MCP files, ignore global
# permission_mode   = "acceptEdits" # if tools are silently denied headless
```

- `allowed_tools` — the whitelist (`Bash(gh:*)` scopes bash to `gh`; `mcp__<server>`
  exposes an MCP server's tools). Nothing runs unless it is listed.
- `mcp_config` — path(s) to MCP server JSON. Edit `.mcp.json` in the workspace to
  add servers (gdrive, glean, …). A missing file is ignored.

**MCP config is per-CLI — `.mcp.json` is Claude-only.** For the others, point the
CLI at its own native MCP config:

| CLI | Where MCP servers live | Relevant `[models.<role>]` keys |
|---|---|---|
| `claude` | `.mcp.json` | `allowed_tools`, `mcp_config`, `strict_mcp_config`, `permission_mode` |
| `codex` | `~/.codex/config.toml` `[mcp_servers]` | `sandbox`, `extra_args = ["-c", "…"]` |
| `gemini` | `.gemini/settings.json` (`gemini mcp`) | `mcp_server_names`, `allowed_tools`, `approval_mode` |

`extra_args` (any CLI) is appended verbatim as an escape hatch for flags not
covered above.

> Tools run in the CLI's own permission/sandbox model. Give a role only the
> tools it needs, and scope bash (`Bash(gh:*)`, not bare `Bash`).

### Run bounds and gates

The remaining `harness.toml` sections tune cadence and the merge gate:

```toml
[run]
max_rounds            = 200   # absolute round cap (overnight targets 100-120)
max_wall_clock_hours  = 12    # second cap
verifier_c_every      = 1     # 1 = every round; raise to reduce verifier spend
patch_max_sections    = 3     # reject round-commits touching more than N sections
spawn_timeout_seconds = 300   # absolute cap per CLI spawn
silence_timeout_seconds = 300 # cap for no stdout/stderr activity during a spawn

[scorecard]
regression_tolerance  = 0.05  # a dimension may not drop more than this and still merge

[claim_graph]
stale_proposals_threshold_rounds  = 5
bootstrap_registry_size_threshold = 5
```

Note that the `[run]` values in `harness.toml` are the configured defaults; the
`--rounds` and `--hours` flags passed to `harness run` set the caps for
a given invocation.

---

## Workspace layout

After a few rounds, a running workspace looks like this:

```
my-design/
├── harness.toml              # configuration
├── goal.toml                 # goal + decision registry (versioned)
├── constitution.md           # judgment rules (versioned)
├── seed_doc.md               # starting document
├── evidence/                 # ev-*.md — cited evidence, written by the harness
├── rejections/               # rj-*.md — why rounds were rejected
├── pivots/                   # pv-*.md — human-dropped direction changes
├── variants/nodes/v-NNN/     # competing variants: doc sections + scorecard.json
├── rounds/round-NNNNNN/      # per-round plan, patch, review, verification, decision
├── actions.jsonl             # append-only log of every tool call and denial
├── morning_brief.md          # written at the end of each run
└── hooks/                    # pre-commit + commit-msg validators
```

Generated artifacts (`derived/`, `rounds/*/scratch/`, per-spawn `CONTEXT.md`) are
gitignored and rebuilt on demand — git is the single source of truth.

---

## Auditing a run

Because every state transition is a commit with a closed-vocabulary trailer, the
entire history is queryable without any custom tooling:

```bash
# Score trajectory across the run
git log --format=%B | grep "^Score-Delta:"

# Rejections by class
git log --grep "^Reason: scope-violation"

# Full history of one variant
git log -- variants/nodes/v-007/

# Reviewer accept rate, coverage, denials
jq 'select(.denied)' actions.jsonl
```

---

## Project status

This is the v0 harness. The `harness/` package implements the orchestrator,
context builder, subprocess wrapper, pure-Python verifiers, scorecard, claim
graph, round ledger, and morning brief. Deferred follow-ups (concurrency
lockfile, additional rejection classes, live access-policy enforcement, and
claim-graph wiring into the morning brief) are tracked in
[`TODOS.md`](./TODOS.md). The full design rationale lives in
[`auto_design_spec.md`](./auto_design_spec.md) and under `docs/superpowers/`.
