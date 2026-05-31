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
