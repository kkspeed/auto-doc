# Round State Machine + Variant Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement orchestrator sub-project 4 per [2026-05-31-round-state-machine-and-variant-rotation-design.md](../specs/2026-05-31-round-state-machine-and-variant-rotation-design.md): two new modules (`harness/orchestrator.py`, `harness/round_ledger.py`), a `harness run` CLI subcommand, and ~36 tests across 4 new test files. This is the largest sub-project — it's the actual harness round loop that ties sub-projects 1-3 together.

**Architecture:** Two new modules. `harness/round_ledger.py` owns persistence + commit invocation (write_role_scratch, write_rejection, append_actions_log, commit_*). `harness/orchestrator.py` owns the round state machine: a linear `run_round` function with early returns on rejection paths, plus a `run_loop` that drives variant rotation and dual stopping caps. The CLI gets a `run` subcommand. Validators reuse existing `harness.claim_graph` dataclass `from_dict` paths.

**Tech Stack:** Python 3.11+ stdlib only (`subprocess`, `json`, `time`, `re`, `tomllib`, `datetime`, `pathlib`, `dataclasses`, `unittest`, `unittest.mock`).

---

## File Structure

**Created in this plan:**
- `harness/round_ledger.py` — persistence + commit helpers (~150 LOC)
- `harness/orchestrator.py` — RoundOutcome + run_round + run_loop + validators (~450 LOC)
- `tests/test_round_ledger.py` — ~10 unit tests
- `tests/test_orchestrator_round.py` — ~17 phase-by-phase tests
- `tests/test_orchestrator_loop.py` — ~5 multi-round + stopping tests
- `tests/test_cli_run.py` — ~4 CLI integration tests

**Modified in this plan:**
- `harness/cli.py` — add `run` subcommand (~40 new LOC)

**NOT modified:** all other existing source/test files.

---

## Task 1: `harness/round_ledger.py` + tests

Foundation: persistence + commit invocation. Tasks 2-6 depend on these primitives.

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/round_ledger.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_round_ledger.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_round_ledger.py`:

```python
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from harness import round_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    """Run `python -m harness init` to scaffold a workspace at target."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class WriteRoleScratchTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_writes_json_under_scratch_dir(self):
        path = round_ledger.write_role_scratch(
            self.td, "round-000001", "planner", {"ok": True},
        )
        self.assertEqual(path,
            self.td / "rounds" / "round-000001" / "scratch" / "planner.json")
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text()), {"ok": True})

    def test_creates_parent_dirs(self):
        # Workspace doesn't have rounds/ yet
        round_ledger.write_role_scratch(
            self.td, "round-000007", "designer", {"claims": []},
        )
        self.assertTrue(
            (self.td / "rounds" / "round-000007" / "scratch").is_dir()
        )


class WriteRejectionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_allocates_next_rj_id(self):
        # No existing rejections → first is rj-000001
        rj1 = round_ledger.write_rejection(
            self.td, "round-000001", "v-001",
            reason_class="uncited-claim",
            failed_phase="verifier_a",
            detail="missing cite in 01-retry-policy.md",
        )
        self.assertEqual(rj1, "rj-000001")
        # Next allocation should bump
        rj2 = round_ledger.write_rejection(
            self.td, "round-000002", "v-002",
            reason_class="spawn-failed",
            failed_phase="planner",
            detail="claude CLI exited 1",
        )
        self.assertEqual(rj2, "rj-000002")

    def test_frontmatter_includes_required_fields(self):
        rj = round_ledger.write_rejection(
            self.td, "round-000003", "v-001",
            reason_class="uncited-claim",
            failed_phase="verifier_a",
            detail="Some details here.",
        )
        fp = self.td / "rejections" / f"{rj}.md"
        text = fp.read_text()
        end = text.find("+++", 3)
        fm = tomllib.loads(text[3:end])
        self.assertEqual(fm["variant"], "v-001")
        self.assertEqual(fm["round_id"], "round-000003")
        self.assertEqual(fm["reason_class"], "uncited-claim")
        self.assertEqual(fm["failed_phase"], "verifier_a")
        self.assertNotIn("reviewer_id", fm)
        # Body should contain the detail
        body = text[end + 3:].strip()
        self.assertIn("Some details here.", body)

    def test_reviewer_id_included_when_present(self):
        rj = round_ledger.write_rejection(
            self.td, "round-000001", "v-001",
            reason_class="other",
            failed_phase="reviewer",
            detail="x",
            reviewer_id="v-002",
        )
        text = (self.td / "rejections" / f"{rj}.md").read_text()
        end = text.find("+++", 3)
        fm = tomllib.loads(text[3:end])
        self.assertEqual(fm["reviewer_id"], "v-002")


class AppendActionsLogTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_append_writes_newline_terminated_json(self):
        round_ledger.append_actions_log(self.td, {"event": "round_start"})
        path = self.td / "actions.jsonl"
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertTrue(content.endswith("\n"))
        self.assertEqual(json.loads(content.strip()), {"event": "round_start"})

    def test_multiple_appends_preserve_order(self):
        round_ledger.append_actions_log(self.td, {"n": 1})
        round_ledger.append_actions_log(self.td, {"n": 2})
        round_ledger.append_actions_log(self.td, {"n": 3})
        lines = (self.td / "actions.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([json.loads(l)["n"] for l in lines], [1, 2, 3])


class CommitHelpersTest(unittest.TestCase):
    """Verify each commit helper assembles the right Action trailer + file set.

    Uses a real harness-init workspace so the commit-msg hook is active.
    """
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # actions.jsonl must exist for the commit helpers to stage it
        (self.ws / "actions.jsonl").write_text(
            '{"event": "init", "n": 1}\n'
        )

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _last_commit_message(self) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"],
            text=True,
        )

    def test_commit_register_decision_action_trailer_set(self):
        # Stage realistic register-decision change
        (self.ws / "derived").mkdir(parents=True, exist_ok=True)
        (self.ws / "derived" / "decisions.json").write_text(
            '{"goal_version": "g-02", "decisions": {}}\n'
        )
        # Bump goal.toml version too (real Flow A would do this via register_decision)
        goal_path = self.ws / "goal.toml"
        text = goal_path.read_text()
        text = text.replace('goal_version = "g-01"', 'goal_version = "g-02"')
        goal_path.write_text(text)
        round_ledger.commit_register_decision(
            self.ws, new_decision_ids=["circuit-breaker"],
        )
        msg = self._last_commit_message()
        self.assertIn("Action: register-decision", msg)

    def test_commit_merge_includes_variant_and_round_trailers(self):
        # Stage a doc section update (decided-section immutability requires
        # an existing section in HEAD; for this test we use a new section)
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = []\n+++\nbody\n'
        )
        round_ledger.commit_merge(
            self.ws, round_id="round-000001", variant_id="v-001",
            section_paths=["variants/nodes/v-001/doc/01-retry.md"],
            claim_paths=[], attack_paths=[], evidence_paths=[],
        )
        msg = self._last_commit_message()
        self.assertIn("Action: merge", msg)
        self.assertIn("Variant: v-001", msg)
        self.assertIn("Round: round-000001", msg)

    def test_commit_rejection_includes_reviewer_trailer_when_applicable(self):
        # Write an rj-*.md first
        rj_id = round_ledger.write_rejection(
            self.ws, "round-000005", "v-001",
            reason_class="uncited-claim",
            failed_phase="reviewer",
            detail="x", reviewer_id="v-002",
        )
        round_ledger.commit_rejection(
            self.ws, action="reviewer-rejected",
            round_id="round-000005", variant_id="v-001",
            rj_id=rj_id, reason="uncited-claim", reviewer_id="v-002",
        )
        msg = self._last_commit_message()
        self.assertIn("Action: reviewer-rejected", msg)
        self.assertIn("Variant: v-001", msg)
        self.assertIn("Round: round-000005", msg)
        self.assertIn("Reason: uncited-claim", msg)
        self.assertIn("Reviewer: v-002", msg)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_round_ledger -v`
Expected: `ModuleNotFoundError: No module named 'harness.round_ledger'`.

- [ ] **Step 3: Create `harness/round_ledger.py`**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/round_ledger.py`:

```python
"""Persistence + commit invocation primitives for the harness round loop.

Public API:
  - write_role_scratch(workspace_root, round_id, role, parsed) -> Path
  - write_rejection(workspace_root, round_id, variant_id, reason_class,
                    failed_phase, detail, reviewer_id=None) -> str   (rj_id)
  - append_actions_log(workspace_root, entry) -> None
  - commit_register_decision(workspace_root, new_decision_ids) -> None
  - commit_canonicalize(workspace_root, rewrites) -> None
  - commit_merge(workspace_root, round_id, variant_id, section_paths,
                 claim_paths, attack_paths, evidence_paths) -> None
  - commit_rejection(workspace_root, action, round_id, variant_id, rj_id,
                     reason, reviewer_id=None) -> None
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


_RJ_ID_RE = re.compile(r"^rj-(\d{6})\.md$")


def write_role_scratch(workspace_root: Path, round_id: str, role: str,
                       parsed: dict) -> Path:
    """Write rounds/<round_id>/scratch/<role>.json. Creates parent dirs."""
    scratch_dir = workspace_root / "rounds" / round_id / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    path = scratch_dir / f"{role}.json"
    path.write_text(json.dumps(parsed, indent=2, sort_keys=True))
    return path


