# TODOs — auto_design_doc

Captured by `/plan-eng-review` on 2026-05-21. Each entry includes enough context that someone picking it up in 3 months understands the motivation and where to start.

---

## 1. Add specific rejection `reason_class` entries: `output-parse-fail`, `spawn-failed`, `cross-field-fail`

**What:** Extend the closed enum of `rejection.reason_class` in `harness.py`'s dataclasses (and the inlined hook copy) with three new entries:
- `output-parse-fail` — agent produced unparseable JSON after retry-once
- `spawn-failed` — claude or codex CLI exited non-zero twice in a row
- `cross-field-fail` — designer JSON validated on type but failed cross-field consistency (phantom section_id, dangling evidence_id, etc.)

**Why:** v0 currently lumps all three under `reason_class: other`. Audit queries against `git log --grep "^Reason: other"` lose signal — you can't distinguish a spawn flake from a parser bug from a cross-field violation. Specific reason classes let you `git log --grep "^Reason: spawn-failed" | wc -l` and immediately see rate-limit incidence.

**Pros:** Sharper audit queries; sharper failure-mode statistics; clearer commit messages.
**Cons:** Adds 3 enum entries (+ 3 strings in error-construction sites). Schema parity test must be updated.
**Context:** During /plan-eng-review (2026-05-21), the rejection.reason_class enum was reviewed against the failure-modes table. The three new classes correspond to documented orchestrator failure paths (CLI Invocation Reliability section of the design doc, plus the cross-field validation added under Finding 2.2). The `other` bucket still exists for genuinely-unanticipated rejections.
**Depends on / blocked by:** Nothing; can land in v0.1 alongside any other small enum-set update.

---

## 2. Lockfile for `harness run` to prevent concurrent invocations against the same workspace

**What:** Add a PID-based lockfile to `harness run` startup. Write `workspace/.harness-run.lock` containing the PID at start; check on startup that any existing lockfile's PID is dead before proceeding; clean the lockfile on normal exit and on SIGTERM/SIGINT cleanup. Provide a `--force` flag to bypass a stuck lock (with a warning).

**Why:** v0 has no concurrency control. Two simultaneous `harness run` invocations against the same workspace (user running twice in two terminals, or a cron-job race condition) will corrupt state — both processes spawn agents, both try to commit, ledger writes conflict, `actions.jsonl` interleaves. The original spec §1 had `.harness/locks/` but that directory is deferred to v0.1 alongside PreToolUse.

**Pros:** Eliminates one of the two critical gaps flagged in the /plan-eng-review failure-modes table; safer for cron / scheduled runs; "tired humans at 3am" + "systems over heroes" principles.
**Cons:** ~20 LOC of stdlib `os.getpid()` + `os.kill(pid, 0)` ping. Adds a startup check and a cleanup path. The `--force` escape hatch is one more user-facing flag.
**Context:** /plan-eng-review D13 (2026-05-21). User accepted the gap as v0.1 work since first-overnight users are unlikely to hit double-start without scripting it deliberately.
**Depends on / blocked by:** Nothing; standalone.

---

## 3. Re-evaluate Aider as a partial substrate after v0's first overnight

**What:** After v0 ships and at least one full 8-hour overnight run completes successfully, re-evaluate whether Aider (or another small Python tool like it) would have saved time on the edit-apply-commit edge cases v0 actually hit. Specifically check: malformed diffs, re-fragmented patches, conflicts with already-committed state, model-provider variance in patch formatting.

**Why:** During /office-hours D4 (2026-05-17), Codex suggested Aider as a substrate that gives ~50% of the orchestrator plumbing (CLI agent execution, repo context, git workflow, model provider abstraction, edit/apply/commit ergonomics). User chose to defer the decision to v1 to "evaluate from data, not speculation." That deferral can easily get lost if not captured here.

**Pros:** Forces a deliberate re-evaluation rather than silently skipping it; if v0 hit real edit/commit pain Aider would have solved, that's a clear signal to integrate; if v0 was smooth, that's evidence the stdlib-only path is the right one.
**Cons:** One more decision to make at v0.1 planning time. The corporate-portability argument (P4) still applies to Aider — its transitive deps need checking against any organization's approved-package list.
**Context:** /plan-eng-review D14 (2026-05-21). Decision criteria for v0.1: did v0's hand-rolled diff/patch/commit logic produce more than ~3 distinct bug categories during the first 5 overnight runs? If yes, evaluate Aider integration. If no, keep stdlib-only path.
**Depends on / blocked by:** Blocked by v0 first overnight + at least 5 subsequent runs to collect real data.

---

## 4. Wire a live `denied` signal so `constitution_compliance` isn't a dead constant

