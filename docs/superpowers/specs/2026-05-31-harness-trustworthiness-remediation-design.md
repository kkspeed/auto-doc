# Harness Trustworthiness Remediation — Design

**Status:** approved
**Date:** 2026-05-31
**Parent spec:** `auto_design_spec.md` (§1 layout, §4–5 access, §11 round flow + crash recovery, §14 CONTEXT.md)
**Trigger:** External review (2026-05-31) found a freshly-`init`'d workspace cannot complete a real round: the agents lack work context, the derived decision registry is never bootstrapped, and a hook rejection crashes the loop instead of being recorded.

## 0. Purpose

Make the harness *trustworthy for a real first overnight run*. Five blockers/correctness fixes, verified against the code:

1. **#1 Agents lack real work context.** Designer/Reviewer/Verifier-C CONTEXT.md render only registered decisions and synthesized tables — no goal description, no document body, no patch, no evidence. `build_verifier_c_context` (`harness/context.py:381-387`) renders *only* the header + decisions, yet `VERIFIER_C_PROMPT` (`harness/orchestrator.py:194`) tells it to "read the doc patch and cited evidence." Compounding: `spawn_role` runs the CLI with **no `cwd`** (`harness/spawn.py:68-70`), so even file pointers would not resolve.
2. **#2 `derived/decisions.json` is never bootstrapped.** `cmd_init` (`harness/cli.py:31-64`) copies the template and commits but never generates it; the seed `[[decision]]` entries in `goal.toml` never reach the cache. The pre-commit hook's `validate_claim_decision_id_resolution` (`pre-commit:125-144`) then **rejects** any claim against a seed decision (empty registry → "not registered").
3. **#3 Commit failures crash the run.** `commit_merge` → `subprocess.check_call` (`round_ledger.py:103-112`); `run_round` calls it without try/except (`orchestrator.py:790-796`). A hook rejection raises `CalledProcessError` out of `run_round` → `run_loop` dies instead of recording `rj-*.md`. With #2, this is the *first round of the first run*.
4. **#4 Weak validation + silent skips.** `validate_designer_json` (`orchestrator.py:79`) checks only top-level presence/list-ness; materialization silently `continue`s past malformed evidence/claim IDs (`orchestrator.py:221-224`). Silent skip is the wrong failure mode for an audit harness.
5. **#5 Canonical registry never maintained.** `add_canonical_position` exists (`claim_graph.py:442`) but the orchestrator never calls it on authoring; Phase 7b only canonicalizes toward slugs already in the registry (`orchestrator.py:761`), so the registry stays empty and canonicalization never fires.

Out of scope (recorded / deferred): #6 detector→brief wiring (TODOS #6), #7 scorecard objectivity (TODOS #4; partly downstream of #1), #8 LOC/readability refactor.

