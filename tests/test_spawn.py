import dataclasses
import io
import unittest
from unittest import mock

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


import shutil
import tempfile
from pathlib import Path as _Path


def _make_config(scenario, scenario_arg=None, marker_file=None,
                 spawn_timeout=10, silence=10):
    """Build a harness_config dict that points to the fake CLI for ALL tools."""
    extras = ["--scenario", scenario]
    if scenario_arg:
        extras += ["--scenario-arg", scenario_arg]
    if marker_file:
        extras += ["--marker-file", marker_file]
    return {
        "models": {
            "planner": {"tool": "claude", "model": "fake-model"},
            "designer": {"tool": "claude", "model": "fake-model"},
            "reviewer": {"tool": "claude", "model": "fake-model"},
            "verifier_c": {"tool": "claude", "model": "fake-model"},
        },
        "run": {
            "spawn_timeout_seconds": spawn_timeout,
            "_silence_threshold_seconds_for_tests": silence,
            "_retry_sleep_seconds_for_tests": 0,   # don't actually wait 30s in tests
            "_fake_cli_argv_for_tests": extras,
        },
    }


class _PatchedDispatch:
    """Test helper: patch _TOOL_INVOKERS so 'claude' runs the fake CLI."""
    def __init__(self, extras):
        self.extras = extras
        self._saved = None

    def __enter__(self):
        self._saved = dict(spawn._TOOL_INVOKERS)
        def _fake_claude(model):
            return [sys.executable, FAKE_CLI, *self.extras]
        spawn._TOOL_INVOKERS["claude"] = _fake_claude
        return self

    def __exit__(self, *exc):
        spawn._TOOL_INVOKERS.clear()
        spawn._TOOL_INVOKERS.update(self._saved)


class SpawnRoleHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.td = _Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_spawn_role_returns_parsed_ok(self):
        cfg = _make_config("ok")
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="# context", prompt="do a thing",
                workspace_root=self.td, round_id="round-000001",
                variant_id="v-001",
            )
        self.assertEqual(result.verdict, "ok")
        self.assertIsNotNone(result.parsed)
        self.assertTrue(result.parsed["ok"])

    def test_spawn_role_without_validator_skips_schema_check(self):
        cfg = _make_config("validate_fail")  # output is valid JSON but wrong shape
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="", workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        # No validator means any JSON is accepted
        self.assertEqual(result.verdict, "ok")
        self.assertEqual(result.parsed, {"wrong_field": "x"})

    def test_spawn_role_unwraps_claude_json_envelope(self):
        cfg = _make_config("claude_json_envelope")

        def planner_validator(d):
            for key in ("round", "variant", "stance", "intent",
                        "target_sections"):
                if key not in d:
                    raise ValueError(f"missing {key}")

        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="", workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
                validator=planner_validator,
            )
        self.assertEqual(result.verdict, "ok")
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.parsed["round"], "round-000001")
        self.assertEqual(result.parsed["target_sections"], [])

    def test_spawn_role_writes_context_md_to_round_scratch(self):
        cfg = _make_config("ok")
        ctx = "# planner context\nfoo bar baz"
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md=ctx, prompt="p",
                workspace_root=self.td,
                round_id="round-000042", variant_id="v-001",
            )
        scratch = self.td / "rounds" / "round-000042" / "scratch" / "planner.context.md"
        self.assertTrue(scratch.exists())
        self.assertEqual(scratch.read_text(), ctx)


class SpawnRoleRetryTest(unittest.TestCase):
    def setUp(self):
        self.td = _Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_nonzero_exit_retries_after_sleep(self):
        # transient scenario: first invocation fails, second succeeds
        marker = self.td / "transient-marker"
        cfg = _make_config("transient", marker_file=str(marker))
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        self.assertEqual(result.verdict, "ok")
        self.assertEqual(result.retry_count, 1)
        self.assertTrue(result.parsed["retry"])

    def test_nonzero_exit_both_attempts_fail_returns_spawn_failed(self):
        cfg = _make_config("nonzero")
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        self.assertEqual(result.verdict, "spawn-failed")
        self.assertEqual(result.retry_count, 1)

    def test_nonjson_output_retries_with_appended_prompt_hint(self):
        cfg = _make_config("nonjson")
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        # Both attempts return nonjson; both fail parse → output-parse-fail
        self.assertEqual(result.verdict, "output-parse-fail")
        self.assertEqual(result.retry_count, 1)

    def test_validator_failure_retries_with_appended_error_text(self):
        cfg = _make_config("validate_fail")
        def strict_validator(d):
            if "ok" not in d:
                raise ValueError("missing required field 'ok'")
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
                validator=strict_validator,
            )
        self.assertEqual(result.verdict, "output-parse-fail")
        self.assertEqual(result.retry_count, 1)

    def test_parse_retry_both_attempts_fail_returns_output_parse_fail(self):
        # Covered by test_nonjson_output_retries above; this is an alias
        # asserting the same verdict for explicit-coverage tracking.
        cfg = _make_config("nonjson")
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        self.assertEqual(result.verdict, "output-parse-fail")

    def test_nonzero_exit_retry_succeeds_returns_ok(self):
        # transient scenario: first invocation fails, second succeeds.
        # Explicit assertion that the retry produces verdict='ok'.
        marker = self.td / "transient-marker-2"
        cfg = _make_config("transient", marker_file=str(marker))
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        self.assertEqual(result.verdict, "ok")
        self.assertEqual(result.retry_count, 1)


class SpawnRoleTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.td = _Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_hang_returns_timeout_verdict(self):
        cfg = _make_config("hang", spawn_timeout=2, silence=2)
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        self.assertEqual(result.verdict, "timeout")

    def test_timeout_is_not_retryable(self):
        cfg = _make_config("hang", spawn_timeout=2, silence=2)
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
        # retry_count must be 0 — no retry attempted
        self.assertEqual(result.verdict, "timeout")
        self.assertEqual(result.retry_count, 0)

    def test_overall_spawn_timeout_respected(self):
        # spawn_timeout_seconds is the absolute cap; silence_threshold is much
        # larger here. The hang scenario triggers the spawn-level timeout,
        # not the silence one.
        cfg = _make_config("hang", spawn_timeout=1, silence=30)
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            import time as _time
            t0 = _time.monotonic()
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="round-000001", variant_id="v-001",
            )
            elapsed = _time.monotonic() - t0
        self.assertEqual(result.verdict, "timeout")
        self.assertEqual(result.retry_count, 0)
        # Should respect the 1-second spawn timeout, not wait for silence
        self.assertLess(elapsed, 5)


class SpawnRoleToolDispatchTest(unittest.TestCase):
    def setUp(self):
        self.td = _Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_unknown_tool_in_harness_config_raises_value_error(self):
        cfg = {
            "models": {"planner": {"tool": "no-such-tool", "model": "x"}},
            "run": {"spawn_timeout_seconds": 10},
        }
        with self.assertRaises(ValueError):
            spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="r", variant_id="v-001",
            )

    def test_tool_not_on_path_returns_spawn_failed(self):
        cfg = {
            "models": {"planner": {"tool": "claude", "model": "x"}},
            "run": {"spawn_timeout_seconds": 10,
                    "_retry_sleep_seconds_for_tests": 0},
        }
        # Patch invoker to point at a nonexistent binary
        saved = dict(spawn._TOOL_INVOKERS)
        try:
            spawn._TOOL_INVOKERS["claude"] = lambda m: ["/nonexistent/binary-xyz"]
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="r", variant_id="v-001",
            )
        finally:
            spawn._TOOL_INVOKERS.clear()
            spawn._TOOL_INVOKERS.update(saved)
        self.assertEqual(result.verdict, "spawn-failed")

    def test_tool_invoker_includes_configured_model_in_argv(self):
        # Just verify _invoke_claude / _invoke_codex / _invoke_gemini all
        # include the model string in their argv output.
        for name in ("claude", "codex", "gemini"):
            argv = spawn._TOOL_INVOKERS[name]("my-specific-model")
            self.assertIn("my-specific-model", argv,
                          f"{name} invoker did not include model in argv")


class SpawnRoleConfigTest(unittest.TestCase):
    def setUp(self):
        self.td = _Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_missing_role_in_harness_config_raises_key_error(self):
        cfg = {"models": {}, "run": {"spawn_timeout_seconds": 10}}
        with self.assertRaises(KeyError):
            spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="r", variant_id="v-001",
            )

    def test_spawn_timeout_seconds_read_from_harness_toml(self):
        # spawn_timeout_seconds=1 with a hang scenario should time out in ~1s
        cfg = _make_config("hang", spawn_timeout=1, silence=10)
        with _PatchedDispatch(cfg["run"]["_fake_cli_argv_for_tests"]):
            import time as _time
            t0 = _time.monotonic()
            result = spawn.spawn_role(
                role="planner", harness_config=cfg,
                context_md="", prompt="",
                workspace_root=self.td,
                round_id="r", variant_id="v-001",
            )
            elapsed = _time.monotonic() - t0
        self.assertEqual(result.verdict, "timeout")
        self.assertLess(elapsed, 5)


class SpawnCwdTest(unittest.TestCase):
    def _fake_popen_capture(self):
        captured = {}

        class _FakeProc:
            def __init__(self, *a, **kw):
                captured["cwd"] = kw.get("cwd")
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b'{}')
                self.stderr = io.BytesIO(b'')
                self.returncode = 0
            def poll(self): return 0
            def wait(self, timeout=None): return 0
            def kill(self): pass

        return captured, _FakeProc

    def test_run_with_heartbeat_passes_cwd(self):
        captured, fake = self._fake_popen_capture()
        with mock.patch.object(spawn.subprocess, "Popen", fake):
            spawn._run_with_heartbeat(
                ["echo"], "hi", 5, silence_threshold_seconds=5,
                cwd="/tmp/some-workspace")
        self.assertEqual(captured["cwd"], "/tmp/some-workspace")

    def test_run_with_heartbeat_none_cwd_passes_none(self):
        captured, fake = self._fake_popen_capture()
        with mock.patch.object(spawn.subprocess, "Popen", fake):
            spawn._run_with_heartbeat(
                ["echo"], "hi", 5, silence_threshold_seconds=5, cwd=None)
        self.assertIsNone(captured["cwd"])  # None, not the string "None"


if __name__ == "__main__":
    unittest.main()
