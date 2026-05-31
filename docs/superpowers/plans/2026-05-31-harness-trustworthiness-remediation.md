# Harness Trustworthiness Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a freshly-`init`'d workspace able to run a real round without crashing and with agents that can actually see their work — bootstrap the derived decision cache, seed variant docs, give agents on-disk context pointers (+ run the CLI in the workspace), convert commit/hook failures into rejection records (with a clean-worktree safety rail), fail loud on bad data, and maintain the canonical slug registry.

**Architecture:** A new `harness/bootstrap.py` owns init-time and run-time setup (decision-cache rebuild from `goal.toml`, variant-doc seeding from `seed_doc.md`, empty-registry baseline, clean-worktree guard). `context.py` builders gain ordered on-disk pointer sections; `spawn.py` runs the CLI with `cwd=workspace_root`. `round_ledger`'s commit helpers capture stderr; `run_round` captures a round-start SHA and wraps every commit so a hook rejection resets the round (`git reset --hard` is made safe by the clean-worktree guard) and records `hook-rejected`. Materialization fails loud instead of silently skipping, and the orchestrator appends authored position slugs to the registry via an `Action: registry-sync` commit.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `tomllib`, `subprocess`, `re`, `pathlib`, `shutil`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-05-31-harness-trustworthiness-remediation-design.md`

---

## Conventions

- Full suite: `python3 -m unittest discover tests/`. One file: `python3 -m unittest tests.test_bootstrap -v`.
- Commit with the harness identity; end every body with the co-author trailer:
  ```bash
  git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf '<subject>\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
  ```
- All work on `main` (no remote; established session pattern).
- Many orchestrator tests reuse helper factories in `tests/test_orchestrator_round.py` (`_planner_ok`, `_designer_ok`, `_reviewer_ok`, `_verifier_c_ok`, `_harness_config`/scaffold). Read that file before writing orchestrator tests and reuse those helpers.

---

## Task 1: bootstrap.py — decision cache, empty registry, clean-worktree guard

**Files:**
- Create: `harness/bootstrap.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests** — Create `tests/test_bootstrap.py`:

```python
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import bootstrap

REPO_ROOT = Path(__file__).resolve().parent.parent
GOAL_TOML = """\
[goal]
title = "T"
description = "D"
goal_version = "g-01"

[[decision]]
id = "retry-policy"
question = "How to retry?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "old-thing"
question = "Gone?"
status = "retired"
introduced_at = "g-01"
"""


class RebuildDecisionsCacheTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        (self.td / "goal.toml").write_text(GOAL_TOML)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_writes_all_decisions_from_goal_toml(self):
        bootstrap.rebuild_decisions_cache(self.td)
        data = json.loads((self.td / "derived" / "decisions.json").read_text())
        self.assertIn("retry-policy", data["decisions"])
        self.assertIn("old-thing", data["decisions"])
        self.assertEqual(
            data["decisions"]["retry-policy"]["question"], "How to retry?")
        self.assertEqual(
            data["decisions"]["retry-policy"]["status"], "open")

    def test_idempotent_overwrite(self):
        bootstrap.rebuild_decisions_cache(self.td)
        bootstrap.rebuild_decisions_cache(self.td)  # no raise
        data = json.loads((self.td / "derived" / "decisions.json").read_text())
        self.assertEqual(len(data["decisions"]), 2)

    def test_missing_goal_toml_writes_empty(self):
        (self.td / "goal.toml").unlink()
        bootstrap.rebuild_decisions_cache(self.td)
        data = json.loads((self.td / "derived" / "decisions.json").read_text())
        self.assertEqual(data["decisions"], {})


class EnsureEmptyRegistryTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_creates_when_absent(self):
        bootstrap.ensure_empty_registry(self.td)
        p = self.td / "derived" / "canonical_slug_registry.json"
        self.assertTrue(p.exists())
        json.loads(p.read_text())  # parses

    def test_does_not_overwrite_existing(self):
        p = self.td / "derived" / "canonical_slug_registry.json"
        p.parent.mkdir(parents=True)
        p.write_text('{"sentinel": true}')
        bootstrap.ensure_empty_registry(self.td)
        self.assertIn("sentinel", p.read_text())


class AssertCleanWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        subprocess.check_call(["git", "init", "-q"], cwd=self.td)
        (self.td / "a.txt").write_text("x")
        subprocess.check_call(["git", "-C", str(self.td), "add", "."])
        subprocess.check_call(
            ["git", "-C", str(self.td), "-c", "user.email=h@l",
             "-c", "user.name=h", "commit", "-q", "-m", "init"])

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_clean_passes(self):
        bootstrap.assert_clean_worktree(self.td)  # no raise

    def test_dirty_untracked_raises(self):
        (self.td / "stray.txt").write_text("oops")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.assert_clean_worktree(self.td)

    def test_dirty_modified_raises(self):
        (self.td / "a.txt").write_text("changed")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.assert_clean_worktree(self.td)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_bootstrap -v`
Expected: FAIL — `No module named 'harness.bootstrap'`.

- [ ] **Step 3: Implement `harness/bootstrap.py`**

```python
"""Init-time and run-time workspace setup for the Design Doc Evolution Harness.

- rebuild_decisions_cache: regenerate the derived decision cache from goal.toml.
- ensure_empty_registry: create the persisted append-only canonical slug
  registry baseline if absent.
- seed_variant_docs: seed each active variant's document from seed_doc.md.
- assert_clean_worktree: refuse to operate on a dirty worktree (safety rail for
  the round-reset path). See spec
  docs/superpowers/specs/2026-05-31-harness-trustworthiness-remediation-design.md.
"""
from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path


class DirtyWorktreeError(RuntimeError):
    """Raised when the workspace has uncommitted changes at run/round start."""


