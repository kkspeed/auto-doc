# Subprocess Wrapper + CONTEXT.md Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement orchestrator sub-project 3 per [2026-05-27-subprocess-wrapper-and-context-builder-design.md](../specs/2026-05-27-subprocess-wrapper-and-context-builder-design.md): `harness/spawn.py` with `spawn_role`, tool dispatch, heartbeat+timeout, and JSON validate-retry, plus `harness/context.py` with four per-role builders, plus the `fake_cli.py` fixture to mock real CLIs in tests.

**Architecture:** Two new self-contained modules (no imports from each other). `spawn.py` owns the subprocess lifecycle: tool-specific argv assembly via `_TOOL_INVOKERS` dict, `_run_with_heartbeat` for the polling/SIGTERM/SIGKILL/output-buffering loop, `spawn_role` for the retry/parse orchestration that returns a frozen `RoleOutput`. `context.py` reads `derived/decisions.json`, `derived/canonical_slug_registry.json`, claim/section/rejection files, and (for reviewer) `git log` — each builder returns a markdown string.

**Tech Stack:** Python 3.11+ stdlib only (`subprocess`, `threading`, `time`, `signal`, `json`, `tomllib`, `pathlib`, `dataclasses`, `collections`). `unittest` for tests. Tests use a tiny `fake_cli.py` script as a stand-in for real claude/codex/gemini CLIs.

---

## File Structure

**Created in this plan:**
- `harness/spawn.py` — RoleOutput + spawn_role + `_run_with_heartbeat` + `_TOOL_INVOKERS` (~280 LOC)
- `harness/context.py` — `build_planner_context`/`build_designer_context`/`build_reviewer_context`/`build_verifier_c_context` + shared helpers (~200 LOC)
- `tests/fixtures/fake_cli.py` — scenario-driven fake CLI for spawn tests (~60 LOC, executable)
- `tests/test_spawn.py` — ~25 tests across 7 test classes
- `tests/test_context.py` — ~15 tests across 4 test classes

**NOT modified:** all existing files. This sub-project is purely additive.

---

## Task 1: `fake_cli.py` fixture + `RoleOutput` dataclass

This task lands two foundational pieces in parallel: the fake CLI that all spawn tests will use, and the `RoleOutput` dataclass that's the public return shape of `spawn_role`. Doing them together keeps the first commit small and gives Task 2 a stable invariant to build against.

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/fixtures/fake_cli.py` (executable)
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py`

