# Harness Trustworthiness Remediation — Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six integrity/correctness findings from the external review of the harness-trustworthiness remediation — a stale-cache-after-reset Critical, three Highs (designer round/variant not validated; Reviewer/Verifier-C can't see claim files; materialization overwrites existing ledger IDs), and two Mediums (bootstrap fails open on malformed goal.toml; Planner lacks goal/pointer context).

**Architecture:** Targeted corrections to `harness/bootstrap.py`, `harness/orchestrator.py`, and `harness/context.py`. The bootstrap cache is rebuilt from the canonical `claim_graph` loader (fail-loud); the commit-reset path re-derives the cache; the designer's self-reported round/variant are validated and the trusted `round_id` is used for the patch.diff path; materialization treats an existing on-disk ledger ID as a hard failure; Reviewer/Verifier-C and Planner contexts gain the missing pointers.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `tomllib`, `subprocess`, `pathlib`), `unittest`.

**Findings source:** External review (2026-05-31) of commits `9e44f2c..4bd8b47`. Closes `TODOS.md` #7 (Task 3 here).

---

## Conventions

- Full suite: `python3 -m unittest discover tests/` (~5 min, ~397 tests). One file: `python3 -m unittest tests.test_bootstrap -v`.
- Commit with the harness identity; end every body with the co-author trailer:
  ```bash
  git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf '<subject>\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
  ```
- All work on `main`.
- `harness/claim_graph` is imported in orchestrator.py as `cg` and in bootstrap.py as `cg` (added in round 1). `harness/bootstrap` is imported in orchestrator.py as `bootstrap`.

---

## Task 1: bootstrap — fail loud on malformed goal.toml (reuse the canonical loader)

**Finding #5 (Medium).** `rebuild_decisions_cache` reimplements goal.toml parsing and swallows TOML errors / silently skips invalid entries, yielding a false-empty registry. Reuse `cg.load_decisions_from_goal_toml` (raises `SchemaError` on missing `goal_version`/malformed decisions) + `cg.dump_decisions_to_json`.

**Files:**
- Modify: `harness/bootstrap.py` (`rebuild_decisions_cache`)
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Update the failing tests** — In `tests/test_bootstrap.py`, the `GOAL_TOML` fixture already has `goal_version`. REPLACE the two graceful-on-bad-input tests in `RebuildDecisionsCacheTest` (`test_malformed_toml_writes_empty`, `test_non_string_id_skipped`) with fail-loud versions, and keep the others:

```python
    def test_malformed_toml_raises(self):
        (self.td / "goal.toml").write_text("[[invalid")
        with self.assertRaises(Exception):
            bootstrap.rebuild_decisions_cache(self.td)

    def test_non_string_id_raises(self):
        (self.td / "goal.toml").write_text(
            '[goal]\ngoal_version = "g-01"\n'
            '[[decision]]\nid = 42\nquestion = "q"\n'
            'status = "open"\nintroduced_at = "g-01"\n')
        with self.assertRaises(Exception):
            bootstrap.rebuild_decisions_cache(self.td)

    def test_missing_goal_version_raises(self):
        (self.td / "goal.toml").write_text(
            '[goal]\ntitle = "t"\n'
            '[[decision]]\nid = "retry-policy"\nquestion = "q"\n'
            'status = "open"\nintroduced_at = "g-01"\n')
        with self.assertRaises(Exception):
            bootstrap.rebuild_decisions_cache(self.td)
```

(Keep `test_writes_all_decisions_from_goal_toml`, `test_idempotent_overwrite`, `test_missing_goal_toml_writes_empty` — they remain valid. Note the `GOAL_TOML` fixture must have a `goal_version` under `[goal]`; confirm it does, and if not add `goal_version = "g-01"`.)

- [ ] **Step 2: Run tests to verify they fail** — `python3 -m unittest tests.test_bootstrap.RebuildDecisionsCacheTest -v` — expect FAIL (current code writes empty instead of raising).

- [ ] **Step 3: Implement** — In `harness/bootstrap.py`, replace `rebuild_decisions_cache` with:

```python
def rebuild_decisions_cache(workspace_root: Path) -> None:
    """Regenerate derived/decisions.json from goal.toml via the canonical
    claim_graph loader. Missing goal.toml -> empty cache. A goal.toml that
    exists but is malformed (bad TOML, missing goal_version, invalid/duplicate
    decision) raises (SchemaError / TOMLDecodeError) — a trustworthiness
    bootstrap must fail loud rather than silently produce a false-empty
    registry. derived/ is gitignored; consumers read this file from the tree."""
    goal_path = workspace_root / "goal.toml"
    out_path = workspace_root / "derived" / "decisions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not goal_path.exists():
        out_path.write_text(
            json.dumps({"decisions": {}}, indent=2, sort_keys=True))
        return
    decisions, goal_version = cg.load_decisions_from_goal_toml(goal_path)
    cg.dump_decisions_to_json(decisions, goal_version, out_path)
```

(`cg` is already imported in bootstrap.py from round 1. `json` is imported.)

- [ ] **Step 4: Run tests to verify they pass** — `python3 -m unittest tests.test_bootstrap -v` — expect PASS.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover tests/`. IMPORTANT: any test that writes a `goal.toml` WITHOUT a `goal_version` under `[goal]` and then triggers a rebuild (directly, or via `harness init` / `run_loop`) will now raise. Most tests scaffold via the real template (which has `goal_version`) or use `_write_goal_toml` (which includes it). If a test breaks with `SchemaError: [goal] table missing goal_version`, fix that test's `goal.toml` to include `goal_version = "g-01"` (it's a malformed fixture, not a code bug). Report any such fixes.

- [ ] **Step 6: Commit**

```bash
git add harness/bootstrap.py tests/test_bootstrap.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(bootstrap): rebuild_decisions_cache fails loud on malformed goal.toml (reuse canonical loader)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: orchestrator — rebuild the decision cache after a commit-reset (Critical)

**Finding #1 (Critical).** `register_decision` (Phase 7a) mutates goal.toml (tracked) AND derived/decisions.json (gitignored). If a later commit in the round fails, `_commit_reject` does `git reset --hard` (rolls back goal.toml) + `git clean -fd` (preserves the ignored cache) — so the cache keeps a decision goal.toml no longer has. The next round validates against this false registry. Fix: rebuild the cache from the rolled-back goal.toml inside `_commit_reject`, right after the reset.

**Files:**
- Modify: `harness/orchestrator.py` (`_commit_reject` closure)
- Test: `tests/test_orchestrator_hook_reject.py`

- [ ] **Step 1: Write the failing test** — Append to `tests/test_orchestrator_hook_reject.py` (it already imports orchestrator, round_ledger, the helpers, and `_RETRY_CLAIM`):

```python
class CommitRejectRebuildsCacheTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_cache_rebuilt_to_match_goal_toml_after_reset(self):
        # A designer proposes a NEW decision (registered in Phase 7a, mutating
        # goal.toml + decisions.json); then the merge commit is forced to fail.
        # After _commit_reject's reset, decisions.json must NOT contain the
        # proposed decision (goal.toml rolled back, so the cache must too).
        import json as _json
        claim = {"id": "cl-000001", "section_id": "new-policy",
                 "decision_id": "new-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "some-pos",
                 "proposed_decision": {"id": "new-policy",
                                       "question": "New?",
                                       "rationale": "needed"}}
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "new-policy", "verdict": "approve",
             "rationale": "ok"}])
        def boom(*a, **kw):
            raise subprocess.CalledProcessError(
                1, ["git", "commit"], stderr="hook said no")
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
                _planner_ok(), _designer_ok(claims=[claim]),
                reviewer, _verifier_c_ok()]), \
             mock.patch("harness.round_ledger.commit_merge", side_effect=boom):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "hook-rejected")
        cache = _json.loads(
            (self.ws / "derived" / "decisions.json").read_text())
        self.assertNotIn("new-policy", cache["decisions"])
```

(`_reviewer_ok` accepts a `decision_proposals=` kwarg — verify its signature in `tests/test_orchestrator_round.py`; it does. If the proposal-approval path needs the designer claim to also carry `proposed_decision`, the claim above includes it.)

- [ ] **Step 2: Run test to verify it fails** — `python3 -m unittest tests.test_orchestrator_hook_reject.CommitRejectRebuildsCacheTest -v` — expect FAIL (the cache still contains `new-policy` because `_commit_reject` preserves the ignored cache).