def rebuild_decisions_cache(workspace_root: Path) -> None:
    """Regenerate derived/decisions.json from goal.toml's [[decision]] array.

    Deterministic, idempotent overwrite. derived/ is gitignored; both the
    context builders and the pre-commit hook read this file from the working
    tree, so it need not be committed.
    """
    goal_path = workspace_root / "goal.toml"
    decisions: dict[str, dict] = {}
    if goal_path.exists():
        try:
            data = tomllib.loads(
                goal_path.read_text(encoding="utf-8", errors="replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        for d in data.get("decision", []) or []:
            d_id = d.get("id")
            if not isinstance(d_id, str) or not d_id:
                continue
            decisions[d_id] = {
                "id": d_id,
                "question": d.get("question", ""),
                "status": d.get("status", "open"),
                "introduced_at": d.get("introduced_at", ""),
            }
    out_dir = workspace_root / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "decisions.json").write_text(
        json.dumps({"decisions": decisions}, indent=2, sort_keys=True))


def ensure_empty_registry(workspace_root: Path) -> None:
    """Create derived/canonical_slug_registry.json as an empty registry if it
    does not already exist. The registry is persisted append-only state (it
    carries alias history) and must never be clobbered."""
    p = workspace_root / "derived" / "canonical_slug_registry.json"
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({}, indent=2, sort_keys=True))