## 1. Approved design decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Variant document bootstrap | **Seed each active variant from `seed_doc.md`** at `run_loop` start (variant_count is known there, not at init) |
| D2 | #1 context delivery | **On-disk pointers** (ordered file paths in CONTEXT.md, parent spec §14) + small inline synthesized tables; the coding agent reads what it needs. NOT inlined bodies |
| D3 | `spawn_role` cwd | **Run the CLI with `cwd=workspace_root`** so pointers resolve (folded into #1; not optional) |
| D4 | #3 commit-failure recovery | **Reset to `round_start_sha`** (`git reset --hard` + `git clean -fd`), then record a single `hook-rejected` rejection. Round didn't happen; idempotent |
| D5 | derived authority | `decisions.json` is a **rebuildable cache** from `goal.toml`; `canonical_slug_registry.json` is **persisted append-only state** (tracked/force-committed) — they cannot both be "rebuilt" without losing alias history |
| D6 | #4 failure mapping | Shape/cross-field validation → `output-parse-fail` (via spawn validate-retry); malformed/duplicate materialized IDs → **raise** → `cross-field-fail` rejection. No silent skips |

## 2. Architecture & file structure

| File | Responsibility | Create/Modify |
|---|---|---|
| `harness/bootstrap.py` | `rebuild_decisions_cache`, `seed_variant_docs`, `ensure_empty_registry`. Pure-ish (filesystem only). | Create |
| `harness/cli.py` | `cmd_init` calls bootstrap (decisions cache + empty registry). | Modify |
| `harness/orchestrator.py` | `run_loop` seeds variants + rebuilds cache at start; `run_round` captures `round_start_sha`, wraps commits with reset-on-failure, materializes `patch.diff`, maintains the registry, fails loud. | Modify |
| `harness/context.py` | Add "Read these first (on disk)" pointer sections + inline goal title/description to all three builders. | Modify |
| `harness/spawn.py` | `_run_with_heartbeat` / `spawn_role` thread `cwd=workspace_root` into `Popen`. | Modify |
| `harness/round_ledger.py` | `hook-rejected` in `_ALLOWED_REASONS`; helper for the `registry-sync` commit if not already present; commit helpers usable from the reset path. | Modify |
| `workspace_template/hooks/commit-msg` | `hook-rejected` Action + Reason. | Modify |

`bootstrap.py` is a new focused module so init-time and run-time setup logic lives in one place rather than bloating cli.py / orchestrator.py.

## 3. Bootstrap (#2, D1, D5)

### 3.1 `rebuild_decisions_cache(workspace_root) -> None`
Read `goal.toml`'s `[[decision]]` array; write `derived/decisions.json` as `{"decisions": {id: {"id","question","status","introduced_at"}}}`, sorted by id. Idempotent; overwrites. `derived/` is gitignored — both `context._load_decisions` and the pre-commit hook read it from the working tree, so it need not be committed. Called from: `cmd_init`, `run_loop` start, and after each merged round (absorbs goal edits/pivots).

### 3.2 `ensure_empty_registry(workspace_root) -> None`
If `derived/canonical_slug_registry.json` is absent, write an empty `CanonicalSlugRegistry().to_dict()`. Called from `cmd_init`, which then force-commits it (Action: init) so the persisted append-only state has a baseline. (The registry is *not* rebuilt — see D5.)

### 3.3 `seed_variant_docs(workspace_root, variant_count) -> list[str]`
For each `v-001..v-{variant_count:03d}` whose `variants/nodes/{v}/doc/` has no `*.md`, write `seed_doc.md`'s body into `doc/00-overview.md` with frontmatter `section_id = "overview"`, `created_round`, `tags = []`. Returns the relative paths created. Called once at `run_loop` start; the created files are committed in a single `Action: init` commit (the init whitelist is unrestricted). If `seed_doc.md` is missing, no-op (a workspace may legitimately start empty).

## 4. Agent context via pointers (#1, D2, D3)

### 4.1 CONTEXT.md additions
Each builder gains a `## Read these first (on disk)` section listing ordered, workspace-relative paths, plus inlined goal title + description (small, high-value). The agent uses its own file tools to read them.

- **Designer** (`build_designer_context`): `goal.toml`; `variants/nodes/{v}/doc/` (current sections); each `target_sections` path from `rounds/{round}/scratch/planner.json`; `evidence/` (or `evidence/INDEX.md` if present); `rounds/{round}/plan.md`.
- **Reviewer** (`build_reviewer_context`): `rounds/{round}/patch.diff`; the evidence files cited by this round's claims; `variants/nodes/{v}/doc/`.
- **Verifier C** (`build_verifier_c_context`): `rounds/{round}/patch.diff`; the evidence files cited by this round's claims (listed explicitly by path). This is the material its prompt already assumes.

Existing inline tables (registered decisions, positions, proposals, posture) are retained — they synthesize cross-file state cheaply.

### 4.2 `rounds/{round}/patch.diff`
`_materialize_designer_output` writes the designer's `patch_diff` string to `rounds/{round}/patch.diff` so Reviewer/Verifier-C have a stable on-disk pointer. (Today the patch lives only inside `scratch/designer.json`.) Path is under `rounds/{round}/` which the access intent already grants those roles.

### 4.3 `spawn_role` cwd (D3)
Thread `cwd=workspace_root` from `spawn_role` through `_run_with_heartbeat` into `subprocess.Popen(...)`. Without this, every relative pointer resolves against the harness's CWD, not the workspace, and #1 does not actually work. CONTEXT.md is still delivered on stdin as today.

## 5. Commit failure → rejection (#3, D4)

### 5.1 Round-start snapshot
`run_round` captures `round_start_sha = git rev-parse HEAD` immediately on entry (before any materialization).

### 5.2 Wrapped commits
Every commit in `run_round` (`commit_register_decision` 7a, `commit_canonicalize` 7b, the new `registry-sync` from §6, `commit_merge` 8) is wrapped. On `subprocess.CalledProcessError`:
1. `git -C <ws> reset --hard <round_start_sha>` then `git -C <ws> clean -fd` — erase any partial commits from this round and all materialized/untracked files.
2. Allocate `rj-*.md` (frontmatter `reason_class = "hook-rejected"`, `failed_phase = "commit"`, body = the captured git stderr).
3. Commit it: `Action: hook-rejected`, `Variant`, `Round`, `Reason: hook-rejected`.
4. Return `RoundOutcome(verdict="hook-rejected", reason="hook-rejected", rj_id=...)`.

The rejection commit stages only `rejections/rj-*.md` + `actions.jsonl`, so it passes the hook. (If even that fails — degenerate — the exception propagates; documented as an accepted v0 limit.)

### 5.3 Hook vocabulary
`workspace_template/hooks/commit-msg`: add `"hook-rejected"` to `ALLOWED_ACTIONS` and `ALLOWED_REASONS`; `TRAILER_REQUIREMENTS["hook-rejected"] = {"Variant","Round","Reason"}`; `ACTION_FILE_WHITELIST["hook-rejected"] = ["rejections/rj-*.md","actions.jsonl"]`. `round_ledger._ALLOWED_REASONS += "hook-rejected"`.

## 6. Canonical registry maintenance (#5)

### 6.1 Append on authoring
After a round passes all gates and before the merge commit, walk the round's materialized claims; for each with `claim_type == "decision"` and a non-empty `position` slug, call `add_canonical_position(registry, decision_id, slug)` (idempotent, append-only — it raises if the slug is already an alias, which is the correct invariant). Load the registry from `derived/canonical_slug_registry.json` (created empty at init).

### 6.2 Commit + ordering
If any slug was newly appended, persist `derived/canonical_slug_registry.json` and commit with `Action: registry-sync` (its whitelist already permits `derived/canonical_slug_registry.json` + `actions.jsonl`). The round phase order becomes: **7a register-decision → 7b canonicalize → 6 registry-sync → 8 merge**. The append runs *after* 7b deliberately: Phase 7b's reviewer-proposed canonicalizations must target a *prior-round established* canonical, not a slug authored in this same round (which would be circular). All of 7a/7b/6/8 are inside the §5.2 wrapped set, so any commit failure resets the whole round.

### 6.3 Effect on Phase 7b
Phase 7b's high-confidence canonicalization (`orchestrator.py:761`) checks `at["to"]` is already canonical. With §6.1 populating canonicals from real authoring, a reviewer proposing `from→to` where `to` is an in-use canonical now actually applies, instead of always being skipped.

## 7. Fail loud (#4, D6)

### 7.1 `validate_designer_json`
Add: `patch_diff` is a `str`; each `evidence` item is a dict with `id` (matching `ev-\d{6}`), `confidence`, `citations` (list), `claim`, `excerpt`; each claim's `evidence_ids` resolve to an id present in this round's `evidence` list; `d["round"]`/`d["variant"]` equal the round's. On any failure raise `ValueError` → `spawn_role` validate-retry → terminal `output-parse-fail`.

### 7.2 Materialization
Replace the silent `continue` on a malformed/unsafe `ev-`/`cl-`/`at-` id (`orchestrator.py:221-224` and the claim/attack loops) with `raise RuntimeError(...)`. Detect duplicate ids within the round (overwrite collision) → raise. The existing `except RuntimeError` in `run_round` maps this to a `cross-field-fail` rejection (which now also resets via §5, since materialization precedes any commit — actually materialization failure is pre-commit, so it uses the existing `_reject` path, no reset needed).

## 8. Testing (TDD)

- **`tests/test_bootstrap.py`** (new): `rebuild_decisions_cache` produces the seed decisions from a template `goal.toml`; `seed_variant_docs` creates valid section files for N variants and is idempotent (no overwrite when doc exists); `ensure_empty_registry`.
- **`tests/test_cli_init.py`** (extend): after `harness init`, `derived/decisions.json` exists with the seed decisions and `derived/canonical_slug_registry.json` exists (empty) and is committed.
- **`tests/test_orchestrator_round.py`** (extend): a full mocked round on a **freshly-init'd** workspace with **no manual `derived/` seeding** reaches `merge` — the regression current tests hide by hand-seeding `decisions.json`.
- **`tests/test_orchestrator_hook_reject.py`** (new): a round whose merge commit is forced to fail the hook resets to round-start (no orphan register-decision/canonicalize commits remain) and records `verdict="hook-rejected"`.
- **`tests/test_context.py`** (extend): each builder's output contains the on-disk pointer paths and the goal title/description; Verifier-C context names `patch.diff` + the cited evidence paths.
- **`tests/test_spawn.py`** (extend): `spawn_role` invokes the runner with `cwd=workspace_root`.
- **registry**: materializing decision claims appends canonicals and commits `registry-sync`; a subsequent high-confidence canonicalization toward an in-use canonical now applies.
- **fail-loud**: `validate_designer_json` rejects each new bad-shape case; materialization raises (not skips) on a malformed/duplicate id.
- **hook**: `hook-rejected` Action accepted with required trailers / rejected without.

## 9. Out of scope (recorded)

- #6 live detector → morning-brief wiring (TODOS #6).
- #7 scorecard objectivity / `denied` producer (TODOS #4) — partly resolved once #1 gives the reviewer real material, but the `denied` signal still needs the (deferred) policy layer.
- #8 `claim_graph.py` split / LOC reduction.
- The PreToolUse access-policy enforcement (`access-policy.yaml`, parent §4–5) remains deferred; pointers rely on the agent's default file tools, not on a policy layer.
