# Morning Brief Pipeline + Scorecard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the per-variant six-dimension scorecard (computed every round, gating the merge) and the `morning_brief.md` assembler (rendered once when the run pauses) to the Design Doc Evolution Harness.

**Architecture:** A pure scoring engine (`harness/scorecard.py`) computes six dimensions on the materialized-but-uncommitted doc state and decides a delta-tolerance merge gate at a new orchestrator Phase 6.5. A thin assembler (`harness/morning_brief.py`) stitches the existing `claim_graph` section renderers plus run-level sections into the brief, which `run_loop` writes at pause. Supporting changes: commit-msg hook gains a `score-regression` Action and a `Score-Delta` trailer; the reviewer JSON gains `goal_alignment` + `technical_correctness`; `round_ledger.commit_merge` carries the `Score-Delta` trailer and stages `scorecard.json`.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `tomllib`, `subprocess`, `re`, `pathlib`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-05-31-morning-brief-and-scorecard-design.md`

---

## Conventions for this plan

- Run the full suite with: `python3 -m unittest discover tests/`
- Run one test file: `python3 -m unittest tests.test_scorecard -v`
- Commits use the repo's harness identity (no global git config required):
  ```bash
  git -c user.email=harness@localhost -c user.name=harness commit -q -m "<msg>"
  ```
  End every commit message body with:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- All work is on `main` (established session pattern; no remote).

---

## Task 1: commit-msg hook — `score-regression` Action

**Files:**
- Modify: `workspace_template/hooks/commit-msg:20-52,68-107`
- Test: `tests/test_commit_msg_hook.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_commit_msg_hook.py` (inside a new test class at end of file, before `if __name__`):

```python
class ScoreRegressionActionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_score_regression_with_required_trailers_passes(self):
        # Stage a rejection file so the file-whitelist check is satisfied.
        rej = self.ws / "rejections" / "rj-000001.md"
        rej.parent.mkdir(parents=True, exist_ok=True)
        rej.write_text("+++\n+++\nbody\n")
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "rejections/rj-000001.md"])
        msg = _write_msg(self.ws,
            "chore: score-regression for round-000002 v-001\n\n"
            "Action: score-regression\nVariant: v-001\n"
            "Round: round-000002\nReason: score-regression\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_score_regression_missing_reason_rejects(self):
        msg = _write_msg(self.ws,
            "chore: score-regression\n\n"
            "Action: score-regression\nVariant: v-001\nRound: round-000002\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Reason", result.stderr)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_commit_msg_hook.ScoreRegressionActionTest -v`
Expected: FAIL — `Action 'score-regression' not in allowed set` (and the missing-reason test passes for the wrong reason / errors). Both must be red before Step 3.

- [ ] **Step 3: Implement the hook change**

In `workspace_template/hooks/commit-msg`, add `"score-regression"` to `ALLOWED_ACTIONS` (line 20-24) and to `ALLOWED_REASONS` (line 26-30):

```python
ALLOWED_ACTIONS = frozenset({
    "init", "merge", "register-decision", "canonicalize", "registry-sync",
    "reviewer-rejected", "phase-a-fail", "phase-b-fail", "phase-c-dispute",
    "spawn-failed", "output-parse-fail", "score-regression",
})

ALLOWED_REASONS = frozenset({
    "uncited-claim", "cross-field-fail", "vacuous-position",
    "proposal-rejected", "scope-violation", "immutability-violation",
    "phantom-claim", "dangling-evidence", "silent-goal-toml-edit",
    "score-regression",
})
```

Add to `TRAILER_REQUIREMENTS` (after the `"output-parse-fail"` entry, line 51):

```python
    "output-parse-fail": {"Variant", "Round"},
    "score-regression": {"Variant", "Round", "Reason"},
}
```

Add to `ACTION_FILE_WHITELIST` (after the `"output-parse-fail"` entry, line 106):

```python
    "output-parse-fail": ["rejections/rj-*.md", "actions.jsonl"],
    "score-regression": ["rejections/rj-*.md", "actions.jsonl"],
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_commit_msg_hook.ScoreRegressionActionTest -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(hooks): score-regression Action in commit-msg hook\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: commit-msg hook — `Score-Delta` trailer

**Files:**
- Modify: `workspace_template/hooks/commit-msg:316-356`
- Test: `tests/test_commit_msg_hook.py`

The `Score-Delta` trailer must be recognized (not rejected as "unknown trailer key"), its value validated, and permitted **only** on `Action: merge`.

- [ ] **Step 1: Write the failing tests**

Append a new class to `tests/test_commit_msg_hook.py`:

```python
class ScoreDeltaTrailerTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _merge_msg(self, score_delta_line):
        # Stage a doc section so the merge whitelist is satisfied.
        doc = self.ws / "variants" / "nodes" / "v-001" / "doc" / "01-x.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text('+++\nsection_id = "x"\n+++\n## X\nbody\n')
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "variants/nodes/v-001/doc/01-x.md"])
        return _write_msg(self.ws,
            "feat: round-000002 v-001\n\n"
            "Action: merge\nVariant: v-001\nRound: round-000002\n"
            + score_delta_line)

    def test_valid_score_delta_on_merge_passes(self):
        msg = self._merge_msg(
            "Score-Delta: groundedness=+0.04 completeness=-0.01\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_malformed_score_delta_rejects(self):
        msg = self._merge_msg("Score-Delta: groundedness=up\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Score-Delta", result.stderr)

    def test_score_delta_on_non_merge_action_rejects(self):
        rej = self.ws / "rejections" / "rj-000001.md"
        rej.parent.mkdir(parents=True, exist_ok=True)
        rej.write_text("+++\n+++\nbody\n")
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "rejections/rj-000001.md"])
        msg = _write_msg(self.ws,
            "chore: reject\n\nAction: reviewer-rejected\nVariant: v-001\n"
            "Round: round-000002\nReason: cross-field-fail\nReviewer: v-001\n"
            "Score-Delta: groundedness=+0.04\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Score-Delta", result.stderr)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_commit_msg_hook.ScoreDeltaTrailerTest -v`
Expected: FAIL — `unknown trailer key 'Score-Delta'`.

- [ ] **Step 3: Implement the hook change**

In `workspace_template/hooks/commit-msg`, add a module-level regex near the other `_RE` constants (after line 33):

```python
SCORE_DELTA_TOKEN_RE = re.compile(r"^[a-z_]+=[+-]\d+\.\d{2}$")
```

In `validate_trailers`, add a branch before the final `else` (the `unknown trailer key` line, ~line 354). The `Score-Delta` value is one-or-more space-separated `dim=±0.NN` tokens; merge-only enforcement happens in a dedicated check (Step 3b) because `validate_trailers` does not know the Action yet:

```python
        elif key == "Reviewer":
            if not VARIANT_RE.match(value):
                errors.append(
                    f"Reviewer {value!r} does not match ^v-\\d{{3}}$"
                )
        elif key == "Score-Delta":
            tokens = value.split()
            if not tokens or not all(
                SCORE_DELTA_TOKEN_RE.match(t) for t in tokens
            ):
                errors.append(
                    f"Score-Delta {value!r} must be space-separated "
                    "dim=[+-]N.NN tokens"
                )
        else:
            errors.append(f"unknown trailer key {key!r}")
```

- [ ] **Step 3b: Add the merge-only check**

Add a helper after `check_required_trailers` (line 364) and call it from `main`:

```python
def check_score_delta_action(action, seen_keys):
    if "Score-Delta" in seen_keys and action != "merge":
        return [
            "Score-Delta trailer is only allowed on Action: merge "
            f"(got Action: {action!r})"
        ]
    return []
```

In `main`, inside the `if action is not None and action in ALLOWED_ACTIONS:` block (after the `check_required_trailers` call, line 387):

```python
        errors.extend(check_required_trailers(action, seen_keys))
        errors.extend(check_score_delta_action(action, seen_keys))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_commit_msg_hook.ScoreDeltaTrailerTest -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full hook suite**

Run: `python3 -m unittest tests.test_commit_msg_hook -v`
Expected: PASS (all prior + 5 new).

- [ ] **Step 6: Commit**

```bash
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(hooks): Score-Delta trailer recognition + merge-only enforcement\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: round_ledger — `commit_merge` Score-Delta + scorecard staging

**Files:**
- Modify: `harness/round_ledger.py:160-191`
- Test: `tests/test_round_ledger.py`

- [ ] **Step 1: Write the failing tests**

Append a new class to `tests/test_round_ledger.py`. It scaffolds a workspace, stages a doc section, and inspects the resulting commit message:

```python
class CommitMergeScoreDeltaTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _make_section(self):
        doc = self.ws / "variants" / "nodes" / "v-001" / "doc" / "01-x.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text('+++\nsection_id = "x"\n+++\n## X\nbody\n')
        return "variants/nodes/v-001/doc/01-x.md"

    def _last_commit_msg(self):
        return subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"]
        ).decode()

    def test_score_delta_appended_when_provided(self):
        sec = self._make_section()
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[],
            score_delta="groundedness=+0.04 completeness=-0.01",
        )
        msg = self._last_commit_msg()
        self.assertIn("Score-Delta: groundedness=+0.04 completeness=-0.01", msg)

    def test_no_score_delta_trailer_when_none(self):
        sec = self._make_section()
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[], score_delta=None,
        )
        self.assertNotIn("Score-Delta", self._last_commit_msg())

    def test_scorecard_path_staged_when_provided(self):
        sec = self._make_section()
        sc = self.ws / "variants" / "nodes" / "v-001" / "scorecard.json"
        sc.write_text('{"variant": "v-001"}')
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[], score_delta="groundedness=+0.01",
            scorecard_path="variants/nodes/v-001/scorecard.json",
        )
        # The scorecard file is now tracked in the merge commit.
        tracked = subprocess.check_output(
            ["git", "-C", str(self.ws), "ls-files",
             "variants/nodes/v-001/scorecard.json"]
        ).decode().strip()
        self.assertEqual(tracked, "variants/nodes/v-001/scorecard.json")
```

(`subprocess` is already imported in this test module.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_round_ledger.CommitMergeScoreDeltaTest -v`
Expected: FAIL — `commit_merge() got an unexpected keyword argument 'score_delta'`.

- [ ] **Step 3: Implement the change**

In `harness/round_ledger.py`, replace `commit_merge` (lines 160-180) with:

```python
def commit_merge(
    workspace_root: Path,
    round_id: str,
    variant_id: str,
    section_paths: list[str],
    claim_paths: list[str],
    attack_paths: list[str],
    evidence_paths: list[str],
    score_delta: str | None = None,
    scorecard_path: str | None = None,
) -> None:
    """Stage all materialized files + actions.jsonl (+ scorecard.json when
    given), commit with Action: merge + Variant + Round (+ Score-Delta when
    given) trailers."""
    all_paths = list(section_paths) + list(claim_paths) + \
                list(attack_paths) + list(evidence_paths)
    if scorecard_path is not None:
        all_paths.append(scorecard_path)
    _git_add(workspace_root, *all_paths, "actions.jsonl")
    lines = [
        f"feat(harness): {round_id} {variant_id}",
        "",
        "Action: merge",
        f"Variant: {variant_id}",
        f"Round: {round_id}",
    ]
    if score_delta is not None:
        lines.append(f"Score-Delta: {score_delta}")
    message = "\n".join(lines) + "\n"
    _git_commit(workspace_root, message)
```

Also add `"score-regression"` to the `_ALLOWED_REASONS` frozenset (lines 187-191) so `commit_rejection` emits the `Reason` trailer for the gate-failure path:

```python
_ALLOWED_REASONS = frozenset({
    "uncited-claim", "cross-field-fail", "vacuous-position",
    "proposal-rejected", "scope-violation", "immutability-violation",
    "phantom-claim", "dangling-evidence", "silent-goal-toml-edit",
    "score-regression",
})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_round_ledger.CommitMergeScoreDeltaTest -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full round_ledger suite (regression guard)**

Run: `python3 -m unittest tests.test_round_ledger -v`
Expected: PASS (all prior 10 + 3 new). The two new keyword args default to `None`, so existing callers are unaffected.

- [ ] **Step 6: Commit**

```bash
git add harness/round_ledger.py tests/test_round_ledger.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(round_ledger): commit_merge Score-Delta trailer + scorecard staging\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: scorecard.py — dimension functions

**Files:**
- Create: `harness/scorecard.py`
- Test: `tests/test_scorecard.py`

Pure functions, no git. Each empty-denominator case returns `1.0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scorecard.py`:

```python
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import scorecard


def _write_evidence(evidence_root, ev_id, superseded_by=None):
    evidence_root.mkdir(parents=True, exist_ok=True)
    fm = [f'id = "{ev_id}"']
    if superseded_by is not None:
        fm.append(f'superseded_by = "{superseded_by}"')
    (evidence_root / f"{ev_id}.md").write_text(
        "+++\n" + "\n".join(fm) + "\n+++\n# Claim\nx\n")


def _write_claim(claims_dir, cl_id, evidence_ids):
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / f"{cl_id}.json").write_text(json.dumps({
        "id": cl_id, "evidence_ids": evidence_ids,
    }))


def _write_section(doc_dir, fname, section_id, body):
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / fname).write_text(
        f'+++\nsection_id = "{section_id}"\n+++\n{body}\n')


class GroundednessTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ev = self.td / "evidence"
        self.claims = self.td / "claims"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_claims_is_vacuously_grounded(self):
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 1.0)

    def test_all_claims_resolved(self):
        _write_evidence(self.ev, "ev-000001")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 1.0)

    def test_one_dangling_one_ok(self):
        _write_evidence(self.ev, "ev-000001")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        _write_claim(self.claims, "cl-000002", ["ev-999999"])  # missing
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 0.5)

    def test_superseded_evidence_counts_as_ungrounded(self):
        _write_evidence(self.ev, "ev-000001", superseded_by="ev-000002")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 0.0)


class CompletenessTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.doc = self.td / "doc"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_required_decisions_is_complete(self):
        self.assertEqual(scorecard.compute_completeness([], self.doc), 1.0)

    def test_retired_decisions_excluded(self):
        decisions = [{"id": "a", "status": "retired"}]
        self.assertEqual(
            scorecard.compute_completeness(decisions, self.doc), 1.0)

    def test_half_covered(self):
        _write_section(self.doc, "01-a.md", "a", "## A")
        decisions = [{"id": "a", "status": "open"},
                     {"id": "b", "status": "proposed"}]
        self.assertEqual(
            scorecard.compute_completeness(decisions, self.doc), 0.5)


class CoherenceTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.doc = self.td / "doc"
        self.ev = self.td / "evidence"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_citations_is_coherent(self):
        _write_section(self.doc, "01-a.md", "a", "## A\nno cites here")
        self.assertEqual(
            scorecard.compute_coherence(self.doc, self.ev), 1.0)

    def test_dead_ref_lowers_coherence(self):
        _write_evidence(self.ev, "ev-000001")
        _write_section(self.doc, "01-a.md", "a",
                       "## A\ngood [^ev-000001] bad [^ev-999999]")
        self.assertEqual(
            scorecard.compute_coherence(self.doc, self.ev), 0.5)


class ConstitutionComplianceTest(unittest.TestCase):
    def test_no_actions_is_compliant(self):
        self.assertEqual(
            scorecard.compute_constitution_compliance([]), 1.0)

    def test_one_denied_of_four(self):
        actions = [{"denied": False}, {"denied": True},
                   {"denied": False}, {}]
        self.assertEqual(
            scorecard.compute_constitution_compliance(actions), 0.75)