def assert_clean_worktree(workspace_root: Path) -> None:
    """Raise DirtyWorktreeError if the worktree has any modified/staged/
    untracked non-ignored path. The round-reset path uses `git reset --hard`,
    which is only safe when the tree is known clean at round start."""
    out = subprocess.run(
        ["git", "-C", str(workspace_root), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if out.stdout.strip():
        raise DirtyWorktreeError(
            "workspace has uncommitted changes — commit or discard before "
            f"running:\n{out.stdout.rstrip()}")
```

(Note: `seed_variant_docs` is added in Task 2, not here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_bootstrap -v`
Expected: PASS (RebuildDecisionsCacheTest, EnsureEmptyRegistryTest, AssertCleanWorktreeTest).

- [ ] **Step 5: Commit**

```bash
git add harness/bootstrap.py tests/test_bootstrap.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(bootstrap): decision-cache rebuild, empty registry, clean-worktree guard\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: bootstrap.py — seed_variant_docs

**Files:**
- Modify: `harness/bootstrap.py` (append)
- Test: `tests/test_bootstrap.py` (append)

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_bootstrap.py`:

```python
class SeedVariantDocsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        (self.td / "seed_doc.md").write_text("<!-- seed -->\n")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_seeds_n_variants(self):
        created = bootstrap.seed_variant_docs(self.td, 2)
        self.assertEqual(created, [
            "variants/nodes/v-001/doc/00-overview.md",
            "variants/nodes/v-002/doc/00-overview.md",
        ])
        body = (self.td / "variants" / "nodes" / "v-001" / "doc"
                / "00-overview.md").read_text()
        self.assertIn('section_id = "overview"', body)
        self.assertIn("<!-- seed -->", body)

    def test_idempotent_when_doc_exists(self):
        bootstrap.seed_variant_docs(self.td, 1)
        # A doc now exists for v-001; second call must not re-seed it.
        created = bootstrap.seed_variant_docs(self.td, 1)
        self.assertEqual(created, [])

    def test_missing_seed_doc_is_noop(self):
        (self.td / "seed_doc.md").unlink()
        self.assertEqual(bootstrap.seed_variant_docs(self.td, 2), [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_bootstrap.SeedVariantDocsTest -v`
Expected: FAIL — `module 'harness.bootstrap' has no attribute 'seed_variant_docs'`.

- [ ] **Step 3: Implement** — Append to `harness/bootstrap.py`:

```python
def seed_variant_docs(workspace_root: Path, variant_count: int) -> list[str]:
    """Seed each active variant (v-001..v-{variant_count:03d}) that has no doc
    yet with seed_doc.md's body as a single overview section. Returns the list
    of relative paths created (empty if nothing was seeded). No-op when
    seed_doc.md is absent."""
    seed_path = workspace_root / "seed_doc.md"
    if not seed_path.exists():
        return []
    seed_body = seed_path.read_text(encoding="utf-8", errors="replace")
    created: list[str] = []
    for n in range(1, variant_count + 1):
        variant_id = f"v-{n:03d}"
        doc_dir = workspace_root / "variants" / "nodes" / variant_id / "doc"
        if doc_dir.exists() and any(doc_dir.glob("*.md")):
            continue  # already has a document; do not re-seed
        doc_dir.mkdir(parents=True, exist_ok=True)
        rel = f"variants/nodes/{variant_id}/doc/00-overview.md"
        frontmatter = (
            "+++\n"
            'section_id = "overview"\n'
            'tags = []\n'
            "+++\n\n"
        )
        (workspace_root / rel).write_text(frontmatter + seed_body)
        created.append(rel)
    return created
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_bootstrap -v`
Expected: PASS (all bootstrap tests).

- [ ] **Step 5: Commit**

```bash
git add harness/bootstrap.py tests/test_bootstrap.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(bootstrap): seed_variant_docs from seed_doc.md\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: cli.py init — bootstrap the derived cache + registry baseline

**Files:**
- Modify: `harness/cli.py` (`cmd_init`)
- Test: `tests/test_cli_init.py`

`cmd_init` must, after copying the template and `git init` but **before** the scaffold commit, generate `derived/decisions.json` and the empty registry, and force-add the registry so the persisted baseline is committed. (decisions.json stays gitignored/uncommitted — it is a rebuildable cache.)

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_cli_init.py` (it already scaffolds via the CLI; reuse its style):

```python
class InitBootstrapsDerivedTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _init(self):
        from harness import cli
        rc = cli.cmd_init(self.ws, reactivate=False)
        self.assertEqual(rc, 0)

    def test_decisions_cache_has_seed_decisions(self):
        self._init()
        data = json.loads(
            (self.ws / "derived" / "decisions.json").read_text())
        self.assertIn("retry-policy", data["decisions"])

    def test_registry_baseline_committed(self):
        self._init()
        tracked = subprocess.check_output(
            ["git", "-C", str(self.ws), "ls-files",
             "derived/canonical_slug_registry.json"]).decode().strip()
        self.assertEqual(tracked, "derived/canonical_slug_registry.json")
```

(Ensure `json`, `subprocess`, `shutil`, `tempfile`, `Path` are imported at the top of the test file; add any missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cli_init.InitBootstrapsDerivedTest -v`
Expected: FAIL — decisions.json missing / registry not tracked.

- [ ] **Step 3: Implement** — In `harness/cli.py`, add the import at top:

```python
from harness import bootstrap
```

In `cmd_init`, after the template copy loop and `git init` + `git config core.hooksPath` but before the `git add .` / commit, insert the bootstrap calls. Concretely, replace the existing commit block (the `try:` that runs `git init`, config, `git add .`, commit) so the bootstrap runs between `git init`/config and `git add .`:

```python
    commit_msg = "harness: scaffold workspace\n\nAction: init\n"
    try:
        subprocess.check_call(["git", "init", "-q"], cwd=target_dir)
        subprocess.check_call(
            ["git", "config", "core.hooksPath", "hooks/"],
            cwd=target_dir,
        )
        # Bootstrap derived state before the scaffold commit:
        #  - decisions.json: rebuildable cache (gitignored; not committed)
        #  - canonical_slug_registry.json: persisted append-only baseline
        bootstrap.rebuild_decisions_cache(target_dir)
        bootstrap.ensure_empty_registry(target_dir)
        subprocess.check_call(["git", "add", "."], cwd=target_dir)
        # Force-add the registry (derived/ is gitignored) so its baseline is
        # tracked. decisions.json stays ignored — it is rebuilt each run.
        subprocess.check_call(
            ["git", "-C", str(target_dir), "add", "-f",
             "derived/canonical_slug_registry.json"])
        subprocess.check_call(
            ["git",
             "-c", "user.email=harness@localhost",
             "-c", "user.name=harness",
             "commit", "-q", "-m", commit_msg],
            cwd=target_dir,
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        print(f"harness init: git step failed: {exc}", file=sys.stderr)
        return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cli_init -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add harness/cli.py tests/test_cli_init.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(cli): init bootstraps derived decision cache + registry baseline\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: commit-msg hook — `hook-rejected` Action

**Files:**
- Modify: `workspace_template/hooks/commit-msg`
- Test: `tests/test_commit_msg_hook.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_commit_msg_hook.py`:

```python
class HookRejectedActionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_hook_rejected_with_required_trailers_passes(self):
        rej = self.ws / "rejections" / "rj-000001.md"
        rej.parent.mkdir(parents=True, exist_ok=True)
        rej.write_text("+++\n+++\nbody\n")
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "rejections/rj-000001.md"])
        msg = _write_msg(self.ws,
            "chore: hook-rejected for round-000002 v-001\n\n"
            "Action: hook-rejected\nVariant: v-001\n"
            "Round: round-000002\nReason: hook-rejected\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_hook_rejected_missing_reason_rejects(self):
        msg = _write_msg(self.ws,
            "chore: hook-rejected\n\n"
            "Action: hook-rejected\nVariant: v-001\nRound: round-000002\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Reason", result.stderr)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_commit_msg_hook.HookRejectedActionTest -v`
Expected: FAIL — `Action 'hook-rejected' not in allowed set`.

- [ ] **Step 3: Implement** — In `workspace_template/hooks/commit-msg`:

Add `"hook-rejected"` to `ALLOWED_ACTIONS` and to `ALLOWED_REASONS`.

Add to `TRAILER_REQUIREMENTS` (after the `"score-regression"` entry):
```python
    "hook-rejected": {"Variant", "Round", "Reason"},
```

Add to `ACTION_FILE_WHITELIST` (after the `"score-regression"` entry):
```python
    "hook-rejected": ["rejections/rj-*.md", "actions.jsonl"],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_commit_msg_hook -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add workspace_template/hooks/commit-msg tests/test_commit_msg_hook.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(hooks): hook-rejected Action for commit-failure rejections\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: round_ledger — stderr-capturing commits, hook-rejected reason, registry-sync helper

**Files:**
- Modify: `harness/round_ledger.py`
- Test: `tests/test_round_ledger.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_round_ledger.py`:

```python
class CommitStderrCaptureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_failed_commit_raises_with_stderr(self):
        # Commit with no staged changes fails; stderr must be captured on the
        # raised CalledProcessError (not sent to the terminal).
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            round_ledger._git_commit(self.ws, "subject\n\nAction: init\n")
        self.assertIsNotNone(ctx.exception.stderr)


class CommitRegistrySyncTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_registry_sync_commit(self):
        reg = self.ws / "derived" / "canonical_slug_registry.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text('{"retry-policy": {"canonical": ["expo"], "aliases": {}}}')
        (self.ws / "actions.jsonl").touch()
        round_ledger.commit_registry_sync(self.ws)
        msg = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"]).decode()
        self.assertIn("Action: registry-sync", msg)
        tracked = subprocess.check_output(
            ["git", "-C", str(self.ws), "ls-files",
             "derived/canonical_slug_registry.json"]).decode().strip()
        self.assertEqual(tracked, "derived/canonical_slug_registry.json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_round_ledger.CommitStderrCaptureTest tests.test_round_ledger.CommitRegistrySyncTest -v`
Expected: FAIL — `_git_commit` raises but `.stderr` is None (check_call doesn't capture); `commit_registry_sync` undefined.

- [ ] **Step 3: Implement** — In `harness/round_ledger.py`:

Replace `_git_commit` and `_git_add` so they capture stderr:

```python
def _git_commit(workspace_root: Path, message: str) -> None:
    """Run git commit; capture stderr so callers can record it on failure."""
    subprocess.run(
        ["git",
         "-c", "user.email=harness@localhost",
         "-c", "user.name=harness",
         "commit", "-q", "-m", message],
        cwd=workspace_root,
        capture_output=True, text=True, check=True,
    )


def _git_add(workspace_root: Path, *paths: str) -> None:
    if not paths:
        return
    subprocess.run(
        ["git", "-C", str(workspace_root), "add", "-f", *paths],
        capture_output=True, text=True, check=True,
    )
```

(`subprocess.run(..., check=True)` raises `CalledProcessError` with `.stderr` populated because `capture_output=True`.)

Add `"hook-rejected"` to the `_ALLOWED_REASONS` frozenset.

Add a `commit_registry_sync` helper near the other commit helpers:

```python
def commit_registry_sync(workspace_root: Path) -> None:
    """Stage derived/canonical_slug_registry.json + actions.jsonl (force-add,
    since derived/ is gitignored) and commit with Action: registry-sync."""
    _git_add(
        workspace_root,
        "derived/canonical_slug_registry.json", "actions.jsonl",
    )
    message = (
        "feat(harness): sync canonical slug registry\n\n"
        "Action: registry-sync\n"
    )
    _git_commit(workspace_root, message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_round_ledger -v`
Expected: PASS (all prior + new). The `check_call`→`run(check=True)` change keeps the raise-on-nonzero contract, so existing callers are unaffected.

- [ ] **Step 5: Commit**

```bash
git add harness/round_ledger.py tests/test_round_ledger.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(round_ledger): stderr-capturing commits, hook-rejected reason, registry-sync\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: spawn.py — run the CLI with cwd=workspace_root

**Files:**
- Modify: `harness/spawn.py` (`_run_with_heartbeat`, `spawn_role`)
- Test: `tests/test_spawn.py`

- [ ] **Step 1: Write the failing test** — Append to `tests/test_spawn.py` (reuse its existing imports/fixtures; it already patches subprocess via a fake CLI). Add a test that asserts `Popen` receives `cwd=workspace_root`:

```python
class SpawnCwdTest(unittest.TestCase):
    def test_run_with_heartbeat_passes_cwd(self):
        import harness.spawn as spawn_mod
        captured = {}
        real_popen = spawn_mod.subprocess.Popen

        class _FakeProc:
            def __init__(self, *a, **kw):
                captured["cwd"] = kw.get("cwd")
                self.stdin = __import__("io").BytesIO()
                self.stdout = __import__("io").BytesIO(b'{}')
                self.stderr = __import__("io").BytesIO(b'')
                self.returncode = 0
            def poll(self): return 0
            def wait(self, timeout=None): return 0
            def kill(self): pass

        with unittest.mock.patch.object(spawn_mod.subprocess, "Popen",
                                        _FakeProc):
            spawn_mod._run_with_heartbeat(
                ["echo"], "hi", spawn_timeout_seconds=5,
                cwd="/tmp/some-workspace",
            )
        self.assertEqual(captured["cwd"], "/tmp/some-workspace")
```

(Ensure `unittest.mock` is imported in the test file; add `from unittest import mock` or `import unittest.mock` if missing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_spawn.SpawnCwdTest -v`
Expected: FAIL — `_run_with_heartbeat` has no `cwd` parameter (TypeError).

- [ ] **Step 3: Implement** — In `harness/spawn.py`:

Add a `cwd` parameter to `_run_with_heartbeat` and pass it to `Popen`:

```python
def _run_with_heartbeat(
    cmd: list[str],
    stdin_text: str,
    spawn_timeout_seconds: int,
    silence_threshold_seconds: int = 90,
    cwd: str | Path | None = None,
) -> _RunResult:
```

In the `Popen(...)` call, add `cwd=str(cwd) if cwd is not None else None`:

```python
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
            cwd=str(cwd) if cwd is not None else None,
        )
```

In `spawn_role`, pass `cwd=workspace_root` at **every** `_run_with_heartbeat` call site (pass 1, the non-zero-exit retry, and the validate-retry pass). Each call becomes e.g.:

```python
    result1 = _run_with_heartbeat(
        cmd, stdin_text, spawn_timeout, silence_threshold,
        cwd=workspace_root,
    )
```

Find all call sites: `grep -n "_run_with_heartbeat(" harness/spawn.py` and add `cwd=workspace_root` to each (there are three).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_spawn -v`
Expected: PASS (existing + new). `Path` may need importing in spawn.py for the type hint — it is already imported there; verify with `grep -n "from pathlib" harness/spawn.py`.

- [ ] **Step 5: Commit**

```bash
git add harness/spawn.py tests/test_spawn.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(spawn): run CLI agents with cwd=workspace_root so context pointers resolve\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: context.py + prompts — on-disk pointers, goal title/description, read-instruction

**Files:**
- Modify: `harness/context.py` (designer/reviewer/verifier_c builders + a shared helper)
- Modify: `harness/orchestrator.py` (DESIGNER_PROMPT, REVIEWER_PROMPT, VERIFIER_C_PROMPT)
- Test: `tests/test_context.py`, `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_context.py` (reuse its existing scaffold/imports):

```python
class ContextPointersTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)  # reuse this file's existing helper
        # rebuild the decision cache so goal title/description + decisions load
        from harness import bootstrap
        bootstrap.rebuild_decisions_cache(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_context_has_pointers_and_goal(self):
        out = context.build_designer_context(self.ws, "round-000001", "v-001")
        self.assertIn("Read these first", out)
        self.assertIn("goal.toml", out)
        self.assertIn("variants/nodes/v-001/doc/", out)
        self.assertIn("Example: API resilience design", out)  # goal title

    def test_verifier_c_context_points_at_patch_and_evidence(self):
        out = context.build_verifier_c_context(self.ws, "round-000001", "v-001")
        self.assertIn("Read these first", out)
        self.assertIn("rounds/round-000001/patch.diff", out)

    def test_reviewer_context_points_at_patch(self):
        out = context.build_reviewer_context(self.ws, "round-000001", "v-001")
        self.assertIn("rounds/round-000001/patch.diff", out)
```

And append to `tests/test_orchestrator_round.py`:

```python
class PromptReadInstructionTest(unittest.TestCase):
    def test_prompts_compel_reading(self):
        for p in (orchestrator.DESIGNER_PROMPT, orchestrator.REVIEWER_PROMPT,
                  orchestrator.VERIFIER_C_PROMPT):
            self.assertIn("Read these first", p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_context.ContextPointersTest tests.test_orchestrator_round.PromptReadInstructionTest -v`
Expected: FAIL — no pointer section / no goal title / prompts lack the instruction.

- [ ] **Step 3: Implement context helpers** — In `harness/context.py`, add a shared loader + pointer renderer near `_header`:

```python
def _load_goal_meta(workspace_root: Path) -> tuple[str, str]:
    """Return (title, description) from goal.toml's [goal] table."""
    p = workspace_root / "goal.toml"
    if not p.exists():
        return ("", "")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return ("", "")
    g = data.get("goal", {})
    return (g.get("title", ""), g.get("description", ""))


def _render_goal_and_pointers(workspace_root: Path, title: str,
                              description: str, pointers: list[str]) -> str:
    lines = ["## Goal", "", f"**{title}**", "", description, "",
             "## Read these first (on disk)", "",
             "Read every path below before answering; the summary tables are "
             "an index, not a substitute for the source.", ""]
    for ptr in pointers:
        lines.append(f"- `{ptr}`")
    lines.append("")
    return "\n".join(lines)
```

Then in each builder, insert the goal+pointers block right after the header. For **`build_designer_context`** (after `out = [_header(...), ""]`):

```python
    title, description = _load_goal_meta(workspace_root)
    out.append(_render_goal_and_pointers(
        workspace_root, title, description, [
            "goal.toml",
            f"variants/nodes/{variant_id}/doc/",
            f"rounds/{round_id}/scratch/planner.json",
            "evidence/",
        ]))
```

For **`build_reviewer_context`**:

```python
    title, description = _load_goal_meta(workspace_root)
    out.append(_render_goal_and_pointers(
        workspace_root, title, description, [
            f"rounds/{round_id}/patch.diff",
            "evidence/",
            f"variants/nodes/{variant_id}/doc/",
        ]))
```

For **`build_verifier_c_context`** (replace its minimal body):

```python
def build_verifier_c_context(workspace_root: Path, round_id: str,
                            variant_id: str) -> str:
    decisions = _load_decisions(workspace_root)
    goal_version = _load_goal_version(workspace_root)
    title, description = _load_goal_meta(workspace_root)
    out = [_header("verifier_c", round_id, variant_id, goal_version), ""]
    out.append(_render_goal_and_pointers(
        workspace_root, title, description, [
            f"rounds/{round_id}/patch.diff",
            "evidence/",
        ]))
    out.append(_render_registered_decisions(decisions))
    return "\n".join(out)
```

(`tomllib` is already imported in context.py — verify with `grep -n "import tomllib" harness/context.py`.)

- [ ] **Step 4: Implement prompt instruction** — In `harness/orchestrator.py`, append to each of `DESIGNER_PROMPT`, `REVIEWER_PROMPT`, `VERIFIER_C_PROMPT` (inside the parenthesized string concatenation, before the final `"Output ONLY valid JSON."`) a fragment:

```python
    "Before answering, read every path listed under 'Read these first (on "
    "disk)' in the CONTEXT above; do not rely on the summary tables alone. "
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_context tests.test_orchestrator_round.PromptReadInstructionTest -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite (regression guard for context output changes)**

Run: `python3 -m unittest discover tests/`
Expected: PASS. If any existing context test asserted exact full-output equality, update it to match the new sections.

- [ ] **Step 7: Commit**

```bash
git add harness/context.py harness/orchestrator.py tests/test_context.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(context): on-disk pointers + goal meta in agent context; prompts compel reading\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: materialize patch.diff + fail-loud validation

**Files:**
- Modify: `harness/orchestrator.py` (`_materialize_designer_output`, `_materialize_reviewer_attacks`, `validate_designer_json`)
- Test: `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write the failing tests** — Append to `tests/test_orchestrator_round.py`:

```python
class MaterializeFailLoudTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_malformed_evidence_id_raises(self):
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [{"id": "../etc/passwd"}],
                  "claims": []}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", parsed)

    def test_duplicate_claim_id_raises(self):
        c = {"id": "cl-000001", "decision_id": "retry-policy",
             "section_id": "retry-policy", "claim_type": "decision",
             "position": "expo", "evidence_ids": []}
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [],
                  "claims": [dict(c), dict(c)]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", parsed)


class ValidateDesignerStrictTest(unittest.TestCase):
    def test_non_string_patch_diff_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": 123,
                "evidence": [], "claims": []})

    def test_claim_evidence_id_not_in_round_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": "",
                "evidence": [{"id": "ev-000001", "confidence": "high",
                              "citations": [], "claim": "c", "excerpt": "e"}],
                "claims": [{"id": "cl-000001", "decision_id": "d",
                            "section_id": "d", "claim_type": "decision",
                            "position": "p", "evidence_ids": ["ev-999999"]}]})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_orchestrator_round.MaterializeFailLoudTest tests.test_orchestrator_round.ValidateDesignerStrictTest -v`
Expected: FAIL — current code silently skips malformed ids / shallow validator.

- [ ] **Step 3: Implement patch.diff write + fail-loud materialize** — In `_materialize_designer_output`:

(a) Replace the evidence silent-skip:
```python
        ev_id = ev.get("id", "")
        if not ev_id or not _ID_RE.match(ev_id):
            raise RuntimeError(
                f"materialize: malformed/unsafe evidence id {ev_id!r}")
```

(b) Replace the claims silent-skip and add duplicate detection. Before the claims loop add `seen_claim_ids: set[str] = set()`, then:
```python
        cl_id = claim.get("id", "")
        if not cl_id or not _ID_RE.match(cl_id):
            raise RuntimeError(
                f"materialize: malformed/unsafe claim id {cl_id!r}")
        if cl_id in seen_claim_ids:
            raise RuntimeError(
                f"materialize: duplicate claim id {cl_id!r} in round")
        seen_claim_ids.add(cl_id)
```

(c) After the patch_diff apply block (whether or not patch_diff was empty), write the stable patch.diff pointer file:
```python
    # Write the round's patch.diff pointer (always, even when empty) so
    # Reviewer/Verifier-C CONTEXT.md can point at a stable on-disk file.
    round_dir = workspace_root / "rounds" / parsed.get("round", "")
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "patch.diff").write_text(patch_diff)
```
Place this right before `return materialized, section_paths, ...`. (Note: `parsed["round"]` is the round id; the validator guarantees it is present.)

In `_materialize_reviewer_attacks`, replace its attack silent-skip the same way:
```python
        at_id = at.get("id", "")
        if not at_id or not _ID_RE.match(at_id):
            raise RuntimeError(
                f"materialize: malformed/unsafe attack id {at_id!r}")
