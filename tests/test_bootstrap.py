import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import bootstrap

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

    def test_malformed_toml_writes_empty(self):
        (self.td / "goal.toml").write_text("[[invalid")
        bootstrap.rebuild_decisions_cache(self.td)
        data = json.loads((self.td / "derived" / "decisions.json").read_text())
        self.assertEqual(data["decisions"], {})

    def test_non_string_id_skipped(self):
        (self.td / "goal.toml").write_text(
            '[[decision]]\nid = 42\nquestion = "q"\nstatus = "open"\n')
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

    def test_non_git_dir_raises(self):
        # A directory outside any git repo: git status must fail, and the
        # guard must fail-closed (NOT treat an undeterminable tree as clean).
        nongit = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, nongit, ignore_errors=True)
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            bootstrap.assert_clean_worktree(nongit)


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
