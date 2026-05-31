# Workspace Bootstrap + Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement orchestrator sub-project 1 per [2026-05-24-workspace-bootstrap-and-hooks-design.md](../specs/2026-05-24-workspace-bootstrap-and-hooks-design.md): ship `harness init` CLI, the missing `workspace_template/` files (`harness.toml`, `seed_doc.md`, `.gitignore`), the pre-commit hook (cl/at schema + cross-field + citation), and the commit-msg hook (closed-vocab trailers + Action-aware file-set whitelist + scope + immutability).

**Architecture:** Two-layer hook split forced by git hook order — pre-commit runs file-local checks (no access to commit message yet), commit-msg runs Action-aware checks. Both hooks are standalone Python scripts (Python 3.11+ stdlib only); pre-commit duplicates `harness/claim_graph.py` validators verbatim with a parity test, per the existing inline-duplication-over-import-coupling discipline. CLI is a stdlib `argparse` subparser shape so sub-projects 4 and 6 can plug in `run` and `resume` later.

**Tech Stack:** Python 3.11+ stdlib (`argparse`, `tomllib`, `pathlib`, `re`, `json`, `subprocess`, `shutil`, `unittest`); `git` on PATH.

---

## File Structure

**Created in this plan:**
- `harness/cli.py` — argparse-based CLI module with `init` subcommand
- `harness/__main__.py` — entry shim for `python -m harness <...>`
- `workspace_template/harness.toml` — heavily commented orchestrator config
- `workspace_template/seed_doc.md` — empty stub with explanatory header comment
- `workspace_template/.gitignore` — ignore CONTEXT.md, derived/, scratch, etc.
- `workspace_template/hooks/pre-commit` — executable Python script (cl/at schema + cross-field + citation)
- `workspace_template/hooks/commit-msg` — executable Python script (trailer schema + file-set whitelist + scope + immutability)
- `tests/test_cli_init.py` — fixture-driven init tests (~7)
- `tests/test_hook_parity.py` — parity between inlined hook validators and `harness.claim_graph` canonical (~3 method-level, ~24 fixtures)
- `tests/test_pre_commit_hook.py` — integration tests (~10) against a temp git repo
- `tests/test_commit_msg_hook.py` — integration tests (~15) against a temp git repo

**Modified in this plan:**
- `tests/test_workspace_template.py` — extend with smoke checks for the three new template files

**Created but not used in this plan (deferred to later sub-projects):**
- None.

---

## Task 1: Workspace template files (harness.toml, seed_doc.md, .gitignore)

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/harness.toml`
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/seed_doc.md`
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/.gitignore`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_workspace_template.py`

