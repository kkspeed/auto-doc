# Morning Brief Pipeline + Scorecard — Design (Sub-project 5)

**Status:** approved
**Date:** 2026-05-31
**Parent spec:** `auto_design_spec.md` §16 (round flow step 16), §17 (scorecard), §5.3 of `2026-05-22-claim-graph-redesign-design.md` (morning_brief sections)

## 0. Purpose

Close the two parent-spec mechanisms still unbuilt after sub-project 4:

1. **Scorecard** (§17): per-variant multi-dimensional scoring, computed on every merge, gating the merge, and recorded both as `scorecard.json` (latest) and as `Score-Delta` commit trailers (trajectory).
2. **Morning brief**: the human's between-rounds artifact. Section *renderers* already live in `claim_graph.py`; this sub-project adds the data-gathering + run-level sections + top-level assembly, rendered once when the run pauses.

This sub-project also makes the schema/hook changes the two approved design decisions require (active merge gate; reviewer-sourced judgment dimensions).

## 1. Approved design decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Scorecard role | **Active merge gate** — can reject a round (`score-regression`), not just record |
| D2 | Judgment-dimension source | **Reviewer emits `goal_alignment` + `technical_correctness`**; Verifier C sharpens correctness |
| D3 | Brief timing | **Rendered once when `run_loop` pauses**; overwrites prior brief |
| D4 | Threshold semantics | **Delta tolerance vs baseline** (δ, no absolute floors) |
| D5 | `technical_correctness` composition | `reviewer_score × vc_confirm_rate` when VC ran, else `reviewer_score` alone |
| D6 | Bootstrap | First round for a variant has no baseline → **gate passes unconditionally**, establishes baseline, no `Score-Delta` trailer |
| D7 | No-op rounds | A round improving **no** dimension fails the gate (faithful §17 "improve ≥1 dim") |
| D8 | "Survived adversarial review" | **Deferred to v0.1** (needs agent narrative v0 schemas don't carry) |
| D9 | "Still weak" | **Kept**, derived mechanically from Verifier-C `weak` verdicts this run |

## 2. Architecture & file structure

| File | Responsibility | Create/Modify |
|---|---|---|
| `harness/scorecard.py` | Pure scoring engine: compute six dimensions, apply merge gate, format `Score-Delta`. No git; reads only paths it is handed. | Create |
| `harness/morning_brief.py` | Gather workspace state at pause; assemble full `morning_brief.md`. Imports `cg.render_*`. | Create |
| `harness/orchestrator.py` | Insert Phase 6.5 gate; write `scorecard.json`; render brief at `run_loop` pause. | Modify |
| `harness/round_ledger.py` | `commit_merge` gains `score_delta` + scorecard staging; new `commit_score_regression`. | Modify |
| `workspace_template/hooks/commit-msg` | New `score-regression` Action + Reason; `Score-Delta` trailer key + validator. | Modify |
| `harness/orchestrator.py` validators | `validate_reviewer_json` enforces `goal_alignment` + `technical_correctness`. | Modify |
| `workspace_template/harness.toml` | New `[scorecard]` table (`regression_tolerance`). | Modify |

`scorecard.py` and `morning_brief.py` stay separate from the 1449-LOC `claim_graph.py`: the scoring engine is round-loop machinery, not claim-graph data; the brief assembler is a thin orchestration layer over the existing renderers.

## 3. The six dimensions

Computed per variant, on the **materialized-but-uncommitted** doc state (after the designer patch is applied to the working tree, before any registry commit). All dimensions are floats in [0,1].

| Dimension | v0 formula | Data source |
|---|---|---|
| `groundedness` | claims passing Verifier A+B ÷ total claims (this variant) | `verifiers` results + variant `claims/` |
| `goal_alignment` | reviewer's `goal_alignment` field | reviewer JSON |
| `technical_correctness` | `reviewer.technical_correctness × vc_confirm_rate` (VC ran) else `reviewer.technical_correctness` | reviewer JSON × Verifier C `per_claim` |
| `completeness` | decisions with status ∈ {open, proposed} that have a doc section ÷ total such decisions | `goal.toml` (or `derived/decisions.json`) + variant `doc/` |
| `coherence` | 1 − (dead `[^ev-*]` refs ÷ total citations) | doc scan vs `evidence/` |
| `constitution_compliance` | 1 − (denied actions this round ÷ actions this round) | `actions.jsonl` |

Definitions:

- **`vc_confirm_rate`** = confirmed ÷ (confirmed + weak) over Verifier-C `per_claim` verdicts. Disputes already rejected the round (Phase 6), so only `confirm`/`weak` remain at gate time. If Verifier C did not run this round (`verifier_c_every` cadence), `technical_correctness` = `reviewer.technical_correctness` with no penalty.
- **Empty denominators** (0 claims, 0 citations, 0 actions, 0 required decisions) → that dimension = **1.0** (vacuously satisfied). Documented per dimension.
- **Dead ref** = a `[^ev-NNNNNN]` citation appearing in the variant's doc whose `evidence/ev-NNNNNN.md` is missing OR carries a non-empty `supersedes`/superseded marker. Citation extraction reuses the canonical `[^ev-NNNNNN]` form (the only legal citation form per parent §3).
- **Deferred to v0.1:** contradiction detection in `coherence` (v0 is dead-refs-only); the "Survived adversarial review" brief section (D8).

## 4. Merge gate (orchestrator Phase 6.5)

Placement: after Verifier C passes (`orchestrator.py` ~line 632), **before** Phase 7a register-decision. At this point the designer patch is materialized in the working tree but uncommitted, and no registry mutation has been committed — so a gate failure orphans nothing.

```
… Verifier C passes
   ↓
Phase 6.5: compute scorecard on materialized doc state
   ├─ no prior scorecard.json for this variant (bootstrap, D6)
   │     → PASS, write scorecard.json, no Score-Delta trailer
   ├─ baseline exists, gate PASS (D4/D7):
   │     (≥1 dim strictly improved) AND (no dim dropped > δ below its prior)
   │     → write scorecard.json; carry Score-Delta into the merge commit
   └─ baseline exists, gate FAIL:
         → _discard_materialized()           (nothing registry-committed yet)
         → write rejections/rj-*.md
         → commit_score_regression(...)       (Action: score-regression)
         → return RoundOutcome(verdict="score-regression")
   ↓ (pass)
Phase 7a register-decision → 7b canonicalize → Phase 8 merge
```

- **δ** = `harness.toml [scorecard].regression_tolerance`, default **0.05**.
- **Gate predicate** (baseline present): let `prior`, `new` be the two dimension dicts over the common keys. `improved = any(new[d] > prior[d])`. `regressed = any(new[d] < prior[d] − δ)`. **Pass iff `improved and not regressed`.** Dimensions absent from either side are skipped (forward-compatible if the dimension set grows).
- **`scorecard.json`** holds only the latest scorecard (trajectory lives in git per §17). Written on every passing round (including bootstrap). It is staged into the **Phase 8 merge commit** (whitelist already allows `variants/nodes/v-*/scorecard.json`).
- **`Score-Delta` trailer**: `Score-Delta: groundedness=+0.04 goal_alignment=+0.00 technical_correctness=-0.02 completeness=+0.10 coherence=+0.00 constitution_compliance=+0.00` — all six dims, signed, two decimals, space-separated. **Omitted on the bootstrap round** (no baseline to diff).

### 4.1 `scorecard.json` schema

```json
{
  "variant": "v-001",
  "round": "round-000042",
  "dimensions": {
    "groundedness": 0.95,
    "goal_alignment": 0.80,
    "technical_correctness": 0.76,
    "completeness": 0.60,
    "coherence": 1.00,
    "constitution_compliance": 1.00
  }
}
```

No wall-clock timestamp (keeps the artifact deterministic for tests and reconstructable from git). `round` is the temporal anchor.

## 5. Hook + schema changes

### 5.1 commit-msg hook (`workspace_template/hooks/commit-msg`)

- `ALLOWED_ACTIONS` += `"score-regression"`.
- `TRAILER_REQUIREMENTS["score-regression"]` = `{"Variant", "Round", "Reason"}`.
- `ACTION_FILE_WHITELIST["score-regression"]` = `["rejections/rj-*.md", "actions.jsonl"]`.
- `ALLOWED_REASONS` += `"score-regression"` (documented reason_class in parent §3; previously absent from the hook's frozenset).
- `validate_trailers`: recognize `Score-Delta` as a known key. Value format: one-or-more space-separated `^[a-z_]+=[+-]\d+\.\d{2}$` tokens. Reject malformed values; reject `Score-Delta` on any Action other than `merge`.

### 5.2 reviewer JSON (`validate_reviewer_json` in `orchestrator.py`)

- Require `goal_alignment`: float in [0,1].
- Require `technical_correctness`: float in [0,1].
- Reviewer CONTEXT.md / `REVIEWER_PROMPT` instructs the reviewer to emit both, each with a one-line rationale (rationale is free-text, not validated).

### 5.3 `harness.toml`

```toml
[scorecard]
# Merge gate: a round must improve >=1 dimension and may not drop any dimension
# by more than this tolerance below its prior value. See harness/scorecard.py.
regression_tolerance = 0.05
```

## 6. `round_ledger.py` changes

- `commit_merge(...)` gains a `score_delta: str | None` parameter. When non-None, appends a `Score-Delta: <str>` trailer line. Stages `variants/nodes/<variant>/scorecard.json` alongside the existing materialized paths.
- New `commit_score_regression(workspace_root, round_id, variant_id, rj_id, score_delta)`: stages `rejections/<rj_id>.md` + `actions.jsonl`, commits with `Action: score-regression`, `Variant`, `Round`, `Reason: score-regression`. (Mirrors `commit_rejection`; does not stage `scorecard.json` — no scorecard is written on a failed gate.)

## 7. `morning_brief.py` — assembly

`render_morning_brief(workspace_root: Path) -> str` returns the full document; `run_loop` writes it to `workspace/morning_brief.md` exactly once, when the loop stops (caps hit / stopping criteria). Section order:

1. **Header** — title + run summary (rounds run, merges, rejections by class) gathered from this run's commits / `actions.jsonl`.
2. **Position collisions** — `cg.render_position_collisions_table` (data via existing collision detector).
3. **Decisional asymmetry** — `cg.render_decisional_asymmetry_table`.
4. **Pending registry changes** — `cg.render_pending_registry_changes`.
5. **Canonicalizations applied this run** — `cg.render_canonicalizations_applied` (data from this run's `Action: canonicalize` commits).
6. **Stale proposals** — `cg.render_stale_proposals_table`.
7. **Score trajectory** — per variant, parsed from `git log --grep "Action: merge"` `Score-Delta` trailers; one row per round showing per-dim deltas.
8. **Still weak** (D9) — claims with a Verifier-C `weak` verdict this run, with claim_id + rationale.
9. **Rejected this run** — this run's rejection commits grouped by `reason_class`.
10. **What I'd ask you to look at first** — ranking heuristic (parent §5.3): contested decisions > decisional asymmetry > regressed scores > stale proposals. Emits the top items across those buckets.

Each gather step is a small private function in `morning_brief.py`; the renderers stay in `claim_graph.py`. Empty sections fall through to the renderers' existing friendly empty-state lines. "Survived adversarial review" is omitted with a one-line v0 note (D8).

### 7.1 "This run" boundary

A run's commit range is `<sha at run_loop start>..HEAD`. `run_loop` captures the start SHA (via `git rev-parse HEAD`) before the first round and passes it as a `since_sha` argument to `render_morning_brief`, which scopes trajectory / rejected / canonicalization gathering to `since_sha..HEAD` rather than all history. When `since_sha` is `None` (e.g. a fresh workspace with no prior commits), gathering falls back to the full history.

## 8. Testing (TDD)

- **`tests/test_scorecard.py`** (new): each dimension formula incl. every empty-denominator case; `vc_confirm_rate` with/without VC; gate pass / fail / bootstrap; δ boundary (drop of exactly δ passes, δ+ε fails); `Score-Delta` string format; no-op round fails (D7).
- **`tests/test_orchestrator_score_gate.py`** (new): Phase 6.5 with mocked spawns — bootstrap merges; improving round merges + writes `scorecard.json` + Score-Delta trailer; regressing round rejects with `score-regression`, discards materialized tree, leaves no registry commit.
- **`tests/test_commit_msg_hook.py`** (extend): `score-regression` Action accepted with required trailers / rejected without; `Score-Delta` accepted on merge with valid format / rejected with malformed value / rejected on non-merge Action.
- **`tests/test_morning_brief_render.py`** (extend): full-assembly ordering; score-trajectory parse from git log; still-weak from VC verdicts; "look at first" ranking; empty-workspace friendly states.
- **reviewer validator tests** (extend existing): missing/out-of-range `goal_alignment`/`technical_correctness` rejected.

## 9. Out of scope (v0.1)

- "Survived adversarial review" brief section (D8).
- Contradiction detection in `coherence` (v0 dead-refs-only).
- Per-dimension absolute floors (D4 chose delta tolerance; floors are a possible v0.1 addition).
- Per-decision evidence-ledger pruning in brief tables (already a noted v0.1 item).
