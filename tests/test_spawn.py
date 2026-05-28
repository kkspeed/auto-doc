import dataclasses
import unittest

from harness import spawn


class RoleOutputDataclassTest(unittest.TestCase):
    def test_role_output_default_fields_set(self):
        r = spawn.RoleOutput(verdict="ok")
        self.assertEqual(r.verdict, "ok")
        self.assertIsNone(r.parsed)
        self.assertEqual(r.stderr_tail, "")
        self.assertEqual(r.elapsed_seconds, 0.0)
        self.assertEqual(r.retry_count, 0)

    def test_role_output_is_frozen(self):
        r = spawn.RoleOutput(verdict="ok")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.verdict = "spawn-failed"


import os
import sys
from pathlib import Path

FAKE_CLI = str(
    Path(__file__).parent / "fixtures" / "fake_cli.py"
)


def _fake_cmd(scenario, *extra):
    """Build a subprocess argv that runs the fake CLI with a given scenario."""
    return [sys.executable, FAKE_CLI, "--scenario", scenario, *extra]


class RunWithHeartbeatTest(unittest.TestCase):
    def test_ok_run_returns_stdout_and_returncode_zero(self):
        result = spawn._run_with_heartbeat(
            _fake_cmd("ok"), stdin_text="hello world",
            spawn_timeout_seconds=10, silence_threshold_seconds=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.verdict, "ok")
        # The fake echoes stdin length back as JSON
        import json
        payload = json.loads(result.stdout)
        self.assertEqual(payload["echo_len"], len("hello world"))

    def test_nonzero_exit_returncode_propagates(self):
        result = spawn._run_with_heartbeat(
            _fake_cmd("nonzero"), stdin_text="",
            spawn_timeout_seconds=10, silence_threshold_seconds=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.verdict, "ok")   # ran cleanly; just exited nonzero
        self.assertIn("boom", result.stderr_tail)

    def test_spawn_timeout_kills_after_configured_seconds(self):
        result = spawn._run_with_heartbeat(
            _fake_cmd("hang"), stdin_text="",
            spawn_timeout_seconds=2, silence_threshold_seconds=10,
        )
        self.assertEqual(result.verdict, "timeout")
        # Elapsed should be roughly the timeout, not the full 60s sleep
        self.assertLess(result.elapsed_seconds, 5)

    def test_stderr_silence_threshold_triggers_sigterm(self):
        # hang scenario consumes stdin then sleeps forever (no stderr output)
        result = spawn._run_with_heartbeat(
            _fake_cmd("hang"), stdin_text="",
            spawn_timeout_seconds=10, silence_threshold_seconds=2,
        )
        self.assertEqual(result.verdict, "timeout")
        self.assertLess(result.elapsed_seconds, 8)

    def test_silence_grace_period_then_sigkill_if_alive(self):
        # The hang scenario ignores SIGTERM (it's just time.sleep in a loop).
        # The grace period elapses, then SIGKILL fires. Both should work for
        # this test — the verdict is "timeout" either way.
        result = spawn._run_with_heartbeat(
            _fake_cmd("hang"), stdin_text="",
            spawn_timeout_seconds=15, silence_threshold_seconds=1,
        )
        self.assertEqual(result.verdict, "timeout")

    def test_stderr_tail_bounded_to_max_lines(self):
        # "slow N" emits heartbeats; for N=1 second at 100ms intervals, that's
        # ~10 lines. We assert the tail is non-empty and bounded.
        result = spawn._run_with_heartbeat(
            _fake_cmd("slow", "--scenario-arg", "1"), stdin_text="",
            spawn_timeout_seconds=10, silence_threshold_seconds=5,
        )
        self.assertEqual(result.verdict, "ok")
        self.assertEqual(result.returncode, 0)
        # Stderr should contain heartbeats; tail count is bounded internally
        line_count = result.stderr_tail.count("\n") + 1
        self.assertLessEqual(line_count, 100)


if __name__ == "__main__":
    unittest.main()