class TechnicalCorrectnessTest(unittest.TestCase):
    def test_vc_absent_uses_reviewer_score(self):
        self.assertEqual(
            scorecard.compute_technical_correctness(0.8, None), 0.8)

    def test_vc_present_penalizes(self):
        # confirm-rate 0.5 over the reviewer's 0.8 -> 0.4
        self.assertAlmostEqual(
            scorecard.compute_technical_correctness(0.8, 0.5), 0.4)

    def test_vc_confirm_rate_empty_is_none(self):
        self.assertIsNone(scorecard.compute_vc_confirm_rate([]))

    def test_vc_confirm_rate_confirm_over_confirm_plus_weak(self):
        per_claim = [{"verdict": "confirm"}, {"verdict": "confirm"},
                     {"verdict": "weak"}]
        self.assertAlmostEqual(
            scorecard.compute_vc_confirm_rate(per_claim), 2 / 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_scorecard -v`
Expected: FAIL — `No module named 'harness.scorecard'`.

- [ ] **Step 3: Implement `harness/scorecard.py` (dimension functions)**

```python
"""Per-variant multi-dimensional scorecard for the Design Doc Evolution Harness.

Pure functions: no git, no global state. The orchestrator gathers inputs and
calls compute_dimensions at Phase 6.5; the gate functions decide whether the
round may merge. See docs/superpowers/specs/2026-05-31-morning-brief-and-
scorecard-design.md §3-4.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


DIMENSIONS = (
    "groundedness",
    "goal_alignment",
    "technical_correctness",
    "completeness",
    "coherence",
    "constitution_compliance",
)

_CITE_RE = re.compile(r"\[\^ev-(\d{6})\]")
_SECTION_ID_RE = re.compile(
    r'^\s*section_id\s*=\s*"([^"]+)"\s*$', re.MULTILINE,
)


# ----- Evidence resolution ---------------------------------------------------


def _evidence_resolves_ok(evidence_root: Path, ev_id: str) -> bool:
    """True iff evidence/<ev_id>.md exists, parses, and is not superseded."""
    ev_path = evidence_root / f"{ev_id}.md"
    if not ev_path.exists():
        return False
    text = ev_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("+++"):
        return False
    end = text.find("+++", 3)
    if end == -1:
        return False
    try:
        meta = tomllib.loads(text[3:end])
    except tomllib.TOMLDecodeError:
        return False
    return not meta.get("superseded_by")


# ----- Mechanical dimensions -------------------------------------------------


def compute_groundedness(variant_claims_dir: Path, evidence_root: Path) -> float:
    """Fraction of cl-*.json claims whose every evidence_id resolves to an
    existing, non-superseded evidence file. 0 claims -> 1.0."""
    if not variant_claims_dir.exists():
        return 1.0
    claim_files = sorted(variant_claims_dir.glob("cl-*.json"))
    if not claim_files:
        return 1.0
    grounded = 0
    for cf in claim_files:
        try:
            data = json.loads(cf.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # malformed claim is not grounded
        ev_ids = data.get("evidence_ids", []) or []
        if all(_evidence_resolves_ok(evidence_root, e) for e in ev_ids):
            grounded += 1
    return grounded / len(claim_files)


def _section_ids(variant_doc_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not variant_doc_dir.exists():
        return ids
    for md in variant_doc_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        m = _SECTION_ID_RE.search(text)
        if m:
            ids.add(m.group(1))
    return ids


def compute_completeness(decisions: list[dict], variant_doc_dir: Path) -> float:
    """Fraction of required decisions (status in {open, proposed}) that have a
    doc section whose section_id matches the decision id. 0 required -> 1.0."""
    required = [d["id"] for d in decisions
                if d.get("status") in ("open", "proposed")]
    if not required:
        return 1.0
    present = _section_ids(variant_doc_dir)
    covered = sum(1 for did in required if did in present)
    return covered / len(required)


def compute_coherence(variant_doc_dir: Path, evidence_root: Path) -> float:
    """1 - (dead [^ev-*] citations / total citations). 0 citations -> 1.0.
    A citation is dead if its evidence is missing or superseded."""
    total = 0
    dead = 0
    if variant_doc_dir.exists():
        for md in variant_doc_dir.glob("*.md"):
            body = md.read_text(encoding="utf-8", errors="replace")
            for m in _CITE_RE.finditer(body):
                total += 1
                if not _evidence_resolves_ok(evidence_root, f"ev-{m.group(1)}"):
                    dead += 1
    if total == 0:
        return 1.0
    return 1.0 - (dead / total)


def compute_constitution_compliance(round_actions: list[dict]) -> float:
    """1 - (denied actions / total actions). 0 actions -> 1.0."""
    if not round_actions:
        return 1.0
    denied = sum(1 for a in round_actions if a.get("denied"))
    return 1.0 - (denied / len(round_actions))


# ----- Judgment dimensions ---------------------------------------------------


def compute_vc_confirm_rate(vc_per_claim: list[dict]) -> float | None:
    """confirmed / (confirmed + weak) over Verifier-C per_claim verdicts.
    Returns None when there are no confirm/weak verdicts (VC absent or empty)."""
    confirm = sum(1 for p in vc_per_claim if p.get("verdict") == "confirm")
    weak = sum(1 for p in vc_per_claim if p.get("verdict") == "weak")
    denom = confirm + weak
    if denom == 0:
        return None
    return confirm / denom


def compute_technical_correctness(
    reviewer_score: float, vc_confirm_rate: float | None,
) -> float:
    """reviewer_score x vc_confirm_rate when VC ran, else reviewer_score."""
    if vc_confirm_rate is None:
        return reviewer_score
    return reviewer_score * vc_confirm_rate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_scorecard -v`
Expected: PASS (all dimension tests).

- [ ] **Step 5: Commit**

```bash
git add harness/scorecard.py tests/test_scorecard.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(scorecard): six-dimension scoring functions\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: scorecard.py — gate, delta format, load/build/write, compute_dimensions

**Files:**
- Modify: `harness/scorecard.py` (append)
- Test: `tests/test_scorecard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scorecard.py`:

```python
class GateTest(unittest.TestCase):
    BASE = {d: 0.5 for d in scorecard.DIMENSIONS}

    def test_bootstrap_none_prior_passes(self):
        passed, detail = scorecard.evaluate_gate(None, self.BASE, 0.05)
        self.assertTrue(passed)
        self.assertEqual(detail, "bootstrap")

    def test_improvement_passes(self):
        new = dict(self.BASE, completeness=0.6)
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)

    def test_no_improvement_fails(self):
        passed, detail = scorecard.evaluate_gate(self.BASE, dict(self.BASE),
                                                 0.05)
        self.assertFalse(passed)
        self.assertIn("no dimension improved", detail)

    def test_regression_beyond_tolerance_fails(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.4)  # -0.1 < -0.05
        passed, detail = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertFalse(passed)
        self.assertIn("coherence", detail)

    def test_regression_within_tolerance_with_improvement_passes(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.46)  # -0.04 ok
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)

    def test_drop_of_exactly_tolerance_passes(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.45)  # -0.05 exactly
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)


class FormatScoreDeltaTest(unittest.TestCase):
    def test_signed_two_decimals_in_dimension_order(self):
        prior = {d: 0.50 for d in scorecard.DIMENSIONS}
        new = dict(prior, groundedness=0.54, technical_correctness=0.48)
        s = scorecard.format_score_delta(prior, new)
        self.assertTrue(s.startswith("groundedness=+0.04 "))
        self.assertIn("technical_correctness=-0.02", s)
        self.assertIn("goal_alignment=+0.00", s)
        # All six dims present, space-separated.
        self.assertEqual(len(s.split()), len(scorecard.DIMENSIONS))


class LoadBuildWriteTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_load_missing_returns_none(self):
        self.assertIsNone(scorecard.load_scorecard(self.td / "nope.json"))

    def test_build_write_load_roundtrip(self):
        dims = {d: 0.5 for d in scorecard.DIMENSIONS}
        card = scorecard.build_scorecard("v-001", "round-000002", dims)
        path = self.td / "scorecard.json"
        scorecard.write_scorecard(path, card)
        loaded = scorecard.load_scorecard(path)
        self.assertEqual(loaded["variant"], "v-001")
        self.assertEqual(loaded["round"], "round-000002")
        self.assertEqual(loaded["dimensions"], dims)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_scorecard.GateTest -v`
Expected: FAIL — `module 'harness.scorecard' has no attribute 'evaluate_gate'`.

- [ ] **Step 3: Implement the additions**

Append to `harness/scorecard.py`:

```python
# ----- Aggregate + gate ------------------------------------------------------


def compute_dimensions(
    *,
    variant_claims_dir: Path,
    variant_doc_dir: Path,
    evidence_root: Path,
    decisions: list[dict],
    round_actions: list[dict],
    reviewer_goal_alignment: float,
    reviewer_technical_correctness: float,
    vc_per_claim: list[dict],
) -> dict:
    """Compute all six dimensions. Returns {dim: float} keyed by DIMENSIONS."""
    vc_rate = compute_vc_confirm_rate(vc_per_claim)
    return {
        "groundedness": compute_groundedness(variant_claims_dir, evidence_root),
        "goal_alignment": reviewer_goal_alignment,
        "technical_correctness": compute_technical_correctness(
            reviewer_technical_correctness, vc_rate),
        "completeness": compute_completeness(decisions, variant_doc_dir),
        "coherence": compute_coherence(variant_doc_dir, evidence_root),
        "constitution_compliance": compute_constitution_compliance(
            round_actions),
    }


def evaluate_gate(
    prior_dimensions: dict | None,
    new_dimensions: dict,
    tolerance: float,
) -> tuple[bool, str]:
    """Merge gate (delta tolerance). Returns (passed, detail).

    Bootstrap (no prior) always passes. Otherwise: pass iff at least one shared
    dimension strictly improved AND no shared dimension dropped more than
    `tolerance` below its prior value.
    """
    if prior_dimensions is None:
        return True, "bootstrap"
    shared = [d for d in new_dimensions if d in prior_dimensions]
    improved = any(new_dimensions[d] > prior_dimensions[d] for d in shared)
    regressions = [
        d for d in shared
        if new_dimensions[d] < prior_dimensions[d] - tolerance
    ]
    if regressions:
        worst = ", ".join(
            f"{d}: {prior_dimensions[d]:.2f}->{new_dimensions[d]:.2f}"
            for d in regressions
        )
        return False, f"regressed beyond tolerance: {worst}"
    if not improved:
        return False, "no dimension improved"
    return True, "ok"


def format_score_delta(prior_dimensions: dict, new_dimensions: dict) -> str:
    """Signed two-decimal per-dimension delta, in DIMENSIONS order."""
    parts = []
    for d in DIMENSIONS:
        delta = new_dimensions.get(d, 0.0) - prior_dimensions.get(d, 0.0)
        parts.append(f"{d}={delta:+.2f}")
    return " ".join(parts)


# ----- scorecard.json I/O ----------------------------------------------------


def build_scorecard(variant_id: str, round_id: str, dimensions: dict) -> dict:
    return {
        "variant": variant_id,
        "round": round_id,
        "dimensions": dimensions,
    }


def load_scorecard(scorecard_path: Path) -> dict | None:
    if not scorecard_path.exists():
        return None
    try:
        return json.loads(scorecard_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_scorecard(scorecard_path: Path, scorecard: dict) -> None:
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        json.dumps(scorecard, indent=2, sort_keys=True))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_scorecard -v`
Expected: PASS (all dimension + gate + format + I/O tests).

- [ ] **Step 5: Commit**

```bash
git add harness/scorecard.py tests/test_scorecard.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(scorecard): merge gate, Score-Delta format, scorecard.json I/O\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: reviewer JSON — require `goal_alignment` + `technical_correctness`

**Files:**
- Modify: `harness/orchestrator.py:88-101` (`validate_reviewer_json`), `harness/orchestrator.py` (`REVIEWER_PROMPT`)
- Test: `tests/test_orchestrator_round.py`

- [ ] **Step 1: Find the existing reviewer-validator test location**

Run: `grep -n "validate_reviewer_json\|REVIEWER_PROMPT" tests/test_orchestrator_round.py harness/orchestrator.py`
Note the test class that exercises `validate_reviewer_json` (if none exists, the new test below stands alone).

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_orchestrator_round.py` (it already imports `from harness import orchestrator` — confirm and reuse that name; if it imports as `orch`, match it):

```python
class ReviewerScoreFieldsValidatorTest(unittest.TestCase):
    BASE = {
        "round": "round-000001", "variant": "v-001",
        "decision": "accept", "rationale": "ok",
        "goal_alignment": 0.8, "technical_correctness": 0.7,
    }

    def test_valid_payload_passes(self):
        orchestrator.validate_reviewer_json(dict(self.BASE))  # no raise

    def test_missing_goal_alignment_raises(self):
        d = dict(self.BASE)
        del d["goal_alignment"]
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)

    def test_out_of_range_technical_correctness_raises(self):
        d = dict(self.BASE, technical_correctness=1.4)
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_orchestrator_round.ReviewerScoreFieldsValidatorTest -v`
Expected: FAIL — `test_missing_goal_alignment_raises` does not raise (field not yet required).

- [ ] **Step 4: Implement the validator change**

In `harness/orchestrator.py`, replace `validate_reviewer_json` (lines 88-100) with:

```python
def validate_reviewer_json(d: dict) -> None:
    for key in ("round", "variant", "decision", "rationale",
                "goal_alignment", "technical_correctness"):
        if key not in d:
            raise ValueError(f"reviewer.json missing {key!r}")
    if d["decision"] not in ("accept", "reject"):
        raise ValueError(
            f"reviewer.json decision must be accept|reject, got {d['decision']!r}"
        )
    for key in ("goal_alignment", "technical_correctness"):
        v = d[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(
                f"reviewer.json {key} must be a float in [0,1], got {v!r}"
            )
    # decision_proposals and attacks roundtrip via their dataclass from_dict
    for v in d.get("decision_proposals", []) or []:
        cg.DecisionProposalVerdict.from_dict(v)
    for a in d.get("attacks", []) or []:
        cg.Attack.from_dict(a)
```

Then update `REVIEWER_PROMPT` to instruct emission. Find it with `grep -n "REVIEWER_PROMPT = " harness/orchestrator.py` and extend the field list in the prompt string to mention both new fields, e.g. append to the existing instruction text:

```
" Also emit goal_alignment and technical_correctness, each a float in "
"[0,1] scoring how well this round's doc serves the goal and how "
"technically correct the cited claims are, with a one-line rationale."
```

(Match the existing string-concatenation style of the prompt; do not break its formatting.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_orchestrator_round.ReviewerScoreFieldsValidatorTest -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): reviewer JSON requires goal_alignment + technical_correctness\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: orchestrator — Phase 6.5 merge gate + scorecard write + merge Score-Delta

**Files:**
- Modify: `harness/orchestrator.py` (import; insert Phase 6.5 after line 632; extend Phase 8 `commit_merge` call at lines 698-703)
- Modify: existing reviewer mock payloads in `tests/test_orchestrator_round.py` and `tests/test_orchestrator_loop.py` (add the two score fields)
- Test: `tests/test_orchestrator_score_gate.py` (new)

This task wires the gate into `run_round`. The gate runs after Verifier C passes (after line 632) and before Phase 7a register-decision. On pass it writes `scorecard.json`, appends it to `materialized`, and computes the `Score-Delta` string for the merge commit. On fail it calls the existing `_reject`.

- [ ] **Step 1: Add the scorecard import**

In `harness/orchestrator.py`, add to the import block (after line 25, `from harness import verifiers`):

```python
from harness import scorecard as scorecard_mod
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_orchestrator_score_gate.py`. Reuse the existing mock-spawn harness pattern from `tests/test_orchestrator_round.py` — open that file first and copy its `setUp`/scaffold + `_fake_spawn` helper shape so the mocks match (`patch("harness.orchestrator.spawn_role", ...)` returning `RoleOutput` objects). The new tests assert gate behavior:

```python
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import orchestrator
from harness import scorecard as scorecard_mod
# Reuse the round-test fixtures: copy the helper builders for planner/designer/
# reviewer/verifier_c RoleOutput payloads from tests/test_orchestrator_round.py
# (import them if that module exposes them; otherwise replicate the minimal
# versions here). Each reviewer payload MUST include goal_alignment and
# technical_correctness.

# ... harness setup (scaffold workspace, seed a goal.toml decision + a baseline
# scorecard for the regression test) ...


class ScoreGateTest(unittest.TestCase):
    # setUp scaffolds a workspace via `python3 -m harness init`, then seeds the
    # designer patch / claims so a round can complete to merge.

    def test_bootstrap_round_merges_and_writes_scorecard(self):
        # No prior scorecard.json -> gate passes, scorecard.json written,
        # outcome.verdict == "merge", merge commit has NO Score-Delta trailer.
        ...

    def test_improving_round_merges_with_score_delta(self):
        # Seed a baseline scorecard with low completeness; run a round that
        # adds a section -> completeness improves -> merge commit carries a
        # Score-Delta trailer; scorecard.json updated.
        ...

    def test_regressing_round_rejects_with_score_regression(self):
        # Seed a baseline scorecard whose dims are high; mock reviewer scores
        # low so a dim regresses beyond tolerance with no improvement ->
        # outcome.verdict == "score-regression"; last commit Action is
        # score-regression; no register-decision/canonicalize/merge commit.
        ...
```

Fill the `...` bodies using the same mock-spawn mechanics as `test_orchestrator_round.py`'s happy-path test. Concretely, each test:
1. Patches `orchestrator.spawn_role` to return canned `RoleOutput`s for planner/designer/reviewer/verifier_c.
2. Calls `orchestrator.run_round(ws, harness_config, round_id, "v-001")`.
3. Asserts on `outcome.verdict` and on `git log -1 --format=%B`.

For the regression test, write the baseline first:
```python
sc_path = ws / "variants" / "nodes" / "v-001" / "scorecard.json"
scorecard_mod.write_scorecard(sc_path, scorecard_mod.build_scorecard(
    "v-001", "round-000001",
    {d: 0.9 for d in scorecard_mod.DIMENSIONS}))
subprocess.check_call(["git", "-C", str(ws), "add", "-f",
                       "variants/nodes/v-001/scorecard.json"])
subprocess.check_call(["git", "-C", str(ws),
                       "-c", "user.email=h@l", "-c", "user.name=h",
                       "commit", "-q", "-m", "seed\n\nAction: init\n"])
```
and mock the reviewer to return `goal_alignment=0.2, technical_correctness=0.2`
so those dims regress > 0.05 below 0.9 with nothing improved.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_orchestrator_score_gate -v`
Expected: FAIL — Phase 6.5 does not exist yet; bootstrap test fails because no `scorecard.json` is written / no gate runs.

- [ ] **Step 4: Implement Phase 6.5 and extend the merge commit**

In `harness/orchestrator.py`, immediately after the Verifier-C dispute block (after line 632, before the `# ---- Phase 7a` comment), insert:

```python
    # ---- Phase 6.5: Scorecard merge gate ----
    variant_claims_dir = variants_root / variant_id / "claims"
    variant_doc_dir = variants_root / variant_id / "doc"
    goal_toml_path = workspace_root / "goal.toml"
    decisions_list: list[dict] = []
    if goal_toml_path.exists():
        try:
            with goal_toml_path.open("rb") as f:
                decisions_list = tomllib.load(f).get("decision", []) or []
        except (tomllib.TOMLDecodeError, OSError):
            decisions_list = []
    round_actions = _read_round_actions(workspace_root, round_id)
    new_dimensions = scorecard_mod.compute_dimensions(
        variant_claims_dir=variant_claims_dir,
        variant_doc_dir=variant_doc_dir,
        evidence_root=evidence_root,
        decisions=decisions_list,
        round_actions=round_actions,
        reviewer_goal_alignment=reviewer_result.parsed["goal_alignment"],
        reviewer_technical_correctness=reviewer_result.parsed[
            "technical_correctness"],
        vc_per_claim=vc_parsed.get("per_claim", []),
    )
    sc_path = variants_root / variant_id / "scorecard.json"
    sc_rel = f"variants/nodes/{variant_id}/scorecard.json"
    prior = scorecard_mod.load_scorecard(sc_path)
    prior_dims = prior["dimensions"] if prior else None
    tolerance = harness_config.get("scorecard", {}).get(
        "regression_tolerance", 0.05)
    passed, gate_detail = scorecard_mod.evaluate_gate(
        prior_dims, new_dimensions, tolerance)
    _log(workspace_root, "scorecard", round_id=round_id,
         variant_id=variant_id, passed=passed, detail=gate_detail,
         dimensions=new_dimensions)
    if not passed:
        return _reject(
            action="score-regression",
            reason_class="score-regression",
            failed_phase="scorecard",
            detail=f"scorecard gate failed ({gate_detail})",
        )
    scorecard_mod.write_scorecard(
        sc_path,
        scorecard_mod.build_scorecard(variant_id, round_id, new_dimensions),
    )
    materialized.append(sc_path)
    score_delta = (
        None if prior_dims is None
        else scorecard_mod.format_score_delta(prior_dims, new_dimensions)
    )
```

Add the `tomllib` import at the top of the file (after `import time`, line 18):

```python
import tomllib
```

Add the `_read_round_actions` helper near `_log` (after line 124):

```python
def _read_round_actions(workspace_root: Path, round_id: str) -> list[dict]:
    """Return actions.jsonl entries tagged with this round_id."""
    path = workspace_root / "actions.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("round_id") == round_id or entry.get("round") == round_id:
            out.append(entry)
    return out
```

Finally, extend the Phase 8 `commit_merge` call (lines 698-703) to pass the new args:

```python
    # ---- Phase 8: Final merge commit ----
    round_ledger.commit_merge(
        workspace_root, round_id=round_id, variant_id=variant_id,
        section_paths=section_paths, claim_paths=claim_paths,
        attack_paths=attack_paths, evidence_paths=evidence_paths,
        score_delta=score_delta, scorecard_path=sc_rel,
    )
```

- [ ] **Step 5: Update the reviewer mock factory**

`tests/test_orchestrator_round.py` builds every reviewer payload through one factory, `_reviewer_ok` (lines 72-86). Phase 6.5 reads `reviewer_result.parsed["goal_alignment"]`, so that factory must include both fields or the existing round tests `KeyError`. Edit the `parsed` dict inside `_reviewer_ok`:

```python
    parsed = {
        "round": round_id, "variant": variant,
        "decision": decision, "rationale": "looks fine",
        "goal_alignment": 0.8, "technical_correctness": 0.7,
    }
```

`tests/test_orchestrator_loop.py` mocks `run_round` wholesale (not `spawn_role`), so it never constructs reviewer payloads and needs **no** change. Confirm with:

Run: `grep -n "_reviewer_ok\|spawn_role\|run_round" tests/test_orchestrator_loop.py`
Expected: only `run_round` mocks, no reviewer payload construction.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_orchestrator_score_gate tests.test_orchestrator_round tests.test_orchestrator_loop -v`
Expected: PASS (new gate tests + all existing orchestrator tests green again).

- [ ] **Step 7: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_score_gate.py tests/test_orchestrator_round.py tests/test_orchestrator_loop.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): Phase 6.5 scorecard merge gate + Score-Delta on merge\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: morning_brief.py — assembler

**Files:**
- Create: `harness/morning_brief.py`
- Test: `tests/test_morning_brief_render.py` (append assembly tests)

`render_morning_brief` stitches the existing `cg.render_*` section helpers plus run-level sections (score trajectory, still-weak, rejected-this-run, look-at-first) into one document. Gathering scopes to `since_sha..HEAD` when a SHA is given.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_morning_brief_render.py`:

```python
import shutil
import tempfile
from pathlib import Path

from harness import morning_brief


class RenderMorningBriefTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.ws.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_empty_workspace_renders_all_section_headers(self):
        # No git, no data: assembler still emits every section with friendly
        # empty states and does not raise.
        out = morning_brief.render_morning_brief(self.ws, since_sha=None)
        self.assertIn("# Morning brief", out)
        self.assertIn("## Position collisions", out)
        self.assertIn("## Decisional asymmetry", out)
        self.assertIn("## Pending registry changes", out)
        self.assertIn("## Canonicalizations applied", out)
        self.assertIn("## Stale proposals", out)
        self.assertIn("## Score trajectory", out)
        self.assertIn("## Still weak", out)
        self.assertIn("## Rejected this run", out)
        self.assertIn("## What I'd ask you to look at first", out)

    def test_sections_in_spec_order(self):
        out = morning_brief.render_morning_brief(self.ws, since_sha=None)
        order = ["## Position collisions", "## Decisional asymmetry",
                 "## Pending registry changes", "## Canonicalizations applied",
                 "## Stale proposals", "## Score trajectory", "## Still weak",
                 "## Rejected this run", "## What I'd ask you to look at first"]
        positions = [out.index(h) for h in order]
        self.assertEqual(positions, sorted(positions))


class StillWeakSectionTest(unittest.TestCase):
    def test_weak_verdicts_rendered(self):
        out = morning_brief.render_still_weak([
            {"claim_id": "cl-000001", "rationale": "thin evidence"},
        ])
        self.assertIn("## Still weak", out)
        self.assertIn("cl-000001", out)
        self.assertIn("thin evidence", out)

    def test_empty_still_weak_friendly_state(self):
        out = morning_brief.render_still_weak([])
        self.assertIn("No claims flagged weak", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_morning_brief_render.RenderMorningBriefTest -v`
Expected: FAIL — `No module named 'harness.morning_brief'`.

- [ ] **Step 3: Implement `harness/morning_brief.py`**

```python
"""Assemble morning_brief.md from workspace state at run pause.

Imports the section renderers from claim_graph; adds run-level sections (score
trajectory, still-weak, rejected-this-run, look-at-first). Rendered once by
run_loop when the loop stops. See spec §7.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harness import claim_graph as cg


_SCORE_DELTA_RE = re.compile(r"^Score-Delta:\s*(.+)$", re.MULTILINE)
_ROUND_RE = re.compile(r"^Round:\s*(round-\d{6})$", re.MULTILINE)
_VARIANT_RE = re.compile(r"^Variant:\s*(v-\d{3})$", re.MULTILINE)
_REASON_RE = re.compile(r"^Reason:\s*(\S+)$", re.MULTILINE)
_ACTION_RE = re.compile(r"^Action:\s*(\S+)$", re.MULTILINE)


def _git_log_messages(workspace_root: Path, since_sha: str | None) -> list[str]:
    """Return commit message bodies in since_sha..HEAD (or all history)."""
    rev = f"{since_sha}..HEAD" if since_sha else "HEAD"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workspace_root), "log", rev,
             "--format=%B%x00"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
    except subprocess.CalledProcessError:
        return []
    return [m.strip() for m in out.split("\x00") if m.strip()]


