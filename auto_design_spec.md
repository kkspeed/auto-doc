# Design Doc Evolution Harness

## 0. Purpose

Multi-hour autonomous loop. Refines tech design doc. Grounded in repo + external sources. No human in loop during run. Human pivots between rounds. Output: one doc. Process: 3–5 evolving variants.

## 1. Layout

```
project-root/
├── workspace/        # harness state, has .git
├── repo/             # external code, read-only worktree(s), pinned SHA
├── sources/          # other adapters' caches, gitignored
└── .harness/         # orchestrator-private, agent-invisible
    ├── access-policy.yaml
    ├── secrets.env
    └── locks/
```

```
workspace/
├── .git/
├── CONTEXT.md                 # per-spawn, gitignored
├── harness.yaml               # config
├── goal.yaml                  # versioned in git
├── constitution.md            # versioned in git
├── schemas/*.schema.json
├── hooks/                     # see §6
├── evidence/{ev-*.md, INDEX.md}
├── rejections/{rj-*.md, INDEX.md}
├── pivots/pv-*.md
├── variants/nodes/v-NNN/
│   ├── doc/NN-*.md            # frontmatter = provenance
│   └── scorecard.json
├── rounds/round-NNNNNN/
│   ├── manifest.json
│   ├── plan.md
│   ├── patch.diff
│   ├── review.md
│   ├── verification.json
│   ├── decision.json
│   └── scratch/               # gitignored
├── actions.jsonl              # tool calls + denials
├── derived/                   # gitignored, rebuilt
└── archive/
```

Gitignore: `CONTEXT.md`, `derived/`, `rounds/*/scratch/`, `*.tmp`, `repo/`, `sources/*/cache/`.

## 2. Names

| Thing | Format |
|---|---|
| Variant | `v-NNN` |
| Round | `round-NNNNNN` |
| Evidence | `ev-NNNNNN` |
| Rejection | `rj-NNNNNN` |
| Pivot | `pv-NNN` |
| Goal ver | `g-NN` |
| Constitution ver | `c-NN` |
| Repo ref | full SHA stored, 7-char displayed |
| Doc section | `NN-kebab.md`, step 10 |

UTC ISO-8601. Forward slashes. IDs URL-safe lowercase.

## 3. File schemas

**Evidence** (`evidence/ev-*.md`):
```markdown
+++
id = "ev-001024"
ts = "2026-05-18T04:55:51Z"
round = "round-000042"
variant = "v-007"
role = "designer"
model = "claude-opus-4-7-20260315"
goal_version = "g-03"
constitution_version = "c-02"
confidence = "high"     # one of: "high", "medium", "low"
citations = [{source = "...", ref = "...", lines = "...", sha = "..."}]
# `supersedes` is omitted when null; set when this evidence is superseded:
# supersedes = "ev-NNNNNN"
verification = {phase_a = "pass", phase_b = "pass", phase_c = "pending"}
caveats = []
+++
# Claim
...
# Excerpt
...
# Unresolved
...
```

Frontmatter is TOML delimited by `+++` (stdlib `tomllib` parses it; spec frontmatter does NOT use YAML because the v0 harness commits to stdlib-only Python — no `PyYAML` dep).

**Rejection** (`rejections/rj-*.md`): frontmatter has `reason_class` (closed: scope-violation, duplicate-of-prior, uncited-claim, phase-a-fail, phase-b-fail, phase-c-dispute, constitution-violation, coverage-gap, score-regression, other), `patch_ref`, `evidence_against`, `evidence_disputed`, `supersedable_by`. Body = freeform.

**Doc section** (`variants/nodes/v-NNN/doc/NN-*.md`):
```markdown
+++
section_id = "retry-policy"
created_round = "round-000042"
created_role = "designer"
goal_version = "g-03"
evidence_ids = ["ev-001024"]
tags = ["decided"]
history = [{round = "round-000042", op = "create", evidence_added = ["ev-001024"]}]
+++
## Retry policy
The service uses exponential backoff [^ev-001024]...
```

Citation: `[^ev-NNNNNN]`. Only legal form.

**Pivot** (`pivots/pv-*.md`): frontmatter has `type` (scope_expansion | requirement_change | correction | constraint_added | scope_cut | source_added), `goal_updates`, `invalidate`, `preserve`. Body = freeform.