```

- [ ] **Step 4: Implement stricter `validate_designer_json`** — Replace it:

```python
def validate_designer_json(d: dict) -> None:
    for key in ("round", "variant", "patch_diff", "evidence", "claims"):
        if key not in d:
            raise ValueError(f"designer.json missing {key!r}")
    if not isinstance(d["patch_diff"], str):
        raise ValueError("designer.json patch_diff must be a string")
    if not isinstance(d["claims"], list):
        raise ValueError("designer.json claims must be a list")
    if not isinstance(d["evidence"], list):
        raise ValueError("designer.json evidence must be a list")
    evidence_ids = set()
    for ev in d["evidence"]:
        if not isinstance(ev, dict):
            raise ValueError("designer.json evidence item must be an object")
        ev_id = ev.get("id")
        if not isinstance(ev_id, str) or not re.match(r"^ev-\d{6}$", ev_id):
            raise ValueError(
                f"designer.json evidence id invalid: {ev_id!r}")
        for k in ("confidence", "citations", "claim", "excerpt"):
            if k not in ev:
                raise ValueError(
                    f"designer.json evidence {ev_id} missing {k!r}")
        evidence_ids.add(ev_id)
    for c in d["claims"]:
        cg.Claim.from_dict(c)  # slug + required-field checks
        for ref in c.get("evidence_ids", []) or []:
            if ref not in evidence_ids:
                raise ValueError(
                    f"designer.json claim {c.get('id')} cites {ref!r} not in "
                    "this round's evidence")