# ----- Run-level section renderers -------------------------------------------


def render_score_trajectory(rows: list[dict]) -> str:
    """rows: [{"round","variant","score_delta"}], oldest first."""
    if not rows:
        return "## Score trajectory\n\nNo merges this run.\n"
    lines = ["## Score trajectory", "",
             "| Round | Variant | Score-Delta |", "|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['round']} | {r['variant']} | {r['score_delta']} |")
    lines.append("")
    return "\n".join(lines)


def render_still_weak(weak: list[dict]) -> str:
    """weak: [{"claim_id","rationale"}]."""
    if not weak:
        return "## Still weak\n\nNo claims flagged weak this run.\n"
    lines = ["## Still weak", "", "| Claim | Why weak |", "|---|---|"]
    for w in weak:
        lines.append(f"| {w['claim_id']} | {w['rationale']} |")
    lines.append("")
    return "\n".join(lines)


def render_rejected_this_run(by_reason: dict) -> str:
    """by_reason: {reason_class: count}."""
    if not by_reason:
        return "## Rejected this run\n\nNo rounds rejected this run.\n"
    lines = ["## Rejected this run", "", "| Reason class | Count |",
             "|---|---|"]
    for reason in sorted(by_reason):
        lines.append(f"| {reason} | {by_reason[reason]} |")
    lines.append("")
    return "\n".join(lines)