- [ ] **Step 3: Implement** — In `harness/orchestrator.py` `_commit_reject`, add a cache rebuild immediately after the `git clean -fd` call and before `write_rejection`:

```python
        subprocess.run(["git", "-C", str(workspace_root), "clean", "-fd"],
                       capture_output=True, text=True)
        # The reset rolls back goal.toml. The derived/decisions.json cache is
        # now inconsistent with it: if register_decision force-committed the
        # cache this round, `reset --hard` to round-start (where it was
        # untracked) DELETES it; if it was only an ignored working-tree write,
        # `clean -fd` leaves it STALE with a decision goal.toml no longer has.
        # Either way, re-derive the cache from the rolled-back goal.toml so the
        # next round validates against the true registry.
        bootstrap.rebuild_decisions_cache(workspace_root)
        detail = (exc.stderr or "").strip() or "commit failed"
```

(`bootstrap` is imported in orchestrator.py. Note: after a reset to round-start, goal.toml is valid, so the Task-1 fail-loud rebuild will not raise here.)

- [ ] **Step 4: Run test to verify it passes** — `python3 -m unittest tests.test_orchestrator_hook_reject.CommitRejectRebuildsCacheTest -v` — expect PASS.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover tests/` — expect all green.

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_hook_reject.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(orchestrator): rebuild decision cache after commit-reset so it matches rolled-back goal.toml\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: orchestrator — validate designer round/variant; use trusted round_id for patch.diff

**Finding #2 (High)** + closes `TODOS.md` #7. `validate_designer_json` only checks shape; a designer returning a wrong `round` makes `_materialize_designer_output` write `patch.diff` under `rounds/{parsed["round"]}/` so the Reviewer/Verifier-C pointer (`rounds/{round_id}/patch.diff`) 404s. Fix: (a) reject the round when the designer's `round`/`variant` don't equal the active ones; (b) thread the trusted `round_id` into `_materialize_designer_output` and use it for the patch.diff path.

**Files:**
- Modify: `harness/orchestrator.py` (`run_round` designer phase, `_materialize_designer_output`)
- Test: `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_orchestrator_round.py`:

```python
class DesignerRoundVariantGuardTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_wrong_round_is_rejected(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "expo"}
        # Designer reports the wrong round.
        bad = _designer_ok(claims=[claim])
        bad.parsed["round"] = "round-999999"
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
                _planner_ok(), bad, _reviewer_ok(), _verifier_c_ok()]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertNotEqual(outcome.verdict, "merge")
        self.assertEqual(outcome.reason, "cross-field-fail")


class MaterializePatchDiffRoundTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_patch_diff_written_under_trusted_round_id(self):
        # Even if parsed["round"] disagrees, patch.diff lands under round_id.
        parsed = {"round": "round-999999", "variant": "v-001",
                  "patch_diff": "", "evidence": [], "claims": []}
        orchestrator._materialize_designer_output(
            self.ws, "v-001", "round-000007", parsed)
        self.assertTrue(
            (self.ws / "rounds" / "round-000007" / "patch.diff").exists())
        self.assertFalse(
            (self.ws / "rounds" / "round-999999" / "patch.diff").exists())
```

(`RoleOutput` is a frozen dataclass; `bad.parsed["round"] = ...` mutates the dict it holds, which is allowed — the dataclass freeze protects the fields, not the dict contents. Confirm `_designer_ok` returns a `RoleOutput` whose `.parsed` is a plain dict; it does.)

- [ ] **Step 2: Run tests to verify they fail** — `python3 -m unittest tests.test_orchestrator_round.DesignerRoundVariantGuardTest tests.test_orchestrator_round.MaterializePatchDiffRoundTest -v` — expect FAIL (no equality check; materialize signature has no round_id).

- [ ] **Step 3: Implement the round/variant guard** — In `harness/orchestrator.py` `run_round`, after the designer spawn succeeds and `write_role_scratch` is called for the designer (around line 525, before the `_materialize_designer_output` call), add:

```python
    dparsed = designer_result.parsed
    if dparsed.get("round") != round_id or dparsed.get("variant") != variant_id:
        return _reject(
            action="phase-a-fail",
            reason_class="cross-field-fail",
            failed_phase="designer",
            detail=(f"designer round/variant mismatch: got "
                    f"round={dparsed.get('round')!r} variant="
                    f"{dparsed.get('variant')!r}, expected "
                    f"round={round_id!r} variant={variant_id!r}"))