```

(`re` is already imported in orchestrator.py.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_orchestrator_round -v`
Expected: PASS. Existing happy-path round tests must still pass — their `_designer_ok` payloads carry matching evidence/claim ids; if any existing fixture has a claim citing an id not in its evidence list, fix that fixture to be self-consistent (it should be, per the schema).

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): write rounds/<r>/patch.diff; fail loud on bad ids + strict designer validation\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 9: run_loop — bootstrap at start, rebuild cache after merged rounds

**Files:**
- Modify: `harness/orchestrator.py` (`run_loop`)
- Test: `tests/test_orchestrator_loop.py`

- [ ] **Step 1: Write the failing test** — Append to `tests/test_orchestrator_loop.py`:

```python
class RunLoopBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_seeds_variants_and_cache_at_start(self):
        def fake_run_round(workspace_root, harness_config, round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed", elapsed_seconds=0.01)
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            orchestrator.run_loop(self.ws, _harness_config(),
                                  max_rounds=1, variant_count=2)
        # decision cache bootstrapped
        self.assertTrue((self.ws / "derived" / "decisions.json").exists())
        # variant docs seeded + committed
        self.assertTrue((self.ws / "variants" / "nodes" / "v-001" / "doc"
                         / "00-overview.md").exists())

    def test_aborts_on_dirty_worktree(self):
        from harness import bootstrap
        (self.ws / "stray.txt").write_text("dirty")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            orchestrator.run_loop(self.ws, _harness_config(), max_rounds=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_orchestrator_loop.RunLoopBootstrapTest -v`