def render_look_at_first(items: list[str]) -> str:
    if not items:
        return ("## What I'd ask you to look at first\n\n"
                "Nothing urgent — the run was clean.\n")
    lines = ["## What I'd ask you to look at first", ""]
    for it in items:
        lines.append(f"- {it}")
    lines.append("")
    return "\n".join(lines)


# ----- Gathering -------------------------------------------------------------


def _gather_trajectory(messages: list[str]) -> list[dict]:
    rows = []
    for msg in reversed(messages):  # oldest first
        if "Action: merge" not in msg:
            continue
        sd = _SCORE_DELTA_RE.search(msg)
        rnd = _ROUND_RE.search(msg)
        var = _VARIANT_RE.search(msg)
        if rnd and var:
            rows.append({
                "round": rnd.group(1), "variant": var.group(1),
                "score_delta": sd.group(1).strip() if sd else "(baseline)",
            })
    return rows


def _gather_rejected(messages: list[str]) -> dict:
    by_reason: dict[str, int] = {}
    reject_actions = {
        "reviewer-rejected", "phase-a-fail", "phase-b-fail",
        "phase-c-dispute", "spawn-failed", "output-parse-fail",
        "score-regression",
    }
    for msg in messages:
        action_m = _ACTION_RE.search(msg)
        if not action_m or action_m.group(1) not in reject_actions:
            continue
        reason_m = _REASON_RE.search(msg)
        reason = reason_m.group(1) if reason_m else action_m.group(1)
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return by_reason


