# Grounding Verification Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sentence-level citation gate (Verifier A.1) with diff-scoped, paragraph-level LLM grounding judged by Verifier C, feeding the groundedness scorecard dimension.

**Architecture:** Delete `verify_citation_completeness` and its sentence-split machinery. Keep mechanical hard gates (cite-resolution, excerpt-match) and add a tiny `verify_frontmatter_wellformed`. Verifier C gains a `groundedness` float (judged over the round's changed decided paragraphs) that feeds `compute_dimensions`, replacing the reviewer's groundedness, still capped by the mechanical claim-resolution fraction.

**Tech Stack:** Python 3.14, stdlib `unittest`/`pytest`, `tomllib`, `subprocess`+git. Run tests with `.venv` active: `source .venv/bin/activate`.

**Spec:** `docs/superpowers/specs/2026-06-08-grounding-verification-redesign-design.md`

---

### Task 1: Add `verify_frontmatter_wellformed` mechanical gate

Preserve A.1's one non-sentence guarantee (a section whose `tags` isn't a list silently reads as non-decided) as a standalone hard gate.

**Files:**
- Modify: `harness/verifiers.py`
- Test: `tests/test_verifiers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_verifiers.py` (reuse the existing `_write_section` helper and a temp `variants` dir like the other verifier tests):

```python
class FrontmatterWellformedTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_list_tags_passes(self):
        _write_section(self.variants, "v-001", "01-a", ["decided"], "body\n")
        result = v.verify_frontmatter_wellformed(self.variants)
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.failures, [])

    def test_tags_not_a_list_fails(self):
        doc = self.variants / "v-001" / "doc"
        doc.mkdir(parents=True)
        (doc / "01-a.md").write_text(
            '+++\nsection_id = "x"\ntags = "decided"\n+++\nbody\n')
        result = v.verify_frontmatter_wellformed(self.variants)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "malformed-frontmatter")

    def test_missing_variants_root_passes(self):
        result = v.verify_frontmatter_wellformed(self.td / "nope")
        self.assertEqual(result.verdict, "pass")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_verifiers.py::FrontmatterWellformedTest -v`
Expected: FAIL with `AttributeError: module 'harness.verifiers' has no attribute 'verify_frontmatter_wellformed'`

- [ ] **Step 3: Implement `verify_frontmatter_wellformed`**

`_walk_sections` SKIPS sections with malformed frontmatter, so this check must walk files directly. Add to `harness/verifiers.py` (after `_walk_sections`, before the Verifier A.1 section):

```python
def verify_frontmatter_wellformed(variants_nodes_root: Path) -> VerifierResult:
    """Every section file's +++ frontmatter must parse AND its `tags` must be a
    list. A non-list `tags` silently reads as non-decided (grounding bypass), so
    this is a hard gate. Walks files directly — _walk_sections silently skips the
    malformed sections this is meant to catch."""
    failures: list[VerifierFailure] = []
    if not variants_nodes_root.exists():
        return VerifierResult(verdict="pass", failures=[])
    for variant_dir in sorted(variants_nodes_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        doc_dir = variant_dir / "doc"
        if not doc_dir.exists():
            continue
        for md in sorted(doc_dir.glob("*.md")):
            rel = str(md.relative_to(variants_nodes_root.parent.parent))
            text = md.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("+++"):
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail="missing +++ frontmatter fence"))
                continue
            end = text.find("+++", 3)
            if end == -1:
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail="unterminated +++ fence"))
                continue
            try:
                meta = tomllib.loads(text[3:end])
            except tomllib.TOMLDecodeError as e:
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail=f"TOML parse error: {e}"))
                continue
            if not isinstance(meta.get("tags", []), list):
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel,
                    detail=f"tags is {type(meta.get('tags')).__name__!r}, "
                           "expected a list"))
    return VerifierResult(
        verdict="fail" if failures else "pass", failures=failures)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_verifiers.py::FrontmatterWellformedTest -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): add frontmatter-wellformed mechanical gate"
```

---

### Task 2: Delete Verifier A.1 (sentence-level cite gate) + its dead code

**Files:**
- Modify: `harness/verifiers.py` (remove `verify_citation_completeness` and the regexes only it used)
- Modify: `tests/test_verifiers.py` (delete the A.1 test class)

- [ ] **Step 1: Delete the A.1 test class**

