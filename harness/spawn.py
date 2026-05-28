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
        stdout=bytes(stdout_buf),
        stderr_tail="\n".join(stderr_lines),
        elapsed_seconds=elapsed,
        verdict=verdict,
    )