Expected: FAIL — no seeding; dirty worktree not checked.

- [ ] **Step 3: Implement** — In `harness/orchestrator.py` add the import:

```python
from harness import bootstrap
```

In `run_loop`, at the very start of the function body (before `loop_start = ...`):

```python
    bootstrap.assert_clean_worktree(workspace_root)
    bootstrap.rebuild_decisions_cache(workspace_root)
    bootstrap.ensure_empty_registry(workspace_root)
    seeded = bootstrap.seed_variant_docs(workspace_root, variant_count)
    if seeded:
        round_ledger._git_add(workspace_root, *seeded)
        round_ledger._git_commit(
            workspace_root,
            "harness: seed variant documents\n\nAction: init\n")
```

(The `max_rounds`/`max_wall_clock_hours` validation that currently sits at the top of `run_loop` should run **before** the clean-worktree assert so a bad-args call still raises `ValueError` first — keep that `if max_rounds is None and max_wall_clock_hours is None: raise ValueError(...)` block as the first statement, then the bootstrap block.)

After each **merged** round, rebuild the decision cache so goal edits/pivots are absorbed. In the loop, after `outcomes.append(outcome)`:

```python
        if outcome.verdict == "merge":
            bootstrap.rebuild_decisions_cache(workspace_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_orchestrator_loop -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_loop.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): run_loop bootstraps derived cache + seeds variants; clean-worktree guard\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 10: run_round — commit-failure recovery (reset → hook-rejected) + fresh-init regression

**Files:**
- Modify: `harness/orchestrator.py` (`run_round`)
- Test: `tests/test_orchestrator_hook_reject.py` (new), `tests/test_orchestrator_round.py` (fresh-init regression)

- [ ] **Step 1: Write the failing tests** — Create `tests/test_orchestrator_hook_reject.py`. Reuse the mock-spawn helpers from `tests/test_orchestrator_round.py` (import them) and force the merge commit to fail by monkeypatching `round_ledger.commit_merge` to raise a `CalledProcessError` with stderr:

All of `_scaffold_workspace`, `_harness_config`, `_planner_ok`, `_designer_ok`, `_reviewer_ok`, `_verifier_c_ok` are module-level functions in `tests/test_orchestrator_round.py` (verified), so import them directly:

```python
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator, round_ledger
from tests.test_orchestrator_round import (
    _scaffold_workspace, _harness_config, _planner_ok, _designer_ok,
    _reviewer_ok, _verifier_c_ok,
)