```

- [ ] **Step 4: Implement the round_id thread** — Change `_materialize_designer_output`'s signature to accept `round_id`:

```python
def _materialize_designer_output(
    workspace_root: Path, variant_id: str, round_id: str, parsed: dict,
) -> tuple[list[Path], list[str], list[str], list[str], list[str]]:
```

Inside it, the patch.diff write currently uses `parsed.get("round", "")` — change it to use the trusted `round_id`:

```python
    round_dir = workspace_root / "rounds" / round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "patch.diff").write_text(patch_diff, encoding="utf-8")
```

Update the call site in `run_round` (around line 533):

```python
        materialized, section_paths, claim_paths, _att_unused, evidence_paths = \
            _materialize_designer_output(
                workspace_root, variant_id, round_id, designer_result.parsed,
            )
```

Update the existing `_materialize_designer_output(...)` call sites in `tests/test_orchestrator_round.py` (the `MaterializeFailLoudTest` cases from round 1, which call it as `(self.ws, "v-001", parsed)`) to pass a round_id: `(self.ws, "v-001", "round-000001", parsed)`. Find them with `grep -n "_materialize_designer_output(" tests/test_orchestrator_round.py` and update each.

- [ ] **Step 5: Run tests** — `python3 -m unittest tests.test_orchestrator_round -v` — expect PASS (new + updated existing).

- [ ] **Step 6: Run the full suite** — `python3 -m unittest discover tests/` — expect all green. The happy-path round fixtures use `round="round-000001"`/`variant="v-001"` matching the run_round args, so the guard doesn't trip them.

- [ ] **Step 7: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(orchestrator): validate designer round/variant; write patch.diff under trusted round_id\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: orchestrator — materialization must not overwrite existing ledger IDs

**Finding #4 (High).** Within-round duplicate claim IDs are rejected, but an `ev-/cl-/at-` ID that already exists ON DISK (from a prior round) is silently overwritten. For an append-only audit ledger, reusing an existing ID is a hard `cross-field-fail`.

**Files:**
- Modify: `harness/orchestrator.py` (`_materialize_designer_output`, `_materialize_reviewer_attacks`)
- Test: `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_orchestrator_round.py`:

```python
class MaterializeNoOverwriteTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_existing_evidence_id_raises(self):
        ev_dir = self.ws / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "ev-000001.md").write_text("+++\nid = \"ev-000001\"\n+++\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "claims": [],
                  "evidence": [{"id": "ev-000001", "confidence": "high",
                                "citations": [], "claim": "c", "excerpt": "e"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_existing_claim_id_raises(self):
        cl_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        cl_dir.mkdir(parents=True, exist_ok=True)
        (cl_dir / "cl-000001.json").write_text("{}")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [],
                  "claims": [{"id": "cl-000001", "section_id": "retry-policy",
                              "decision_id": "retry-policy",
                              "claim_type": "decision", "position": "expo",
                              "evidence_ids": []}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_existing_attack_id_raises(self):
        at_dir = self.ws / "variants" / "nodes" / "v-001" / "attacks"
        at_dir.mkdir(parents=True, exist_ok=True)
        (at_dir / "at-000001.json").write_text("{}")
        parsed = {"attacks": [{"id": "at-000001", "at_type": "dispute_claim"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_reviewer_attacks(
                self.ws, "v-001", parsed)
```

- [ ] **Step 2: Run tests to verify they fail** — `python3 -m unittest tests.test_orchestrator_round.MaterializeNoOverwriteTest -v` — expect FAIL (current code overwrites).

- [ ] **Step 3: Implement** — In `_materialize_designer_output`, in the evidence loop, after the id-format check and before `ev_path.write_text(...)`, add an existence guard. The evidence block currently is:

```python
        ev_path = evidence_dir / f"{ev_id}.md"
        ev_path.write_text(text)
```
Change to:
```python
        ev_path = evidence_dir / f"{ev_id}.md"
        if ev_path.exists():
            raise RuntimeError(
                f"materialize: evidence id {ev_id!r} already exists on disk "
                "(append-only ledger violation)")
        ev_path.write_text(text)
```

In the claims loop, before `cl_path.write_text(...)`:
```python
        cl_path = claims_dir / f"{cl_id}.json"
        if cl_path.exists():
            raise RuntimeError(
                f"materialize: claim id {cl_id!r} already exists on disk "
                "(append-only ledger violation)")
        cl_path.write_text(json.dumps(claim, indent=2, sort_keys=True))
```

In `_materialize_reviewer_attacks`, before `at_path.write_text(...)`:
```python
        at_path = attacks_dir / f"{at_id}.json"
        if at_path.exists():
            raise RuntimeError(
                f"materialize: attack id {at_id!r} already exists on disk "
                "(append-only ledger violation)")
        at_path.write_text(json.dumps(at, indent=2, sort_keys=True))
```

(Read each loop first and place the guard right before the corresponding `write_text`. The `RuntimeError` is caught by run_round's `except RuntimeError` → `cross-field-fail` rejection for the designer path, and the round-1 attack-materialize try/except for the attack path.)

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_orchestrator_round.MaterializeNoOverwriteTest -v` — expect PASS.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover tests/` — expect all green. (Happy-path tests use fresh IDs each round, so the guard doesn't trip. If a multi-round test reuses an ID across rounds, that test was relying on overwrite — report it; it likely needs distinct IDs per round.)

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(orchestrator): reject re-use of existing ledger ids (append-only integrity)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: context — Reviewer + Verifier-C get the round's claim files

**Finding #3 (High).** Reviewer/Verifier-C pointers list `evidence/` but no claim dir, so Verifier C (which must emit `per_claim`) can't enumerate the `cl-*.json` it must verify. Add the variant's `claims/` dir to both pointer lists.

**Files:**
- Modify: `harness/context.py` (`build_reviewer_context`, `build_verifier_c_context`)
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_context.py` (the `ContextPointersTest` class from round 1 already sets up a workspace via the helpers; add to it OR a sibling class — here a sibling reusing the same setup pattern):

```python
class ContextClaimPointersTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"}})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reviewer_points_at_claims_dir(self):
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("variants/nodes/v-001/claims/", out)

    def test_verifier_c_points_at_claims_dir(self):
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        self.assertIn("variants/nodes/v-001/claims/", out)
```

- [ ] **Step 2: Run tests to verify they fail** — `python3 -m unittest tests.test_context.ContextClaimPointersTest -v` — expect FAIL.

- [ ] **Step 3: Implement** — In `harness/context.py`, add the claims-dir pointer to both builders' pointer lists.

In `build_reviewer_context`, the `_render_goal_and_pointers(...)` call's pointer list is `[f"rounds/{round_id}/patch.diff", "evidence/", f"variants/nodes/{variant_id}/doc/"]`. Add the claims dir:
```python
    out.append(_render_goal_and_pointers(
        title, description, [
            f"rounds/{round_id}/patch.diff",
            f"variants/nodes/{variant_id}/claims/",
            "evidence/",
            f"variants/nodes/{variant_id}/doc/",
        ]))
```

In `build_verifier_c_context`, the pointer list is `[f"rounds/{round_id}/patch.diff", "evidence/"]`. Add the claims dir:
```python
    out.append(_render_goal_and_pointers(
        title, description, [
            f"rounds/{round_id}/patch.diff",
            f"variants/nodes/{variant_id}/claims/",
            "evidence/",
        ]))
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_context.ContextClaimPointersTest -v` — expect PASS.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover tests/` — expect all green.

- [ ] **Step 6: Commit**

```bash
git add harness/context.py tests/test_context.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(context): Reviewer + Verifier-C point at the round claims dir so per_claim can be grounded\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: context — Planner gets goal + pointer context

**Finding #6 (Medium).** Planner (which chooses target sections) lacks the `## Goal` + `## Read these first` block that Designer/Reviewer/VC received.

**Files:**
- Modify: `harness/context.py` (`build_planner_context`)
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test** — Append to `tests/test_context.py`:

```python
class PlannerContextPointersTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_decisions(self.td, {})

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_planner_has_goal_and_pointers(self):
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        self.assertIn("Read these first", out)
        self.assertIn("goal.toml", out)
        self.assertIn("variants/nodes/v-001/doc/", out)
        self.assertIn("test", out)  # goal title from _write_goal_toml
```

- [ ] **Step 2: Run test to verify it fails** — `python3 -m unittest tests.test_context.PlannerContextPointersTest -v` — expect FAIL.

- [ ] **Step 3: Implement** — In `harness/context.py` `build_planner_context`, after `out = [_header("planner", ...), ""]` and before `out.append(_render_registered_decisions(decisions))`, insert the goal+pointers block (mirroring the designer builder):

```python
    title, description = _load_goal_meta(workspace_root)
    out.append(_render_goal_and_pointers(
        title, description, [
            "goal.toml",
            f"variants/nodes/{variant_id}/doc/",
            "rejections/",
        ]))
```

- [ ] **Step 4: Run test** — `python3 -m unittest tests.test_context.PlannerContextPointersTest -v` — expect PASS.

- [ ] **Step 5: Run the full suite** — `python3 -m unittest discover tests/` — expect all green.

- [ ] **Step 6: Commit**

```bash
git add harness/context.py tests/test_context.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'fix(context): Planner gets goal + read-first pointers (it chooses target sections)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: cross-cutting verification + /code-review

- [ ] **Step 1: Full suite green** — `python3 -m unittest discover tests/` — OK.

- [ ] **Step 2: Sanity — fresh init still bootstraps, and a malformed goal.toml fails loud**

```bash
cd /tmp && rm -rf _r2 && PYTHONPATH=/Users/liwen/develop/projects/auto_design_doc python3 -m harness init /tmp/_r2 && PYTHONPATH=/Users/liwen/develop/projects/auto_design_doc python3 -c "import json,pathlib; d=json.loads(pathlib.Path('/tmp/_r2/derived/decisions.json').read_text()); print('decisions:', sorted(d['decisions']))" && rm -rf /tmp/_r2
```
Expected: `decisions: ['circuit-breaker-policy', 'rate-limit-policy', 'retry-policy']`.

- [ ] **Step 3: Mark `TODOS.md` #7 resolved** — Task 3 closed it. Edit `TODOS.md`: under entry #7, append a line `**RESOLVED (2026-05-31):** remediation round 2 Task 3 — designer round/variant validated + patch.diff written under trusted round_id.` Commit:
```bash
git add TODOS.md
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'docs(todos): mark #7 resolved by remediation round 2\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 4: Cross-cutting /code-review** — Run `/code-review` over the round-2 range (`<sha before Task 1>..HEAD`). Fix Critical findings inline; record the rest in `TODOS.md`.

---

## Plan self-review

**Finding coverage:** #5→T1, #1→T2, #2→T3, #4→T4, #3→T5, #6→T6, verification→T7. ✓

**Type/signature consistency:**
- `_materialize_designer_output(workspace_root, variant_id, round_id, parsed)` — new signature in T3; T4 adds existence guards inside it (same signature); the round-1 test call sites are updated in T3 Step 4. ✓
- `rebuild_decisions_cache` keeps its `(workspace_root)` signature (T1); T2 calls it in `_commit_reject`. ✓
- `_render_goal_and_pointers(title, description, pointers)` — used unchanged in T5/T6 (matches the round-1 signature after RT7's cleanup dropped `workspace_root`). ✓

**Ordering dependency:** T1 (fail-loud rebuild) lands before T2 (which calls rebuild in `_commit_reject`) — on a post-reset goal.toml the rebuild won't raise. T3 changes the materialize signature before T4 adds guards inside it. ✓

**Known fixture risks (flagged, not silent):**
- T1: any test goal.toml lacking `goal_version` now raises on rebuild — T1 Step 5 calls this out and says to fix the fixture.
- T3: the round-1 `_materialize_designer_output` call sites need the new `round_id` arg — T3 Step 4 calls this out.
- T4: a multi-round test reusing a ledger ID across rounds would now fail — T4 Step 5 calls this out.