**INDEX.md** (per dir): regenerated, gitignored. One line per entry: `ev-001024 | round-000042 | high | retry policy claim`.

**actions.jsonl**: `{ts, round, variant, role, model, tool, args, result_summary, duration_ms, denied?, deny_reason?}`. Append-only. Diagnostic.

Schemas in `schemas/`. Pre-commit hook validates frontmatter.

## 4. Access enforcement

**Two-layer.** PreToolUse hook (in-loop, where supported) + pre-commit hook (post-hoc, always).

Intent matrix (what *should* be allowed):

| Path | Designer | Reviewer | Verifier C | Planner | Reconciler | Repo Adapter |
|---|---|---|---|---|---|---|
| `CONTEXT.md`, `goal.yaml`, `constitution.md` | R | R | R | R | R (RW goal+const) | R |
| `harness.yaml`, `.harness/` | – | – | – | – | – | – |
| `evidence/` | create-only | R | R | R | create-only | – |
| `rejections/` | R | create-only | create-only | R | create-only | – |
| `pivots/` | R | R | R | R | append | – |
| `variants/{own}/doc/` | RW | R | R | R | RW | – |
| `variants/{other}/doc/` | R | R | R | R | R | – |
| `rounds/{own}/` | RW | RW | RW | RW | R | scratch only |
| `actions.jsonl` | append | append | append | append | append | append |
| `repo/` | R | R | R | – | – | R |
| `sources/` | R | R | R | – | – | – |

`create-only`: agent may create new files matching ID pattern; never edit existing. New canonical files (evidence, rejections) actually written by orchestrator from agent JSON output in `rounds/{round}/scratch/`. The hook permits agent self-writes too for cases where the orchestrator-write path isn't used.

## 5. Access policy file

`.harness/access-policy.yaml`:

```yaml
roles:
  designer:
    read: ["**"]
    write:
      allow:
        - "rounds/{round}/**"
        - "variants/nodes/{variant}/doc/**"
        - "variants/nodes/{variant}/passport.json"
      create_only:
        - "evidence/ev-*.md"
        - "rounds/{round}/scratch/**"
      deny: ["**"]
    bash:
      allow: ["git log:*", "git show:*", "git blame:*", "cat:*", "head:*", "wc:*", "ls:*", "grep:*", "find:*"]
      deny: ["*"]
  reviewer:
    read: ["**"]
    write:
      allow: ["rounds/{round}/**"]
      create_only: ["rejections/rj-*.md", "rounds/{round}/scratch/**"]
      deny: ["**"]
    bash: { allow: [...], deny: ["*"] }
  verifier_c:
    read: ["evidence/**", "rounds/{round}/**", "repo/**", "schemas/**"]
    write:
      allow: ["rounds/{round}/verification.json", "rounds/{round}/scratch/**"]
      create_only: ["rejections/rj-*.md"]
      deny: ["**"]
    bash: { allow: ["git show:*", "cat:*", "head:*"], deny: ["*"] }
  planner:
    read: ["goal.yaml", "constitution.md", "rejections/**", "variants/nodes/{variant}/**", "pivots/**"]
    write:
      allow: ["rounds/{round}/plan.md", "rounds/{round}/scratch/**"]
      deny: ["**"]
    bash: { allow: ["cat:*", "ls:*"], deny: ["*"] }
  reconciler:
    read: ["**"]
    write:
      allow: ["goal.yaml", "constitution.md", "pivots/**", "variants/nodes/*/doc/**", "rounds/{round}/**"]
      create_only: ["evidence/ev-*.md"]
      deny: ["**"]
    bash: { allow: ["git log:*", "git show:*", "git blame:*"], deny: ["*"] }
  repo_adapter:
    read: ["repo/**", "rounds/{round}/scratch/**", "schemas/**"]
    write:
      allow: ["rounds/{round}/scratch/**"]
      deny: ["**"]
    bash:
      allow: ["git log:*", "git show:*", "git blame:*", "grep:*", "find:*", "cat:*", "head:*", "wc:*", "ls:*"]
      deny: ["*"]

shell_safety:
  deny_contains: [";", "&&", "||", "|", ">", ">>", "<", "`", "$("]
  exceptions_per_role: {}
```

Orchestrator sets env at spawn: `HARNESS_ROLE`, `HARNESS_VARIANT`, `HARNESS_ROUND`. Hook substitutes `{variant}` and `{round}` from env.

## 6. Hooks

`workspace/hooks/`:

- `pre-tool-use.py` — loads `.harness/access-policy.yaml`, reads env, rules per call. Logs every event (allowed + denied) to `actions.jsonl`. Returns deny with explicit reason.
- `pre-commit` — validates frontmatter against schemas, citation completeness (`[^ev-*]` resolves to non-superseded), `decided` sections immutable, patch touches ≤ N sections, trailer keys legal.
- `commit-msg` — validates commit trailer schema, closed key set.

PreToolUse fires for Claude Code agents only. Other vendors fall back to pre-commit as primary defense (see §7).

Shell command parsing: `shlex.split`, command-name allowlist match against `bash.allow`, deny any string containing `shell_safety.deny_contains` unless role has explicit exception.

## 7. Model assignment

Each role rotation entry is a CLI invocation spec, not just a model:

`harness.yaml`:
```yaml
roles:
  designer:
    rotation:
      - cmd: claude
        model: claude-opus-4-7-20260315
        flags: ["-p", "--allowedTools", "Read,Grep,Glob,Bash,Write,Edit"]
        hook_capable: true
        stance_rotation: [entry-point-first, leaf-first, test-first, config-first]
      - cmd: codex
        model: gpt-5-20260301
        flags: ["exec", "--full-auto"]
        hook_capable: false
        stance_rotation: [...]
  reviewer:
    rotation: [...]
  verifier_c:
    rotation:
      - cmd: codex
        model: gpt-5-20260301
        flags: [...]
        hook_capable: false
    policy: every_3rd_round
  planner:
    rotation: [{cmd: claude, model: claude-haiku-4-5-20251001, ...}]
  repo_adapter:
    rotation:
      - cmd: claude
        model: claude-opus-4-7-20260315
        hook_capable: true     # required for this role
    constraint: hook_capable_only

constraints:
  designer_and_reviewer_must_differ: true
  verifier_c_must_differ_from_designer: true
```

**`hook_capable: false` agents** (Codex, etc.) get no PreToolUse enforcement. Defense relies on:
1. Tool allowlist passed to the CLI itself (vendor-specific flags)
2. Post-hoc pre-commit hook rejecting forbidden changes (round fails at commit)
3. Restricted role assignment: roles with strict read-only or sensitive paths (repo adapter, reconciler) require `hook_capable: true`

Pin model snapshots. Single-family fallback: all rotations one cmd+model.

## 8. CLI runner

Replaces "model adapter layer." Per-vendor differences live in templates, not API client code.

```python
@dataclass
class AgentSpawn:
    cmd: str                       # "claude" | "codex" | ...
    model: str
    flags: list[str]
    cwd: Path                      # workspace/
    env: dict                      # HARNESS_ROLE, HARNESS_VARIANT, HARNESS_ROUND, secrets
    prompt: str                    # full prompt; CONTEXT.md inlined or referenced
    output_path: Path              # where agent writes structured output
    timeout_s: int
```

`run()`: shells out, streams stdout/stderr to `rounds/<n>/scratch/<role>.log`, waits, reads `output_path`. Validates against schema. Retries once on schema failure.

Per-vendor template files (`adapters/cli/claude.yaml`, `adapters/cli/codex.yaml`) capture flag syntax, tool-allowlist format, output conventions. Adding a vendor = one template.

## 9. Repo adapter

Sandbox: `cwd=workspace/`, but the role-policy denies all writes outside `rounds/{round}/scratch/`, all `bash` outside the read-only allowlist. `repo/` is also mounted read-only at OS level as belt-and-suspenders.

**`hook_capable: true` required.** The repo is huge and external; in-loop enforcement of read-only matters more here than anywhere.

Spawn config:
- Pinned SHA per run, `git worktree add --detach` to `repo/`
- Turn limit ~20–30; partial returns with `unresolved` populated
- Skill `repo-exploration` loaded: structure-first, follow imports, distinguish def/use, quote-don't-paraphrase, budget-aware
- Stance assigned per call (entry-point-first / leaf-first / etc.)

Returns Evidence-shaped JSON to `rounds/<n>/scratch/repo-query-<id>.json`. Orchestrator validates + writes `evidence/ev-*.md`.

Cache: `(question_hash, repo_sha) → evidence_id`. Invalidate across SHA.

## 10. Source adapter interface

```
sources/<name>/
├── adapter.yaml   # name, version, query, auth
├── index.md       # cached items, stable IDs
└── cache/         # gitignored
```

Returns Evidence JSON. `citations[].source = "<name>"`. Schema-validated. Adding source = one `adapter.yaml` + one spawn script. Same role-policy and hook treatment as repo adapter.

## 11. Round state machine

```
1. orchestrator: pick variant + roles + models per harness.yaml rotations
2. orchestrator: mkdir rounds/<n>/, write manifest.json
3. orchestrator: regenerate CONTEXT.md for Planner
4. spawn Planner (env: HARNESS_ROLE=planner, HARNESS_ROUND=<n>, HARNESS_VARIANT=<v>)
5. orchestrator pre-flight: plan vs goal/scope/rejection log
   FAIL → commit "plan-rejected", end
6. regenerate CONTEXT.md for Designer
7. spawn Designer → patch.diff + evidence JSON in scratch/
8. orchestrator validates + writes evidence/ev-*.md
9. Verifier A (pure Python: cite exists at SHA?)
   FAIL → commit "verifier-a-fail", end
10. Verifier B (pure Python: excerpt matches?)
    FAIL → commit "verifier-b-fail", end
11. regenerate CONTEXT.md for Reviewer (no Designer reasoning)
12. spawn Reviewer → review.md (+rejection JSON if reject)
    REJECT → write rj-*.md, commit "reviewer-rejected", end
13. if policy: spawn Verifier C → verification.json
    DISPUTE → write rj-*.md, commit "verifier-c-disputed", end
14. orchestrator applies patch to variants/<v>/doc/
15. orchestrator updates section frontmatter (provenance)
16. orchestrator computes scorecard, writes scorecard.json
17. commit "round NNNN: merged" with full trailer
18. orchestrator rebuilds derived/
19. check stopping criteria
```

Verifier A and B are pure Python, not agent spawns. No CLI, no model.

One commit per terminal outcome. Mid-round writes uncommitted. Crash → `git checkout -- . && git clean -fd`, round didn't happen. Idempotent.

## 12. Round manifest

`rounds/<n>/manifest.json`: variant, started_at, goal_version, constitution_version, repo_sha, roles{cmd, model, stance, adversarial_angle, hook_capable}, policy{verifier_c, checkpoint_intensity}.

Written first. Fixed for the round.

## 13. Commits

One per terminal outcome + structural events (pivot, spawn, archive, consolidate, goal/constitution bump).

Trailer (closed keys):
```
Round: round-000042
Variant: v-007
Role: reviewer
Cmd: codex
Model: gpt-5-20260301
Hook-Capable: false
Action: reject|accept|merge|spawn|archive|pivot|consolidate|fail
Reason: <reason_class or none>
Evidence-Added: ev-..., ev-...
Evidence-Disputed: ev-...
Rejection-Id: rj-...
Goal-Version: g-03
Constitution-Version: c-02
Repo-Sha: 7f3a9b27...
Score-Delta: groundedness=+0.04 coherence=+0.01 ...
```

`commit-msg` hook enforces. Unknown keys = rejected commit.

Derived data reconstructible from `git log --format=%B | git interpret-trailers --parse` + frontmatter scans. Git wins on disagreement.

## 14. CONTEXT.md

Per-spawn, ~30–80 lines:

1. Role this round (role, variant, stance, model, deliverable)
2. What to read first (ordered paths, role-specific)
3. What's new (recent pivots, recent rejections this variant)
4. Where evidence comes from (repo SHA, available sources)
5. How to record (output paths, schemas)
6. Hard constraints (denials you'll hit, what the hook will reject)

Reviewer's "read first" excludes Designer's plan + designer-cited evidence ordering. Independence in the filesystem.

Parameterized by role + manifest. No hand-tuning.

## 15. Variant DAG

Start: linear main, variants as directories under `variants/nodes/`. Migrate to branch-per-variant if cross-contamination becomes real.

Crossover = real git merge (if branch-per-variant) or explicit consolidation commit (if linear). Archive = move to `archive/`.

Population: `harness.yaml.population.max_active_variants`. Spawn policy: `on_plateau | every_n_rounds | manual`.

Per-variant: stance rotates per round. Optional cross-variant model assignment for diversification.

## 16. Constitution

Prose (`constitution.md`): judgment rules. Weight evidence by doc-status. Treat non-finalized sources as weak signal. Distinguish observation/inference/decision. Never invent APIs. Prefer reducing scope over speculation. Surface conflicts.

Hooks: mechanical rules (pre-commit):
- All doc claims have `[^ev-*]` citation
- All `[^ev-*]` resolve to non-superseded evidence
- Sections tagged `decided` immutable without goal-version override
- Patch touches ≤ N sections
- Frontmatter validates
- Trailer schema present

PreToolUse: access policy (already covered).

Hook rejection = round fails. Loud and final.

Loaded into every CONTEXT.md. Re-injected mid-session every N turns for long agent sessions.

## 17. Scorecard

Merge rule: improve ≥1 dim, regress 0 below threshold.

Dimensions: groundedness, goal_alignment, technical_correctness, completeness, coherence, constitution_compliance.

Scoring model: goal_alignment and technical_correctness are Reviewer judgments (the latter scaled by Verifier C confirm-rate). groundedness and coherence are Reviewer-judged on a continuous [0,1] scale, each **capped** by its mechanical check (`min(llm, mechanical)`): an objectively clean dimension lets the continuous judgment through, while an objective defect (ungrounded claim, dead/superseded citation) caps the score. completeness is **purely Reviewer-judged** — its mechanical proxy (a doc section whose `section_id` equals a decision id) is too coarse to cap a real judgment (decisions are routinely covered in prose or shared sections), so it is used only as a fallback when the Reviewer omits the score. constitution_compliance stays a pure mechanical policy count (1 − denied/total). The caps/judgments exist because the bare count-fractions snap to 0.0/1.0 on the small inputs of early rounds, which made the merge gate reject legitimate progress.

Seed baseline (round 0): when a seed doc is present, a one-time **seed-judge** spawn scores it on all six dimensions and writes the round-0 `scorecard.json` per variant *before* round 1. This means round 1 onward is gated against the seed's real quality instead of bootstrapping into the mechanical "empty input → 1.0" defaults (which made every later round look like a regression and get rejected). No dimension is assumed perfect; an empty/stub seed scores low and is easy to improve, a thorough drafted seed scores high and must be genuinely beaten. The seed-judge spawn degrades gracefully (a failed judge → no baseline → round 1 bootstraps) so a flaky judge never blocks the run.

Per-variant `scorecard.json` updated on merge. Trajectory in commit trailers, derivable via `git log`.

## 18. Pivots

Trigger: human drops `pivots/pv-NNN.md` between rounds. Orchestrator polls before next round.

Pivot frontmatter: `type`, `goal_updates`, `invalidate`, `preserve`. Body freeform.

Reconciler runs once:
1. Walk `evidence/` — mark contradicted as stale (re-verify, don't delete)
2. Walk `variants/<all>/doc/` — tag sections keep|revisit|obsolete
3. Bump `goal.yaml`/`constitution.md` version if needed
4. Adjust scorecard dimensions if pivot demands
5. Write pivot brief → loaded into subsequent CONTEXT.md

Conservative default: keep. Require evidence to invalidate.

Asymmetric apply for uncertain pivots (pivot 2 of 4, keep 2 on old path).

Post-pivot: forced re-derivation round in affected areas. Designer must justify kept decisions against new goal. Breaks anchoring.

Pivot whiplash: track pivot rate. Surface if high.

## 19. Stopping

Any → pause:
- `max_rounds` or `max_wall_clock_hours` exceeded
- Plateau: N consecutive rounds no merge above threshold + rejection diversity dropped
- Oscillation: patch+revert pattern
- Failure cascade: 3 consecutive failed rounds same variant → pause variant
- Score regression: monotonic decline over M rounds

Pause = stop scheduling, write `STOPPED.md`, leave workspace clean. Resume = remove `STOPPED.md`.

Convergence pass: consolidate best variants → single doc → final Verifier C on all claims.

## 20. Coverage

Computed from `actions.jsonl`: per-file/dir read counts.

Surfacing:
- Designer CONTEXT.md (every N rounds): under-explored dirs
- Repo adapter response: appended hint
- Reviewer coverage critique pass

Curiosity rounds every K rounds: no specific question, scout under-touched dirs.

## 21. Exploration variance

Per-spawn stance rotation (Designer + Reviewer always differ this round). ~20% unfamiliar-territory budget per session. Pre-plan + one orchestrator-injected perturbation.

Cross-round: stance for variant N rotates by round. Persistent stance bias optional, default off.

## 22. Failure modes monitored

| Mode | Mitigation |
|---|---|
| Hallucination | Verifier A/B mechanical, C cross-model |
| Reviewer rubber-stamp | Accept rate target 30–70%, disagreement rate on verification, adversarial hit rate |
| Coverage drift | Coverage map + curiosity rounds + critique |
| Anchoring post-pivot | Forced re-derivation, pivot brief loaded first |
| Oscillation | Rejection log consulted pre-plan, loop detector |
| Length bloat | Length-aware scoring, periodic consolidation |
| Variant convergence | Stance rotation, optional cross-model assignment, archive duplicates |
| Pivot whiplash | Track pivot rate, surface |
| Schema drift | Pre-commit validates frontmatter |
| Capability shortcut | Hook-enforce, don't trust prompts |
| Hook gap on non-Claude | Restrict sensitive roles to `hook_capable: true`; pre-commit as backstop |
| Access policy bug | Denials logged to actions.jsonl, reviewable |

## 23. Skills

```
.claude/skills/
├── design-doc-patching/SKILL.md
├── repo-exploration/SKILL.md
├── adversarial-review/SKILL.md
├── evidence-verification/SKILL.md
├── pivot-reconciliation/SKILL.md
└── source-adapter-authoring/SKILL.md
```

Loaded by role-appropriate agents. Skills = how to do work; harness = what work + what to keep.

## 24. Harness components

```
harness/
├── orchestrator.py        # round loop, state machine
├── context_builder.py     # generates CONTEXT.md per spawn
├── cli_runner.py          # spawns agents per harness.yaml templates
├── schema_validator.py    # frontmatter + JSON validation
├── verifier_ab.py         # pure-Python phase A and B
├── scorecard.py           # multi-dim scoring, merge rules
├── coverage.py            # actions.jsonl → derived/coverage.json
├── stopping.py            # convergence/oscillation/cascade detectors
├── git_ops.py             # commit trailer write/parse, recovery
├── adapters/cli/{claude.yaml, codex.yaml, ...}
└── recovery.py            # crash → git checkout, resume

workspace/hooks/
├── pre-tool-use.py        # access policy enforcement (PreToolUse)
├── pre-commit             # frontmatter, citation, decided-section, trailer
└── commit-msg             # trailer schema
```

Harness owns: scheduling, validation, git, derived data, recovery, A/B verification. Agents own: judgment, exploration, writing.

## 25. Crash recovery

`cd workspace && git status`. Clean → resume from HEAD round+1. Dirty → `git checkout -- . && git clean -fd`, resume from HEAD. No DB consistency.

`derived/`, `INDEX.md` rebuilt. `repo/` worktree re-mounted at HEAD-trailer SHA.

## 26. Audit queries (free from git)

- Score trajectory: parse `Score-Delta` from `git log`
- Rejections by class: `git log --grep="^Reason: scope-violation"`
- Per-variant history: `git log -- variants/nodes/v-007/`
- Since last pivot: `git log <pivot-sha>..HEAD`
- Reproduce state at round N: `git checkout <round-N-sha>`
- Reviewer accept rate: ratio merge/reject commits over window
- Convergence: derivative of score per dim over recent N
- Coverage: `cat derived/coverage.json`
- Hook denials by role: `jq 'select(.denied)' actions.jsonl | group by role`

## 27. Invariants

- Markdown + YAML frontmatter for LLM-read files; JSONL only for `actions.jsonl`
- Schemas in `schemas/`, hook-validated
- Closed vocabularies for `reason_class`, role, op, action, trailer keys
- Pinned model snapshots, no aliases
- One commit per logical state transition with full trailer
- `[^ev-NNNNNN]` is the only citation syntax in docs
- Canonical ledger files (`evidence/`, `rejections/`) written by orchestrator from agent JSON, not by agents directly
- Two-layer access enforcement: PreToolUse where supported + pre-commit always
- `hook_capable: true` required for repo adapter, reconciler, any role with broad write access
- Reviewer CONTEXT.md never contains Designer's reasoning
- Designer and Reviewer use different stances + (when policy allows) different models per round
- Crash = `git checkout -- . && clean`, no custom recovery
- `harness.yaml` and `.harness/` invisible to agents