# A realistic decision claim against a seed decision from the template goal.toml.
_RETRY_CLAIM = {
    "id": "cl-000001", "section_id": "retry-policy",
    "decision_id": "retry-policy", "claim_type": "decision",
    "evidence_ids": [], "assertion": "Use expo-backoff.",
    "position": "expo-backoff",
}
```

The test (note: `_scaffold_workspace` runs the real `harness init`, which after Task 3 already bootstraps `derived/decisions.json` from `goal.toml` — no manual seeding needed):

```python
class HookRejectResetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)  # init bootstraps the decision cache

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_merge_commit_failure_resets_and_records_hook_rejected(self):
        spawns = [_planner_ok(),
                  _designer_ok(claims=[dict(_RETRY_CLAIM)]),
                  _reviewer_ok(), _verifier_c_ok()]
        def boom(*a, **kw):
            raise subprocess.CalledProcessError(
                1, ["git", "commit"], stderr="hook said no")
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=spawns), \
             mock.patch("harness.round_ledger.commit_merge",
                        side_effect=boom):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "hook-rejected")
        # last commit is the hook-rejected rejection, not a merge
        msg = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"]).decode()
        self.assertIn("Action: hook-rejected", msg)
        # rejection body carries the captured stderr
        rj = sorted((self.ws / "rejections").glob("rj-*.md"))[-1].read_text()
        self.assertIn("hook said no", rj)
        # worktree is clean (reset succeeded; no orphaned materialized files)
        st = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st.strip(), "")
