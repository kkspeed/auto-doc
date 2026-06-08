import json
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from harness import bootstrap
from harness import claim_graph as cg

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

    def test_malformed_toml_raises(self):
        (self.td / "goal.toml").write_text("[[invalid")
        with self.assertRaises(tomllib.TOMLDecodeError):
            bootstrap.rebuild_decisions_cache(self.td)

    def test_non_string_id_raises(self):
        (self.td / "goal.toml").write_text(
            '[goal]\ngoal_version = "g-01"\n'
            '[[decision]]\nid = 42\nquestion = "q"\n'
            'status = "open"\nintroduced_at = "g-01"\n')
        with self.assertRaises(cg.SchemaError):
            bootstrap.rebuild_decisions_cache(self.td)

    def test_missing_goal_version_raises(self):
        (self.td / "goal.toml").write_text(
            '[goal]\ntitle = "t"\n'
            '[[decision]]\nid = "retry-policy"\nquestion = "q"\n'
            'status = "open"\nintroduced_at = "g-01"\n')
        with self.assertRaises(cg.SchemaError):
            bootstrap.rebuild_decisions_cache(self.td)


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

    def test_non_git_dir_raises(self):
        # A directory outside any git repo: git status must fail, and the
        # guard must fail-closed (NOT treat an undeterminable tree as clean).
        nongit = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, nongit, ignore_errors=True)
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.assert_clean_worktree(nongit)


class RecoverWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        subprocess.check_call(["git", "init", "-q"], cwd=self.td)
        # A committed ledger file so we can exercise the modify/restore path.
        doc = self.td / "variants" / "nodes" / "v-001" / "doc"
        doc.mkdir(parents=True)
        (doc / "00-overview.md").write_text("committed\n")
        (self.td / "goal.toml").write_text('goal_version = "g-01"\n')
        subprocess.check_call(["git", "-C", str(self.td), "add", "."])
        subprocess.check_call(
            ["git", "-C", str(self.td), "-c", "user.email=h@l",
             "-c", "user.name=h", "commit", "-q", "-m", "init"])

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _status(self):
        return subprocess.run(
            ["git", "-C", str(self.td), "status", "--porcelain"],
            capture_output=True, text=True).stdout.strip()

    def test_clean_returns_empty(self):
        self.assertEqual(bootstrap.recover_worktree(self.td), [])

    def test_untracked_ledger_file_discarded(self):
        stray = (self.td / "variants" / "nodes" / "v-001" / "doc"
                 / "02-six-week-compression.md")
        stray.write_text("leaked by an llm spawn\n")
        discarded = bootstrap.recover_worktree(self.td)
        self.assertIn(
            "variants/nodes/v-001/doc/02-six-week-compression.md", discarded)
        self.assertFalse(stray.exists())
        self.assertEqual(self._status(), "")

    def test_untracked_ledger_dir_discarded(self):
        new = self.td / "variants" / "nodes" / "v-003" / "doc"
        new.mkdir(parents=True)
        (new / "00-overview.md").write_text("whole new variant tree\n")
        bootstrap.recover_worktree(self.td)
        self.assertFalse((self.td / "variants" / "nodes" / "v-003").exists())
        self.assertEqual(self._status(), "")

    def test_modified_ledger_file_restored(self):
        f = self.td / "variants" / "nodes" / "v-001" / "doc" / "00-overview.md"
        f.write_text("clobbered\n")
        bootstrap.recover_worktree(self.td)
        self.assertEqual(f.read_text(), "committed\n")
        self.assertEqual(self._status(), "")

    def test_staged_ledger_file_restored(self):
        f = self.td / "variants" / "nodes" / "v-001" / "doc" / "00-overview.md"
        f.write_text("clobbered\n")
        subprocess.check_call(["git", "-C", str(self.td), "add", str(f)])
        bootstrap.recover_worktree(self.td)
        self.assertEqual(f.read_text(), "committed\n")
        self.assertEqual(self._status(), "")

    def test_actions_jsonl_discarded(self):
        (self.td / "actions.jsonl").write_text('{"event":"x"}\n')
        bootstrap.recover_worktree(self.td)
        self.assertFalse((self.td / "actions.jsonl").exists())

    def test_untracked_root_stray_discarded(self):
        # The reported case: an agent wrote a non-ledger file at the workspace
        # root. Untracked + not operator config → discard, wherever it lives.
        stray = self.td / "repo_adapter.json"
        stray.write_text('{"leaked":"by an agent spawn"}\n')
        discarded = bootstrap.recover_worktree(self.td)
        self.assertIn("repo_adapter.json", discarded)
        self.assertFalse(stray.exists())
        self.assertEqual(self._status(), "")

    def test_untracked_operator_file_raises_and_preserves_everything(self):
        # A not-yet-committed operator config must NOT be deleted, and the
        # presence of operator dirt makes recovery refuse to touch anything.
        (self.td / ".mcp.json").write_text('{"mcpServers":{}}\n')
        ledger_stray = self.td / "evidence"
        ledger_stray.mkdir()
        (ledger_stray / "ev-000001.md").write_text("x")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.recover_worktree(self.td)
        self.assertTrue((self.td / ".mcp.json").exists())
        self.assertTrue((ledger_stray / "ev-000001.md").exists())

    def test_modified_config_raises(self):
        (self.td / "goal.toml").write_text('goal_version = "g-02"\n')
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.recover_worktree(self.td)

    def test_modified_non_ledger_tracked_file_raises(self):
        # An edit to a tracked file outside the ledger is never auto-clobbered.
        readme = self.td / "README.md"
        readme.write_text("v1\n")
        subprocess.check_call(["git", "-C", str(self.td), "add", "README.md"])
        subprocess.check_call(
            ["git", "-C", str(self.td), "-c", "user.email=h@l",
             "-c", "user.name=h", "commit", "-q", "-m", "add readme"])
        readme.write_text("operator edit\n")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.recover_worktree(self.td)

    def test_non_git_dir_raises(self):
        nongit = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, nongit, ignore_errors=True)
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.recover_worktree(nongit)


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
        created = bootstrap.seed_variant_docs(self.td, 1)
        self.assertEqual(created, [])

    def test_missing_seed_doc_is_noop(self):
        (self.td / "seed_doc.md").unlink()
        self.assertEqual(bootstrap.seed_variant_docs(self.td, 2), [])

    def test_frontmatter_parses_as_toml(self):
        import tomllib
        bootstrap.seed_variant_docs(self.td, 1)
        text = (self.td / "variants" / "nodes" / "v-001" / "doc"
                / "00-overview.md").read_text()
        fm = text.split("+++")[1]
        meta = tomllib.loads(fm)
        self.assertEqual(meta["section_id"], "overview")
        self.assertEqual(meta["tags"], [])
        self.assertEqual(meta["created_round"], "round-000000")

    def test_seeds_into_existing_empty_doc_dir(self):
        # doc dir exists but has no *.md -> not yet seeded, so seed it.
        (self.td / "variants" / "nodes" / "v-001" / "doc").mkdir(parents=True)
        created = bootstrap.seed_variant_docs(self.td, 1)
        self.assertEqual(created, ["variants/nodes/v-001/doc/00-overview.md"])