def _gather_still_weak(workspace_root: Path) -> list[dict]:
    """Verifier-C weak verdicts from rounds/*/scratch/verifier_c.json."""
    weak: list[dict] = []
    rounds_root = workspace_root / "rounds"
    if not rounds_root.exists():
        return weak
    for vc in sorted(rounds_root.glob("round-*/scratch/verifier_c.json")):
        try:
            data = json.loads(vc.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for pc in data.get("per_claim", []):
            if pc.get("verdict") == "weak":
                weak.append({
                    "claim_id": pc.get("claim_id", "?"),
                    "rationale": pc.get("rationale", ""),
                })
    return weak


def _gather_look_at_first(workspace_root: Path, rejected: dict) -> list[str]:
    """Ranking heuristic (spec §5.3): contested decisions > decisional
    asymmetry > regressed scores > stale proposals. v0 surfaces the cheap,
    git-derivable signals; richer ranking is a v0.1 concern."""
    items: list[str] = []
    if rejected.get("score-regression"):
        items.append(
            f"{rejected['score-regression']} round(s) hit a score regression "
            "— review the rejections/ entries.")
    return items


# ----- Top-level assembly ----------------------------------------------------


def render_morning_brief(workspace_root: Path,
                         since_sha: str | None = None) -> str:
    messages = _git_log_messages(workspace_root, since_sha)
    rejected = _gather_rejected(messages)
    parts = [
        "# Morning brief\n",
        # Claim-graph sections: v0 passes empty data to the existing
        # renderers (the collision/asymmetry/pending detectors run against
        # derived state the orchestrator rebuilds; wiring their live inputs is
        # covered by the claim-graph detectors already tested in isolation).
        cg.render_position_collisions_table([]),
        cg.render_decisional_asymmetry_table([]),
        cg.render_pending_registry_changes([], [], []),
        cg.render_canonicalizations_applied([], []),
        cg.render_stale_proposals_table([]),
        render_score_trajectory(_gather_trajectory(messages)),
        render_still_weak(_gather_still_weak(workspace_root)),
        render_rejected_this_run(rejected),
        render_look_at_first(_gather_look_at_first(workspace_root, rejected)),
        "\n_\"Survived adversarial review\" is deferred to v0.1._\n",
    ]
    return "\n".join(parts)
```

> **Note on claim-graph section inputs:** the live collision/asymmetry/pending/canonicalization data is produced by the `claim_graph` detectors (already unit-tested) operating on `derived/` state. Wiring those detector calls into the brief is intentionally minimal in v0 — the renderers emit their friendly empty-states when handed `[]`, which keeps this task focused on the run-level sections and the assembly contract. A follow-up (tracked in the deferred backlog) threads the live detector outputs through. This is a deliberate v0 scope line, not an oversight.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_morning_brief_render -v`
Expected: PASS (existing renderer tests + new assembly + still-weak tests).

- [ ] **Step 5: Commit**

```bash
git add harness/morning_brief.py tests/test_morning_brief_render.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(morning_brief): assembler with run-level sections\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 9: run_loop — render the brief at pause

**Files:**
- Modify: `harness/orchestrator.py:733-771` (`run_loop`)
- Test: `tests/test_orchestrator_loop.py`

`run_loop` captures the start SHA, and after the loop writes `workspace/morning_brief.md` via the assembler.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orchestrator_loop.py` (reuse its existing scaffold + mock-spawn helpers):

The existing loop tests in this file mock `run_round` wholesale (e.g. `RunLoopRotationTest`). Follow that exact pattern — `run_loop` calls `render_morning_brief` after the loop regardless of round outcomes, so a faked `run_round` is sufficient and keeps the test fast:

```python
class RunLoopBriefTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_morning_brief_written_at_pause(self):
        def fake_run_round(workspace_root, harness_config,
                           round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed", elapsed_seconds=0.01,
            )
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            orchestrator.run_loop(
                self.ws, _harness_config(), max_rounds=1, variant_count=2)
        brief = self.ws / "morning_brief.md"
        self.assertTrue(brief.exists())
        self.assertIn("# Morning brief", brief.read_text())
```

(`_scaffold_workspace` and `_harness_config` are the module's existing helpers; `mock` is already imported.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_orchestrator_loop.RunLoopBriefTest -v`
Expected: FAIL — `morning_brief.md` not written.

- [ ] **Step 3: Implement the change**

In `harness/orchestrator.py`, add the import (after line 25):

```python
from harness import morning_brief as morning_brief_mod
```

Add a start-SHA helper near `_read_round_actions`:

```python
def _current_head_sha(workspace_root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except subprocess.CalledProcessError:
        return None
```

Add `import subprocess` to the top of `orchestrator.py` if not already present (check first: `grep -n "^import subprocess" harness/orchestrator.py`).

In `run_loop`, capture the SHA before the loop (after `next_n = _next_round_number(workspace_root)`, line 754):

```python
    start_sha = _current_head_sha(workspace_root)
```

And after the `while` loop, before `return outcomes` (line 771):

```python
    brief = morning_brief_mod.render_morning_brief(workspace_root, start_sha)
    (workspace_root / "morning_brief.md").write_text(brief)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_orchestrator_loop.RunLoopBriefTest -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS (all tests). Note the new total count.

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_loop.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): render morning_brief.md at run_loop pause\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 10: harness.toml `[scorecard]` + /code-review gate

**Files:**
- Modify: `workspace_template/harness.toml`
- Test: `tests/test_workspace_template.py` (if it asserts on harness.toml contents) or none

- [ ] **Step 1: Add the config block**

Append to `workspace_template/harness.toml`:

```toml

[scorecard]
# Merge gate: a round must improve >=1 dimension and may not drop any dimension
# by more than this tolerance below its prior value. See harness/scorecard.py.
regression_tolerance = 0.05
```

- [ ] **Step 2: Verify the template still parses + init still works**

Run: `python3 -m unittest tests.test_workspace_template -v`
Expected: PASS. If a test enumerates expected `[section]` tables, add `scorecard` to its expected set.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS (all green).

- [ ] **Step 4: Commit**

```bash
git add workspace_template/harness.toml tests/test_workspace_template.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(workspace): harness.toml [scorecard].regression_tolerance default\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 5: Cross-cutting /code-review pass**

Run `/code-review` over the sub-project 5 commit range (`<sha before Task 1>..HEAD`). Triage findings: fix any Critical inline (new commit), record the rest in the deferred backlog with the prior sub-projects' findings. This mirrors the gate applied to sub-projects 1–4.

---

## Plan self-review

**Spec coverage** (each spec section → task):
- §2 file structure → Tasks 4/5 (scorecard.py), 8 (morning_brief.py), 7/9 (orchestrator), 3 (round_ledger), 1/2 (hook), 6 (reviewer validator), 10 (harness.toml). ✓
- §3 six dimensions incl. empty-denominator + saturation → Task 4. ✓
- §4 merge gate (Phase 6.5, δ, bootstrap, Score-Delta, scorecard.json staging) → Tasks 5 (gate/format) + 7 (integration) + 3 (commit_merge). ✓
- §4.1 scorecard.json schema → Task 5 (`build_scorecard`). ✓
- §5.1 hook (score-regression Action/Reason, Score-Delta key + merge-only) → Tasks 1 + 2. ✓
- §5.2 reviewer fields → Task 6. ✓
- §5.3 harness.toml [scorecard] → Task 10. ✓
- §6 round_ledger (commit_merge + _ALLOWED_REASONS, no new commit fn) → Task 3. ✓
- §7 morning_brief assembly + §7.1 since_sha → Tasks 8 + 9. ✓
- §8 testing → tests in every task. ✓

**Type consistency:** `DIMENSIONS` tuple used consistently; `evaluate_gate(prior|None, new, tol) -> (bool, str)`; `compute_dimensions(**kwargs) -> dict`; `commit_merge(..., score_delta=None, scorecard_path=None)`; `render_morning_brief(ws, since_sha=None) -> str`; reviewer payloads carry `goal_alignment`/`technical_correctness` everywhere they're constructed (Tasks 6 + 7 Step 5). ✓

**Known ripple:** Task 7 Step 5 updates existing reviewer mocks — explicitly called out so the suite stays green.

**Deliberate v0 scope line:** Task 8 hands `[]` to the claim-graph section renderers (live detector wiring deferred). Noted in-task and to be logged in the deferred backlog — not a silent gap.