```

And append the **fresh-init regression** to `tests/test_orchestrator_round.py` — a round on a workspace whose `derived/decisions.json` was produced ONLY by bootstrap (no hand-seeding) reaches merge:

```python
class FreshInitRoundReachesMergeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        # ONLY the real `harness init` (which, after Task 3, bootstraps
        # derived/decisions.json from goal.toml). NO manual seeding.
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_round_merges_without_manual_decision_seeding(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "Use expo-backoff.",
                 "position": "expo-backoff"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
```

> **Expectation:** this regression **passes** once Tasks 1–3 have landed (init now bootstraps the cache), so by Task 10 it is a green guard rather than a red-first test. Its value is locking the chain: to see the *original* bug, temporarily delete `derived/decisions.json` after scaffold and watch the pre-commit reject the `retry-policy` claim as "not registered." The existing `RunRoundHappyPathTest` hand-seeds `decisions.json`; that still works because the bootstrap writes the same `retry-policy` entry. No existing fixture needs changing for this task — `_designer_ok(claims=[...])` already accepts an explicit claim list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_orchestrator_hook_reject -v`
Expected: FAIL — `run_round` currently lets the `CalledProcessError` propagate (no `hook-rejected` handling), so the test errors rather than asserting `verdict == "hook-rejected"`. (The `FreshInitRoundReachesMergeTest` regression added to `test_orchestrator_round.py` will already be green, since Tasks 1–3 bootstrap the cache — that is expected; it is a guard, see the Expectation note above.)

- [ ] **Step 3: Implement** — In `harness/orchestrator.py` `run_round`:

(a) At the very top of `run_round` (after the `_log("round_start", ...)`), assert clean + capture the start SHA:

```python
    bootstrap.assert_clean_worktree(workspace_root)
    round_start_sha = subprocess.check_output(
        ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
        text=True).strip()
```

(b) Add a helper closure inside `run_round` (near `_reject`) that performs the reset + records a hook-rejected rejection:

```python
    def _commit_reject(exc: subprocess.CalledProcessError) -> RoundOutcome:
        subprocess.run(["git", "-C", str(workspace_root), "reset",
                        "--hard", round_start_sha],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(workspace_root), "clean", "-fd"],
                       capture_output=True, text=True)
        detail = (exc.stderr or "").strip() or "commit failed"
        rj_id = round_ledger.write_rejection(
            workspace_root, round_id, variant_id,
            reason_class="hook-rejected", failed_phase="commit",
            detail=detail)
        _log(workspace_root, "rejection", round_id=round_id, rj_id=rj_id,
             reason_class="hook-rejected", failed_phase="commit")
        round_ledger.commit_rejection(
            workspace_root, action="hook-rejected", round_id=round_id,
            variant_id=variant_id, rj_id=rj_id, reason="hook-rejected")
        _log(workspace_root, "commit", round_id=round_id,
             action="hook-rejected")
        _log(workspace_root, "round_end", round_id=round_id,
             verdict="hook-rejected")
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict="hook-rejected", reason="hook-rejected", rj_id=rj_id,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts)
```

(c) Wrap the Phase 7a/7b/8 commit calls. The cleanest is to wrap the whole commit sequence (register-decision through merge) in a single try/except so any of them triggers the reset:

```python
    try:
        # ---- Phase 7a: register-decision ----  (existing block)
        # ---- Phase 7b: canonicalize ----       (existing block)
        # ---- Phase <registry-sync> ----         (added in Task 11)
        # ---- Phase 8: merge commit ----         (existing block)
        ...
    except subprocess.CalledProcessError as exc:
        return _commit_reject(exc)
```

Indent the existing Phase 7a/7b/8 bodies into this `try`. Keep `_log` calls inside. The `return RoundOutcome(... verdict="merge" ...)` stays at the end of the `try` (after the merge commit). On the success path nothing changes; on any commit failure the except runs the reset + records hook-rejected.

`bootstrap` and `subprocess` are imported (subprocess added in sub-project 5 / Task 9). Confirm `import subprocess` exists at the top of orchestrator.py.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_orchestrator_hook_reject tests.test_orchestrator_round.FreshInitRoundReachesMergeTest -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS. (Existing round tests that hand-seed `derived/decisions.json` still work — bootstrap overwrites with the same data; if a test seeded a custom decision id not in goal.toml, the round still reads it from the cache only if the cache is rebuilt — those tests call `run_round` directly and don't rebuild, so their hand-seeded cache survives. Confirm no regression; if one breaks, it relied on a decision id absent from the template goal.toml and should seed that id into goal.toml or keep its manual cache write.)

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_hook_reject.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): commit-failure resets round to start + records hook-rejected\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 11: run_round — maintain the canonical registry on authoring

**Files:**
- Modify: `harness/orchestrator.py` (`run_round`, between Phase 7b and Phase 8, inside the Task 10 try-block)
- Test: `tests/test_orchestrator_round.py`

- [ ] **Step 1: Write the failing test** — Append to `tests/test_orchestrator_round.py`:

```python
class RegistryMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        from harness import bootstrap
        bootstrap.rebuild_decisions_cache(self.ws)
        bootstrap.ensure_empty_registry(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_authored_position_appended_to_registry(self):
        # The designer authors a decision claim with position 'expo-backoff'
        # under retry-policy; after a merged round the registry contains it.
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "Use expo-backoff.",
                 "position": "expo-backoff"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
        reg = json.loads((self.ws / "derived"
                          / "canonical_slug_registry.json").read_text())
        # 'expo-backoff' is now canonical under retry-policy
        self.assertIn("expo-backoff",
                      reg.get("retry-policy", {}).get("canonical", []))
        # a registry-sync commit exists in this round's history
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B"]).decode()
        self.assertIn("Action: registry-sync", log)
```

The `CanonicalSlugRegistry` dict shape is `{decision_id: {"canonical": [...], "aliases": {...}}}` — confirm via `harness/claim_graph.py` (`ensure_decision` / `to_dict`) and adjust the assertion if the persisted shape differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_orchestrator_round.RegistryMaintenanceTest -v`
Expected: FAIL — registry stays empty; no registry-sync commit.

- [ ] **Step 3: Implement** — In `run_round`, inside the Task 10 try-block, **after** Phase 7b (canonicalize) and **before** Phase 8 (merge), add the registry-sync phase:

```python
        # ---- Phase: registry-sync (append authored position slugs) ----
        reg_path = workspace_root / "derived" / "canonical_slug_registry.json"
        if reg_path.exists():
            registry = cg.CanonicalSlugRegistry.from_dict(
                json.loads(reg_path.read_text()))
        else:
            registry = cg.CanonicalSlugRegistry()
        appended = False
        for claim in designer_result.parsed.get("claims", []) or []:
            if claim.get("claim_type") != "decision":
                continue
            decision_id = claim.get("decision_id")
            position = claim.get("position")
            if not decision_id or not position:
                continue
            try:
                cg.add_canonical_position(registry, decision_id, position)
                appended = True
            except cg.RegistryInvariantError:
                # slug already an alias for this decision — append-only
                # invariant; skip (it is already represented).
                pass
        if appended:
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            reg_path.write_text(json.dumps(
                registry.to_dict(), indent=2, sort_keys=True))
            round_ledger.commit_registry_sync(workspace_root)
            _log(workspace_root, "commit", round_id=round_id,
                 action="registry-sync")
```

(Verify the exception type `add_canonical_position` raises on an alias collision — read `harness/claim_graph.py:442`; if it raises `SchemaError` rather than `RegistryInvariantError`, catch that type instead. Use whatever the function actually raises.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_orchestrator_round.RegistryMaintenanceTest -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover tests/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/orchestrator.py tests/test_orchestrator_round.py
git -c user.email=harness@localhost -c user.name=harness commit -q -m "$(printf 'feat(orchestrator): append authored position slugs to canonical registry (registry-sync)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 12: cross-cutting /code-review

- [ ] **Step 1: Full suite green**

Run: `python3 -m unittest discover tests/`
Expected: OK.

- [ ] **Step 2: Sanity — a real fresh init has the derived cache**

```bash
cd /tmp && rm -rf _ti_probe && PYTHONPATH=/Users/liwen/develop/projects/auto_design_doc python3 -m harness init /tmp/_ti_probe && PYTHONPATH=/Users/liwen/develop/projects/auto_design_doc python3 -c "import json,pathlib; d=json.loads(pathlib.Path('/tmp/_ti_probe/derived/decisions.json').read_text()); print('decisions:', sorted(d['decisions']))" && rm -rf /tmp/_ti_probe
```
Expected: `decisions: ['circuit-breaker-policy', 'rate-limit-policy', 'retry-policy']`.

- [ ] **Step 3: Cross-cutting /code-review pass**

Run `/code-review` over the sub-project commit range (`<sha before Task 1>..HEAD`). Fix Critical findings inline (new commit); record the rest in `TODOS.md`. Mirrors the gate applied to sub-projects 1–5.

---

## Plan self-review

**Spec coverage** (each spec section → task):
- §3.1 rebuild_decisions_cache → Task 1; §3.2 ensure_empty_registry + force-add inventory → Task 1 + Task 3 + Task 5 (registry-sync) ; §3.3 seed_variant_docs → Task 2. ✓
- §4.1 pointers + §4.1a prompt instruction → Task 7; §4.2 patch.diff → Task 8; §4.3 spawn cwd → Task 6. ✓
- §5.0 clean-worktree guard → Task 1 (fn) + Task 9 (run_loop) + Task 10 (run_round); §5.1 round_start_sha → Task 10; §5.2 wrapped commits + reset + ignored-file preservation → Task 10; §5.3 hook vocab → Task 4 (hook) + Task 5 (_ALLOWED_REASONS); §5.4 stderr capture → Task 5. ✓
- §6 registry maintenance + 7a→7b→registry-sync→8 ordering → Task 11 (placed between 7b and 8 inside Task 10's try). ✓
- §7.1 strict validator → Task 8; §7.2 fail-loud materialize → Task 8. ✓
- §8 testing: bootstrap (T1/T2), fresh-init regression (T10), hook-reject + stderr + dirty-abort (T10/T9), context pointers + prompt (T7), spawn cwd (T6), registry (T11), fail-loud (T8), hook vocab (T4). ✓

**Type/name consistency:** `bootstrap.{rebuild_decisions_cache, ensure_empty_registry, seed_variant_docs, assert_clean_worktree, DirtyWorktreeError}`; `round_ledger.{commit_registry_sync, _git_commit, _git_add}` (stderr-capturing); `_run_with_heartbeat(..., cwd=)`; `orchestrator` prompts contain "Read these first"; `Action: hook-rejected`/`registry-sync` consistent across hook, ledger, orchestrator. ✓

**Ordering dependency:** Task 11's registry-sync is inserted **inside** Task 10's try-block between 7b and 8 — Task 10 must land before Task 11. Task 5 (registry-sync helper, stderr commits) precedes Tasks 10/11. Task 4 (hook vocab) precedes Task 10. ✓

**Known fixture risk (flagged, not silent):** several orchestrator tests historically hand-seed `derived/decisions.json` and may author claims against a custom decision id. Tasks 8/10/11 require the designer fixture to use a real seed decision id (`retry-policy`) and self-consistent evidence ids; the implementer must reconcile `_designer_ok`/`_reviewer_ok` accordingly (called out in Task 10 Step 1 note and Task 11 Step 1).