In `tests/test_verifiers.py`, delete the entire test class that exercises `verify_citation_completeness` (the class containing `test_decided_section_with_cited_sentences_passes`, `test_decided_section_with_uncited_assertion_fails`, `test_metadata_lines_skipped`, `test_blockquoted_and_bulleted_metadata_skipped`, `test_bold_emphasis_without_colon_still_requires_cite`, `test_heading_lines_skipped`, `test_code_blocks_skipped`, `test_empty_body_passes`, `test_malformed_tags_emits_warning`, etc.). Keep the `_walk_sections`, cite-resolution, excerpt-match, and the new FrontmatterWellformed test classes.

- [ ] **Step 2: Delete `verify_citation_completeness` and its private regexes**

In `harness/verifiers.py`, remove the function `verify_citation_completeness` (its whole `def ... return VerifierResult(...)` block) and these module regexes used ONLY by it:
`_FENCED_CODE_RE`, `_HEADING_LINE_RE`, `_METADATA_LINE_RE`, `_SENTENCE_SPLIT_RE`, `_LETTER_RE`.

KEEP `_CITE_RE` (used by `verify_cite_resolution` and `verify_excerpt_match`).

- [ ] **Step 3: Verify no remaining references**

Run: `grep -n "verify_citation_completeness\|_SENTENCE_SPLIT_RE\|_HEADING_LINE_RE\|_METADATA_LINE_RE\|_LETTER_RE\|_FENCED_CODE_RE" harness/ tests/`
Expected: only the orchestrator Phase-3 call site at `harness/orchestrator.py` (fixed in Task 3). No other hits.

- [ ] **Step 4: Run the verifiers suite**

Run: `source .venv/bin/activate && python -m pytest tests/test_verifiers.py -q`
Expected: PASS (remaining classes green)

- [ ] **Step 5: Commit**

```bash
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "refactor(verifiers): remove sentence-level citation gate (Verifier A.1)"
```

---

### Task 3: Rewire run_round Phase 3 (drop completeness, add frontmatter check)

**Files:**
- Modify: `harness/orchestrator.py:1379-1405` (Phase 3 block)
- Modify: `tests/test_orchestrator_round.py` (`RunRoundVerifierAFailureTest`)

- [ ] **Step 1: Replace the Phase 3 block**

Replace `harness/orchestrator.py` lines 1379-1405 (the `# ---- Phase 3: Verifier A` block, from the comment through the `return _reject(... failed_phase="verifier_a" ...)`) with:

```python
    # ---- Phase 3: Verifier A (mechanical: frontmatter + cite resolution) ----
    r_frontmatter = verifiers.verify_frontmatter_wellformed(variants_root)
    r_resolution = verifiers.verify_cite_resolution(
        variants_root, evidence_root,
    )
    failure_count_a = len(r_frontmatter.failures) + len(r_resolution.failures)
    _log(workspace_root, "verifier_complete",
         round_id=round_id, verifier="a",
         failure_count=failure_count_a,
         verdict="pass" if failure_count_a == 0 else "fail")
    if failure_count_a > 0:
        if r_frontmatter.failures:
            reason = "cross-field-fail"
            failures = r_frontmatter.failures
        else:
            reason = "dangling-evidence"
            failures = r_resolution.failures
        detail_lines = [
            f"{f.variant} {f.section_path}: {f.detail}"
            for f in failures[:20]
        ]
        return _reject(
            action="phase-a-fail",
            reason_class=reason,
            failed_phase="verifier_a",
            detail="\n".join(detail_lines),
        )
```

