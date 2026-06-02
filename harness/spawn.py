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
    cwd: "str | Path | None" = None,
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
            cwd=str(cwd) if cwd is not None else None,
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
                # errors="replace" prevents UnicodeEncodeError from killing the
                # writer thread silently. Lossy encoding is the right trade-off
                # vs producing a misleading "timeout" verdict — a few replaced
                # characters are observable, a silent timeout is not.
                proc.stdin.write(stdin_text.encode("utf-8", errors="replace"))
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

    # Snapshot the deque under the same lock the reader threads use, so that
    # any still-running reader can't be mid-append while we iterate.
    with lock:
        stderr_snapshot = list(stderr_lines)
        stdout_snapshot = bytes(stdout_buf)

    # Close pipes explicitly to avoid ResourceWarning on killed processes
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass

    elapsed = time.monotonic() - spawn_start
    return _RunResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_snapshot,
        stderr_tail="\n".join(stderr_snapshot),
        elapsed_seconds=elapsed,
        verdict=verdict,
    )


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


def _loads_role_json(stdout: bytes) -> dict:
    """Parse a CLI response into the role JSON object.

    Claude Code's `--output-format json` returns an execution envelope whose
    `result` field contains the assistant's text. The harness validators expect
    the assistant's role JSON, not the CLI envelope.
    """
    parsed = _json.loads(stdout)
    if (
        isinstance(parsed, dict)
        and parsed.get("type") == "result"
        and isinstance(parsed.get("result"), str)
    ):
        parsed = _json.loads(parsed["result"])
    if not isinstance(parsed, dict):
        raise ValueError("role output must be a JSON object")
    return parsed


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
      - Timeout (spawn or silence)      -> verdict="timeout", no retry
      - Non-zero exit                   -> sleep 30s, retry once. Still bad?
                                          verdict="spawn-failed", retry_count=1
      - JSON parse error or validator   -> retry once with appended hint prompt.
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
        cwd=workspace_root,
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
            cwd=workspace_root,
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
    # The except clauses below catch both JSONDecodeError (from json.loads)
    # and any exception the validator raises. Validator authors can use any
    # exception type — all are treated uniformly as parse failures and
    # trigger the validate-retry-once contract.
    parse_error: Exception | None = None
    try:
        parsed = _loads_role_json(parse_target)
        if validator is not None:
            validator(parsed)
        return RoleOutput(
            verdict="ok", parsed=parsed, stderr_tail=stderr_tail,
            elapsed_seconds=elapsed, retry_count=retry_count,
        )
    except _json.JSONDecodeError as e:
        parse_error = e
    except Exception as e:
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
        cwd=workspace_root,
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
        parsed = _loads_role_json(result3.stdout)
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
