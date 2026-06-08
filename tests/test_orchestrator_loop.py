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


# These tests mock run_round, so the only real spawn_role caller left in
# run_loop is the round-0 seed scorer. No-op it module-wide: round 1 then
# bootstraps, which is what every assertion here assumes. Seed scoring has its
# own dedicated coverage in test_orchestrator_score_gate.py.
_seed_patcher = None


def setUpModule():
    global _seed_patcher
    _seed_patcher = mock.patch(
        "harness.orchestrator.score_seed_docs", return_value=[])
    _seed_patcher.start()


def tearDownModule():
    if _seed_patcher is not None:
        _seed_patcher.stop()


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
        self.assertTrue((self.ws / "derived" / "decisions.json").exists())
        self.assertTrue((self.ws / "variants" / "nodes" / "v-001" / "doc"
                         / "00-overview.md").exists())
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B"]).decode()
        self.assertIn("harness: seed variant documents", log)
        self.assertIn("Action: init", log)

    def test_aborts_on_operator_dirty_worktree(self):
        # Operator-owned dirt (an edit to tracked goal.toml) still aborts rather
        # than being silently clobbered.
        from harness import bootstrap
        goal = self.ws / "goal.toml"
        goal.write_text(goal.read_text() + "\n# operator edit\n")
        with self.assertRaises(bootstrap.DirtyWorktreeError):
            orchestrator.run_loop(self.ws, _harness_config(), max_rounds=1)

    def test_recovers_from_untracked_root_stray(self):
        # An agent-written non-ledger stray at the workspace root (the reported
        # repo_adapter.json case) is discarded; the run proceeds, not aborts.
        stray = self.ws / "repo_adapter.json"
        stray.write_text('{"leaked":true}\n')

        def fake_run_round(workspace_root, harness_config, round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed", elapsed_seconds=0.01)
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            outcomes = orchestrator.run_loop(
                self.ws, _harness_config(), max_rounds=1, variant_count=2)
        self.assertEqual(len(outcomes), 1)
        self.assertFalse(stray.exists())

    def test_recovers_from_leaked_ledger_file(self):
        # The reported failure mode: a previous round left an uncommitted doc
        # section in the worktree (e.g. an LLM spawn wrote it directly). The run
        # must recover and proceed, not abort with DirtyWorktreeError.
        stray = (self.ws / "variants" / "nodes" / "v-001" / "doc"
                 / "02-six-week-compression.md")
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("leaked, never committed\n")

        def fake_run_round(workspace_root, harness_config, round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed", elapsed_seconds=0.01)
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            outcomes = orchestrator.run_loop(
                self.ws, _harness_config(), max_rounds=1, variant_count=2)
        self.assertEqual(len(outcomes), 1)
        self.assertFalse(stray.exists())

    def test_merge_round_rebuilds_decision_cache(self):
        # Append a new decision to goal.toml, then a merged round must refresh
        # derived/decisions.json to include it.
        import tomllib
        goal = self.ws / "goal.toml"
        goal.write_text(goal.read_text() +
            '\n[[decision]]\nid = "new-thing"\n'
            'question = "?"\nstatus = "open"\nintroduced_at = "g-01"\n')
        # Commit the goal edit so the worktree is clean for run_loop's guard.
        subprocess.check_call(
            ["git", "-C", str(self.ws), "add", "goal.toml"])
        subprocess.check_call(
            ["git", "-C", str(self.ws),
             "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "--no-verify", "-m", "edit goal"])
        def fake_run_round(workspace_root, harness_config, round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="merge", elapsed_seconds=0.01)
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            orchestrator.run_loop(self.ws, _harness_config(),
                                  max_rounds=1, variant_count=2)
        import json as _json
        data = _json.loads(
            (self.ws / "derived" / "decisions.json").read_text())
        self.assertIn("new-thing", data["decisions"])


class RunLoopResumeNoBriefAbortTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_second_run_loop_does_not_abort_on_morning_brief(self):
        def fake_run_round(workspace_root, harness_config, round_id, variant_id):
            return orchestrator.RoundOutcome(
                round_id=round_id, variant_id=variant_id,
                verdict="spawn-failed", elapsed_seconds=0.01)
        with mock.patch("harness.orchestrator.run_round",
                        side_effect=fake_run_round):
            orchestrator.run_loop(self.ws, _harness_config(),
                                  max_rounds=1, variant_count=2)
            # morning_brief.md now exists (written at loop pause). A second
            # run_loop must NOT abort on the clean-worktree guard.
            self.assertTrue((self.ws / "morning_brief.md").exists())
            orchestrator.run_loop(self.ws, _harness_config(),
                                  max_rounds=1, variant_count=2)


if __name__ == "__main__":
    unittest.main()