- [ ] **Step 1: Write the fake CLI fixture**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/fixtures/fake_cli.py`:

```python
#!/usr/bin/env python3
"""Scenario-driven fake CLI for spawn-wrapper tests.

Invocation: python3 fake_cli.py --scenario <name> [--marker-file <path>]

Scenarios:
  ok            — read stdin, write {"ok": true, "echo_len": N} to stdout, exit 0
  nonzero       — write "boom" to stderr, exit 1
  nonjson       — write "not json output" to stdout, exit 0
  slow N        — emit heartbeat lines every 100ms for N seconds, then {"ok": true}
  hang          — read stdin, then sleep forever
  validate_fail — write {"wrong_field": "x"} to stdout, exit 0
  transient     — first invocation exit 1; second invocation exit 0 with {"ok": true}.
                  Uses --marker-file to track invocation count across runs.
"""
import argparse
import hashlib
import json
import os
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True)
    p.add_argument("--scenario-arg", default=None,
                   help="Numeric arg for scenarios like 'slow N'")
    p.add_argument("--marker-file", default=None,
                   help="Per-test marker file for the 'transient' scenario")
    # Accept and ignore any --model / -p / --output-format / etc. so the same
    # script can pretend to be claude, codex, or gemini under any argv shape.
    args, _ = p.parse_known_args()

    if args.scenario == "ok":
        stdin_text = sys.stdin.read()
        out = {"ok": True, "echo_len": len(stdin_text),
               "echo_sha": hashlib.sha256(stdin_text.encode()).hexdigest()[:16]}
        print(json.dumps(out))
        sys.exit(0)
    elif args.scenario == "nonzero":
        sys.stderr.write("boom\n")
        sys.exit(1)
    elif args.scenario == "nonjson":
        print("not json output")
        sys.exit(0)
    elif args.scenario == "slow":
        n = int(args.scenario_arg or "1")
        end = time.monotonic() + n
        while time.monotonic() < end:
            sys.stderr.write("heartbeat\n")
            sys.stderr.flush()
            time.sleep(0.1)
        print(json.dumps({"ok": True}))
        sys.exit(0)
    elif args.scenario == "hang":
        sys.stdin.read()    # consume whatever the parent sends
        while True:
            time.sleep(60)
    elif args.scenario == "validate_fail":
        print(json.dumps({"wrong_field": "x"}))
        sys.exit(0)
    elif args.scenario == "transient":
        if not args.marker_file:
            sys.stderr.write("transient scenario requires --marker-file\n")
            sys.exit(2)
        if os.path.exists(args.marker_file):
            # Second invocation: succeed
            print(json.dumps({"ok": True, "retry": True}))
            sys.exit(0)
        else:
            # First invocation: drop marker and fail
            with open(args.marker_file, "w") as f:
                f.write("first")
            sys.stderr.write("transient-fail\n")
            sys.exit(1)
    else:
        sys.stderr.write(f"unknown scenario: {args.scenario}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
```

Make it executable:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
chmod +x tests/fixtures/fake_cli.py
```

- [ ] **Step 2: Write failing dataclass tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn -v`
Expected: `ModuleNotFoundError: No module named 'harness.spawn'`.

- [ ] **Step 4: Create `harness/spawn.py` with the dataclass**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py`:

```python
"""Subprocess wrapper for the Design Doc Evolution Harness.

Spawns the configured CLI tool (claude / codex / gemini) for a given role,
sends a per-role CONTEXT.md + role-specific prompt, parses the JSON output,
optionally runs a validator, and returns a structured RoleOutput.

Public API:
  - RoleOutput: frozen dataclass with verdict + parsed + stderr_tail + elapsed
  - spawn_role: invoke a configured role with retry/timeout/heartbeat
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleOutput:
    verdict: str                    # "ok" | "spawn-failed" | "timeout" | "output-parse-fail"
    parsed: dict | None = None
    stderr_tail: str = ""
    elapsed_seconds: float = 0.0
    retry_count: int = 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn -v`
Expected: 2 tests pass.

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 220 tests / OK` (218 existing + 2 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add tests/fixtures/fake_cli.py harness/spawn.py tests/test_spawn.py
git commit -m "feat(spawn): RoleOutput dataclass + fake_cli.py fixture for spawn tests"
```

---

## Task 2: `_run_with_heartbeat` helper

This is the lowest-level primitive in `spawn.py`. It owns the subprocess lifecycle: spawn, write stdin, read stdout/stderr in threads, poll for completion, enforce both the overall spawn timeout and the 90-second-stderr-silence heartbeat with SIGTERM→SIGKILL escalation.

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py` (append)

- [ ] **Step 1: Write failing heartbeat tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py` (before the `if __name__ == "__main__":` line):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn.RunWithHeartbeatTest -v`
Expected: `AttributeError: module 'harness.spawn' has no attribute '_run_with_heartbeat'`.

- [ ] **Step 3: Append `_run_with_heartbeat` to `harness/spawn.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py`:

```python


# ----- Internal: heartbeat-watching subprocess runner -------------------------


import collections
import subprocess
import threading
import time
from dataclasses import field


_STDERR_TAIL_MAX_LINES = 100


@dataclass(frozen=True)
class _RunResult:
    returncode: int
    stdout: bytes
    stderr_tail: str
    elapsed_seconds: float
    verdict: str   # "ok" (process completed; returncode tells if it succeeded)
                   # or "timeout" (we killed it for silence or spawn-timeout)


def _run_with_heartbeat(
    cmd: list[str],
    stdin_text: str,
    spawn_timeout_seconds: int,
    silence_threshold_seconds: int = 90,
) -> _RunResult:
    """Spawn cmd, send stdin_text, watch for output silence.

    The process runs until ONE of:
      - Exits naturally (verdict="ok")
      - Spawn-timeout elapses (verdict="timeout", SIGKILL)
      - silence_threshold_seconds of no stderr/stdout activity (verdict="timeout",
        SIGTERM with 5s grace before SIGKILL)

    Daemon threads read stdout and stderr in parallel; the stderr line count
    is capped to _STDERR_TAIL_MAX_LINES via a bounded deque to prevent
    pathological output from filling memory.
    """
    spawn_start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
        )
    except FileNotFoundError as e:
        # Tool not on PATH; report as "ran cleanly with non-zero exit" so the
        # caller's retry/spawn-failed logic handles it uniformly.
        return _RunResult(
            returncode=127, stdout=b"", stderr_tail=str(e),
            elapsed_seconds=0.0, verdict="ok",
        )

    stdout_buf = bytearray()
    stderr_lines: collections.deque = collections.deque(maxlen=_STDERR_TAIL_MAX_LINES)
    last_output_ref = [spawn_start]   # mutable so threads can update; lock-guarded
    lock = threading.Lock()

    def _write_stdin():
        try:
            if stdin_text:
                proc.stdin.write(stdin_text.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def _read_stdout():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                stdout_buf.extend(chunk)
                with lock:
                    last_output_ref[0] = time.monotonic()
        except (OSError, ValueError):
            pass

    def _read_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                if not line:
                    break
                stderr_lines.append(line.decode(errors="replace").rstrip("\n"))
                with lock:
                    last_output_ref[0] = time.monotonic()
        except (OSError, ValueError):
            pass

    threads = [
        threading.Thread(target=_write_stdin, daemon=True),
        threading.Thread(target=_read_stdout, daemon=True),
        threading.Thread(target=_read_stderr, daemon=True),
    ]
    for t in threads:
        t.start()

    verdict = "ok"
    while True:
        if proc.poll() is not None:
            break
        now = time.monotonic()
        if now - spawn_start > spawn_timeout_seconds:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            verdict = "timeout"
            break
        with lock:
            silence = now - last_output_ref[0]
        if silence > silence_threshold_seconds:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            verdict = "timeout"
            break
        time.sleep(1)

    # Drain reader threads (best-effort)
    for t in threads:
        t.join(timeout=1)

    elapsed = time.monotonic() - spawn_start
    return _RunResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=bytes(stdout_buf),
        stderr_tail="\n".join(stderr_lines),
        elapsed_seconds=elapsed,
        verdict=verdict,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn.RunWithHeartbeatTest -v`
Expected: All 6 heartbeat tests pass. (Total spawn test count: 8 of 8.) The tests with `silence_threshold_seconds=2` should complete in well under 10s each.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 226 tests / OK` (220 + 6).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/spawn.py tests/test_spawn.py
git commit -m "feat(spawn): _run_with_heartbeat helper with SIGTERM/SIGKILL escalation"
```

---

## Task 3: `spawn_role` — tool dispatch + retry + parse orchestration

This is the public entry point. It composes `_run_with_heartbeat` with the tool dispatch table, the 30-second retry-on-non-zero, the JSON parse + validator, and the validate-retry-once contract.

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py` (append)

- [ ] **Step 1: Write failing spawn_role tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_spawn.py` (before the `if __name__ == "__main__":` line):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn -v`
Expected: New SpawnRole* test classes fail with `AttributeError: module 'harness.spawn' has no attribute 'spawn_role'`. The previously-passing 8 tests (dataclass + heartbeat) still pass.

- [ ] **Step 3: Append `spawn_role` + tool invokers to `harness/spawn.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/spawn.py`:

```python


# ----- Tool invokers (CLI argv builders) --------------------------------------


def _invoke_claude(model: str) -> list[str]:
    return ["claude", "-p", "--output-format", "json", "--model", model]


def _invoke_codex(model: str) -> list[str]:
    return ["codex", "exec", "--model", model, "--json"]


def _invoke_gemini(model: str) -> list[str]:
    return ["gemini", "--model", model, "--output", "json"]


_TOOL_INVOKERS = {
    "claude": _invoke_claude,
    "codex":  _invoke_codex,
    "gemini": _invoke_gemini,
}


# ----- Public: spawn_role -----------------------------------------------------


import json as _json
from pathlib import Path
from typing import Callable


_DEFAULT_SPAWN_TIMEOUT_SECONDS = 300
_DEFAULT_SILENCE_THRESHOLD_SECONDS = 90
_NONZERO_RETRY_SLEEP_SECONDS = 30


def spawn_role(
    role: str,
    harness_config: dict,
    context_md: str,
    prompt: str,
    workspace_root: Path,
    round_id: str,
    variant_id: str | None,
    validator: Callable[[dict], None] | None = None,
) -> RoleOutput:
    """Spawn the configured CLI tool for `role`, deliver context+prompt via
    stdin, parse JSON output, optionally validate.

    Retry semantics (see spec §3.1):
      - Timeout (spawn or silence)      → verdict="timeout", no retry
      - Non-zero exit                   → sleep 30s, retry once. Still bad?
                                          verdict="spawn-failed", retry_count=1
      - JSON parse error or validator   → retry once with appended hint prompt.
        raises                            Still bad? verdict="output-parse-fail",
                                          retry_count=1
    """
    models_config = harness_config["models"]
    if role not in models_config:
        raise KeyError(f"role {role!r} not in harness_config['models']")
    role_cfg = models_config[role]
    tool = role_cfg["tool"]
    model = role_cfg["model"]
    if tool not in _TOOL_INVOKERS:
        raise ValueError(
            f"tool {tool!r} for role {role!r} not in supported set "
            f"{sorted(_TOOL_INVOKERS)}"
        )

    run_cfg = harness_config.get("run", {})
    spawn_timeout = run_cfg.get(
        "spawn_timeout_seconds", _DEFAULT_SPAWN_TIMEOUT_SECONDS,
    )
    silence_threshold = run_cfg.get(
        "_silence_threshold_seconds_for_tests",
        _DEFAULT_SILENCE_THRESHOLD_SECONDS,
    )
    retry_sleep = run_cfg.get(
        "_retry_sleep_seconds_for_tests", _NONZERO_RETRY_SLEEP_SECONDS,
    )

    # Persist CONTEXT.md to scratch (for audit; gitignored)
    scratch_dir = workspace_root / "rounds" / round_id / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (scratch_dir / f"{role}.context.md").write_text(context_md)

    cmd = _TOOL_INVOKERS[tool](model)
    stdin_text = context_md + "\n\n" + prompt

    # --- Pass 1: invoke ---
    result1 = _run_with_heartbeat(
        cmd, stdin_text, spawn_timeout, silence_threshold,
    )
    if result1.verdict == "timeout":
        return RoleOutput(
            verdict="timeout", stderr_tail=result1.stderr_tail,
            elapsed_seconds=result1.elapsed_seconds, retry_count=0,
        )

    parse_target = result1.stdout
    retry_count = 0
    stderr_tail = result1.stderr_tail
    elapsed = result1.elapsed_seconds

    # Retry once on non-zero exit
    if result1.returncode != 0:
        time.sleep(retry_sleep)
        result2 = _run_with_heartbeat(
            cmd, stdin_text, spawn_timeout, silence_threshold,
        )
        retry_count = 1
        elapsed += result2.elapsed_seconds
        stderr_tail = result2.stderr_tail
        if result2.verdict == "timeout":
            return RoleOutput(
                verdict="timeout", stderr_tail=stderr_tail,
                elapsed_seconds=elapsed, retry_count=1,
            )
        if result2.returncode != 0:
            return RoleOutput(
                verdict="spawn-failed", stderr_tail=stderr_tail,
                elapsed_seconds=elapsed, retry_count=1,
            )
        parse_target = result2.stdout

    # --- Pass 2: parse + validate ---
    parse_error: Exception | None = None
    try:
        parsed = _json.loads(parse_target)
        if validator is not None:
            validator(parsed)
        return RoleOutput(
            verdict="ok", parsed=parsed, stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=retry_count,
        )
    except (_json.JSONDecodeError, Exception) as e:
        # Note: Exception catches the validator's chosen exception type too.
        # We narrow below to avoid double-handling KeyError/ValueError from
        # OUR own code, by tagging the raised exceptions explicitly:
        if isinstance(e, (KeyError, AttributeError)) and not isinstance(
                e, _json.JSONDecodeError):
            # Re-raise programming errors so tests catch them
            raise
        parse_error = e

    # Retry once with appended hint
    retry_prompt = (
        prompt
        + f"\n\nYour previous output failed schema validation: {parse_error}. "
        + "Output ONLY valid JSON matching the schema. No prose."
    )
    retry_stdin = context_md + "\n\n" + retry_prompt
    result3 = _run_with_heartbeat(
        cmd, retry_stdin, spawn_timeout, silence_threshold,
    )
    elapsed += result3.elapsed_seconds
    stderr_tail = result3.stderr_tail
    if result3.verdict == "timeout":
        return RoleOutput(
            verdict="output-parse-fail", stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=1,
        )
    if result3.returncode != 0:
        return RoleOutput(
            verdict="output-parse-fail", stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=1,
        )
    try:
        parsed = _json.loads(result3.stdout)
        if validator is not None:
            validator(parsed)
        return RoleOutput(
            verdict="ok", parsed=parsed, stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=1,
        )
    except Exception:
        return RoleOutput(
            verdict="output-parse-fail", stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=1,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_spawn -v`
Expected: All ~23 spawn tests pass (2 dataclass + 6 heartbeat + 3 happy + 5 retry + 2 timeout + 3 dispatch + 2 config = 23).

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 241 tests / OK` (226 + 15 new spawn_role tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/spawn.py tests/test_spawn.py
git commit -m "feat(spawn): spawn_role orchestration — tool dispatch, retry-on-nonzero, validate-retry"
```

---

## Task 4: `harness/context.py` — four per-role context builders

This task lands the entire `context.py` module in one go, since the four builders share helpers and their tests share fixture-building helpers. Each builder is short (~30-50 LOC); the shared computation is in `_render_registered_decisions` and `_load_decisions`.

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/context.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_context.py`

- [ ] **Step 1: Write failing context tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_context.py`:

```python
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import context


def _write_decisions(workspace, decisions: dict, goal_version="g-01"):
    p = workspace / "derived" / "decisions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "goal_version": goal_version,
        "decisions": decisions,
    }, indent=2))


def _write_goal_toml(workspace, goal_version="g-01"):
    p = workspace / "goal.toml"
    p.write_text(
        f'[goal]\ntitle = "test"\ngoal_version = "{goal_version}"\n'
    )


def _write_section(workspace, variant, name, tags, body, claim_id="cl-000001"):
    doc_dir = workspace / "variants" / "nodes" / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    fp = doc_dir / f"{name}.md"
    fp.write_text(
        f'+++\nsection_id = "x"\nclaim_id = "{claim_id}"\n'
        f'tags = [{tag_str}]\n+++\n{body}'
    )
    return fp


def _write_claim(workspace, variant, claim_id, decision_id, position=None,
                 proposed_decision=None, claim_type="decision"):
    claims_dir = workspace / "variants" / "nodes" / variant / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": claim_id, "section_id": decision_id, "decision_id": decision_id,
        "claim_type": claim_type, "evidence_ids": [], "assertion": "x",
    }
    if position is not None:
        payload["position"] = position
    if proposed_decision is not None:
        payload["proposed_decision"] = proposed_decision
    fp = claims_dir / f"{claim_id}.json"
    fp.write_text(json.dumps(payload, indent=2))
    return fp


def _write_rejection(workspace, variant, rj_id, summary):
    rej_dir = workspace / "rejections"
    rej_dir.mkdir(parents=True, exist_ok=True)
    fp = rej_dir / f"{rj_id}.md"
    fp.write_text(
        f'+++\nvariant = "{variant}"\n'
        f'summary = "{summary}"\n+++\nBody\n'
    )
    return fp


def _write_constitution(workspace):
    p = workspace / "constitution.md"
    p.write_text(
        "# Constitution\n\n## Slug discipline\n\n"
        "Use kebab-case ASCII slugs.\n\n"
        "## Other section\n\nUnrelated.\n"
    )


def _write_harness_toml(workspace, bootstrap_threshold=5):
    p = workspace / "harness.toml"
    p.write_text(
        "[claim_graph]\n"
        f"bootstrap_registry_size_threshold = {bootstrap_threshold}\n"
        "stale_proposals_threshold_rounds = 5\n"
    )


class BuildPlannerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_planner_lists_only_open_and_proposed_decisions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
            "auth-strategy": {"id": "auth-strategy", "question": "?",
                              "status": "proposed", "introduced_at": "g-01"},
            "dead-thing": {"id": "dead-thing", "question": "?",
                           "status": "retired", "introduced_at": "g-01"},
        })
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        self.assertIn("retry-policy", out)
        self.assertIn("auth-strategy", out)
        self.assertNotIn("dead-thing", out)

    def test_planner_shows_stale_proposals_when_present(self):
        # Empty stale list → section omitted; populated → section shown.
        # The planner uses detect_stale_proposals; absent data means no section.
        # Here we just verify the empty case produces no "stale" mention.
        _write_decisions(self.td, {})
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        # With no proposed decisions, there's nothing to be stale about
        self.assertNotIn("## Stale proposals", out)

    def test_planner_recent_rejections_filtered_by_variant(self):
        _write_decisions(self.td, {})
        _write_rejection(self.td, "v-001", "rj-000001", "first rejection")
        _write_rejection(self.td, "v-002", "rj-000002", "other variant")
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        self.assertIn("first rejection", out)
        self.assertNotIn("other variant", out)


class BuildDesignerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_constitution(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_shows_own_variant_positions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-001", "cl-000001", "retry-policy",
                     position="expo-backoff")
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("expo-backoff", out)
        self.assertIn("cl-000001", out)

    def test_designer_does_not_show_other_variant_positions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-002", "cl-000099", "retry-policy",
                     position="linear-no-backoff")
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        # Designer for v-001 should NOT see v-002's positions
        self.assertNotIn("linear-no-backoff", out)
        self.assertNotIn("cl-000099", out)

    def test_designer_shows_own_pending_proposals(self):
        _write_decisions(self.td, {})  # registry is empty
        _write_claim(self.td, "v-001", "cl-000001", "circuit-breaker",
                     position="half-open",
                     proposed_decision={"id": "circuit-breaker",
                                        "question": "When to reset?",
                                        "rationale": "needed for resilience"})
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("circuit-breaker", out)
        self.assertIn("When to reset?", out)

    def test_designer_includes_slug_discipline_section(self):
        _write_decisions(self.td, {})
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("Slug discipline", out)
        self.assertIn("kebab-case", out)


class BuildReviewerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_harness_toml(self.td)
        # Initialize a git repo so the reviewer's git-log scan works
        subprocess.check_call(
            ["git", "init", "-q"], cwd=self.td,
        )
        subprocess.check_call(
            ["git", "config", "user.email", "test@x"], cwd=self.td,
        )
        subprocess.check_call(
            ["git", "config", "user.name", "test"], cwd=self.td,
        )

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reviewer_shows_positions_across_all_variants(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-001", "cl-000001", "retry-policy",
                     position="expo-backoff")
        _write_claim(self.td, "v-002", "cl-000099", "retry-policy",
                     position="linear-no-backoff")
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        # Reviewer sees BOTH variants' positions
        self.assertIn("expo-backoff", out)
        self.assertIn("linear-no-backoff", out)
        self.assertIn("v-001", out)
        self.assertIn("v-002", out)

    def test_reviewer_includes_pending_proposals_from_current_round(self):
        _write_decisions(self.td, {})
        # Designer.json for round-000001 with a proposed_decision payload
        scratch = self.td / "rounds" / "round-000001" / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "designer.json").write_text(json.dumps({
            "round": "round-000001",
            "variant": "v-001",
            "claims": [
                {"id": "cl-000001", "section_id": "x", "decision_id": "x",
                 "claim_type": "decision", "evidence_ids": [],
                 "assertion": "y", "position": "z",
                 "proposed_decision": {"id": "circuit-breaker",
                                       "question": "When?", "rationale": "r"}},
            ],
        }))
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("circuit-breaker", out)

    def test_reviewer_includes_recent_canonicalize_commits(self):
        _write_decisions(self.td, {})
        # Create a couple of commits with canonicalize trailers
        (self.td / "file1.txt").write_text("x")
        subprocess.check_call(["git", "add", "."], cwd=self.td)
        subprocess.check_call(
            ["git", "commit", "-q", "-m",
             "first\n\nAction: canonicalize\nRound: round-000001\n"],
            cwd=self.td,
        )
        (self.td / "file2.txt").write_text("y")
        subprocess.check_call(["git", "add", "."], cwd=self.td)
        subprocess.check_call(
            ["git", "commit", "-q", "-m",
             "second\n\nAction: merge\nVariant: v-001\nRound: round-000002\n"],
            cwd=self.td,
        )
        out = context.build_reviewer_context(self.td, "round-000003", "v-001")
        # Canonicalize commit appears; merge commit does NOT
        self.assertIn("canonicalize", out)

    def test_reviewer_includes_registry_size(self):
        _write_decisions(self.td, {
            "a": {"id": "a", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
            "b": {"id": "b", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
        })
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("registry_size: 2", out)

    def test_reviewer_registry_size_below_threshold_flagged_as_bootstrap_permissive(self):
        _write_decisions(self.td, {
            "a": {"id": "a", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
        })
        _write_harness_toml(self.td, bootstrap_threshold=5)
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("bootstrap-permissive", out)


class BuildVerifierCContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_verifier_c_lists_registered_decisions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        self.assertIn("retry-policy", out)

    def test_verifier_c_omits_designer_and_reviewer_specific_sections(self):
        _write_decisions(self.td, {})
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        # Should NOT contain any of the designer-/reviewer-only sections
        self.assertNotIn("Slug discipline", out)
        self.assertNotIn("Positions you have committed", out)
        self.assertNotIn("All positions in use across variants", out)
        self.assertNotIn("registry_size", out)

    def test_verifier_c_header_includes_round_and_variant_and_goal_version(self):
        _write_decisions(self.td, {})
        out = context.build_verifier_c_context(
            self.td, "round-000042", "v-007"
        )
        self.assertIn("round-000042", out)
        self.assertIn("v-007", out)
        self.assertIn("g-01", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_context -v`
Expected: `ModuleNotFoundError: No module named 'harness.context'`.

- [ ] **Step 3: Create `harness/context.py` with all four builders**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/context.py`:

```python
"""Per-role CONTEXT.md builders for the Design Doc Evolution Harness.

Each role's CONTEXT.md is a markdown string built from on-disk claim graph
state (derived/decisions.json, variants/, evidence/, rejections/) plus
configuration (harness.toml, goal.toml). The orchestrator (sub-project 4)
passes these strings to spawn_role; agents see ONLY their role's view.

Public API (per redesign spec §6.2):
  - build_planner_context     — lightest: registered decisions + stale +
                                recent rejections (filtered by variant)
  - build_designer_context    — registered decisions + own positions +
                                own pending proposals + slug discipline
  - build_reviewer_context    — heaviest: all positions across all variants +
                                pending designer proposals + recent
                                canonicalizations + registry size posture
  - build_verifier_c_context  — lightest: registered decisions only
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import tomllib
from pathlib import Path


# ----- Shared helpers ---------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds",
    )


def _load_decisions(workspace_root: Path) -> dict:
    """Return {decision_id: {"id", "question", "status", "introduced_at"}}.

    Returns an empty dict if derived/decisions.json is missing or malformed.
    """
    p = workspace_root / "derived" / "decisions.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return data.get("decisions", {})


def _load_goal_version(workspace_root: Path) -> str:
    """Return goal_version from goal.toml's [goal] table, or 'unknown'."""
    p = workspace_root / "goal.toml"
    if not p.exists():
        return "unknown"
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return "unknown"
    return data.get("goal", {}).get("goal_version", "unknown")


def _load_harness_toml(workspace_root: Path) -> dict:
    """Return parsed harness.toml or {}."""
    p = workspace_root / "harness.toml"
    if not p.exists():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return {}


def _header(role: str, round_id: str, variant_id: str,
            goal_version: str) -> str:
    return (
        f"# CONTEXT — {role}\n"
        f"\n"
        f"Round: {round_id}\n"
        f"Variant: {variant_id}\n"
        f"Goal version: {goal_version}\n"
        f"Generated: {_now_iso()}\n"
    )


def _render_registered_decisions(decisions: dict) -> str:
    """Render the shared 'Registered decisions' section.

    Filters to status in {open, proposed}; retired decisions are omitted.
    """
    open_and_proposed = sorted(
        ((d_id, d) for d_id, d in decisions.items()
         if d.get("status") in ("open", "proposed")),
        key=lambda x: x[0],
    )
    if not open_and_proposed:
        return (
            "## Registered decisions\n\n"
            "No registered decisions yet.\n"
        )
    lines = ["## Registered decisions", "", "| ID | Question | Status |",
             "|---|---|---|"]
    for d_id, d in open_and_proposed:
        lines.append(f"| {d_id} | {d.get('question', '?')} | "
                     f"{d.get('status', '?')} |")
    lines.append("")
    return "\n".join(lines)


def _load_claims_for_variant(workspace_root: Path, variant_id: str) -> list[dict]:
    """Return parsed cl-*.json dicts for the given variant. Empty list on
    missing directory."""
    claims_dir = workspace_root / "variants" / "nodes" / variant_id / "claims"
    if not claims_dir.exists():
        return []
    out = []
    for fp in sorted(claims_dir.glob("cl-*.json")):
        try:
            out.append(json.loads(
                fp.read_text(encoding="utf-8", errors="replace")
            ))
        except json.JSONDecodeError:
            continue
    return out


def _load_all_variants_claims(workspace_root: Path) -> dict[str, list[dict]]:
    """Return {variant_id: [claim_dict, ...]} for every variant present."""
    variants_root = workspace_root / "variants" / "nodes"
    if not variants_root.exists():
        return {}
    out: dict[str, list[dict]] = {}
    for variant_dir in sorted(variants_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        out[variant_dir.name] = _load_claims_for_variant(
            workspace_root, variant_dir.name,
        )
    return out


def _load_rejections_for_variant(workspace_root: Path,
                                 variant_id: str,
                                 limit: int = 3) -> list[str]:
    """Return last N rejection summaries for the variant (most recent first)."""
    rej_dir = workspace_root / "rejections"
    if not rej_dir.exists():
        return []
    summaries: list[tuple[str, str]] = []  # (rj_id, summary)
    for fp in sorted(rej_dir.glob("rj-*.md"), reverse=True):
        text = fp.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("+++"):
            continue
        end = text.find("+++", 3)
        if end == -1:
            continue
        try:
            fm = tomllib.loads(text[3:end])
        except tomllib.TOMLDecodeError:
            continue
        if fm.get("variant") != variant_id:
            continue
        summaries.append((fp.stem, fm.get("summary", "(no summary)")))
        if len(summaries) >= limit:
            break
    return [f"{rj_id}: {summary}" for rj_id, summary in summaries]


# ----- Planner ----------------------------------------------------------------


def build_planner_context(workspace_root: Path, round_id: str,
                         variant_id: str) -> str:
    decisions = _load_decisions(workspace_root)
    goal_version = _load_goal_version(workspace_root)
    out = [_header("planner", round_id, variant_id, goal_version), ""]
    out.append(_render_registered_decisions(decisions))
    # Stale proposals section is omitted when empty (per spec); we don't have
    # introduced_round tracking in this sub-project, so skip the section.
    rejections = _load_rejections_for_variant(workspace_root, variant_id)
    if rejections:
        out.append("\n## Recent rejections (this variant)\n")
        for r in rejections:
            out.append(f"- {r}")
        out.append("")
    return "\n".join(out)


# ----- Designer ---------------------------------------------------------------


def _extract_slug_discipline(constitution_text: str) -> str:
    """Extract the '## Slug discipline' section from constitution.md."""
    match = re.search(
        r"(## Slug discipline\b.*?)(?=\n##\s|\Z)",
        constitution_text, re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return ""


def build_designer_context(workspace_root: Path, round_id: str,
                          variant_id: str) -> str:
    decisions = _load_decisions(workspace_root)
    goal_version = _load_goal_version(workspace_root)
    claims = _load_claims_for_variant(workspace_root, variant_id)

    out = [_header("designer", round_id, variant_id, goal_version), ""]
    out.append(_render_registered_decisions(decisions))

    # Own positions table
    positions = [
        c for c in claims
        if c.get("position") and c.get("claim_type") == "decision"
    ]
    out.append(f"\n## Positions you have committed to in {variant_id}\n")
    if positions:
        out.append("| Decision | Position | Claim ID |")
        out.append("|---|---|---|")
        for c in positions:
            out.append(f"| {c.get('decision_id', '?')} | "
                       f"{c.get('position', '?')} | {c.get('id', '?')} |")
    else:
        out.append("(none yet)")
    out.append("")

    # Own pending proposals
    out.append("## Your pending proposals\n")
    pending = []
    for c in claims:
        pd = c.get("proposed_decision")
        if pd and pd.get("id") not in decisions:
            pending.append(pd)
    if pending:
        out.append("| Proposed decision ID | Question | Rationale |")
        out.append("|---|---|---|")
        for pd in pending:
            out.append(f"| {pd.get('id', '?')} | {pd.get('question', '?')} "
                       f"| {pd.get('rationale', '?')} |")
    else:
        out.append("(none)")
    out.append("")

    # Slug discipline (verbatim from constitution.md)
    constitution_path = workspace_root / "constitution.md"
    if constitution_path.exists():
        slug_section = _extract_slug_discipline(
            constitution_path.read_text(encoding="utf-8", errors="replace"),
        )
        if slug_section:
            out.append(slug_section)
            out.append("")

    return "\n".join(out)


# ----- Reviewer ---------------------------------------------------------------


def _recent_canonicalize_commits(workspace_root: Path,
                                 limit: int = 5) -> list[str]:
    """Run `git log --grep` for recent canonicalize commits. Returns list of
    'sha subject' lines. Empty list on git failure or no commits."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_root), "log",
             "--grep=^Action: canonicalize", f"-n{limit}",
             "--format=%h %s"],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def build_reviewer_context(workspace_root: Path, round_id: str,
                          variant_id: str) -> str:
    decisions = _load_decisions(workspace_root)
    goal_version = _load_goal_version(workspace_root)
    all_claims = _load_all_variants_claims(workspace_root)
    harness_cfg = _load_harness_toml(workspace_root)

    out = [_header("reviewer", round_id, variant_id, goal_version), ""]
    out.append(_render_registered_decisions(decisions))

    # All positions across all variants
    out.append("\n## All positions in use across variants\n")
    rows: list[tuple[str, str, str, str]] = []
    for v_id, claims in all_claims.items():
        for c in claims:
            if c.get("position") and c.get("claim_type") == "decision":
                rows.append((c.get("decision_id", "?"), v_id,
                             c.get("position", "?"), c.get("id", "?")))
    if rows:
        out.append("| Decision | Variant | Position | Claim ID |")
        out.append("|---|---|---|---|")
        for d, v, p, c in sorted(rows):
            out.append(f"| {d} | {v} | {p} | {c} |")
    else:
        out.append("(no positions yet)")
    out.append("")

    # Pending designer proposals from this round
    out.append("## Pending designer proposals (this round)\n")
    designer_json = (
        workspace_root / "rounds" / round_id / "scratch" / "designer.json"
    )
    pending: list[dict] = []
    if designer_json.exists():
        try:
            data = json.loads(designer_json.read_text(
                encoding="utf-8", errors="replace"
            ))
            for c in data.get("claims", []):
                pd = c.get("proposed_decision")
                if pd:
                    pending.append(pd)
        except json.JSONDecodeError:
            pass
    if pending:
        out.append("| Proposed decision ID | Question | Rationale |")
        out.append("|---|---|---|")
        for pd in pending:
            out.append(f"| {pd.get('id', '?')} | {pd.get('question', '?')} "
                       f"| {pd.get('rationale', '?')} |")
    else:
        out.append("(none)")
    out.append("")

    # Recent canonicalize commits
    out.append("## Recent canonicalizations (last 5 rounds)\n")
    recent = _recent_canonicalize_commits(workspace_root, limit=5)
    if recent:
        for line in recent:
            out.append(f"- {line}")
    else:
        out.append("(none)")
    out.append("")

    # Registry posture
    threshold = (
        harness_cfg.get("claim_graph", {})
        .get("bootstrap_registry_size_threshold", 5)
    )
    size = len(decisions)
    posture = "bootstrap-permissive" if size < threshold else "default-deny"
    out.append("## Registry posture\n")
    out.append(f"registry_size: {size}")
    out.append(f"bootstrap_threshold: {threshold}")
    out.append(f"posture: {posture}")
    out.append("")

    return "\n".join(out)


# ----- Verifier C -------------------------------------------------------------


def build_verifier_c_context(workspace_root: Path, round_id: str,
                            variant_id: str) -> str:
    decisions = _load_decisions(workspace_root)
    goal_version = _load_goal_version(workspace_root)
    out = [_header("verifier_c", round_id, variant_id, goal_version), ""]
    out.append(_render_registered_decisions(decisions))
    return "\n".join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_context -v`
Expected: All 15 context tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 256 tests / OK` (241 + 15).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/context.py tests/test_context.py
git commit -m "feat(context): per-role CONTEXT.md builders (planner/designer/reviewer/verifier_c)"
```

---

## Task 5: `/code-review` gate over sub-project 3

After Tasks 1-4 land and per-task reviews pass, dispatch a `/code-review` over the full sub-project diff to surface anything the per-task reviews missed.

**Files:** none modified directly in this task. Findings (if any) are addressed in a follow-up commit.

- [ ] **Step 1: Capture the sub-project base SHA**

Before Task 1 begins, capture the base SHA so we can review the whole sub-project's diff at once:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git rev-parse HEAD
```

Record the SHA — this is the `BASE_SHA` for the review. The plan executor should write this down before starting Task 1.

- [ ] **Step 2: Dispatch the `/code-review` subagent**

After Task 4's commit lands, dispatch a subagent that invokes `/code-review` at high effort over the range `BASE_SHA..HEAD`. Use this prompt (substitute `<BASE_SHA>` with the captured value):

```
You are running a /code-review on orchestrator sub-project 3 (Subprocess
Wrapper + CONTEXT.md Builder).

Invoke the `/code-review` skill at effort=high over the commit range
`<BASE_SHA>..HEAD` in /Users/liwen/develop/projects/auto_design_doc.

Sub-project 3 ships two new modules:
- harness/spawn.py: spawn_role function with tool dispatch (claude/codex/
  gemini), 90s-stderr-silence heartbeat, 5min spawn timeout, retry-once on
  non-zero exit and on JSON parse/validator failure.
- harness/context.py: build_<role>_context functions per redesign spec §6.2.

The spec is at docs/superpowers/specs/2026-05-27-subprocess-wrapper-and-
context-builder-design.md. Per-task reviews already covered: dataclass
shape, heartbeat happy/timeout/silence paths, retry semantics, tool
dispatch, per-role context section contents.

Look for issues BEYOND those:
- Concurrency / thread-safety in _run_with_heartbeat under unusual signals
- Edge cases in JSON parsing (trailing whitespace, BOM, partial-output)
- Robustness of git log --grep parsing against quoted/multiline commits
- Performance of _load_all_variants_claims for many variants
- Possible TOML parsing surprises (multi-line strings, arrays)
- Subprocess argv injection risks (model names with shell metacharacters)
- File-handle leaks if the process exits abnormally

Return a single review report with Critical/Important/Minor sections.
File:line refs and concrete fixes for each. Keep under 1500 words.
```

- [ ] **Step 3: Triage findings**

If the review returns:
- **Critical:** address inline before declaring the sub-project complete.
- **Important:** address inline if the fix is small (≤30 LOC); otherwise defer to the deferred-findings backlog with explicit justification.
- **Minor:** record in the deferred backlog for batched cleanup.

If no Critical or Important findings: proceed to Step 4.

- [ ] **Step 4: Commit any inline fixes**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/spawn.py harness/context.py tests/test_spawn.py tests/test_context.py
git commit -m "fix(spawn,context): address /code-review findings from sub-project 3 pass"
```

If no fixes were needed, skip this step.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: at minimum 256 tests pass; possibly more if the review added regression tests.

---

## Spec coverage check

| Spec section | Requirement | Implemented in |
|---|---|---|
| §2.1 RoleOutput dataclass | Task 1 |
| §2.1 spawn_role signature + return type | Task 3 |
| §2.2 four build_<role>_context functions | Task 4 |
| §3.1 spawn_role execution flow | Task 3 |
| §3.1 timeout-not-retryable rule | Task 3 (`SpawnRoleTimeoutTest.test_timeout_is_not_retryable`) |
| §3.1 retry-on-nonzero-after-sleep | Task 3 (`test_nonzero_exit_retries_after_sleep`) |
| §3.1 JSON parse + validate-retry | Task 3 (`test_nonjson_*`, `test_validator_failure_*`) |
| §3.2 _run_with_heartbeat with SIGTERM/SIGKILL escalation | Task 2 |
| §3.2 100-line stderr cap | Task 2 (`test_stderr_tail_bounded_to_max_lines`) |
| §3.2 1-second poll interval | Task 2 (implementation detail; covered by tests for timing) |
| §3.3 _TOOL_INVOKERS dispatch with three CLIs | Task 3 |
| §3.3 unknown tool raises ValueError | Task 3 (`test_unknown_tool_in_harness_config_raises_value_error`) |
| §3.4 planner CONTEXT.md content | Task 4 (`BuildPlannerContextTest`) |
| §3.4 designer CONTEXT.md content | Task 4 (`BuildDesignerContextTest`) |
| §3.4 reviewer CONTEXT.md content | Task 4 (`BuildReviewerContextTest`) |
| §3.4 verifier_c CONTEXT.md content | Task 4 (`BuildVerifierCContextTest`) |
| §3.4 reviewer bootstrap-permissive flag | Task 4 (`test_reviewer_registry_size_below_threshold_*`) |
| §3.5 CONTEXT.md persisted to rounds/<id>/scratch | Task 3 (`test_spawn_role_writes_context_md_to_round_scratch`) |
| §4 test plan — ~40 tests | Task 1 (2) + Task 2 (6) + Task 3 (15) + Task 4 (15) = 38 ≈ ~40 |
| §5 edge cases — missing variants/decisions/etc. | Task 4 (defensive helpers return empty/unknown) |
| §8 success criteria | All Steps 4 of each task |
| /code-review gate | Task 5 |

All in-scope spec items have a task.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", or "implement later" entries in plan body.
- Function names used across tasks match definitions exactly:
  - `RoleOutput` (Task 1) referenced by Task 3.
  - `_run_with_heartbeat(cmd, stdin_text, spawn_timeout_seconds, silence_threshold_seconds)` (Task 2) called by `spawn_role` (Task 3) with the same positional + keyword args.
  - `_TOOL_INVOKERS` dict (Task 3) keyed by `"claude"`, `"codex"`, `"gemini"` strings; matched by test patches.
  - `spawn_role(role, harness_config, context_md, prompt, workspace_root, round_id, variant_id, validator=None)` (Task 3) — signature consistent with tests' invocation patterns.
  - `build_planner_context`, `build_designer_context`, `build_reviewer_context`, `build_verifier_c_context` (Task 4) — same `(workspace_root, round_id, variant_id) -> str` signature for all four.
- Test helpers consistently named: `_write_decisions`, `_write_goal_toml`, `_write_section`, `_write_claim`, `_write_rejection`, `_write_constitution`, `_write_harness_toml` in `test_context.py`; `_fake_cmd`, `_make_config`, `_PatchedDispatch` in `test_spawn.py`.
- Verdict enum values: `"ok"`, `"spawn-failed"`, `"timeout"`, `"output-parse-fail"` — match spec §2.1 verbatim and are asserted in tests.
- Frozen dataclass: `RoleOutput` is `frozen=True`; matches the project's prior `VerifierFailure` precedent (sub-project 2's cleanup).
- Late imports note: `tomllib`, `json`, `re`, `subprocess`, `datetime` are imported at the top of `context.py`. In `spawn.py`, the append-by-task structure introduces `collections`, `subprocess`, `threading`, `time` mid-file in Task 2 and `json`, `Path`, `Callable` mid-file in Task 3. These are intentional for append cleanliness; can be hoisted in the /code-review fixup pass.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-28-subprocess-wrapper-and-context-builder.md`.
