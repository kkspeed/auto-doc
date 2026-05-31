import json
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


class InitRefusesFileTargetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.target = self.td / "ws-as-file"
        self.target.write_text("I am a regular file, not a directory")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_init_refuses_when_target_is_a_file(self):
        result = _run_harness("init", str(self.target))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to clobber existing file", result.stderr)


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
        # Modify a template file so we can verify it survives --reactivate
        sentinel = self.target / "harness.toml"
        sentinel_content = "# user-modified content\n"
        sentinel.write_text(sentinel_content)
        # Unset hooksPath to simulate clone
        subprocess.check_call(
            ["git", "-C", str(self.target), "config", "--unset", "core.hooksPath"],
        )
        # Reactivate
        result = _run_harness("init", str(self.target), "--reactivate")
        self.assertEqual(result.returncode, 0, result.stderr)
        # hooksPath is set again
        result = subprocess.run(
            ["git", "-C", str(self.target), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "hooks/")
        # User's modification to harness.toml survives (no copy happened)
        self.assertEqual(sentinel.read_text(), sentinel_content,
                         "--reactivate must not overwrite existing files")

    def test_reactivate_fails_on_non_git_dir(self):
        self.target.mkdir()
        (self.target / "some_file.txt").write_text("not a git repo")
        result = _run_harness("init", str(self.target), "--reactivate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a git repository", result.stderr)


class InitBootstrapsDerivedTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _init(self):
        result = _run_harness("init", str(self.ws))
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

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


if __name__ == "__main__":
    unittest.main()