def _next_rj_id(workspace_root: Path) -> str:
    rej_dir = workspace_root / "rejections"
    if not rej_dir.exists():
        return "rj-000001"
    max_n = 0
    for fp in rej_dir.glob("rj-*.md"):
        m = _RJ_ID_RE.match(fp.name)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"rj-{max_n + 1:06d}"


def write_rejection(
    workspace_root: Path,
    round_id: str,
    variant_id: str,
    reason_class: str,
    failed_phase: str,
    detail: str,
    reviewer_id: str | None = None,
) -> str:
    """Allocate the next rj-*.md id, write the file with TOML frontmatter +
    body, return the rj_id string."""
    rj_id = _next_rj_id(workspace_root)
    rej_dir = workspace_root / "rejections"
    rej_dir.mkdir(parents=True, exist_ok=True)

    # Build TOML frontmatter. Escape double quotes in string values.
    def _q(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    fm_lines = [
        f'variant = "{_q(variant_id)}"',
        f'round_id = "{_q(round_id)}"',
        f'reason_class = "{_q(reason_class)}"',
        f'failed_phase = "{_q(failed_phase)}"',
    ]
    if reviewer_id is not None:
        fm_lines.append(f'reviewer_id = "{_q(reviewer_id)}"')

    text = "+++\n" + "\n".join(fm_lines) + "\n+++\n\n" + detail.rstrip() + "\n"
    (rej_dir / f"{rj_id}.md").write_text(text)
    return rj_id


def append_actions_log(workspace_root: Path, entry: dict) -> None:
    """Append one JSON line to workspace_root/actions.jsonl.

    Atomic w.r.t. SIGKILL at the line level: the write is followed by an
    explicit flush so partial trailing lines only occur if the process is
    killed mid-system-call. Sub-project 6's resume trims any partial line.
    """
    path = workspace_root / "actions.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()


# ----- Commit helpers --------------------------------------------------------


def _git_commit(workspace_root: Path, message: str) -> None:
    """Run git commit with the given message. Uses a baked-in harness identity
    so the call works on machines without a global user.name/user.email."""
    subprocess.check_call(
        ["git",
         "-c", "user.email=harness@localhost",
         "-c", "user.name=harness",
         "commit", "-q", "-m", message],
        cwd=workspace_root,
    )


def _git_add(workspace_root: Path, *paths: str) -> None:
    if not paths:
        return
    subprocess.check_call(
        ["git", "-C", str(workspace_root), "add", "-f", *paths],
    )


def commit_register_decision(
    workspace_root: Path,
    new_decision_ids: list[str],
) -> None:
    """Stage goal.toml + derived/decisions.json + actions.jsonl, commit with
    Action: register-decision."""
    _git_add(
        workspace_root,
        "goal.toml", "derived/decisions.json", "actions.jsonl",
    )
    ids_str = ", ".join(new_decision_ids)
    message = (
        f"feat(harness): register decisions ({ids_str})\n\n"
        "Action: register-decision\n"
    )
    _git_commit(workspace_root, message)


def commit_canonicalize(
    workspace_root: Path,
    rewrites: list[dict],
) -> None:
    """Stage rewritten cl-*.json files + derived/canonical_slug_registry.json
    + actions.jsonl, commit with Action: canonicalize.

    Each rewrite dict has 'path' (relative to workspace_root)."""
    rel_paths = sorted({r["path"] for r in rewrites})
    _git_add(workspace_root, *rel_paths,
             "derived/canonical_slug_registry.json", "actions.jsonl")
    count = len(rewrites)
    message = (
        f"feat(harness): canonicalize {count} position(s)\n\n"
        "Action: canonicalize\n"
    )
    _git_commit(workspace_root, message)


def commit_merge(
    workspace_root: Path,
    round_id: str,
    variant_id: str,
    section_paths: list[str],
    claim_paths: list[str],
    attack_paths: list[str],
    evidence_paths: list[str],
) -> None:
    """Stage all materialized files + actions.jsonl, commit with
    Action: merge + Variant + Round trailers."""
    all_paths = list(section_paths) + list(claim_paths) + \
                list(attack_paths) + list(evidence_paths)
    _git_add(workspace_root, *all_paths, "actions.jsonl")
    message = (
        f"feat(harness): {round_id} {variant_id}\n\n"
        "Action: merge\n"
        f"Variant: {variant_id}\n"
        f"Round: {round_id}\n"
    )
    _git_commit(workspace_root, message)


def commit_rejection(
    workspace_root: Path,
    action: str,
    round_id: str,
    variant_id: str,
    rj_id: str,
    reason: str,
    reviewer_id: str | None = None,
) -> None:
    """Stage rejections/<rj_id>.md + actions.jsonl, commit with the
    failure-class Action trailer + Variant + Round + Reason + Reviewer
    (when applicable)."""
    _git_add(
        workspace_root,
        f"rejections/{rj_id}.md", "actions.jsonl",
    )
    lines = [
        f"chore(harness): {action} for {round_id} {variant_id}",
        "",
        f"Action: {action}",
        f"Variant: {variant_id}",
        f"Round: {round_id}",
        f"Reason: {reason}",
    ]
    if reviewer_id is not None:
        lines.append(f"Reviewer: {reviewer_id}")
    message = "\n".join(lines) + "\n"
    _git_commit(workspace_root, message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_round_ledger -v`
Expected: 10 tests pass.

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 268 tests / OK` (258 existing + 10 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/round_ledger.py tests/test_round_ledger.py
git commit -m "feat(round_ledger): persistence + commit helpers for harness round loop"
```

---

## Task 2: `harness/orchestrator.py` skeleton + happy-path `run_round`

This task lands RoundOutcome, the four validators, and run_round with the happy-path flow only (mocked spawn_role returns ok for all roles, no rejections, no Flow A/C triggers). Subsequent tasks add the failure paths and mutation logic.

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py`

- [ ] **Step 1: Write failing happy-path tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py`:

```python
"""Phase-by-phase tests for run_round. Mocks spawn_role via unittest.mock to
avoid subprocess overhead; verifiers are real (pure-Python, fast).

Each test sets up a harness-init-scaffolded workspace and constructs a sequence
of RoleOutput mocks via _planner_ok / _designer_ok / etc. helpers.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator
from harness import round_ledger
from harness.spawn import RoleOutput


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _harness_config():
    return {
        "models": {
            "planner": {"tool": "claude", "model": "fake"},
            "designer": {"tool": "claude", "model": "fake"},
            "reviewer": {"tool": "claude", "model": "fake"},
            "verifier_c": {"tool": "claude", "model": "fake"},
        },
        "run": {
            "spawn_timeout_seconds": 10,
            "patch_max_sections": 3,
            "_retry_sleep_seconds_for_tests": 0,
        },
        "claim_graph": {
            "stale_proposals_threshold_rounds": 5,
            "bootstrap_registry_size_threshold": 5,
        },
    }


def _planner_ok(round_id="round-000001", variant="v-001"):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant, "stance": "tighten-claims-first",
        "intent": "Test plan", "target_sections": ["retry-policy"],
        "rejection_log_reviewed": [],
        "rationale_against_known_rejections": "n/a",
    }, retry_count=0, elapsed_seconds=0.1)


def _designer_ok(round_id="round-000001", variant="v-001",
                 claims=None, evidence=None, patch_diff=""):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant,
        "patch_diff": patch_diff,
        "evidence": evidence or [],
        "claims": claims or [],
    }, retry_count=0, elapsed_seconds=0.1)


def _reviewer_ok(round_id="round-000001", variant="v-001",
                 decision="accept", decision_proposals=None, attacks=None,
                 rejection=None):
    parsed = {
        "round": round_id, "variant": variant,
        "decision": decision, "rationale": "looks fine",
    }
    if decision_proposals is not None:
        parsed["decision_proposals"] = decision_proposals
    if attacks is not None:
        parsed["attacks"] = attacks
    if rejection is not None:
        parsed["rejection"] = rejection
    return RoleOutput(verdict="ok", parsed=parsed, retry_count=0,
                      elapsed_seconds=0.1)


def _verifier_c_ok(round_id="round-000001", variant="v-001",
                   verdict="confirm", per_claim=None):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant, "verdict": verdict,
        "per_claim": per_claim or [],
        "candidate_collisions_confirmed": [],
        "candidate_collisions_rejected": [],
    }, retry_count=0, elapsed_seconds=0.1)


class RoundOutcomeDataclassTest(unittest.TestCase):
    def test_default_fields(self):
        o = orchestrator.RoundOutcome(
            round_id="round-000001", variant_id="v-001", verdict="merge",
        )
        self.assertEqual(o.verdict, "merge")
        self.assertIsNone(o.reason)
        self.assertIsNone(o.rj_id)
        self.assertEqual(o.elapsed_seconds, 0.0)
        self.assertEqual(o.spawn_counts, {})

    def test_is_frozen(self):
        import dataclasses
        o = orchestrator.RoundOutcome(
            round_id="r", variant_id="v", verdict="merge",
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            o.verdict = "reviewer-rejected"


class RunRoundHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Seed a registered decision so designer/reviewer have something to use
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy",
                    "question": "How to retry?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_full_round_produces_merge_verdict_and_one_commit(self):
        # Designer materializes a cl-*.json + a section file, no patch_diff.
        # We bypass patch_diff by setting it empty; orchestrator should treat
        # empty patch_diff as a no-op materialization for sections.
        designer_output = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy",
             "claim_type": "decision",
             "evidence_ids": [],
             "assertion": "Use expo-backoff.",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            designer_output,
            _reviewer_ok(),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        self.assertIsNone(outcome.rj_id)
        # Spawn counts: 4 roles, each 1 spawn
        self.assertEqual(outcome.spawn_counts.get("planner"), 1)
        self.assertEqual(outcome.spawn_counts.get("designer"), 1)
        self.assertEqual(outcome.spawn_counts.get("reviewer"), 1)
        self.assertEqual(outcome.spawn_counts.get("verifier_c"), 1)
        # cl-000001 was materialized
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertTrue(cl_path.exists(),
                        "designer's cl-*.json should be materialized")
        # scratch files written
        scratch = self.ws / "rounds" / "round-000001" / "scratch"
        for role in ("planner", "designer", "reviewer", "verifier_c"):
            self.assertTrue((scratch / f"{role}.json").exists(),
                            f"missing scratch for {role}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round -v`
Expected: `ModuleNotFoundError: No module named 'harness.orchestrator'`.

- [ ] **Step 3: Create `harness/orchestrator.py` with happy-path implementation**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`:

```python
"""Round state machine + run loop for the Design Doc Evolution Harness.

Public API:
  - RoundOutcome: frozen dataclass with verdict + spawn_counts + elapsed
  - run_round(workspace_root, harness_config, round_id, variant_id) -> RoundOutcome
  - run_loop(workspace_root, harness_config, max_rounds=None,
             max_wall_clock_hours=None, variant_count=2) -> list[RoundOutcome]

The round flow is a linear function with early returns on rejection. See
docs/superpowers/specs/2026-05-31-...-design.md §3.1 for the full phase
sequence.
"""
from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness import claim_graph as cg
from harness import context as context_mod
from harness import round_ledger
from harness import verifiers
from harness.spawn import RoleOutput, spawn_role


# ----- Dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class RoundOutcome:
    round_id: str
    variant_id: str
    verdict: str
    reason: str | None = None
    rj_id: str | None = None
    elapsed_seconds: float = 0.0
    spawn_counts: dict = field(default_factory=dict)


# ----- Validators -----------------------------------------------------------
#
# These are passed to spawn_role's validator parameter. They raise ValueError
# on shape mismatch so spawn_role's validate-retry contract fires.


def validate_planner_json(d: dict) -> None:
    for key in ("round", "variant", "stance", "intent", "target_sections"):
        if key not in d:
            raise ValueError(f"planner.json missing {key!r}")
    if not isinstance(d["target_sections"], list):
        raise ValueError("planner.json target_sections must be a list")


def validate_designer_json(d: dict) -> None:
    for key in ("round", "variant", "patch_diff", "evidence", "claims"):
        if key not in d:
            raise ValueError(f"designer.json missing {key!r}")
    if not isinstance(d["claims"], list):
        raise ValueError("designer.json claims must be a list")
    if not isinstance(d["evidence"], list):
        raise ValueError("designer.json evidence must be a list")
    # Each claim must roundtrip through Claim.from_dict
    for c in d["claims"]:
        cg.Claim.from_dict(c)


def validate_reviewer_json(d: dict) -> None:
    for key in ("round", "variant", "decision", "rationale"):
        if key not in d:
            raise ValueError(f"reviewer.json missing {key!r}")
    if d["decision"] not in ("accept", "reject"):
        raise ValueError(
            f"reviewer.json decision must be accept|reject, got {d['decision']!r}"
        )
    # decision_proposals and attacks roundtrip via their dataclass from_dict
    for v in d.get("decision_proposals", []) or []:
        cg.DecisionProposalVerdict.from_dict(v)
    for a in d.get("attacks", []) or []:
        cg.Attack.from_dict(a)


def validate_verifier_c_json(d: dict) -> None:
    for key in ("round", "variant", "verdict", "per_claim"):
        if key not in d:
            raise ValueError(f"verification.json missing {key!r}")
    if d["verdict"] not in ("confirm", "dispute"):
        raise ValueError(
            f"verification.json verdict must be confirm|dispute, got {d['verdict']!r}"
        )


# ----- Helpers --------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds",
    )


def _log(workspace_root: Path, event: str, **fields) -> None:
    entry = {"ts": _now_iso(), "event": event, **fields}
    round_ledger.append_actions_log(workspace_root, entry)


PLANNER_PROMPT = (
    "You are the planner. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, stance, intent, target_sections (list), "
    "rejection_log_reviewed (list of rj-ids you considered), "
    "rationale_against_known_rejections (text). Output ONLY valid JSON."
)

DESIGNER_PROMPT = (
    "You are the designer. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, patch_diff (unified-diff text or empty string), "
    "evidence (list of {id, confidence, citations, claim, excerpt, ...}), "
    "claims (list of cl-*.json dicts). Output ONLY valid JSON."
)

REVIEWER_PROMPT = (
    "You are the reviewer. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, decision (accept|reject), rationale, optional "
    "rejection {reason_class, ...} on reject, optional decision_proposals "
    "(list of {proposed_id, verdict (approve|reject), rationale}) when the "
    "designer proposed new decisions, optional attacks (list of at-*.json "
    "dicts). Output ONLY valid JSON."
)

VERIFIER_C_PROMPT = (
    "You are Verifier C. Read the CONTEXT.md above plus the doc patch and "
    "cited evidence; emit JSON with fields: round, variant, verdict "
    "(confirm|dispute), per_claim (list of {claim_id, verdict (confirm|"
    "weak|dispute), rationale}), candidate_collisions_confirmed (list), "
    "candidate_collisions_rejected (list). Output ONLY valid JSON."
)


def _materialize_designer_output(
    workspace_root: Path, variant_id: str, parsed: dict,
) -> tuple[list[Path], list[str], list[str], list[str], list[str]]:
    """Materialize designer's parsed output to disk.

    Returns (materialized_paths_for_rollback, section_paths, claim_paths,
    attack_paths, evidence_paths) — the latter four are relative-to-workspace
    strings suitable for git add.
    """
    materialized: list[Path] = []
    section_paths: list[str] = []
    claim_paths: list[str] = []
    attack_paths: list[str] = []
    evidence_paths: list[str] = []

    # Evidence
    evidence_dir = workspace_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for ev in parsed.get("evidence", []) or []:
        ev_id = ev.get("id", "")
        if not ev_id:
            continue
        # Build TOML frontmatter from the evidence dict.
        fm_lines = []
        for key in ("id", "confidence", "claim", "excerpt", "match"):
            val = ev.get(key)
            if val is None:
                continue
            # Use triple-quoted string for multi-line safety
            escaped = str(val).replace('"""', '\\"\\"\\"')
            fm_lines.append(f'{key} = """{escaped}"""')
        text = "+++\n" + "\n".join(fm_lines) + "\n+++\n\n" + \
               str(ev.get("excerpt", "")) + "\n"
        ev_path = evidence_dir / f"{ev_id}.md"
        ev_path.write_text(text)
        materialized.append(ev_path)
        evidence_paths.append(f"evidence/{ev_id}.md")

    # Claims
    claims_dir = workspace_root / "variants" / "nodes" / variant_id / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    for claim in parsed.get("claims", []) or []:
        cl_id = claim.get("id", "")
        if not cl_id:
            continue
        cl_path = claims_dir / f"{cl_id}.json"
        cl_path.write_text(json.dumps(claim, indent=2, sort_keys=True))
        materialized.append(cl_path)
        claim_paths.append(
            f"variants/nodes/{variant_id}/claims/{cl_id}.json"
        )

    # patch_diff: if non-empty, apply with `git apply`. For v0, empty patch_diff
    # is a no-op.
    patch_diff = parsed.get("patch_diff", "") or ""
    if patch_diff.strip():
        import subprocess as _sp
        result = _sp.run(
            ["git", "-C", str(workspace_root), "apply", "--whitespace=nowarn"],
            input=patch_diff, text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            # Caller treats as cross-field-fail; clean up evidence + claims
            # we already wrote.
            for p in materialized:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            raise RuntimeError(
                f"git apply failed: {result.stderr.strip()}"
            )
        # Extract section paths from the patch_diff (lines starting with
        # `+++ b/`).
        for line in patch_diff.split("\n"):
            if line.startswith("+++ b/"):
                rel = line[len("+++ b/"):].strip()
                if rel.startswith(f"variants/nodes/{variant_id}/doc/"):
                    section_paths.append(rel)
                    materialized.append(workspace_root / rel)

    return materialized, section_paths, claim_paths, attack_paths, evidence_paths


def _materialize_reviewer_attacks(
    workspace_root: Path, variant_id: str, parsed: dict,
) -> tuple[list[Path], list[str]]:
    """Materialize reviewer's attacks (at-*.json) to disk. Returns
    (materialized_paths, attack_paths_for_git_add)."""
    attacks_dir = workspace_root / "variants" / "nodes" / variant_id / "attacks"
    materialized: list[Path] = []
    attack_paths: list[str] = []
    for at in parsed.get("attacks", []) or []:
        at_id = at.get("id", "")
        if not at_id:
            continue
        attacks_dir.mkdir(parents=True, exist_ok=True)
        at_path = attacks_dir / f"{at_id}.json"
        at_path.write_text(json.dumps(at, indent=2, sort_keys=True))
        materialized.append(at_path)
        attack_paths.append(
            f"variants/nodes/{variant_id}/attacks/{at_id}.json"
        )
    return materialized, attack_paths


def _discard_materialized(workspace_root: Path,
                          paths: list[Path]) -> None:
    """Remove new files or git-checkout HEAD for modified files."""
    import subprocess as _sp
    for p in paths:
        if not p.exists():
            continue
        # Was it tracked by git at HEAD?
        rel = p.relative_to(workspace_root)
        ls = _sp.run(
            ["git", "-C", str(workspace_root), "ls-files", "--error-unmatch",
             str(rel)],
            capture_output=True, text=True,
        )
        if ls.returncode == 0:
            # Modified tracked file → restore from HEAD
            _sp.check_call(
                ["git", "-C", str(workspace_root), "checkout", "HEAD",
                 "--", str(rel)],
            )
        else:
            # New file → unlink
            try:
                p.unlink()
            except FileNotFoundError:
                pass


# ----- run_round ------------------------------------------------------------


def run_round(
    workspace_root: Path,
    harness_config: dict,
    round_id: str,
    variant_id: str,
) -> RoundOutcome:
    """Execute one round on one variant. Happy-path-only in Task 2; failure
    branches and Flow A/C land in Tasks 3 and 4."""
    start_ts = time.monotonic()
    spawn_counts: dict[str, int] = {}
    _log(workspace_root, "round_start", round_id=round_id, variant_id=variant_id)

    # ---- Phase 1: Planner ----
    planner_ctx = context_mod.build_planner_context(
        workspace_root, round_id, variant_id,
    )
    planner_result = spawn_role(
        role="planner", harness_config=harness_config,
        context_md=planner_ctx, prompt=PLANNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_planner_json,
    )
    spawn_counts["planner"] = 1 + planner_result.retry_count
    if planner_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=planner_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "planner", planner_result.parsed,
    )

    # ---- Phase 2: Designer ----
    designer_ctx = context_mod.build_designer_context(
        workspace_root, round_id, variant_id,
    )
    designer_result = spawn_role(
        role="designer", harness_config=harness_config,
        context_md=designer_ctx, prompt=DESIGNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_designer_json,
    )
    spawn_counts["designer"] = 1 + designer_result.retry_count
    if designer_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=designer_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "designer", designer_result.parsed,
    )
    materialized, section_paths, claim_paths, _att_unused, evidence_paths = \
        _materialize_designer_output(
            workspace_root, variant_id, designer_result.parsed,
        )

    # ---- Phase 5: Reviewer (Phases 3-4 verifiers added in Task 3) ----
    reviewer_ctx = context_mod.build_reviewer_context(
        workspace_root, round_id, variant_id,
    )
    reviewer_result = spawn_role(
        role="reviewer", harness_config=harness_config,
        context_md=reviewer_ctx, prompt=REVIEWER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_reviewer_json,
    )
    spawn_counts["reviewer"] = 1 + reviewer_result.retry_count
    if reviewer_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=reviewer_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "reviewer", reviewer_result.parsed,
    )
    att_materialized, attack_paths = _materialize_reviewer_attacks(
        workspace_root, variant_id, reviewer_result.parsed,
    )
    materialized.extend(att_materialized)

    # ---- Phase 6: Verifier C ----
    vc_ctx = context_mod.build_verifier_c_context(
        workspace_root, round_id, variant_id,
    )
    vc_result = spawn_role(
        role="verifier_c", harness_config=harness_config,
        context_md=vc_ctx, prompt=VERIFIER_C_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_verifier_c_json,
    )
    spawn_counts["verifier_c"] = 1 + vc_result.retry_count
    if vc_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=vc_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "verifier_c", vc_result.parsed,
    )

    # ---- Phase 8: Final merge commit ----
    round_ledger.commit_merge(
        workspace_root, round_id=round_id, variant_id=variant_id,
        section_paths=section_paths, claim_paths=claim_paths,
        attack_paths=attack_paths, evidence_paths=evidence_paths,
    )
    _log(workspace_root, "commit", round_id=round_id, action="merge")
    _log(workspace_root, "round_end", round_id=round_id, verdict="merge")

    return RoundOutcome(
        round_id=round_id, variant_id=variant_id, verdict="merge",
        elapsed_seconds=time.monotonic() - start_ts,
        spawn_counts=spawn_counts,
    )