- [ ] **Step 1: Append failing smoke tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_workspace_template.py` (before the `if __name__ == "__main__":` line):

```python
class WorkspaceTemplateHarnessTomlTest(unittest.TestCase):
    def test_harness_toml_exists_and_parses(self):
        path = TEMPLATE_DIR / "harness.toml"
        self.assertTrue(path.exists(), f"missing template: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
        # Must have the three pinned sections
        self.assertIn("models", data)
        self.assertIn("run", data)
        self.assertIn("claim_graph", data)

    def test_harness_toml_models_block_has_all_four_roles(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for role in ("planner", "designer", "reviewer", "verifier_c"):
            self.assertIn(role, data["models"],
                          f"models.{role} missing from harness.toml")
            entry = data["models"][role]
            self.assertIn("tool", entry,
                          f"models.{role}.tool missing")
            self.assertIn("model", entry,
                          f"models.{role}.model missing")

    def test_harness_toml_run_block_has_required_keys(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for k in ("max_rounds", "max_wall_clock_hours", "verifier_c_every",
                  "patch_max_sections", "spawn_timeout_seconds"):
            self.assertIn(k, data["run"], f"run.{k} missing")

    def test_harness_toml_claim_graph_block_has_thresholds(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for k in ("stale_proposals_threshold_rounds",
                  "bootstrap_registry_size_threshold"):
            self.assertIn(k, data["claim_graph"], f"claim_graph.{k} missing")


class WorkspaceTemplateSeedDocTest(unittest.TestCase):
    def test_seed_doc_exists(self):
        path = TEMPLATE_DIR / "seed_doc.md"
        self.assertTrue(path.exists(), f"missing template: {path}")

    def test_seed_doc_documents_three_starting_states(self):
        path = TEMPLATE_DIR / "seed_doc.md"
        text = path.read_text()
        for keyword in ("EMPTY", "STUB", "DRAFTED"):
            self.assertIn(keyword, text,
                          f"seed_doc.md must document the {keyword} starting state")


class WorkspaceTemplateGitignoreTest(unittest.TestCase):
    def test_gitignore_exists(self):
        path = TEMPLATE_DIR / ".gitignore"
        self.assertTrue(path.exists(), f"missing template: {path}")

    def test_gitignore_covers_required_patterns(self):
        path = TEMPLATE_DIR / ".gitignore"
        text = path.read_text()
        for pattern in ("CONTEXT.md", "derived/", "rounds/*/scratch/",
                        "*.tmp", "repo/", "sources/*/cache/"):
            self.assertIn(pattern, text,
                          f".gitignore must include {pattern!r}")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_workspace_template -v`
Expected: 3+ failures with `AssertionError: missing template: ...` for each of the three new files.

- [ ] **Step 3: Write `workspace_template/harness.toml`**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/harness.toml`:

```toml
# Harness orchestrator config. Edit and commit changes you make to the defaults.

[models]
# Per-role: which CLI tool to spawn AND which model ID.
# Supported tools: "claude", "codex", "gemini".
# Verifier C should differ from designer/reviewer — same-model self-verification
# is degenerate. If you don't have codex, change verifier_c to a different
# Claude model (e.g., sonnet) via the "claude" tool for a weaker but still
# useful cross-check.

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


[run]
# Loop bounds: whichever hits first ends the run.
max_rounds = 200                  # absolute round cap; overnight targets 100-120
max_wall_clock_hours = 12         # second cap

# Cadence + scope.
verifier_c_every = 1              # 1 = every round; raise to reduce Codex spend
patch_max_sections = 3            # hook rejects round-commits touching >N section files
spawn_timeout_seconds = 300       # per CLI spawn; round fails with reason: spawn-failed


[claim_graph]
# Claim graph thresholds (see harness/claim_graph.py).
stale_proposals_threshold_rounds = 5    # proposed decisions older than N rounds surface as stale
bootstrap_registry_size_threshold = 5   # reviewer permissive on new-decision proposals when registry has fewer entries
```

- [ ] **Step 4: Write `workspace_template/seed_doc.md`**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/seed_doc.md`:

```markdown
<!--
This is the seed design doc. The harness handles three starting states:

  - EMPTY: leave this file blank; early rounds build structure from goal.toml.
  - STUB: add a few notes / outline to anchor early rounds.
  - DRAFTED: paste a complete human-reviewed doc; the harness refines + extends.

All three flow through the same designer/reviewer/Verifier pipeline.
-->
```

- [ ] **Step 5: Write `workspace_template/.gitignore`**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/.gitignore`:

```
CONTEXT.md
derived/
rounds/*/scratch/
*.tmp
repo/
sources/*/cache/
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_workspace_template -v`
Expected: All template tests pass.

- [ ] **Step 7: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 135 tests / OK` (127 existing + 8 new).

- [ ] **Step 8: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/harness.toml workspace_template/seed_doc.md workspace_template/.gitignore tests/test_workspace_template.py
git commit -m "feat(workspace_template): harness.toml, seed_doc.md, .gitignore for sub-project 1"
```

---

## Task 2: `harness init` CLI

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/cli.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/__main__.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_cli_init.py`

- [ ] **Step 1: Write failing CLI tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_cli_init.py`:

```python
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_harness(*args, cwd=None):
    """Run `python -m harness <args>` from REPO_ROOT and capture output."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    result = subprocess.run(
        ["python3", "-m", "harness", *args],
        cwd=cwd or REPO_ROOT,
        env=env,
        capture_output=True, text=True,
    )
    return result


class InitIntoNonexistentDirTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.target = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_init_into_nonexistent_dir_creates_scaffold(self):
        result = _run_harness("init", str(self.target))
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}\nstdout: {result.stdout}")
        # Template files copied
        for rel in ("constitution.md", "goal.toml", "harness.toml",
                    "seed_doc.md", ".gitignore",
                    "hooks/pre-commit", "hooks/commit-msg"):
            self.assertTrue((self.target / rel).exists(),
                            f"missing in scaffold: {rel}")
        # Git initialized
        self.assertTrue((self.target / ".git").exists())
        # hooksPath configured
        result = subprocess.run(
            ["git", "-C", str(self.target), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "hooks/")

    def test_init_initial_commit_has_action_init_trailer(self):
        _run_harness("init", str(self.target))
        result = subprocess.run(
            ["git", "-C", str(self.target), "log", "-1", "--format=%B"],
            capture_output=True, text=True,
        )
        self.assertIn("Action: init", result.stdout)

    def test_init_preserves_hook_executable_bits(self):
        _run_harness("init", str(self.target))
        for hook in ("pre-commit", "commit-msg"):
            hook_path = self.target / "hooks" / hook
            mode = hook_path.stat().st_mode
            self.assertTrue(mode & 0o111,
                            f"{hook} should be executable, got mode {oct(mode)}")


class InitIntoEmptyDirTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.target = self.td / "ws"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_init_into_empty_dir_succeeds(self):
        result = _run_harness("init", str(self.target))
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")
        self.assertTrue((self.target / "harness.toml").exists())


class InitRefusesNonEmptyDirTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.target = self.td / "ws"
        self.target.mkdir()
        (self.target / "some_file.txt").write_text("existing content")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_init_refuses_non_empty_dir(self):
        result = _run_harness("init", str(self.target))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to clobber", result.stderr)


class ReactivateTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.target = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reactivate_skips_copy_and_reconfigures_hooks(self):
        # First init normally
        result = _run_harness("init", str(self.target))
        self.assertEqual(result.returncode, 0, result.stderr)
        # Unset hooksPath to simulate clone
        subprocess.check_call(
            ["git", "-C", str(self.target), "config", "--unset", "core.hooksPath"],
        )
        # Reactivate
        result = _run_harness("init", str(self.target), "--reactivate")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            ["git", "-C", str(self.target), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "hooks/")

    def test_reactivate_fails_on_non_git_dir(self):
        self.target.mkdir()
        (self.target / "some_file.txt").write_text("not a git repo")
        result = _run_harness("init", str(self.target), "--reactivate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_cli_init -v`
Expected: All 7 tests fail with `ModuleNotFoundError: No module named 'harness.__main__'` or similar.

- [ ] **Step 3: Implement `harness/cli.py`**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/cli.py`:

```python
"""Harness CLI. Entry point: `python -m harness <subcommand>`."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = HARNESS_ROOT / "workspace_template"


def cmd_init(target_dir: Path, reactivate: bool) -> int:
    if reactivate:
        return _reactivate(target_dir)

    # Validate target: must not exist OR must be empty
    if target_dir.exists():
        if any(target_dir.iterdir()):
            print(f"harness init: refusing to clobber non-empty {target_dir}",
                  file=sys.stderr)
            return 1
    else:
        target_dir.mkdir(parents=True)

    # Copy template tree (files only; directories created as needed)
    for src in TEMPLATE_DIR.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(TEMPLATE_DIR)
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # Preserve executable bit for hook scripts (rglob may strip them)
        if "hooks" in rel.parts:
            dst.chmod(0o755)

    # Initialize git and configure hooksPath.
    # Hooks live at <target>/hooks/ in the scaffolded workspace (flat layout
    # — v0 simplifies the parent design's project-root/workspace/ nesting,
    # which only matters once repo/ and sources/ adapters land in v0.2+).
    subprocess.check_call(["git", "init", "-q"], cwd=target_dir)
    subprocess.check_call(
        ["git", "config", "core.hooksPath", "hooks/"],
        cwd=target_dir,
    )

    # Initial commit with Action: init trailer
    subprocess.check_call(["git", "add", "."], cwd=target_dir)
    commit_msg = "harness: scaffold workspace\n\nAction: init\n"
    subprocess.check_call(
        ["git", "commit", "-q", "-m", commit_msg],
        cwd=target_dir,
    )

    print(f"harness init: workspace ready at {target_dir}")
    return 0


def _reactivate(target_dir: Path) -> int:
    if not (target_dir / ".git").exists():
        print(f"{target_dir} is not a git repository", file=sys.stderr)
        return 1
    subprocess.check_call(
        ["git", "config", "core.hooksPath", "hooks/"],
        cwd=target_dir,
    )
    print(f"harness init --reactivate: hooks reactivated at {target_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Design Doc Evolution Harness orchestrator.",
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    init_p = subparsers.add_parser("init", help="Scaffold a new workspace")
    init_p.add_argument("dir", type=Path,
                        help="Target directory (must not exist or must be empty)")
    init_p.add_argument(
        "--reactivate", action="store_true",
        help="Re-run hook configuration on an existing workspace (e.g., a clone)",
    )

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement `harness/__main__.py`**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/__main__.py`:

```python
"""Entry shim so `python -m harness <subcommand>` works."""
from harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_cli_init -v`
Expected: All 7 tests pass.

Note: the `init_initial_commit_has_action_init_trailer` test will pass even though no commit-msg hook exists yet — git's commit succeeds because the `workspace/hooks/` directory in the scaffolded workspace is empty (hooks not added until Tasks 3-7).

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 142 tests / OK`.

- [ ] **Step 7: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/cli.py harness/__main__.py tests/test_cli_init.py
git commit -m "feat(harness/cli): init subcommand scaffolds workspace from template"
```

---

## Task 3: Pre-commit hook — schema validation + cross-field validators

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/pre-commit` (executable)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_hook_parity.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_pre_commit_hook.py`

- [ ] **Step 1: Write failing parity tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_hook_parity.py`:

```python
"""Parity test: the inlined validators in workspace_template/hooks/pre-commit
must produce identical verdicts to the canonical validators in
harness/claim_graph.py for every fixture.
"""
import unittest
from pathlib import Path

from harness import claim_graph as cg

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "pre-commit"


def _load_hook_namespace():
    """Exec the hook script in a namespace where __name__ != '__main__' so
    the guarded main() does not auto-run. Returns the namespace dict."""
    ns: dict = {"__name__": "pre_commit_hook"}
    exec(HOOK_PATH.read_text(), ns)
    return ns


# Fixtures: each is (label, dict, should_raise) for claim/attack validation.

VALID_CLAIM = {
    "id": "cl-000001",
    "section_id": "retry-policy",
    "decision_id": "retry-policy",
    "claim_type": "decision",
    "evidence_ids": ["ev-000001"],
    "assertion": "Use exponential backoff.",
    "position": "expo-backoff",
}

CLAIM_FIXTURES = [
    ("valid_decision_claim", VALID_CLAIM, False),
    ("missing_id", {**VALID_CLAIM, "id": None} if False else {k: v for k, v in VALID_CLAIM.items() if k != "id"}, True),
    ("bad_claim_type_enum", {**VALID_CLAIM, "claim_type": "speculation"}, True),
    ("missing_position_on_decision_claim",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"}, True),
    ("position_on_out_of_scope",
     {**VALID_CLAIM, "claim_type": "out_of_scope", "position": "x",
      "out_of_scope_rationale": "elsewhere"}, True),
    ("valid_out_of_scope",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"} |
     {"claim_type": "out_of_scope", "out_of_scope_rationale": "elsewhere"}, False),
    ("valid_unresolved",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"} |
     {"claim_type": "unresolved"}, False),
    ("bad_decision_id_slug", {**VALID_CLAIM, "decision_id": "Bad_Slug"}, True),
    ("bad_position_slug", {**VALID_CLAIM, "position": "Bad_Slug"}, True),
    ("evidence_ids_not_list", {**VALID_CLAIM, "evidence_ids": "ev-1"}, True),
]


VALID_DISPUTE = {
    "id": "at-000001", "at_type": "dispute_claim",
    "target_claim_id": "cl-000001",
    "argument": "Evidence does not support this.",
    "evidence_ids": ["ev-000001"],
}

VALID_CUT = {
    "id": "at-000002", "at_type": "propose_decision_cut",
    "target_decision_id": "auth-strategy",
    "rationale": "Lives in another doc.",
}

VALID_CANON = {
    "id": "at-000003", "at_type": "propose_canonicalization",
    "kind": "position", "scope": "retry-policy",
    "from": "exponential-backoff", "to": "expo-backoff",
    "confidence": "high", "rationale": "Both mean the same.",
}

ATTACK_FIXTURES = [
    ("valid_dispute", VALID_DISPUTE, False),
    ("valid_cut", VALID_CUT, False),
    ("valid_canon", VALID_CANON, False),
    ("bad_at_type", {**VALID_DISPUTE, "at_type": "complain"}, True),
    ("cut_missing_rationale",
     {k: v for k, v in VALID_CUT.items() if k != "rationale"}, True),
    ("canon_position_missing_scope",
     {k: v for k, v in VALID_CANON.items() if k != "scope"}, True),
]


def _make_decisions(registered):
    """Build {id: {"status": ...}} dict in the shape derived/decisions.json uses."""
    return {d_id: {"id": d_id, "question": "?", "status": status,
                   "introduced_at": "g-01"}
            for d_id, status in registered}


def _make_registry(data):
    return data  # registry is already the right shape


CROSS_FIELD_FIXTURES = [
    # (label, claim_dict, decisions, registry, should_raise)
    ("decision_id_resolves_to_open",
     VALID_CLAIM, _make_decisions([("retry-policy", "open")]), {}, False),
    ("decision_id_resolves_to_proposed",
     VALID_CLAIM, _make_decisions([("retry-policy", "proposed")]), {}, False),
    ("decision_id_retired_fails",
     VALID_CLAIM, _make_decisions([("retry-policy", "retired")]), {}, True),
    ("decision_id_unregistered_no_proposal_fails",
     VALID_CLAIM, _make_decisions([]), {}, True),
    ("decision_id_unregistered_with_proposal_passes",
     {**VALID_CLAIM, "decision_id": "circuit-breaker",
      "proposed_decision": {"id": "circuit-breaker",
                            "question": "?", "rationale": "x"}},
     _make_decisions([]), {}, False),
    ("vacuous_position_tbd",
     {**VALID_CLAIM, "position": "tbd"},
     _make_decisions([("retry-policy", "open")]), {}, True),
    ("vacuous_position_unclear",
     {**VALID_CLAIM, "position": "unclear"},
     _make_decisions([("retry-policy", "open")]), {}, True),
    ("position_is_alias_key",
     {**VALID_CLAIM, "position": "exponential-backoff"},
     _make_decisions([("retry-policy", "open")]),
     {"retry-policy": {"canonical": ["expo-backoff"],
                       "aliases": {"exponential-backoff": "expo-backoff"}}},
     True),
]


class ClaimValidatorParityTest(unittest.TestCase):
    def test_claim_validator_parity(self):
        ns = _load_hook_namespace()
        hook_validate = ns["validate_claim_dict"]
        for label, fixture, should_raise in CLAIM_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                try:
                    hook_validate(fixture)
                except Exception:
                    hook_raised = True
                try:
                    cg.Claim.from_dict(fixture)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


class AttackValidatorParityTest(unittest.TestCase):
    def test_attack_validator_parity(self):
        ns = _load_hook_namespace()
        hook_validate = ns["validate_attack_dict"]
        for label, fixture, should_raise in ATTACK_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                try:
                    hook_validate(fixture)
                except Exception:
                    hook_raised = True
                try:
                    cg.Attack.from_dict(fixture)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


class CrossFieldValidatorParityTest(unittest.TestCase):
    def test_cross_field_validator_parity(self):
        ns = _load_hook_namespace()
        hook_resolution = ns["validate_claim_decision_id_resolution"]
        hook_vacuous = ns["validate_claim_position_not_vacuous"]
        hook_alias = ns["validate_claim_position_not_alias"]
        for label, claim, decisions, registry, should_raise in CROSS_FIELD_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                # Hook side: run all three
                try:
                    hook_resolution(claim, decisions)
                    hook_vacuous(claim)
                    hook_alias(claim, registry)
                except Exception:
                    hook_raised = True
                # cg side: run the canonical Claim then the three
                try:
                    cg_claim = cg.Claim.from_dict(claim)
                    cg_decisions_typed = {
                        d_id: cg.Decision.from_dict(d)
                        for d_id, d in decisions.items()
                    }
                    cg_registry = cg.CanonicalSlugRegistry.from_dict(registry)
                    cg.validate_claim_decision_id_resolution(cg_claim, cg_decisions_typed)
                    cg.validate_claim_position_not_vacuous(cg_claim)
                    cg.validate_claim_position_not_alias(cg_claim, cg_registry)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write failing pre-commit integration tests (schema + cross-field)**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_pre_commit_hook.py`:

```python
"""Integration tests for the pre-commit hook script.

Each test sets up a minimal workspace under tempdir (via `harness init`), stages
a known file set, runs the pre-commit script as a subprocess, asserts exit
code + stderr.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "pre-commit"


def _scaffold_workspace(target: Path):
    """Run `harness init` to scaffold a workspace at target."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _write_cl(workspace: Path, variant: str, claim_id: str, **fields):
    """Write a cl-*.json under variants/nodes/<variant>/claims/."""
    claims_dir = workspace / "variants" / "nodes" / variant / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "id": claim_id, "section_id": fields.get("decision_id", "retry-policy"),
        "decision_id": "retry-policy", "claim_type": "decision",
        "evidence_ids": [], "assertion": "x", "position": "expo-backoff",
    }
    base.update(fields)
    fp = claims_dir / f"{claim_id}.json"
    fp.write_text(json.dumps(base, indent=2))
    return fp


def _write_at(workspace: Path, variant: str, attack_id: str, **fields):
    attacks_dir = workspace / "variants" / "nodes" / variant / "attacks"
    attacks_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "id": attack_id, "at_type": "dispute_claim",
        "target_claim_id": "cl-000001",
        "argument": "x", "evidence_ids": [],
    }
    base.update(fields)
    fp = attacks_dir / f"{attack_id}.json"
    fp.write_text(json.dumps(base, indent=2))
    return fp


def _write_registry(workspace: Path, data: dict):
    p = workspace / "derived" / "canonical_slug_registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _write_decisions(workspace: Path, decisions: dict):
    p = workspace / "derived" / "decisions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"goal_version": "g-01", "decisions": decisions},
                            indent=2))


def _stage_all(workspace: Path):
    subprocess.check_call(["git", "-C", str(workspace), "add", "-A"])


def _run_hook(workspace: Path):
    """Invoke the pre-commit script directly. Returns CompletedProcess."""
    return subprocess.run(
        ["python3", str(HOOK_PATH)],
        cwd=workspace,
        capture_output=True, text=True,
    )


class PreCommitSchemaTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_passes_on_valid_claim_and_attack(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001")
        _write_at(self.ws, "v-001", "at-000001")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

    def test_rejects_invalid_claim_json(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", claim_type="speculation")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("claim_type", result.stderr)

    def test_rejects_invalid_attack_json(self):
        _write_at(self.ws, "v-001", "at-000001", at_type="complain")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("at_type", result.stderr)

    def test_rejects_vacuous_position_slug(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", position="tbd")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("vacuous", result.stderr.lower())

    def test_rejects_alias_slug_as_position(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_registry(self.ws, {
            "retry-policy": {"canonical": ["expo-backoff"],
                             "aliases": {"exponential-backoff": "expo-backoff"}},
        })
        _write_cl(self.ws, "v-001", "cl-000001", position="exponential-backoff")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("alias", result.stderr.lower())

    def test_rejects_decision_id_for_retired_decision(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "retired", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("retired", result.stderr.lower())

    def test_collects_multiple_errors_in_one_run(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", claim_type="speculation")
        _write_at(self.ws, "v-001", "at-000001", at_type="complain")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("claim_type", result.stderr)
        self.assertIn("at_type", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_hook_parity tests.test_pre_commit_hook -v`
Expected: All fail with `FileNotFoundError` for the hook script (it doesn't exist yet) or `exec` errors when the test tries to load it.

- [ ] **Step 4: Create the pre-commit hook script**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/pre-commit`:

```python
#!/usr/bin/env python3
"""Pre-commit hook for the Design Doc Evolution Harness.

Self-contained: does NOT import harness/. Duplicates the schema validators
inline per the inline-duplication-over-import-coupling decision. A parity
test (tests/test_hook_parity.py) ensures the inlined logic stays in sync
with harness/claim_graph.py.

Checks: cl-*.json schema, at-*.json schema, cross-field validators
(decision_id resolution, vacuous slug, alias rewriting), citation resolution
(added in Task 4).

Action-aware checks (scope, immutability) live in the commit-msg hook because
pre-commit cannot read the commit's Action trailer.
"""
import json
import re
import subprocess
import sys
from pathlib import Path


# ----- Duplicated from harness/claim_graph.py --------------------------------


class SchemaError(ValueError):
    pass


class RegistryInvariantError(RuntimeError):
    pass


CLAIM_TYPES = frozenset({"decision", "observation", "inference",
                         "out_of_scope", "unresolved"})
AT_TYPES = frozenset({"dispute_claim", "propose_decision_cut",
                      "propose_canonicalization"})
CANONICALIZATION_KINDS = frozenset({"decision_id", "position"})
CONFIDENCES = frozenset({"high", "medium", "low"})
DECISION_STATUSES = frozenset({"open", "proposed", "retired"})

VACUOUS_POSITION_SLUGS = frozenset({
    "tbd", "unclear", "unknown", "not-decided", "not-yet-decided",
    "na", "none", "n-a", "tbd_", "unclear_", "unknown_",
    "not_decided", "not_yet_decided", "n_a",
})

SLUG_REGEX = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


def _require(condition, msg):
    if not condition:
        raise SchemaError(msg)


def _require_enum(value, allowed, field_name):
    _require(value in allowed,
             f"{field_name} must be one of {sorted(allowed)}, got {value!r}")


def _require_slug(value, field_name):
    _require(isinstance(value, str), f"{field_name} must be a string")
    _require(SLUG_REGEX.match(value) is not None,
             f"{field_name} {value!r} is not kebab-case ASCII")


def validate_claim_dict(d):
    """Run the same schema validation as Claim.from_dict (full duplication)."""
    for required in ("id", "section_id", "decision_id", "claim_type",
                     "evidence_ids", "assertion"):
        _require(required in d, f"Claim missing required field {required!r}")
    _require_enum(d["claim_type"], CLAIM_TYPES, "claim_type")
    _require_slug(d["decision_id"], "decision_id")
    _require(isinstance(d["evidence_ids"], list),
             "evidence_ids must be a list")
    claim_type = d["claim_type"]
    position = d.get("position")
    out_of_scope_rationale = d.get("out_of_scope_rationale")
    if claim_type == "out_of_scope":
        _require(out_of_scope_rationale is not None,
                 "out_of_scope claim must have out_of_scope_rationale")
        _require(position is None,
                 "out_of_scope claim must NOT have position")
    elif claim_type == "unresolved":
        _require(position is None,
                 "unresolved claim must NOT have position")
    else:
        _require(position is not None,
                 f"{claim_type} claim must have position")
        _require_slug(position, "position")


def validate_attack_dict(d):
    """Run the same schema validation as Attack.from_dict."""
    _require("id" in d, "Attack missing 'id'")
    _require("at_type" in d, "Attack missing 'at_type'")
    _require_enum(d["at_type"], AT_TYPES, "at_type")
    at_type = d["at_type"]
    if at_type == "dispute_claim":
        for req in ("target_claim_id", "argument"):
            _require(req in d, f"dispute_claim missing {req!r}")
    elif at_type == "propose_decision_cut":
        for req in ("target_decision_id", "rationale"):
            _require(req in d, f"propose_decision_cut missing {req!r}")
        _require_slug(d["target_decision_id"], "target_decision_id")
    elif at_type == "propose_canonicalization":
        for req in ("kind", "from", "to", "confidence", "rationale"):
            _require(req in d, f"propose_canonicalization missing {req!r}")
        _require_enum(d["kind"], CANONICALIZATION_KINDS, "kind")
        _require_enum(d["confidence"], CONFIDENCES, "confidence")
        _require_slug(d["from"], "from")
        _require_slug(d["to"], "to")
        if d["kind"] == "position":
            _require("scope" in d,
                     "propose_canonicalization kind=position requires scope")
            _require_slug(d["scope"], "scope")


def validate_claim_decision_id_resolution(claim_dict, decisions):
    """decisions: {decision_id: {"status": ..., ...}} from derived/decisions.json."""
    decision_id = claim_dict["decision_id"]
    if decision_id in decisions:
        if decisions[decision_id]["status"] == "retired":
            raise SchemaError(
                f"Claim {claim_dict['id']} references retired decision "
                f"{decision_id!r}; new claims may not reference retired decisions"
            )
        return
    proposed = claim_dict.get("proposed_decision")
    if proposed is None:
        raise SchemaError(
            f"Claim {claim_dict['id']} decision_id {decision_id!r} is not "
            "registered and no proposed_decision payload is present"
        )
    if proposed.get("id") != decision_id:
        raise SchemaError(
            f"Claim {claim_dict['id']} proposed_decision.id "
            f"({proposed.get('id')!r}) does not match decision_id ({decision_id!r})"
        )


def validate_claim_position_not_vacuous(claim_dict):
    position = claim_dict.get("position")
    if position is None:
        return
    if position in VACUOUS_POSITION_SLUGS:
        raise SchemaError(
            f"Claim {claim_dict['id']} has vacuous position slug {position!r}; "
            f"use claim_type=unresolved if you genuinely lack a position"
        )


def validate_claim_position_not_alias(claim_dict, registry):
    """registry: {decision_id: {"canonical": [...], "aliases": {...}}}"""
    position = claim_dict.get("position")
    if position is None:
        return
    entry = registry.get(claim_dict["decision_id"])
    if entry is None:
        return
    aliases = entry.get("aliases", {})
    if position in aliases:
        canonical = aliases[position]
        raise SchemaError(
            f"Claim {claim_dict['id']} position {position!r} is an alias of "
            f"canonical {canonical!r} under decision {claim_dict['decision_id']!r}; "
            f"use the canonical slug"
        )


# ----- Hook helpers -----------------------------------------------------------


def staged_files():
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"]
    ).decode()
    return [p for p in out.strip().split("\n") if p]


def load_decisions():
    p = Path("derived/decisions.json")
    if not p.exists():
        return {}
    with p.open() as f:
        data = json.load(f)
    return data.get("decisions", {})


def load_registry():
    p = Path("derived/canonical_slug_registry.json")
    if not p.exists():
        return {}
    with p.open() as f:
        return json.load(f)


def is_claim_file(path_str):
    parts = path_str.split("/")
    return (len(parts) >= 4 and parts[-2] == "claims"
            and parts[-1].startswith("cl-") and parts[-1].endswith(".json"))


def is_attack_file(path_str):
    parts = path_str.split("/")
    return (len(parts) >= 4 and parts[-2] == "attacks"
            and parts[-1].startswith("at-") and parts[-1].endswith(".json"))


# ----- Main -------------------------------------------------------------------


def main():
    errors = []
    decisions = load_decisions()
    registry = load_registry()

    for f in staged_files():
        if is_claim_file(f):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                validate_claim_dict(data)
                validate_claim_decision_id_resolution(data, decisions)
                validate_claim_position_not_vacuous(data)
                validate_claim_position_not_alias(data, registry)
            except (SchemaError, json.JSONDecodeError) as e:
                errors.append(f"{f}: {e}")
        elif is_attack_file(f):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                validate_attack_dict(data)
            except (SchemaError, json.JSONDecodeError) as e:
                errors.append(f"{f}: {e}")

    if errors:
        print("pre-commit failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

Make it executable:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
chmod +x workspace_template/hooks/pre-commit
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_hook_parity tests.test_pre_commit_hook -v`
Expected: All parity tests pass (3 test methods, ~24 subTests) AND all 7 schema integration tests pass.

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 152 tests / OK` (142 + 3 parity methods + 7 integration).

- [ ] **Step 7: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/hooks/pre-commit tests/test_hook_parity.py tests/test_pre_commit_hook.py
git commit -m "feat(hooks): pre-commit schema + cross-field validators (with parity test)"
```

---

## Task 4: Pre-commit hook — citation resolution

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/pre-commit`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_pre_commit_hook.py`

- [ ] **Step 1: Append failing citation tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_pre_commit_hook.py` (before `if __name__ == "__main__":`):

```python
def _write_evidence(workspace: Path, ev_id: str, superseded_by: str | None = None):
    ev_dir = workspace / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    if superseded_by is not None:
        frontmatter = f'superseded_by = "{superseded_by}"\n'
    else:
        frontmatter = ""
    text = f"+++\n{frontmatter}+++\n\nEvidence body.\n"
    (ev_dir / f"ev-{ev_id}.md").write_text(text)


def _write_section(workspace: Path, variant: str, section_name: str, body: str):
    """Write a doc section file with the given body (used to introduce cites)."""
    doc_dir = workspace / "variants" / "nodes" / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / f"{section_name}.md").write_text(body)


class PreCommitCitationTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_passes_cite_resolution_for_existing_evidence(self):
        _write_evidence(self.ws, "000001")
        _write_section(self.ws, "v-001", "01-retry-policy",
                       "Some claim [^ev-000001].\n")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

    def test_rejects_cite_for_missing_evidence(self):
        _write_section(self.ws, "v-001", "01-retry-policy",
                       "Some claim [^ev-999999].\n")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ev-999999", result.stderr)
        self.assertIn("does not resolve", result.stderr)

    def test_rejects_cite_for_superseded_evidence(self):
        _write_evidence(self.ws, "000001", superseded_by="ev-000002")
        _write_evidence(self.ws, "000002")
        _write_section(self.ws, "v-001", "01-retry-policy",
                       "Some claim [^ev-000001].\n")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ev-000001", result.stderr)
        self.assertIn("superseded", result.stderr)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_pre_commit_hook.PreCommitCitationTest -v`
Expected: All 3 tests fail. The "passes" test will fail because the hook doesn't do cite resolution; the "rejects" tests will fail because exit code is 0 (hook ignores cites).

- [ ] **Step 3: Extend the pre-commit hook with citation resolution**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/pre-commit`. Find the `# ----- Hook helpers -----` block and add the following function after `is_attack_file`:

```python


def cite_resolution_check():
    """Scan staged .md files for [^ev-NNNNNN] additions; verify each resolves.

    Returns a list of error message strings (empty on success).
    """
    errors = []
    md_files = [f for f in staged_files() if f.endswith(".md")]
    cite_re = re.compile(r"\[\^ev-(\d{6})\]")

    cites = set()
    for f in md_files:
        diff = subprocess.check_output(
            ["git", "diff", "--cached", "-U0", "--", f]
        ).decode()
        for line in diff.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                for m in cite_re.finditer(line):
                    cites.add(m.group(1))

    for ev_num in sorted(cites):
        ev_path = Path("evidence") / f"ev-{ev_num}.md"
        if not ev_path.exists():
            errors.append(
                f"cite [^ev-{ev_num}] does not resolve (no {ev_path})"
            )
            continue
        text = ev_path.read_text()
        # Check superseded_by in TOML frontmatter
        if text.startswith("+++"):
            end = text.find("+++", 3)
            if end != -1:
                frontmatter = text[3:end]
                if re.search(r'^\s*superseded_by\s*=\s*"[^"]+"', frontmatter,
                             re.MULTILINE):
                    errors.append(
                        f"cite [^ev-{ev_num}] refers to superseded evidence"
                    )
    return errors
```

Then find the `main()` function. Replace the existing `main()` with this version that calls the new check:

```python
def main():
    errors = []
    decisions = load_decisions()
    registry = load_registry()

    for f in staged_files():
        if is_claim_file(f):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                validate_claim_dict(data)
                validate_claim_decision_id_resolution(data, decisions)
                validate_claim_position_not_vacuous(data)
                validate_claim_position_not_alias(data, registry)
            except (SchemaError, json.JSONDecodeError) as e:
                errors.append(f"{f}: {e}")
        elif is_attack_file(f):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                validate_attack_dict(data)
            except (SchemaError, json.JSONDecodeError) as e:
                errors.append(f"{f}: {e}")

    errors.extend(cite_resolution_check())

    if errors:
        print("pre-commit failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_pre_commit_hook -v`
Expected: All 10 tests pass (7 schema + 3 citation).

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 155 tests / OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/hooks/pre-commit tests/test_pre_commit_hook.py
git commit -m "feat(hooks): pre-commit citation resolution check"
```

---

## Task 5: commit-msg hook — trailer parser + closed vocab

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg` (executable)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py`

- [ ] **Step 1: Write failing trailer + closed-vocab tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py`:

```python
"""Integration tests for the commit-msg hook script."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "commit-msg"


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _write_msg(workspace: Path, text: str) -> Path:
    msg_path = workspace / ".git" / "MSG"
    msg_path.write_text(text)
    return msg_path


def _run_hook(workspace: Path, msg_path: Path):
    return subprocess.run(
        ["python3", str(HOOK_PATH), str(msg_path)],
        cwd=workspace,
        capture_output=True, text=True,
    )


def _stage_all(workspace: Path):
    subprocess.check_call(["git", "-C", str(workspace), "add", "-A"])


class CommitMsgTrailerTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_missing_action_trailer_rejects(self):
        msg = _write_msg(self.ws, "some subject\n\nbody without trailers\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Action", result.stderr)

    def test_unknown_action_value_rejects(self):
        msg = _write_msg(self.ws, "subject\n\nAction: explode\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("explode", result.stderr)

    def test_unknown_trailer_key_rejects(self):
        msg = _write_msg(self.ws,
                         "subject\n\nAction: init\nBananas: yellow\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bananas", result.stderr)

    def test_action_merge_missing_variant_rejects(self):
        msg = _write_msg(self.ws,
                         "subject\n\nAction: merge\nRound: round-000001\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Variant", result.stderr)

    def test_action_reviewer_rejected_missing_reviewer_rejects(self):
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: reviewer-rejected\n"
            "Variant: v-001\nRound: round-000001\nReason: uncited-claim\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Reviewer", result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook -v`
Expected: All fail with `FileNotFoundError` for the hook script.

- [ ] **Step 3: Create the commit-msg hook (trailer parsing + closed vocab + required trailers)**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg`:

```python
#!/usr/bin/env python3
"""commit-msg hook for the Design Doc Evolution Harness.

Validates closed-vocab trailers, required-trailers-per-Action, Action-aware
file-set whitelist, scope (≤ patch_max_sections per merge commit), and decided-
section immutability. See docs/superpowers/specs/2026-05-24-workspace-bootstrap-
and-hooks-design.md §3.6.
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path


# ----- Closed-vocab enums -----------------------------------------------------


ALLOWED_ACTIONS = frozenset({
    "init", "merge", "register-decision", "canonicalize", "registry-sync",
    "reviewer-rejected", "phase-a-fail", "phase-b-fail", "phase-c-dispute",
    "spawn-failed", "output-parse-fail",
})

ALLOWED_REASONS = frozenset({
    "uncited-claim", "cross-field-fail", "vacuous-position",
    "proposal-rejected", "scope-violation", "immutability-violation",
    "phantom-claim", "dangling-evidence", "silent-goal-toml-edit",
})

VARIANT_RE = re.compile(r"^v-\d{3}$")
ROUND_RE = re.compile(r"^round-\d{6}$")

# Standard Git trailers that pass through untouched.
STANDARD_TRAILERS = frozenset({"Signed-off-by", "Co-authored-by"})

# Required trailer keys per Action.
TRAILER_REQUIREMENTS = {
    "init": set(),
    "merge": {"Variant", "Round"},
    "register-decision": set(),
    "canonicalize": set(),
    "registry-sync": set(),
    "reviewer-rejected": {"Variant", "Round", "Reason", "Reviewer"},
    "phase-a-fail": {"Variant", "Round", "Reason"},
    "phase-b-fail": {"Variant", "Round", "Reason"},
    "phase-c-dispute": {"Variant", "Round", "Reason"},
    "spawn-failed": {"Variant", "Round"},
    "output-parse-fail": {"Variant", "Round"},
}


# ----- Trailer parsing --------------------------------------------------------


def parse_trailers(text):
    """Return list of (key, value) tuples for trailers in the last paragraph."""
    paragraphs = text.rstrip().split("\n\n")
    if not paragraphs:
        return []
    last = paragraphs[-1]
    trailers = []
    for line in last.split("\n"):
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.+)$", line)
        if m:
            trailers.append((m.group(1), m.group(2).strip()))
    return trailers


def validate_trailers(trailers):
    """Returns (errors, seen_keys) — errors is a list, seen_keys is a dict."""
    errors = []
    seen_keys = {}
    for key, value in trailers:
        if key in STANDARD_TRAILERS:
            continue
        if key in seen_keys:
            errors.append(f"duplicate trailer {key!r}")
            continue
        seen_keys[key] = value
        if key == "Action":
            if value not in ALLOWED_ACTIONS:
                errors.append(
                    f"Action {value!r} not in allowed set "
                    f"{sorted(ALLOWED_ACTIONS)}"
                )
        elif key == "Reason":
            if value not in ALLOWED_REASONS:
                errors.append(
                    f"Reason {value!r} not in allowed set "
                    f"{sorted(ALLOWED_REASONS)}"
                )
        elif key == "Variant":
            if not VARIANT_RE.match(value):
                errors.append(
                    f"Variant {value!r} does not match ^v-\\d{{3}}$"
                )
        elif key == "Round":
            if not ROUND_RE.match(value):
                errors.append(
                    f"Round {value!r} does not match ^round-\\d{{6}}$"
                )
        elif key == "Reviewer":
            if not VARIANT_RE.match(value):
                errors.append(
                    f"Reviewer {value!r} does not match ^v-\\d{{3}}$"
                )
        else:
            errors.append(f"unknown trailer key {key!r}")
    return errors, seen_keys


def check_required_trailers(action, seen_keys):
    if action not in TRAILER_REQUIREMENTS:
        return []
    required = TRAILER_REQUIREMENTS[action]
    return [f"Action: {action} requires trailer {k!r}"
            for k in sorted(required) if k not in seen_keys]


# ----- Main -------------------------------------------------------------------


def main():
    msg_file = sys.argv[1]
    text = Path(msg_file).read_text()

    trailers = parse_trailers(text)
    errors, seen_keys = validate_trailers(trailers)

    if "Action" not in seen_keys:
        errors.append("missing required trailer Action")
        action = None
    else:
        action = seen_keys["Action"]

    if action is not None and action in ALLOWED_ACTIONS:
        errors.extend(check_required_trailers(action, seen_keys))

    if errors:
        print("commit-msg failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

Make it executable:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
chmod +x workspace_template/hooks/commit-msg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook -v`
Expected: All 5 trailer tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 160 tests / OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git commit -m "feat(hooks): commit-msg trailer parser + closed vocab + required-trailers"
```

---

## Task 6: commit-msg hook — Action-aware file-set whitelist

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py`

- [ ] **Step 1: Append failing file-set whitelist tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py` (before `if __name__ == "__main__":`):

```python
def _stage_file(workspace: Path, rel_path: str, content: str = "x\n"):
    """Create a file at rel_path under workspace and stage it."""
    p = workspace / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.check_call(["git", "-C", str(workspace), "add", rel_path])


class CommitMsgFileSetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_action_register_decision_with_doc_file_rejects(self):
        _stage_file(self.ws, "variants/nodes/v-001/doc/01-retry.md",
                    "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(self.ws, "subject\n\nAction: register-decision\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("register-decision", result.stderr)
        self.assertIn("doc/01-retry.md", result.stderr)

    def test_action_canonicalize_with_at_file_rejects(self):
        _stage_file(self.ws, "variants/nodes/v-001/attacks/at-000001.json",
                    "{}\n")
        msg = _write_msg(self.ws, "subject\n\nAction: canonicalize\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("canonicalize", result.stderr)
        self.assertIn("at-000001.json", result.stderr)

    def test_action_registry_sync_allowed_files_pass(self):
        _stage_file(self.ws, "derived/decisions.json", "{}\n")
        _stage_file(self.ws, "derived/canonical_slug_registry.json", "{}\n")
        msg = _write_msg(self.ws, "subject\n\nAction: registry-sync\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_action_merge_allowed_files_pass(self):
        _stage_file(self.ws, "variants/nodes/v-001/doc/01-retry.md",
                    "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        _stage_file(self.ws, "variants/nodes/v-001/claims/cl-000001.json",
                    "{}\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_failure_action_with_evidence_file_rejects(self):
        _stage_file(self.ws, "evidence/ev-000001.md", "x\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: phase-b-fail\nVariant: v-001\n"
            "Round: round-000001\nReason: cross-field-fail\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("phase-b-fail", result.stderr)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook.CommitMsgFileSetTest -v`
Expected: All 5 tests fail because the hook doesn't yet enforce the whitelist (rejecting tests pass exit 0; passing tests get exit 0 too).

- [ ] **Step 3: Add file-set whitelist to the commit-msg hook**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg`. Find the `# ----- Trailer parsing -----` section. Immediately BEFORE it, add this `# ----- File-set whitelist -----` section:

```python
# ----- File-set whitelist -----------------------------------------------------


import fnmatch


def _match_glob(file_path, pattern):
    """Match file_path against a glob pattern. Treats `*` as not crossing `/`."""
    file_parts = file_path.split("/")
    pat_parts = pattern.split("/")
    if len(file_parts) != len(pat_parts):
        return False
    return all(fnmatch.fnmatch(fp, pp)
               for fp, pp in zip(file_parts, pat_parts))


ACTION_FILE_WHITELIST = {
    "init": None,  # no restriction
    "merge": [
        "variants/nodes/v-*/doc/*.md",
        "variants/nodes/v-*/claims/cl-*.json",
        "variants/nodes/v-*/attacks/at-*.json",
        "evidence/ev-*.md",
        "rejections/rj-*.md",
        "variants/nodes/v-*/scorecard.json",
        "actions.jsonl",
    ],
    "register-decision": [
        "goal.toml",
        "derived/decisions.json",
        "actions.jsonl",
    ],
    "canonicalize": [
        "variants/nodes/v-*/claims/cl-*.json",
        "derived/canonical_slug_registry.json",
        "actions.jsonl",
    ],
    "registry-sync": [
        "variants/nodes/v-*/doc/*.md",
        "derived/decisions.json",
        "derived/canonical_slug_registry.json",
        "actions.jsonl",
    ],
    "reviewer-rejected": ["rejections/rj-*.md", "actions.jsonl"],
    "phase-a-fail": ["rejections/rj-*.md", "actions.jsonl"],
    "phase-b-fail": ["rejections/rj-*.md", "actions.jsonl"],
    "phase-c-dispute": ["rejections/rj-*.md", "actions.jsonl"],
    "spawn-failed": ["rejections/rj-*.md", "actions.jsonl"],
    "output-parse-fail": ["rejections/rj-*.md", "actions.jsonl"],
}


def staged_files():
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"]
    ).decode()
    return [p for p in out.strip().split("\n") if p]


def check_file_whitelist(action, files):
    errors = []
    whitelist = ACTION_FILE_WHITELIST.get(action)
    if whitelist is None:
        return errors
    for f in files:
        if not any(_match_glob(f, pattern) for pattern in whitelist):
            errors.append(
                f"Action: {action} does not allow staged file {f!r}; "
                f"allowed patterns: {whitelist}"
            )
    return errors
```

Then find the `main()` function. Replace it with this version that calls the new check:

```python
def main():
    msg_file = sys.argv[1]
    text = Path(msg_file).read_text()

    trailers = parse_trailers(text)
    errors, seen_keys = validate_trailers(trailers)

    if "Action" not in seen_keys:
        errors.append("missing required trailer Action")
        action = None
    else:
        action = seen_keys["Action"]

    if action is not None and action in ALLOWED_ACTIONS:
        errors.extend(check_required_trailers(action, seen_keys))
        files = staged_files()
        errors.extend(check_file_whitelist(action, files))

    if errors:
        print("commit-msg failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook -v`
Expected: All 10 tests pass (5 trailer + 5 file-set).

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 165 tests / OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git commit -m "feat(hooks): commit-msg Action-aware file-set whitelist"
```

---

## Task 7: commit-msg hook — scope + decided-section immutability

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg`
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py`

- [ ] **Step 1: Append failing scope + immutability tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_commit_msg_hook.py` (before `if __name__ == "__main__":`):

```python
def _commit_initial_section(workspace: Path, variant: str, section_name: str,
                            tags: list[str], body: str = "body\n"):
    """Stage and commit (bypassing hooks) a section file as the initial state.

    Returns the file path. Uses --no-verify to skip our own hooks during
    test setup; the test then makes a NEW change to exercise the hook.
    """
    doc_dir = workspace / "variants" / "nodes" / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    fp = doc_dir / f"{section_name}.md"
    fp.write_text(f"+++\nsection_id = \"x\"\ntags = [{tag_str}]\n+++\n{body}")
    subprocess.check_call(["git", "-C", str(workspace), "add", str(fp)])
    subprocess.check_call(
        ["git", "-C", str(workspace), "commit", "--no-verify", "-q",
         "-m", "setup\n\nAction: init\n"],
    )
    return fp


def _modify_section(workspace: Path, fp: Path, new_tags: list[str] | None = None,
                    new_body: str | None = None):
    """Modify the section file. Re-reads original and selectively updates."""
    text = fp.read_text()
    if new_tags is not None:
        tag_str = ", ".join(f'"{t}"' for t in new_tags)
        text = re.sub(r"(tags\s*=\s*)\[[^\]]*\]", rf"\1[{tag_str}]", text)
    if new_body is not None:
        # Replace everything after the second +++
        end = text.find("+++", 3)
        text = text[: end + 3] + "\n" + new_body
    fp.write_text(text)


def _stage(workspace: Path, *paths: str):
    subprocess.check_call(["git", "-C", str(workspace), "add", *paths])


# Need re import for _modify_section above
import re


class CommitMsgScopeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_action_merge_three_sections_passes(self):
        for i in range(1, 4):
            _stage_file(self.ws, f"variants/nodes/v-001/doc/0{i}-s.md",
                        "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_action_merge_four_sections_rejects(self):
        for i in range(1, 5):
            _stage_file(self.ws, f"variants/nodes/v-001/doc/0{i}-s.md",
                        "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("4 doc sections", result.stderr)
        self.assertIn("limit is 3", result.stderr)


class CommitMsgImmutabilityTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decided_section_body_change_with_goal_version_bump_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        # Modify the body
        _modify_section(self.ws, fp, new_body="updated body\n")
        # Modify goal.toml goal_version
        goal_path = self.ws / "goal.toml"
        text = goal_path.read_text()
        text = text.replace('goal_version = "g-01"', 'goal_version = "g-02"')
        goal_path.write_text(text)
        _stage(self.ws, str(fp), str(goal_path))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_decided_section_body_change_without_goal_version_bump_rejects(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_body="updated body\n")
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("immutability", result.stderr.lower())

    def test_decided_section_tag_only_change_on_registry_sync_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_tags=["unresolved"])
        _stage(self.ws, str(fp))
        msg = _write_msg(self.ws, "subject\n\nAction: registry-sync\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_decided_section_tag_only_change_on_merge_rejects(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_tags=["unresolved"])
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("registry-sync", result.stderr)

    def test_non_decided_section_body_change_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["unresolved"])
        _modify_section(self.ws, fp, new_body="updated body\n")
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook.CommitMsgScopeTest tests.test_commit_msg_hook.CommitMsgImmutabilityTest -v`
Expected: All 7 tests fail because the hook doesn't yet enforce scope or immutability.

- [ ] **Step 3: Add scope + immutability checks to the commit-msg hook**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/workspace_template/hooks/commit-msg`. Find the `def check_file_whitelist` function. Immediately AFTER it (before the `# ----- Main -----` section), add these functions:

```python


# ----- Scope + immutability ---------------------------------------------------


def load_harness_config():
    p = Path("harness.toml")
    if not p.exists():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f)


def check_scope(action, files):
    if action != "merge":
        return []
    section_files = [f for f in files
                     if _match_glob(f, "variants/nodes/v-*/doc/*.md")]
    config = load_harness_config()
    limit = config.get("run", {}).get("patch_max_sections", 3)
    if len(section_files) > limit:
        return [
            f"Action: merge touches {len(section_files)} doc sections, "
            f"limit is {limit} (from harness.toml [run].patch_max_sections); "
            f"files: {section_files}"
        ]
    return []


def _git_show_head(path):
    try:
        return subprocess.check_output(
            ["git", "show", f"HEAD:{path}"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except subprocess.CalledProcessError:
        return None


def _git_show_staged(path):
    try:
        return subprocess.check_output(
            ["git", "show", f":{path}"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except subprocess.CalledProcessError:
        return None


def _parse_goal_version(text):
    m = re.search(r'^\s*goal_version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _extract_tags(text):
    if not text.startswith("+++"):
        return []
    end = text.find("+++", 3)
    if end == -1:
        return []
    fm = text[3:end]
    m = re.search(r'^\s*tags\s*=\s*\[([^\]]*)\]', fm, re.MULTILINE)
    if not m:
        return []
    return [t.strip().strip('"').strip("'")
            for t in m.group(1).split(",") if t.strip()]


def _bodies_equal_modulo_tags(a, b):
    """Compare two section files ignoring differences in the tags = [...] line."""
    tags_re = re.compile(r'^(\s*tags\s*=\s*)\[[^\]]*\](\s*)$', re.MULTILINE)
    a_norm = tags_re.sub(r'\1[]\2', a)
    b_norm = tags_re.sub(r'\1[]\2', b)
    return a_norm == b_norm


def check_immutability(action, files):
    errors = []
    section_files = [f for f in files
                     if _match_glob(f, "variants/nodes/v-*/doc/*.md")]
    if not section_files:
        return errors

    # Did this commit also bump goal_version?
    goal_bumped = False
    if "goal.toml" in files:
        head_goal = _git_show_head("goal.toml")
        staged_goal = _git_show_staged("goal.toml")
        if head_goal and staged_goal:
            head_v = _parse_goal_version(head_goal)
            staged_v = _parse_goal_version(staged_goal)
            if head_v != staged_v:
                goal_bumped = True

    for f in section_files:
        head_content = _git_show_head(f)
        if head_content is None:
            continue  # new file; no prior state to violate
        staged_content = _git_show_staged(f)
        if staged_content is None:
            continue
        head_tags = _extract_tags(head_content)
        if "decided" not in head_tags:
            continue  # not yet decided; no immutability constraint
        staged_tags = _extract_tags(staged_content)
        body_unchanged_modulo_tags = _bodies_equal_modulo_tags(
            head_content, staged_content,
        )
        if head_tags != staged_tags and body_unchanged_modulo_tags:
            # Tag-only change
            if action != "registry-sync":
                errors.append(
                    f"{f}: tag-only change on decided section requires "
                    f"Action: registry-sync (got Action: {action!r}); "
                    f"immutability-violation"
                )
        elif not body_unchanged_modulo_tags:
            # Body change (with or without tag change)
            if not goal_bumped:
                errors.append(
                    f"{f}: decided section modified without goal_version "
                    f"bump; immutability-violation"
                )
    return errors
```

Then update `main()` to call the new checks. Find the existing `main()` and replace it with:

```python
def main():
    msg_file = sys.argv[1]
    text = Path(msg_file).read_text()

    trailers = parse_trailers(text)
    errors, seen_keys = validate_trailers(trailers)

    if "Action" not in seen_keys:
        errors.append("missing required trailer Action")
        action = None
    else:
        action = seen_keys["Action"]

    if action is not None and action in ALLOWED_ACTIONS:
        errors.extend(check_required_trailers(action, seen_keys))
        files = staged_files()
        errors.extend(check_file_whitelist(action, files))
        errors.extend(check_scope(action, files))
        errors.extend(check_immutability(action, files))

    if errors:
        print("commit-msg failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_commit_msg_hook -v`
Expected: All 17 tests pass (5 trailer + 5 file-set + 2 scope + 5 immutability).

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 172 tests / OK` (165 + 7 = 172 — slightly higher than the spec's ~162 estimate because the test plan padded fixture counts conservatively).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git commit -m "feat(hooks): commit-msg scope + decided-section immutability checks"
```

---

## Spec coverage check

| Spec section | Requirement | Implemented in |
|---|---|---|
| §1 in-scope item 1 | `harness/cli.py` with init subcommand | Task 2 |
| §1 in-scope item 2 | `workspace_template/harness.toml` (commented) | Task 1 |
| §1 in-scope item 3 | `workspace_template/seed_doc.md` | Task 1 |
| §1 in-scope item 4 | `workspace_template/.gitignore` | Task 1 |
| §1 in-scope item 5 | `workspace_template/hooks/pre-commit` | Tasks 3 + 4 |
| §1 in-scope item 6 | `workspace_template/hooks/commit-msg` | Tasks 5 + 6 + 7 |
| §1 in-scope item 7 | Parity test + fixture-driven hook tests | Tasks 3-7 |
| §2.1 CLI module signature | `main()`, `cmd_init(dir, reactivate)`, `__main__.py` | Task 2 |
| §3.1 harness init behavior | empty/nonexistent target → scaffold + init + commit; non-empty → reject; --reactivate → reconfigure only | Task 2 (all 7 tests cover the matrix) |
| §3.2 harness.toml schema | [models] with dotted-tables, [run], [claim_graph] | Task 1 |
| §3.3 seed_doc.md content | EMPTY/STUB/DRAFTED documentation | Task 1 |
| §3.4 .gitignore content | Six required patterns | Task 1 |
| §3.5 pre-commit checks | cl/at schema, cross-field, citation resolution | Tasks 3 + 4 |
| §3.5 inline-duplication | Verbatim copy of dataclasses + validators in the hook | Task 3 (Step 4 code block) |
| §3.5 parity test | tests/test_hook_parity.py with 3 method-level tests over ~24 fixtures | Task 3 |
| §3.6 commit-msg trailer schema | Closed vocab for Action/Reason; regex for Variant/Round/Reviewer | Task 5 |
| §3.6 required-trailers matrix | Per-Action required set enforced | Task 5 |
| §3.6 file-set whitelist | Per-Action allowed-file globs | Task 6 |
| §3.6 scope check | Action:merge ≤ patch_max_sections doc files | Task 7 |
| §3.6 immutability check | Decided section body change ⇒ requires goal_version bump; tag-only ⇒ allowed iff registry-sync | Task 7 |
| §4 test plan | All test methods and counts | Tasks 1-7 |
| §8 success criteria | SC 1-8 mapped to tests | All tasks |

All in-scope spec requirements have a task.

**Spec items deferred outside this plan (explicitly out of scope per §1 / §10):**
- `harness run`, `harness resume` subcommands — sub-projects 4 + 6.
- PreToolUse hook — v0.1.
- PATH detection for CLI tools — sub-project 4.
- Full evidence frontmatter schema validation — sub-project 3 (orchestrator evidence-ledger writer owns the schema).
- `harness status`, `harness brief` — v0.1.
- PyPI release — post-v0.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", or "implement later" entries in plan body.
- Function names used across tasks match definitions exactly:
  - `validate_claim_dict`, `validate_attack_dict`, `validate_claim_decision_id_resolution`, `validate_claim_position_not_vacuous`, `validate_claim_position_not_alias` (Task 3 hook) — referenced by Task 3 parity tests with identical names.
  - `cite_resolution_check` (Task 4) — referenced by Task 4 main() update.
  - `parse_trailers`, `validate_trailers`, `check_required_trailers`, `staged_files`, `check_file_whitelist`, `check_scope`, `check_immutability` (Tasks 5-7) — internal to the hook, no cross-file references.
  - `cmd_init`, `main` (Task 2 CLI) — referenced by `__main__.py` and tests.
- Module docstring of the pre-commit hook (Task 3) advertises citation resolution as "added in Task 4" — accurate.
- Closed-vocab enums (ALLOWED_ACTIONS, ALLOWED_REASONS) are introduced in Task 5; all tests in Tasks 5-7 use values from these sets.
- Field-name consistency: `decision_id`, `section_id`, `claim_type`, `position`, `evidence_ids`, `out_of_scope_rationale`, `proposed_decision`, `target_claim_id`, `target_decision_id`, `kind`, `scope`, `from`, `to`, `confidence`, `rationale` — all match the existing `harness/claim_graph.py` dataclasses.
- Test helper names (`_scaffold_workspace`, `_write_msg`, `_run_hook`, `_stage_all`, `_stage_file`, `_write_cl`, `_write_at`, `_write_evidence`, `_write_section`, `_commit_initial_section`, `_modify_section`, `_stage`) — each is defined in the test file it's used in; no cross-file imports.
- Filesystem paths in hook (`derived/decisions.json`, `derived/canonical_slug_registry.json`, `evidence/ev-*.md`, `harness.toml`, `goal.toml`) — all match the workspace layout established in Task 1 + parent design §1.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-24-workspace-bootstrap-and-hooks.md`.
