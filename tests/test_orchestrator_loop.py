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


if __name__ == "__main__":
    unittest.main()