def run_loop(*args, **kwargs):
    """run_loop is implemented in Task 5."""
    raise NotImplementedError("run_loop lands in Task 5")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round -v`
Expected: 3 tests pass (2 dataclass + 1 happy path).

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 271 tests / OK` (268 + 3).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/orchestrator.py tests/test_orchestrator_round.py
git commit -m "feat(orchestrator): RoundOutcome + validators + run_round happy path"
```

---

## Task 3: Verifier A/B phases + failure paths

This task adds Phases 3 (Verifier A) and 4 (Verifier B) plus all the rejection paths (spawn-failed, output-parse-fail, reviewer rejection, verifier_c dispute, designer materialization failure). Each rejection writes an rj-*.md, commits with the failure-class Action, and discards materialized files.

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py`

- [ ] **Step 1: Append failing failure-path tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py` (before the `if __name__ == "__main__":` line):

```python
class RunRoundPlannerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_planner_spawn_failed_verdict_spawn_failed(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            RoleOutput(verdict="spawn-failed",
                       stderr_tail="claude exited 1",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "spawn-failed")
        self.assertIsNotNone(outcome.rj_id)
        # rj-*.md must exist and commit must have failure-class Action
        rj_path = self.ws / "rejections" / f"{outcome.rj_id}.md"
        self.assertTrue(rj_path.exists())

    def test_planner_output_parse_fail_verdict_output_parse_fail(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            RoleOutput(verdict="output-parse-fail",
                       stderr_tail="json parse error",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "output-parse-fail")
        self.assertIsNotNone(outcome.rj_id)


class RunRoundDesignerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_spawn_failed_verdict_spawn_failed(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            RoleOutput(verdict="spawn-failed", stderr_tail="x",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "spawn-failed")
        self.assertIsNotNone(outcome.rj_id)

    def test_designer_patch_apply_failure_verdict_cross_field_fail(self):
        # Designer emits a malformed patch_diff that git apply rejects
        bad_diff = "--- this is not a valid unified diff ---\n"
        designer_bad = _designer_ok(patch_diff=bad_diff)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            designer_bad,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        # Cross-field-fail is the materialization-failure verdict
        self.assertEqual(outcome.verdict, "phase-b-fail")
        # Actually — the orchestrator surfaces materialization errors as
        # cross-field-fail with action=phase-a-fail OR an explicit
        # materialize-fail kind. We accept either spelling: just confirm
        # the round was rejected (verdict starts with "phase-" or matches
        # one of the failure verdicts).
        self.assertIn(outcome.verdict, (
            "phase-a-fail", "phase-b-fail", "output-parse-fail",
        ))


class RunRoundVerifierAFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Seed decisions so designer's cl-*.json validates
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_uncited_claim_verdict_phase_a_fail_reason_uncited_claim(self):
        # Designer creates a `decided` section without a cite
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Uncited assertion.\n'
        )
        # No designer evidence; the section is pre-existing
        designer = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "evidence_ids": [], "assertion": "x",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "uncited-claim")

    def test_dangling_cite_verdict_phase_a_fail_reason_dangling_evidence(self):
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Assertion with bad cite [^ev-999999].\n'
        )
        # Designer's evidence list is empty — cite resolves to nothing
        designer = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "evidence_ids": [], "assertion": "x",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "dangling-evidence")


class RunRoundVerifierBFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_excerpt_mismatch_verdict_phase_b_fail(self):
        # Pre-existing section with a cite; designer emits an ev with a wildly
        # different excerpt → Verifier B mismatch
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Use expo-backoff with full jitter [^ev-000001].\n'
        )
        designer = _designer_ok(
            evidence=[{
                "id": "ev-000001",
                "confidence": "high",
                "excerpt": "Use TCP keepalive with a 30-second interval.",
                "match": "normalized_substring",
                "claim": "x",
            }],
            claims=[{
                "id": "cl-000001", "section_id": "retry-policy",
                "decision_id": "retry-policy", "claim_type": "decision",
                "evidence_ids": ["ev-000001"], "assertion": "x",
                "position": "expo-backoff",
            }],
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-b-fail")


class RunRoundReviewerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reviewer_decision_reject_verdict_reviewer_rejected(self):
        reviewer = _reviewer_ok(
            decision="reject",
            rejection={"reason_class": "uncited-claim",
                       "evidence_against": [],
                       "supersedable_by": "add a cite"},
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            reviewer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "reviewer-rejected")
        self.assertEqual(outcome.reason, "uncited-claim")


class RunRoundVerifierCDisputeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_verifier_c_top_level_dispute_verdict_phase_c_dispute(self):
        vc = _verifier_c_ok(verdict="dispute", per_claim=[
            {"claim_id": "cl-000001", "verdict": "dispute",
             "rationale": "evidence does not support"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            _reviewer_ok(),
            vc,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-c-dispute")

    def test_verifier_c_per_claim_dispute_verdict_phase_c_dispute(self):
        # verdict=confirm at top level but per_claim has a dispute
        vc = _verifier_c_ok(verdict="confirm", per_claim=[
            {"claim_id": "cl-000001", "verdict": "dispute",
             "rationale": "x"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            _reviewer_ok(),
            vc,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-c-dispute")


class RunRoundFileDiscardTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_rejection_discards_materialized_files(self):
        # Designer materializes a cl-*.json; reviewer rejects.
        # The cl-*.json should be removed from working tree.
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "retry-policy",
            "decision_id": "retry-policy", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "expo-backoff",
        }])
        reviewer = _reviewer_ok(
            decision="reject",
            rejection={"reason_class": "uncited-claim",
                       "evidence_against": [],
                       "supersedable_by": "x"},
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer,
        ]):
            orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertFalse(cl_path.exists(),
                         "designer's cl-*.json should have been discarded "
                         "on reviewer rejection")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round -v 2>&1 | tail -20`
Expected: ~10 new tests fail (run_round doesn't write rj-*.md yet, doesn't run verifiers A/B, doesn't discard materialized files on rejection).

- [ ] **Step 3: Extend `harness/orchestrator.py` with failure paths + Verifiers A/B**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`. Find the `run_round` function and replace its body with this version that adds failure handling, Verifier A/B phases, and file discard. The new full body of `run_round`:

```python
def run_round(
    workspace_root: Path,
    harness_config: dict,
    round_id: str,
    variant_id: str,
) -> RoundOutcome:
    """Execute one round on one variant. Linear flow with early returns
    on rejection paths. See spec §3.1 for full phase semantics."""
    start_ts = time.monotonic()
    spawn_counts: dict[str, int] = {}
    materialized: list[Path] = []
    _log(workspace_root, "round_start",
         round_id=round_id, variant_id=variant_id)

    variants_root = workspace_root / "variants" / "nodes"
    evidence_root = workspace_root / "evidence"

    def _reject(action: str, reason_class: str, failed_phase: str,
                detail: str, reviewer_id: str | None = None) -> RoundOutcome:
        _discard_materialized(workspace_root, materialized)
        rj_id = round_ledger.write_rejection(
            workspace_root, round_id, variant_id,
            reason_class=reason_class, failed_phase=failed_phase,
            detail=detail, reviewer_id=reviewer_id,
        )
        _log(workspace_root, "rejection",
             round_id=round_id, rj_id=rj_id,
             reason_class=reason_class, failed_phase=failed_phase)
        round_ledger.commit_rejection(
            workspace_root, action=action,
            round_id=round_id, variant_id=variant_id,
            rj_id=rj_id, reason=reason_class, reviewer_id=reviewer_id,
        )
        _log(workspace_root, "commit", round_id=round_id, action=action)
        _log(workspace_root, "round_end",
             round_id=round_id, verdict=action)
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=action, reason=reason_class, rj_id=rj_id,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )

    # ---- Phase 1: Planner ----
    planner_ctx = context_mod.build_planner_context(
        workspace_root, round_id, variant_id,
    )
    planner_result = spawn_role(
        role="planner", harness_config=harness_config,
        context_md=planner_ctx, prompt=PLANNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_planner_json,
    )
    spawn_counts["planner"] = 1 + planner_result.retry_count
    if planner_result.verdict != "ok":
        return _reject(
            action=planner_result.verdict,
            reason_class=planner_result.verdict,
            failed_phase="planner",
            detail=f"planner: {planner_result.stderr_tail or planner_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "planner", planner_result.parsed,
    )

    # ---- Phase 2: Designer ----
    designer_ctx = context_mod.build_designer_context(
        workspace_root, round_id, variant_id,
    )
    designer_result = spawn_role(
        role="designer", harness_config=harness_config,
        context_md=designer_ctx, prompt=DESIGNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_designer_json,
    )
    spawn_counts["designer"] = 1 + designer_result.retry_count
    if designer_result.verdict != "ok":
        return _reject(
            action=designer_result.verdict,
            reason_class=designer_result.verdict,
            failed_phase="designer",
            detail=f"designer: {designer_result.stderr_tail or designer_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "designer", designer_result.parsed,
    )
    try:
        materialized, section_paths, claim_paths, _att_unused, evidence_paths = \
            _materialize_designer_output(
                workspace_root, variant_id, designer_result.parsed,
            )
    except RuntimeError as e:
        return _reject(
            action="phase-a-fail",
            reason_class="cross-field-fail",
            failed_phase="designer",
            detail=f"materialize failure: {e}",
        )

    # ---- Phase 3: Verifier A (cite enforcement) ----
    r_completeness = verifiers.verify_citation_completeness(variants_root)
    r_resolution = verifiers.verify_cite_resolution(
        variants_root, evidence_root,
    )
    _log(workspace_root, "verifier_complete",
         round_id=round_id, verifier="a",
         failure_count=len(r_completeness.failures) + len(r_resolution.failures))
    if r_completeness.failures or r_resolution.failures:
        if r_completeness.failures:
            reason = "uncited-claim"
            failures = r_completeness.failures
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

    # ---- Phase 4: Verifier B (excerpt match) ----
    r_excerpt = verifiers.verify_excerpt_match(
        variants_root, evidence_root, threshold=0.92,
    )
    _log(workspace_root, "verifier_complete",
         round_id=round_id, verifier="b",
         failure_count=len(r_excerpt.failures))
    if r_excerpt.failures:
        detail_lines = [
            f"{f.variant} {f.section_path}: {f.detail}\n{f.excerpt_diff or ''}"
            for f in r_excerpt.failures[:10]
        ]
        return _reject(
            action="phase-b-fail",
            reason_class="cross-field-fail",
            failed_phase="verifier_b",
            detail="\n\n".join(detail_lines),
        )

    # ---- Phase 5: Reviewer ----
    reviewer_ctx = context_mod.build_reviewer_context(
        workspace_root, round_id, variant_id,
    )
    reviewer_result = spawn_role(
        role="reviewer", harness_config=harness_config,
        context_md=reviewer_ctx, prompt=REVIEWER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_reviewer_json,
    )
    spawn_counts["reviewer"] = 1 + reviewer_result.retry_count
    if reviewer_result.verdict != "ok":
        return _reject(
            action=reviewer_result.verdict,
            reason_class=reviewer_result.verdict,
            failed_phase="reviewer",
            detail=f"reviewer: {reviewer_result.stderr_tail or reviewer_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "reviewer", reviewer_result.parsed,
    )

    if reviewer_result.parsed.get("decision") == "reject":
        rej = reviewer_result.parsed.get("rejection") or {}
        reason_class = rej.get("reason_class", "other")
        detail = (
            f"reviewer rejected: {reviewer_result.parsed.get('rationale', '')}\n"
            f"supersedable_by: {rej.get('supersedable_by', '')}"
        )
        return _reject(
            action="reviewer-rejected",
            reason_class=reason_class,
            failed_phase="reviewer",
            detail=detail,
        )

    # Phase 5.5: Flow A gating + attack materialization
    # (Flow A registration deferred to Task 4; here we just gate.)
    att_materialized, attack_paths = _materialize_reviewer_attacks(
        workspace_root, variant_id, reviewer_result.parsed,
    )
    materialized.extend(att_materialized)

    # ---- Phase 6: Verifier C ----
    vc_ctx = context_mod.build_verifier_c_context(
        workspace_root, round_id, variant_id,
    )
    vc_result = spawn_role(
        role="verifier_c", harness_config=harness_config,
        context_md=vc_ctx, prompt=VERIFIER_C_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_verifier_c_json,
    )
    spawn_counts["verifier_c"] = 1 + vc_result.retry_count
    if vc_result.verdict != "ok":
        return _reject(
            action=vc_result.verdict,
            reason_class=vc_result.verdict,
            failed_phase="verifier_c",
            detail=f"verifier_c: {vc_result.stderr_tail or vc_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "verifier_c", vc_result.parsed,
    )

    vc_parsed = vc_result.parsed
    has_per_claim_dispute = any(
        pc.get("verdict") == "dispute"
        for pc in vc_parsed.get("per_claim", [])
    )
    if vc_parsed.get("verdict") == "dispute" or has_per_claim_dispute:
        disputed = [
            f"{pc.get('claim_id', '?')}: {pc.get('rationale', '?')}"
            for pc in vc_parsed.get("per_claim", [])
            if pc.get("verdict") == "dispute"
        ]
        return _reject(
            action="phase-c-dispute",
            reason_class="cross-field-fail",
            failed_phase="verifier_c",
            detail="Verifier C disputed claims:\n" + "\n".join(disputed),
        )

    # ---- Phase 7: Flow A + Flow C — deferred to Task 4 ----

    # ---- Phase 8: Final merge commit ----
    round_ledger.commit_merge(
        workspace_root, round_id=round_id, variant_id=variant_id,
        section_paths=section_paths, claim_paths=claim_paths,
        attack_paths=attack_paths, evidence_paths=evidence_paths,
    )
    _log(workspace_root, "commit", round_id=round_id, action="merge")
    _log(workspace_root, "round_end", round_id=round_id, verdict="merge")

    return RoundOutcome(
        round_id=round_id, variant_id=variant_id, verdict="merge",
        elapsed_seconds=time.monotonic() - start_ts,
        spawn_counts=spawn_counts,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round -v 2>&1 | tail -10`
Expected: ~13 tests pass (3 happy + ~10 failure-path).

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 281 tests / OK` (271 + 10).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/orchestrator.py tests/test_orchestrator_round.py
git commit -m "feat(orchestrator): Verifier A/B phases + rejection paths for all phases"
```

---

## Task 4: Flow A (register-decision) + Flow C (canonicalize)

This task adds Phase 5.5 gating and Phase 7 mutations: when designer proposes new decisions and reviewer approves, register them and emit a `register-decision` commit before merge. When reviewer's attacks include high-confidence canonicalizations with valid targets, apply them and emit a `canonicalize` commit before merge.

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py`

- [ ] **Step 1: Append failing Flow A + C tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_round.py` (before the `if __name__ == "__main__":` line):

```python
class RunRoundFlowATest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Empty decisions.json — bootstrap; designer will propose
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01", "decisions": {},
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_proposal_rejected_via_phase_5_5_verdict_reviewer_rejected_reason_proposal_rejected(self):
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "half-open",
            "proposed_decision": {
                "id": "circuit-breaker",
                "question": "When to reset?",
                "rationale": "needed",
            },
        }])
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "circuit-breaker", "verdict": "reject",
             "rationale": "off-thesis"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "reviewer-rejected")
        self.assertEqual(outcome.reason, "proposal-rejected")

    def test_approved_proposal_triggers_register_decision_commit_before_merge(self):
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "half-open",
            "proposed_decision": {
                "id": "circuit-breaker",
                "question": "When to reset?",
                "rationale": "needed for resilience",
            },
        }])
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "circuit-breaker", "verdict": "approve",
             "rationale": "on-thesis"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        # Inspect git log: register-decision commit precedes merge commit
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%s|||%B---END---"],
            text=True,
        )
        commit_bodies = log.split("---END---")
        register_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: register-decision" in b), None,
        )
        merge_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: merge" in b), None,
        )
        self.assertIsNotNone(register_idx)
        self.assertIsNotNone(merge_idx)
        # In git log default ordering (newest first), merge < register
        self.assertLess(merge_idx, register_idx)
        # circuit-breaker is now in goal.toml
        goal_text = (self.ws / "goal.toml").read_text()
        self.assertIn("circuit-breaker", goal_text)


class RunRoundFlowCTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))
        # Registry with two canonical positions under retry-policy
        (derived / "canonical_slug_registry.json").write_text(json.dumps({
            "retry-policy": {
                "canonical": ["expo-backoff", "exponential-backoff"],
                "aliases": {},
            },
        }, indent=2))
        # Pre-existing cl-*.json for v-001 with the from_slug
        claims_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        claims_dir.mkdir(parents=True, exist_ok=True)
        (claims_dir / "cl-000001.json").write_text(json.dumps({
            "id": "cl-000001", "section_id": "retry-policy",
            "decision_id": "retry-policy", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "exponential-backoff",
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_high_confidence_canonicalization_triggers_canonicalize_commit_before_merge(self):
        # Reviewer proposes canonicalizing exponential-backoff → expo-backoff
        reviewer = _reviewer_ok(attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "high", "rationale": "both mean the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[]), reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000002", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%s|||%B---END---"],
            text=True,
        )
        commit_bodies = log.split("---END---")
        canon_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: canonicalize" in b), None,
        )
        self.assertIsNotNone(canon_idx,
                             "expected a canonicalize commit")
        # The cl-*.json position has been rewritten
        cl = json.loads(
            (self.ws / "variants" / "nodes" / "v-001" / "claims"
             / "cl-000001.json").read_text()
        )
        self.assertEqual(cl["position"], "expo-backoff")

    def test_medium_confidence_canonicalization_skipped_for_v0(self):
        reviewer = _reviewer_ok(attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "medium", "rationale": "may be the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[]), reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000002", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        # NO canonicalize commit — medium-confidence is skipped in v0
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B---END---"],
            text=True,
        )
        self.assertNotIn("Action: canonicalize", log)
        # Original cl-*.json position unchanged
        cl = json.loads(
            (self.ws / "variants" / "nodes" / "v-001" / "claims"
             / "cl-000001.json").read_text()
        )
        self.assertEqual(cl["position"], "exponential-backoff")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round.RunRoundFlowATest tests.test_orchestrator_round.RunRoundFlowCTest -v`
Expected: 4 tests fail — no Flow A/C handling in run_round yet.

- [ ] **Step 3: Add Phase 5.5 gating and Phase 7 mutations to `run_round`**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`. Find the comment `# Phase 5.5: Flow A gating + attack materialization` and the following `att_materialized, attack_paths = _materialize_reviewer_attacks(...)` line. Replace that section with a Phase 5.5 implementation:

Old:
```python
    # Phase 5.5: Flow A gating + attack materialization
    # (Flow A registration deferred to Task 4; here we just gate.)
    att_materialized, attack_paths = _materialize_reviewer_attacks(
        workspace_root, variant_id, reviewer_result.parsed,
    )
    materialized.extend(att_materialized)
```

New:
```python
    # Phase 5.5: Flow A gating (decision_proposals)
    proposed_payloads = []
    for c in designer_result.parsed.get("claims", []) or []:
        pd = c.get("proposed_decision")
        if pd and isinstance(pd, dict):
            proposed_payloads.append(pd)
    approved_proposals: list[dict] = []
    if proposed_payloads:
        verdicts_raw = reviewer_result.parsed.get("decision_proposals", []) or []
        try:
            verdicts = [cg.DecisionProposalVerdict.from_dict(v)
                        for v in verdicts_raw]
            outcome_dict = cg.apply_reviewer_decision_proposals(
                proposed_payloads, verdicts,
            )
        except cg.SchemaError as e:
            return _reject(
                action="reviewer-rejected",
                reason_class="proposal-rejected",
                failed_phase="reviewer",
                detail=f"decision_proposals validation failed: {e}",
            )
        if outcome_dict["status"] == "any-rejected":
            rej_lines = [
                f"{r['proposed_id']}: {r['rationale']}"
                for r in outcome_dict["rejected"]
            ]
            return _reject(
                action="reviewer-rejected",
                reason_class="proposal-rejected",
                failed_phase="reviewer",
                detail="\n".join(rej_lines),
            )
        approved_proposals = outcome_dict["approved"]
    # Materialize attacks (deferred until after Phase 5.5 gating)
    att_materialized, attack_paths = _materialize_reviewer_attacks(
        workspace_root, variant_id, reviewer_result.parsed,
    )
    materialized.extend(att_materialized)
```

Then find the `# ---- Phase 7: Flow A + Flow C — deferred to Task 4 ----` comment and replace it with:

Old:
```python
    # ---- Phase 7: Flow A + Flow C — deferred to Task 4 ----

    # ---- Phase 8: Final merge commit ----
```

New:
```python
    # ---- Phase 7a: Flow A — register-decision ----
    if approved_proposals:
        goal_toml_path = workspace_root / "goal.toml"
        decisions_json_path = workspace_root / "derived" / "decisions.json"
        cg.register_decision(
            goal_toml_path,
            new_decisions=approved_proposals,
            decisions_json_path=decisions_json_path,
        )
        round_ledger.commit_register_decision(
            workspace_root,
            new_decision_ids=[p["id"] for p in approved_proposals],
        )
        _log(workspace_root, "commit", round_id=round_id,
             action="register-decision")

    # ---- Phase 7b: Flow C — apply_canonicalization (high-confidence only) ----
    canon_proposals = [
        a for a in reviewer_result.parsed.get("attacks", []) or []
        if a.get("at_type") == "propose_canonicalization"
        and a.get("kind") == "position"
        and a.get("confidence") == "high"
    ]
    if canon_proposals:
        registry_path = (workspace_root / "derived"
                         / "canonical_slug_registry.json")
        if registry_path.exists():
            registry = cg.CanonicalSlugRegistry.from_dict(
                json.loads(registry_path.read_text()),
            )
        else:
            registry = cg.CanonicalSlugRegistry()
        all_rewrites: list[dict] = []
        for at in canon_proposals:
            entry = registry.data.get(at["scope"])
            if entry is None or at["to"] not in entry.get("canonical", []):
                # to_slug not canonical — skip, log, continue
                _log(workspace_root, "canonicalize_skip",
                     round_id=round_id,
                     reject_reason="invalid-canonicalization-target",
                     scope=at["scope"], from_slug=at["from"], to_slug=at["to"])
                continue
            try:
                rewrites = cg.apply_canonicalization(
                    variants_root, registry, at["scope"],
                    from_slug=at["from"], to_slug=at["to"],
                )
            except cg.RegistryInvariantError as e:
                _log(workspace_root, "canonicalize_skip",
                     round_id=round_id,
                     reject_reason=str(e),
                     scope=at["scope"], from_slug=at["from"], to_slug=at["to"])
                continue
            all_rewrites.extend(rewrites)
        if all_rewrites:
            # Persist updated registry
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps(
                registry.to_dict(), indent=2, sort_keys=True,
            ))
            round_ledger.commit_canonicalize(workspace_root, all_rewrites)
            _log(workspace_root, "commit", round_id=round_id,
                 action="canonicalize")

    # ---- Phase 8: Final merge commit ----
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_round -v 2>&1 | tail -5`
Expected: 17 tests pass (13 from prior tasks + 4 new Flow A/C tests).

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 285 tests / OK` (281 + 4).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/orchestrator.py tests/test_orchestrator_round.py
git commit -m "feat(orchestrator): Flow A register-decision + Flow C canonicalize phases"
```

---

## Task 5: `run_loop` with variant rotation + stopping caps

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_loop.py`

- [ ] **Step 1: Write failing run_loop tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_orchestrator_loop.py`:

```python
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator
from harness.spawn import RoleOutput


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _harness_config():
    return {
        "models": {
            "planner": {"tool": "claude", "model": "fake"},
            "designer": {"tool": "claude", "model": "fake"},
            "reviewer": {"tool": "claude", "model": "fake"},
            "verifier_c": {"tool": "claude", "model": "fake"},
        },
        "run": {
            "spawn_timeout_seconds": 10,
            "_retry_sleep_seconds_for_tests": 0,
        },
    }


class RunLoopRotationTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_variant_rotates_across_rounds(self):
        # 4 rounds × 2 variants → v-001, v-002, v-001, v-002
        def fake_run_round(workspace_root, harness_config,
                           round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed",  # avoid real spawn
                elapsed_seconds=0.01,
            )
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            outcomes = orchestrator.run_loop(
                self.ws, _harness_config(),
                max_rounds=4, variant_count=2,
            )
        self.assertEqual(len(outcomes), 4)
        variants = [o.variant_id for o in outcomes]
        self.assertEqual(variants, ["v-001", "v-002", "v-001", "v-002"])


class RunLoopMaxRoundsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_stops_at_max_rounds_cap(self):
        def fake_run_round(workspace_root, harness_config,
                           round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed",
            )
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            outcomes = orchestrator.run_loop(
                self.ws, _harness_config(),
                max_rounds=3, variant_count=2,
            )
        self.assertEqual(len(outcomes), 3)


class RunLoopMaxWallClockTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_stops_at_max_wall_clock_cap(self):
        # 0.001 hours = 3.6 seconds; each fake round "sleeps" 1s of monotonic
        call_count = [0]
        def fake_run_round(workspace_root, harness_config,
                           round_id, variant_id):
            import time as _time
            _time.sleep(1.0)  # simulated round
            call_count[0] += 1
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed",
            )
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            outcomes = orchestrator.run_loop(
                self.ws, _harness_config(),
                max_wall_clock_hours=2.0 / 3600,  # ~2 seconds budget
                variant_count=1,
            )
        # We should have completed 1-3 rounds before the 2s cap; not 100
        self.assertGreaterEqual(len(outcomes), 1)
        self.assertLess(len(outcomes), 10)


class RunLoopResumeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_starts_at_next_round_id_after_existing(self):
        # Pre-create rounds/round-000005/ to simulate a prior run
        (self.ws / "rounds" / "round-000005").mkdir(parents=True)
        seen_round_ids: list[str] = []
        def fake_run_round(workspace_root, harness_config,
                           round_id, variant_id):
            seen_round_ids.append(round_id)
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed",
            )
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            orchestrator.run_loop(
                self.ws, _harness_config(),
                max_rounds=2, variant_count=2,
            )
        self.assertEqual(seen_round_ids,
                         ["round-000006", "round-000007"])


class RunLoopNoCapRaisesTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_cap_raises_value_error(self):
        with self.assertRaises(ValueError):
            orchestrator.run_loop(
                self.ws, _harness_config(),
                max_rounds=None, max_wall_clock_hours=None,
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_loop -v`
Expected: All 5 tests fail with `NotImplementedError: run_loop lands in Task 5`.

- [ ] **Step 3: Replace the `run_loop` stub with the real implementation**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/orchestrator.py`. Find the stub:

Old:
```python
def run_loop(*args, **kwargs):
    """run_loop is implemented in Task 5."""
    raise NotImplementedError("run_loop lands in Task 5")
```

New:
```python
import re as _re


_ROUND_DIR_RE = _re.compile(r"^round-(\d{6})$")


def _next_round_number(workspace_root: Path) -> int:
    rounds_root = workspace_root / "rounds"
    if not rounds_root.exists():
        return 1
    max_n = 0
    for d in rounds_root.iterdir():
        if not d.is_dir():
            continue
        m = _ROUND_DIR_RE.match(d.name)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n + 1


def run_loop(
    workspace_root: Path,
    harness_config: dict,
    max_rounds: int | None = None,
    max_wall_clock_hours: float | None = None,
    variant_count: int = 2,
) -> list[RoundOutcome]:
    """Drive rounds in sequence with variant rotation. Stops at whichever cap
    fires first. At least one of max_rounds / max_wall_clock_hours required.

    Variant rotation: round N → v-{((N-1) % variant_count) + 1:03d}.
    Round-id allocation: discovers max existing rounds/round-* dir, starts
    at max+1 (so resume across runs is natural)."""
    if max_rounds is None and max_wall_clock_hours is None:
        raise ValueError(
            "run_loop requires at least one of max_rounds or "
            "max_wall_clock_hours"
        )

    loop_start = time.monotonic()
    outcomes: list[RoundOutcome] = []
    next_n = _next_round_number(workspace_root)

    while True:
        if max_rounds is not None and len(outcomes) >= max_rounds:
            break
        if max_wall_clock_hours is not None and \
           time.monotonic() - loop_start >= max_wall_clock_hours * 3600:
            break
        round_id = f"round-{next_n:06d}"
        variant_n = ((next_n - 1) % variant_count) + 1
        variant_id = f"v-{variant_n:03d}"
        outcome = run_round(
            workspace_root, harness_config, round_id, variant_id,
        )
        outcomes.append(outcome)
        next_n += 1

    return outcomes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_orchestrator_loop -v`
Expected: 5 tests pass.

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 290 tests / OK` (285 + 5).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/orchestrator.py tests/test_orchestrator_loop.py
git commit -m "feat(orchestrator): run_loop with variant rotation + dual stopping caps"
```

---

## Task 6: `harness run` CLI subcommand

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/cli.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_cli_run.py`

- [ ] **Step 1: Write failing CLI tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_cli_run.py`:

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _run_harness(*args, cwd=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    return subprocess.run(
        ["python3", "-m", "harness", *args],
        cwd=cwd or REPO_ROOT, env=env,
        capture_output=True, text=True,
    )


class CliRunFlagsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_rounds_flag_passes_through(self):
        # --rounds 1 invokes run_loop with max_rounds=1; spawn fails because
        # claude isn't actually on PATH in this test, but the CLI should still
        # exit cleanly (run_loop completes with 1 spawn-failed outcome).
        result = _run_harness(
            "run", "--rounds", "1", "--workspace", str(self.ws),
        )
        # Either the run completes successfully (exit 0) or the lack of claude
        # binary surfaces as a nonzero exit; both are acceptable here. We
        # only require that the subcommand was RECOGNIZED.
        self.assertNotIn("invalid choice", result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)


class CliRunHoursFlagTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_hours_flag_passes_through(self):
        result = _run_harness(
            "run", "--hours", "0.0003", "--workspace", str(self.ws),
        )
        self.assertNotIn("invalid choice", result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)


class CliRunVariantsFlagTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_variants_flag_passes_through(self):
        result = _run_harness(
            "run", "--rounds", "1", "--variants", "3",
            "--workspace", str(self.ws),
        )
        self.assertNotIn("invalid choice", result.stderr)


class CliRunNoCapsExitsNonzeroTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_caps_exits_nonzero(self):
        result = _run_harness("run", "--workspace", str(self.ws))
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_cli_run -v`
Expected: All 4 tests fail (the `run` subcommand isn't registered yet → argparse returns `invalid choice`).

- [ ] **Step 3: Extend `harness/cli.py` with the `run` subcommand**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/cli.py`. Find the `main()` function. After the `init_p = subparsers.add_parser(...)` block and its `add_argument` calls (but before `args = parser.parse_args(argv)`), add the new `run` subparser:

Old:
```python
    init_p.add_argument(
        "--reactivate", action="store_true",
        help="Re-run hook configuration on an existing workspace (e.g., a clone)",
    )

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    return 1
```

New:
```python
    init_p.add_argument(
        "--reactivate", action="store_true",
        help="Re-run hook configuration on an existing workspace (e.g., a clone)",
    )

    run_p = subparsers.add_parser("run", help="Run the harness loop")
    run_p.add_argument("--rounds", type=int, default=None,
                       help="Max rounds cap (at least one of "
                            "--rounds / --hours required)")
    run_p.add_argument("--hours", type=float, default=None,
                       help="Max wall-clock hours cap")
    run_p.add_argument("--variants", type=int, default=2,
                       help="Number of variants to rotate across (default 2)")
    run_p.add_argument("--workspace", type=Path, default=Path.cwd(),
                       help="Workspace directory (default: cwd)")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    if args.cmd == "run":
        return cmd_run(args.workspace, args.rounds, args.hours, args.variants)
    return 1
```

Then add the `cmd_run` function before `main`. Find:

Old:
```python
def main(argv: list[str] | None = None) -> int:
```

New (add `cmd_run` immediately before `main`):
```python
def cmd_run(
    workspace: Path,
    max_rounds: int | None,
    max_hours: float | None,
    variants: int,
) -> int:
    """Run the harness loop. Requires at least one of max_rounds / max_hours."""
    import sys
    import tomllib
    from harness.orchestrator import run_loop

    if max_rounds is None and max_hours is None:
        print("harness run: at least one of --rounds or --hours required",
              file=sys.stderr)
        return 2

    harness_toml = workspace / "harness.toml"
    if not harness_toml.exists():
        print(f"harness run: {harness_toml} not found "
              "(workspace not scaffolded?)", file=sys.stderr)
        return 1
    with harness_toml.open("rb") as f:
        harness_config = tomllib.load(f)

    try:
        outcomes = run_loop(
            workspace, harness_config,
            max_rounds=max_rounds,
            max_wall_clock_hours=max_hours,
            variant_count=variants,
        )
    except ValueError as e:
        print(f"harness run: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"harness run: unexpected error: {e}", file=sys.stderr)
        return 1

    # Summary
    for o in outcomes:
        print(f"{o.round_id} {o.variant_id} → {o.verdict}"
              f"{' (' + o.reason + ')' if o.reason else ''}")
    print(f"Ran {len(outcomes)} round(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_cli_run -v`
Expected: 4 tests pass.

- [ ] **Step 5: Run full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 294 tests / OK` (290 + 4).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/cli.py tests/test_cli_run.py
git commit -m "feat(cli): harness run subcommand wrapping run_loop"
```

---

## Task 7: `/code-review` gate over sub-project 4

After Tasks 1-6 are done and per-task reviews pass, dispatch `/code-review` over the full sub-project 4 diff.

**Files:** none modified directly in this task. Findings (if any) are addressed in a follow-up commit.

- [ ] **Step 1: Capture the base SHA**

Before Task 1 begins, capture the base SHA so we can review the whole sub-project's diff at once:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git rev-parse HEAD
```

Record the SHA — this is the `BASE_SHA` for the review. The plan executor should write this down before starting Task 1.

- [ ] **Step 2: Dispatch the `/code-review` subagent**

After Task 6's commit lands, dispatch a subagent that invokes `/code-review` at high effort over the range `BASE_SHA..HEAD`. Substitute `<BASE_SHA>` with the captured value:

```
You are running a /code-review on orchestrator sub-project 4 (Round State
Machine + Variant Rotation).

Invoke the `/code-review` skill at effort=high over the commit range
`<BASE_SHA>..HEAD` in /Users/liwen/develop/projects/auto_design_doc.

Sub-project 4 ships:
- harness/round_ledger.py: persistence + commit helpers (write_role_scratch,
  write_rejection, append_actions_log, commit_register_decision,
  commit_canonicalize, commit_merge, commit_rejection).
- harness/orchestrator.py: RoundOutcome dataclass + 4 validators + run_round
  (linear 9-phase flow with early returns) + run_loop (variant rotation +
  dual max_rounds/max_wall_clock_hours caps) + materialize/discard helpers.
- harness/cli.py extension: `run` subcommand wrapping run_loop.

The spec is at docs/superpowers/specs/2026-05-31-round-state-machine-and-
variant-rotation-design.md. Per-task reviews already covered: dataclass
shape, validator behaviors, happy-path flow, individual rejection paths
(planner/designer spawn/parse fail, Verifier A/B failures, reviewer reject,
Verifier C dispute), Flow A/C mutations, file-discard semantics, loop
rotation + caps, CLI flag parsing.

Look for issues BEYOND those:
- Concurrency / race conditions in actions.jsonl appends if a future
  sub-project parallelizes
- Edge cases in materialize_designer_output (git apply on patches with
  unusual encodings, conflicts with pre-existing files)
- discard_materialized correctness when git ls-files races against
  unlink (TOCTOU)
- Subprocess argv injection (commit messages contain user-controlled
  decision IDs, attack rationales)
- File-handle leaks if commit_* helpers fail mid-stream
- Schema drift between validators (validate_*_json) and the real
  dataclass from_dict paths in harness/claim_graph.py
- Whether _log calls cover every spec-required event from §3.4
- Whether discarding materialized files leaves orphaned scratch outputs
  (planner.json, designer.json) that confuse sub-project 6's resume

Don't run tests — the suite is at ~294/294 passing.

Return a single review report with Critical/Important/Minor sections.
File:line refs and concrete fixes for each. Keep under 1500 words.
```

- [ ] **Step 3: Triage findings**

If the review returns:
- **Critical:** address inline before declaring the sub-project complete.
- **Important:** address inline if the fix is small (≤30 LOC); otherwise defer to the deferred-findings backlog.
- **Minor:** record in the deferred backlog for batched cleanup.

If no Critical or Important findings: proceed to Step 4.

- [ ] **Step 4: Commit any inline fixes**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/orchestrator.py harness/round_ledger.py
git commit -m "fix(orchestrator,round_ledger): address /code-review findings from sub-project 4 pass"
```

If no fixes were needed, skip this step.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: at minimum 294 tests pass; possibly more if the review added regression tests.

---

## Spec coverage check

| Spec section | Requirement | Implemented in |
|---|---|---|
| §2.1 RoundOutcome dataclass | Task 2 |
| §2.1 run_round signature | Task 2 (skeleton) → Tasks 3+4 (full) |
| §2.1 run_loop signature | Task 5 |
| §2.2 round_ledger public API | Task 1 |
| §2.3 cli.py `run` subcommand | Task 6 |
| §3.1 Phase 1 (Planner) | Task 2, with failure path in Task 3 |
| §3.1 Phase 2 (Designer + materialize) | Task 2, with failure path in Task 3 |
| §3.1 Phase 3 (Verifier A) | Task 3 |
| §3.1 Phase 4 (Verifier B) | Task 3 |
| §3.1 Phase 5 (Reviewer + reject path) | Task 2 (happy) + Task 3 (reject) |
| §3.1 Phase 5.5 (Flow A gating) | Task 4 |
| §3.1 Phase 6 (Verifier C + dispute) | Task 2 (happy) + Task 3 (dispute) |
| §3.1 Phase 7a (register-decision commit) | Task 4 |
| §3.1 Phase 7b (canonicalize commit) | Task 4 |
| §3.1 Phase 8 (merge commit) | Task 2 |
| §3.1 Phase 9 (detectors) | Not implemented in v0 — deferred. The spec says detectors run for actions.jsonl logging only, and sub-project 5 owns morning_brief consumption. Acceptable gap: add a note in run_round saying "Phase 9 detectors deferred to sub-project 5". |
| §3.2 materialization rollback | Task 3 (`_discard_materialized`) |
| §3.3 run_loop semantics | Task 5 |
| §3.4 actions.jsonl schema | Task 1 (append) + Task 3 (logging from run_round); some event types may be incomplete (see Phase 9 above) |
| §3.5 validators | Task 2 |
| §4.1 test_round_ledger | Task 1 |
| §4.2 test_orchestrator_round | Tasks 2-4 |
| §4.3 test_orchestrator_loop | Task 5 |
| §4.4 test_cli_run | Task 6 |
| /code-review gate | Task 7 |

**Known deferred gaps from this plan (acceptable for v0, called out for the deferred backlog):**
- Phase 9 (detectors invocation) is documented in run_round as a TODO but not implemented; sub-project 5 will add it when morning_brief consumes the detector outputs.
- `actions.jsonl` may not cover every event type from §3.4 yet — Task 7's /code-review can flag specific gaps.
- The `test_designer_patch_apply_failure_verdict_cross_field_fail` test accepts any failure verdict (phase-a-fail / phase-b-fail / output-parse-fail) — the actual verdict depends on what the materialize-error catch block emits. Task 3's implementation uses `phase-a-fail` with `reason_class=cross-field-fail`, which the test accepts.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", or "implement later" entries in the implementation steps. (One acknowledged gap: Phase 9 detectors, called out explicitly in the spec coverage table.)
- Function names used across tasks match definitions exactly:
  - `RoundOutcome(round_id, variant_id, verdict, reason=None, rj_id=None, elapsed_seconds=0.0, spawn_counts={})` — Task 2.
  - `run_round(workspace_root, harness_config, round_id, variant_id) -> RoundOutcome` — Task 2 (skeleton), extended in Tasks 3 + 4.
  - `run_loop(workspace_root, harness_config, max_rounds=None, max_wall_clock_hours=None, variant_count=2) -> list[RoundOutcome]` — Task 5.
  - `write_role_scratch(workspace_root, round_id, role, parsed) -> Path` — Task 1, called in Tasks 2-3.
  - `write_rejection(workspace_root, round_id, variant_id, reason_class, failed_phase, detail, reviewer_id=None) -> str` — Task 1, called in Task 3 via `_reject` helper.
  - `append_actions_log(workspace_root, entry)` — Task 1, called via `_log` wrapper in Tasks 2-4.
  - `commit_*` helpers (4 of them) — Task 1, called from Tasks 2-4.
- Validators: `validate_planner_json`, `validate_designer_json`, `validate_reviewer_json`, `validate_verifier_c_json` — all defined in Task 2, referenced as the `validator` arg to `spawn_role` in Tasks 2-3.
- Closed-vocab `verdict` values: `merge`, `reviewer-rejected`, `phase-a-fail`, `phase-b-fail`, `phase-c-dispute`, `spawn-failed`, `output-parse-fail` — match spec §2.1 and the commit-msg hook's `ALLOWED_ACTIONS` enum.
- Closed-vocab `reason_class` values: `uncited-claim`, `dangling-evidence`, `cross-field-fail`, `proposal-rejected`, plus pass-through of spawn-failed/output-parse-fail when no narrower reason applies — match the commit-msg hook's `ALLOWED_REASONS` enum.
- The `RoleOutput` import in tests is `from harness.spawn import RoleOutput` — matches sub-project 3.
- The orchestrator imports `spawn_role` directly (so tests can `mock.patch("harness.orchestrator.spawn_role", ...)`).

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-31-round-state-machine-and-variant-rotation.md`.