**What:** `scorecard.compute_constitution_compliance` (harness/scorecard.py) scores `1 - denied/total` over the round's `actions.jsonl` entries, keyed on a `denied` field. But no `_log` event in the orchestrator ever writes a `denied` key — so over real data this dimension is structurally always `1.0`, not merely "saturated on clean rounds." When the PreToolUse access-policy / constitution-enforcement layer lands (parent spec §4–5, deferred to v0.1), have it emit a `denied`-tagged action entry on every policy denial so the dimension gains a real signal path.

**Why:** The sub-project-5 spec's saturation note (§3) lists `constitution_compliance` among the ~always-1.0 dims, which is *consistent* — but the final cross-cutting review (2026-05-31) found it's stronger than documented: there's no producer of `denied` anywhere in the codebase, so the dimension can never move. Until the policy layer writes denials, this dimension contributes nothing to the merge gate. Also tighten the spec wording from "saturates in clean rounds" to "no live signal path until the policy layer lands."

**Pros:** Turns a dead dimension into a real one; gives the gate a sixth working signal; aligns the audit story with reality.
**Cons:** Coupled to the (deferred) PreToolUse/access-policy work; can't be done standalone in a meaningful way.
**Context:** Final review of sub-project 5 (2026-05-31), Important issue 1.
**Depends on / blocked by:** Blocked by the PreToolUse / access-policy enforcement layer (parent spec §4–5, itself v0.1).

---

## 5. Distinguish "no-improvement" from "regression" in the scorecard merge gate verdict

**What:** The Phase 6.5 merge gate (D7) rejects any round that improves no dimension, committing it with `Action: score-regression` / `Reason: score-regression` — even when nothing actually regressed (a polish/consolidation round that leaves all dims flat). Split this into two outcomes: a true regression (a dim dropped > tolerance) vs. a no-improvement stall. Either give the stall its own Action/Reason (e.g. `no-improvement`) or relax D7 to allow flat-or-better rounds to merge. Update the morning brief's "Rejected this run" so it doesn't mislabel polish rounds as regressions.

**Why:** With `groundedness`/`coherence`/`constitution_compliance` pinned at 1.0 (see #4), the only live signals are `goal_alignment`/`technical_correctness`/`completeness`. A legitimate round that consolidates without nudging any of those three is rejected and labeled a "regression" — which is confusing to the human reading the brief, since nothing got worse. The final review (2026-05-31) confirmed by execution that an identical-scores second round fails with `no dimension improved`.

**Pros:** Accurate failure labels in the morning brief; lets benign polish rounds merge (if D7 is relaxed); removes a confusing signal for the 3am human.
**Cons:** Touches the gate (orchestrator Phase 6.5), the hook vocab (a new Action/Reason if split), and round_ledger. Re-opens the D7 decision.
**Context:** Final review of sub-project 5 (2026-05-31), Important issue 2 (D7 was the deliberate v0 choice). Also fold in Minor issue 3: guard `prior_dims` against a parsed-but-empty `dimensions: {}` (treat as bootstrap rather than an instant "no dimension improved").
**Depends on / blocked by:** Nothing; standalone gate-semantics change.

---

## 6. Thread live claim-graph detector data into `morning_brief.md`

**What:** `morning_brief.render_morning_brief` (harness/morning_brief.py) currently passes empty lists to the five claim_graph section renderers (Position collisions, Decisional asymmetry, Pending registry changes, Canonicalizations applied, Stale proposals), so those sections always show their friendly empty states. Wire the live claim-graph detector outputs (collision/asymmetry/pending/canonicalization/stale detectors, which already exist and are unit-tested in claim_graph.py) through the assembler, scoped to the current run. Also complete `_gather_look_at_first` to implement the full §5.3 ranking (contested decisions > decisional asymmetry > regressed scores > stale) rather than only surfacing score-regression.

**Why:** This was a deliberate v0 scope line in the sub-project-5 plan (Task 8) — the assembler contract and the run-level sections (trajectory, still-weak, rejected, look-at-first) shipped first; the claim-graph data wiring was deferred to keep the task focused. The renderers and detectors both exist; only the glue is missing. Until it's wired, the morning brief under-reports cross-variant epistemic signals (collisions/asymmetry) that the human most needs on resume.

**Pros:** Completes the morning brief's most decision-relevant sections; reuses already-tested detectors and renderers (glue only); makes "What I'd ask you to look at first" actually rank.
**Cons:** Requires gathering live detector inputs from `derived/` state scoped to the run; some detectors need the full claim graph loaded, which adds I/O to brief rendering.
**Context:** Sub-project 5 plan (2026-05-31), Task 8 documented v0 scope line; final review Minor issue 5.
**Depends on / blocked by:** Nothing; the detectors and renderers already exist.