(`cross-field-fail` is in the commit-msg hook's allowed reasons; `malformed-frontmatter` is a verifier failure *kind*, not a commit Reason.)

- [ ] **Step 2: Repoint the Verifier-A failure test**

In `tests/test_orchestrator_round.py`, find `RunRoundVerifierAFailureTest`. Its current test triggers `phase-a-fail` via an UNCITED assertion in a decided section — that path no longer exists. Replace the offending test method with two tests that exercise the surviving gates. The class `setUp` seeds a decided section file on disk; adapt the body it writes:

```python
    def test_dangling_cite_rejects(self):
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            "Body cites a missing fact [^ev-999999].\n")
        _commit_setup(self.ws)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(), _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "dangling-evidence")

    def test_malformed_frontmatter_rejects(self):
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "x"\ntags = "decided"\n+++\nbody\n')
        _commit_setup(self.ws)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(), _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "cross-field-fail")
```

Delete the old uncited-assertion test method in that class. If the class `setUp` itself writes an uncited decided section that other methods relied on, leave `setUp` but ensure each test writes its own section as above. (Read the class first; adapt to its existing helpers — it already scaffolds `self.ws` and seeds decisions.)

- [ ] **Step 3: Add the "zero cites now merges" regression test**

Add a new test (new class or into `DesignerDirectWriteRoundTest`) proving the screaming case is gone — a decided section with NO cites merges:

```python
class DecidedSectionNoCiteMergesTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decided_section_without_cites_merges(self):
        def _writer():
            _write_doc_section(
                self.ws, "v-001", "01-intro", ["decided"],
                "This section sets context and has no citations.\n")
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=_spawn_seq(
                            [_planner_ok(), _designer_ok(),
                             _reviewer_ok(), _verifier_c_ok()],
                            on_designer=_writer)):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge", outcome.detail)
```

- [ ] **Step 4: Run the round suite**

Run: `source .venv/bin/activate && python -m pytest tests/test_orchestrator_round.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git commit -m "feat(orchestrator): Phase 3 drops sentence cite gate, adds frontmatter gate"
```

---

### Task 4: Verifier C gains a diff-scoped `groundedness` score

**Files:**
- Modify: `harness/orchestrator.py:241-248` (`validate_verifier_c_json`)
- Modify: `harness/orchestrator.py:447-456` (`VERIFIER_C_PROMPT`)
- Modify: `harness/context.py:486-492` (`build_verifier_c_context` pointers)
- Test: `tests/test_orchestrator_round.py`, `tests/test_context.py` (if present; else add a context assertion in round tests)

- [ ] **Step 1: Write failing schema tests**

Add to `tests/test_orchestrator_round.py`:

```python
class ValidateVerifierCGroundednessTest(unittest.TestCase):
    BASE = {"round": "r", "variant": "v", "verdict": "confirm",
            "per_claim": []}

    def test_groundedness_in_range_accepted(self):
        orchestrator.validate_verifier_c_json({**self.BASE,
                                               "groundedness": 0.7})

    def test_groundedness_omitted_accepted(self):
        orchestrator.validate_verifier_c_json(dict(self.BASE))

    def test_groundedness_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_verifier_c_json({**self.BASE,
                                                   "groundedness": 1.5})
```

- [ ] **Step 2: Run to verify the out-of-range test fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_orchestrator_round.py::ValidateVerifierCGroundednessTest -v`
Expected: `test_groundedness_out_of_range_raises` FAILS (no validation yet)

- [ ] **Step 3: Add optional `groundedness` validation**

Replace `validate_verifier_c_json` (`harness/orchestrator.py:241-248`) with:

```python
def validate_verifier_c_json(d: dict) -> None:
    for key in ("round", "variant", "verdict", "per_claim"):
        if key not in d:
            raise ValueError(f"verification.json missing {key!r}")
    if d["verdict"] not in ("confirm", "dispute"):
        raise ValueError(
            f"verification.json verdict must be confirm|dispute, got {d['verdict']!r}"
        )
    # Optional diff-scoped paragraph-grounding score (feeds the scorecard's
    # groundedness dimension). Optional-with-fallback: an omission degrades to
    # the mechanical value rather than hard-failing the round.
    if "groundedness" in d:
        g = d["groundedness"]
        if not isinstance(g, (int, float)) or not (0.0 <= g <= 1.0):
            raise ValueError(
                f"verification.json groundedness must be a float in [0,1], "
                f"got {g!r}")
```

- [ ] **Step 4: Update `VERIFIER_C_PROMPT`**

Replace `harness/orchestrator.py:447-456` with:

```python
VERIFIER_C_PROMPT = (
    "You are Verifier C. Read the CONTEXT.md above plus the round's patch "
    "(rounds/<round>/patch.diff), the FULL text of each changed section file, "
    "and the cited evidence; emit JSON with fields: round, variant, verdict "
    "(confirm|dispute), per_claim (list of {claim_id, verdict (confirm|"
    "weak|dispute), rationale}), candidate_collisions_confirmed (list), "
    "candidate_collisions_rejected (list), groundedness (float in [0,1]). "
    "groundedness judges ONLY the paragraphs THIS round changed (per the patch) "
    "in sections tagged 'decided': does each changed paragraph's FACTUAL content "
    "trace to its cited evidence? Connective/transition/framing sentences need "
    "NO citation and must not lower the score. Do NOT judge untouched paragraphs "
    "or non-decided sections. Use the full continuous range; reserve 0.0/1.0 for "
    "genuine extremes. "
    "Before answering, read every path listed under 'Read these first (on "
    "disk)' in the CONTEXT above; do not rely on the summary tables alone. "
    "Output ONLY valid JSON."
)
```

- [ ] **Step 5: Add the changed-section text to VC context**

In `harness/context.py`, replace the pointer list in `build_verifier_c_context` (`:487-492`) to include the variant's doc dir (so VC reads full section text, not just the diff):

```python
    out.append(_render_goal_and_pointers(
        title, description, [
            f"rounds/{round_id}/patch.diff",
            f"variants/nodes/{variant_id}/doc/",
            f"variants/nodes/{variant_id}/claims/",
            "evidence/",
        ]))
```

- [ ] **Step 6: Add a context assertion test**

Add to `tests/test_orchestrator_round.py` (or `tests/test_context.py` if it exists — check first):

```python
class VerifierCContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_context_points_at_patch_and_doc(self):
        from harness import context as context_mod
        ctx = context_mod.build_verifier_c_context(
            self.ws, "round-000001", "v-001")
        self.assertIn("rounds/round-000001/patch.diff", ctx)
        self.assertIn("variants/nodes/v-001/doc/", ctx)
```

- [ ] **Step 7: Run the new tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_orchestrator_round.py::ValidateVerifierCGroundednessTest tests/test_orchestrator_round.py::VerifierCContextTest -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add harness/orchestrator.py harness/context.py tests/
git commit -m "feat(verifier-c): add diff-scoped groundedness score + context"
```

---

### Task 5: Move scorecard groundedness from reviewer to Verifier C

**Files:**
- Modify: `harness/scorecard.py:181-216` (`compute_dimensions`)
- Modify: `harness/orchestrator.py:1618-1631` (Phase 6.5 call)
- Modify: `harness/orchestrator.py:417-445` (`REVIEWER_PROMPT` — drop groundedness)
- Modify: `harness/orchestrator.py:177` (`validate_reviewer_json` optional-dims list)
- Test: `tests/test_scorecard.py`, `tests/test_orchestrator_score_gate.py`, `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write failing scorecard test**

Add to `tests/test_scorecard.py` (follow its existing fixtures for `variant_claims_dir`/`evidence_root`):

```python
def test_groundedness_uses_vc_score_capped_by_mechanical(self):
    # No claims => mechanical groundedness == 1.0; a low VC score must still
    # pull the dimension down (min), and the reviewer score is ignored.
    dims = scorecard.compute_dimensions(
        variant_claims_dir=self.empty_claims_dir,
        variant_doc_dir=self.doc_dir,
        evidence_root=self.evidence_root,
        decisions=[], round_actions=[],
        reviewer_goal_alignment=0.8,
        reviewer_technical_correctness=0.7,
        vc_per_claim=[],
        vc_groundedness=0.2,
        reviewer_completeness=None,
        reviewer_coherence=None,
    )
    self.assertEqual(dims["groundedness"], 0.2)
```

(If the test class lacks `self.empty_claims_dir`/`self.doc_dir`/`self.evidence_root`, create temp dirs in the test body — an empty claims dir yields mechanical 1.0.)

- [ ] **Step 2: Run to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_scorecard.py::*::test_groundedness_uses_vc_score_capped_by_mechanical -v`
Expected: FAIL with `TypeError: compute_dimensions() got an unexpected keyword argument 'vc_groundedness'`

- [ ] **Step 3: Rename the param in `compute_dimensions`**

In `harness/scorecard.py`, change the signature param `reviewer_groundedness: float | None = None,` (line 191) to `vc_groundedness: float | None = None,` and update the body (lines 202-204):

```python
        "groundedness": _cap(
            vc_groundedness,
            compute_groundedness(variant_claims_dir, evidence_root)),
```

Also update the docstring line that says groundedness is "LLM-judged ... reviewer" to reference Verifier C.

- [ ] **Step 4: Update the Phase 6.5 call site**

In `harness/orchestrator.py` (the `compute_dimensions(...)` call, lines 1618-1631), replace the `reviewer_groundedness=...` keyword with:

```python
        vc_groundedness=vc_parsed.get("groundedness"),
```

(Leave `reviewer_completeness=` and `reviewer_coherence=` as-is. `vc_parsed` is already bound earlier in Phase 6.)

- [ ] **Step 5: Drop groundedness from the reviewer**

In `harness/orchestrator.py` `REVIEWER_PROMPT` (lines 417-445): change "Also emit five quality scores" to "Also emit four quality scores", and DELETE the two lines defining `groundedness (...)`. Keep goal_alignment, technical_correctness, completeness, coherence.

In `validate_reviewer_json` (line 177), change the optional-dims tuple from `("groundedness", "completeness", "coherence")` to `("completeness", "coherence")`.

- [ ] **Step 6: Update existing scorecard/score-gate/round fixtures**

Search and update any test passing `reviewer_groundedness=` to `compute_dimensions`:

Run: `grep -rn "reviewer_groundedness" tests/`

For each hit (likely in `tests/test_scorecard.py` and `tests/test_orchestrator_score_gate.py`), rename the keyword to `vc_groundedness=`. In `tests/test_orchestrator_round.py`, check `_reviewer_ok` / `_verifier_c_ok` helpers: if `_reviewer_ok` sets a `groundedness` key it's now ignored (harmless, but remove for clarity); add an optional `groundedness` kwarg to `_verifier_c_ok` so round tests can drive the dimension:

```python
def _verifier_c_ok(round_id="round-000001", variant="v-001",
                   verdict="confirm", per_claim=None, groundedness=None):
    parsed = {
        "round": round_id, "variant": variant, "verdict": verdict,
        "per_claim": per_claim or [],
        "candidate_collisions_confirmed": [],
        "candidate_collisions_rejected": [],
    }
    if groundedness is not None:
        parsed["groundedness"] = groundedness
    return RoleOutput(verdict="ok", parsed=parsed, retry_count=0,
                      elapsed_seconds=0.1)
```

- [ ] **Step 7: Add "groundedness comes from VC" round test**

Add to `tests/test_orchestrator_round.py`:

```python
class GroundednessFromVerifierCTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_merge_scorecard_groundedness_from_vc(self):
        claim = {"section_id": "retry-policy", "decision_id": "retry-policy",
                 "claim_type": "decision", "evidence_ids": [],
                 "assertion": "Use expo-backoff.", "position": "expo-backoff"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(), _verifier_c_ok(groundedness=0.55),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge", outcome.detail)
        sc = json.loads(
            (self.ws / "variants" / "nodes" / "v-001"
             / "scorecard.json").read_text())
        # mechanical groundedness == 1.0 (claim has no evidence_ids → resolves
        # vacuously); min(0.55, 1.0) == 0.55.
        self.assertEqual(sc["dimensions"]["groundedness"], 0.55)
```

(Confirm against `compute_groundedness`: a claim with empty `evidence_ids` resolves vacuously → mechanical 1.0. If the seed scorecard baseline interferes with the gate, the round still merges because 0.55 is the first real groundedness; adjust the asserted value only if `compute_groundedness` differs.)

- [ ] **Step 8: Run the affected suites**

Run: `source .venv/bin/activate && python -m pytest tests/test_scorecard.py tests/test_orchestrator_score_gate.py tests/test_orchestrator_round.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add harness/scorecard.py harness/orchestrator.py tests/
git commit -m "feat(scorecard): groundedness sourced from Verifier C, not reviewer"
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Run the entire suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: PASS (all green; ~525 tests). Note the run takes ~12 min.

- [ ] **Step 2: Grep for stragglers**

Run: `grep -rn "verify_citation_completeness\|uncited-claim\|reviewer_groundedness" harness/`
Expected: zero hits in `harness/` except possibly `uncited-claim` retained in the commit-msg hook's allowed-reasons vocab (that is intentional — leave it).

- [ ] **Step 3: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "chore: grounding redesign cleanup"
```

---

## Self-Review notes

- **Spec coverage:** delete A.1 (T2), mechanical backbone incl. frontmatter gate (T1, T3), VC diff-scoped groundedness output + prompt + context (T4), scorecard wiring + reviewer removal (T5), tests across all (T1–T5), zero-migration is inherent (no task needed). All spec sections mapped.
- **Type consistency:** new kwarg is `vc_groundedness` everywhere (scorecard signature + Phase 6.5 call + tests); VC field is `groundedness`; `_verifier_c_ok(groundedness=...)` matches.
- **Known soft spot:** Task 5 Step 7's exact asserted scorecard value depends on `compute_groundedness`'s vacuous-resolution behavior and the seed baseline gate; the step says to confirm against the real function and adjust the constant if needed (not the design).
