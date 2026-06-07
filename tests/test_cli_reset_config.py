"""End-to-end tests for `harness reset` and `harness commit-config`.

Each test scaffolds a real workspace via `harness init` (which sets hooksPath),
so the real commit-msg hook runs — commit-config's commits are validated by the
actual hook, not a mock.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_harness(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    return subprocess.run(
        ["python3", "-m", "harness", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def _git(ws, *args, check=True):
    return subprocess.run(["git", "-C", str(ws), *args],
                          check=check, capture_output=True, text=True)


def _commit_no_verify(ws, msg):
    # Bypass the commit-msg hook for test-fixture commits (faking seed/round
    # commits without assembling valid trailers + whitelisted file sets).
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--no-verify", "-q", "-m", msg)


def _head_sha(ws):
    return _git(ws, "rev-parse", "HEAD").stdout.strip()


def _head_msg(ws):
    return _git(ws, "log", "-1", "--format=%B").stdout


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.assertEqual(_run_harness("init", str(self.ws)).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _make_seed_commit(self):
        doc = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc.mkdir(parents=True)
        (doc / "00-overview.md").write_text("seed body\n")
        (self.ws / "variants" / "nodes" / "v-001"
         / "scorecard.json").write_text("{}")
        _git(self.ws, "add", "-A")
        _commit_no_verify(
            self.ws, "harness: seed variant documents\n\nAction: init\n")
        return _head_sha(self.ws)

    def _make_round_commit(self):
        # A tracked round artifact + an untracked rounds/ dir + morning brief.
        ev = self.ws / "evidence"
        ev.mkdir(exist_ok=True)
        (ev / "ev-000001.md").write_text("+++\n+++\nx\n")
        scratch = self.ws / "rounds" / "round-000001" / "scratch"
        scratch.mkdir(parents=True)
        (scratch / "designer.json").write_text("{}")
        (self.ws / "morning_brief.md").write_text("brief\n")
        _git(self.ws, "add", "-A")
        _commit_no_verify(self.ws, "round one\n\nAction: merge\n")

    def test_reset_to_seed_restores_seed_and_clears_rounds(self):
        seed_sha = self._make_seed_commit()
        self._make_round_commit()
        res = _run_harness("reset", "--to", "seed", "--yes",
                           "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0, res.stderr)
        # HEAD is back at the seed commit.
        self.assertEqual(_head_sha(self.ws), seed_sha)
        # Seed doc preserved; round-tracked evidence gone (reset away).
        self.assertTrue((self.ws / "variants" / "nodes" / "v-001"
                         / "doc" / "00-overview.md").exists())
        self.assertFalse((self.ws / "evidence" / "ev-000001.md").exists())
        # Untracked round artifacts removed (round-number counter included).
        self.assertFalse((self.ws / "rounds").exists())
        self.assertFalse((self.ws / "morning_brief.md").exists())

    def test_reset_to_scaffold_drops_seed_docs(self):
        self._make_seed_commit()
        res = _run_harness("reset", "--to", "scaffold", "--yes",
                           "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("harness: scaffold workspace", _head_msg(self.ws))
        # The seed doc was added after scaffold, so it's gone now.
        self.assertFalse((self.ws / "variants" / "nodes" / "v-001"
                          / "doc" / "00-overview.md").exists())

    def test_reset_without_anchor_errors(self):
        # No seed commit exists (only the scaffold), so --to seed has nothing.
        res = _run_harness("reset", "--to", "seed", "--yes",
                           "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 1)
        self.assertIn("no commit matching", res.stderr)

    def test_reset_aborts_without_confirmation(self):
        seed_sha = self._make_seed_commit()
        self._make_round_commit()
        head_before = _head_sha(self.ws)
        # No --yes and no stdin -> input() hits EOF -> abort, no changes.
        res = _run_harness("reset", "--to", "seed", "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 1)
        self.assertIn("aborted", res.stdout)
        self.assertEqual(_head_sha(self.ws), head_before)
        self.assertNotEqual(_head_sha(self.ws), seed_sha)
        self.assertTrue((self.ws / "rounds").exists())


class CommitConfigTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.assertEqual(_run_harness("init", str(self.ws)).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _append(self, rel, text):
        p = self.ws / rel
        p.write_text(p.read_text() + text)

    def test_commits_harness_toml_edit_with_action_init(self):
        self._append("harness.toml", "\n# tweak\n")
        res = _run_harness("commit-config", "-m", "bump cadence",
                           "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0, res.stderr)
        body = _head_msg(self.ws)
        self.assertIn("bump cadence", body)
        self.assertIn("Action: init", body)
        # harness.toml now matches HEAD (committed); worktree clean for it.
        self.assertEqual(
            _git(self.ws, "diff", "--quiet", "HEAD", "--", "harness.toml",
                 check=False).returncode, 0)

    def test_default_message_names_changed_files(self):
        self._append("goal.toml", "\n# note\n")
        res = _run_harness("commit-config", "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0, res.stderr)
        body = _head_msg(self.ws)
        self.assertIn("update config", body)
        self.assertIn("goal.toml", body)

    def test_no_changes_is_a_noop(self):
        head_before = _head_sha(self.ws)
        res = _run_harness("commit-config", "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0)
        self.assertIn("no changes", res.stdout)
        self.assertEqual(_head_sha(self.ws), head_before)

    def test_only_config_files_committed_not_other_staged(self):
        self._append("harness.toml", "\n# x\n")
        # An unrelated staged file must NOT ride along in the config commit.
        (self.ws / "scratch_note.txt").write_text("noise\n")
        _git(self.ws, "add", "scratch_note.txt")
        res = _run_harness("commit-config", "--workspace", str(self.ws))
        self.assertEqual(res.returncode, 0, res.stderr)
        changed = _git(self.ws, "show", "--name-only", "--format=",
                       "HEAD").stdout.split()
        self.assertIn("harness.toml", changed)
        self.assertNotIn("scratch_note.txt", changed)


if __name__ == "__main__":
    unittest.main()
