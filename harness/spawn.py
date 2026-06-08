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
import shutil
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

    def _timeout_stderr_tail(reason: str, existing_tail: str) -> str:
        note = (
            f"timeout: {reason} "
            f"(spawn_timeout_seconds={spawn_timeout_seconds}, "
            f"silence_timeout_seconds={silence_threshold_seconds})"
        )
        return f"{existing_tail}\n{note}".strip() if existing_tail else note

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
            timeout_reason = "spawn timeout exceeded"
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
            timeout_reason = "no stdout/stderr activity"
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
    stderr_tail = "\n".join(stderr_snapshot)
    if verdict == "timeout":
        stderr_tail = _timeout_stderr_tail(timeout_reason, stderr_tail)

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
        stderr_tail=stderr_tail,
        elapsed_seconds=elapsed,
        verdict=verdict,
    )


# ----- Tool invokers (CLI argv builders) --------------------------------------


def _as_str_list(v) -> list[str]:
    """Normalize a config value to a list of strings (str -> [str], list ->
    [str...], anything else / None -> [])."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _effective_tool_cfg(role_cfg: dict, workspace_root) -> dict:
    """Return a copy of role_cfg with mcp_config filtered to files that exist
    (resolved against the spawn cwd = workspace_root). A configured-but-missing
    .mcp.json must not make `claude --mcp-config` error out and fail the spawn —
    a missing MCP file just means "no MCP this run"."""
    mcp = _as_str_list(role_cfg.get("mcp_config"))
    if not mcp:
        return role_cfg
    # workspace_root / m yields m itself when m is absolute, so this resolves
    # both relative and absolute paths correctly.
    present = [m for m in mcp if (workspace_root / m).exists()]
    if present == mcp:
        return role_cfg
    cfg = dict(role_cfg)
    cfg["mcp_config"] = present
    return cfg


def _invoke_claude(model: str, cfg: dict | None = None) -> list[str]:
    """Build the `claude -p` argv, adding tool/MCP flags from the role config.

    Tool access is OFF unless the role config opts in. Recognized keys:
      - allowed_tools: list of tool patterns, e.g. "Read", "Grep",
        "Bash(gh:*)", "mcp__gdrive" (-> --allowedTools). Presence enables tools.
      - mcp_config: path(s) to MCP server JSON, e.g. ".mcp.json"
        (-> --mcp-config). This is Claude's native MCP convention.
      - strict_mcp_config: bool -> --strict-mcp-config (ignore global MCP,
        use only the listed files — reproducible).
      - permission_mode: one of claude's modes (e.g. "acceptEdits",
        "bypassPermissions") -> --permission-mode.
      - extra_args: raw argv appended verbatim (escape hatch).
    """
    cfg = cfg or {}
    cmd = ["claude", "-p", "--output-format", "json", "--model", model]
    mcp = _as_str_list(cfg.get("mcp_config"))
    if mcp:
        cmd += ["--mcp-config", *mcp]
        if cfg.get("strict_mcp_config"):
            cmd.append("--strict-mcp-config")
    allowed = _as_str_list(cfg.get("allowed_tools"))
    if allowed:
        cmd += ["--allowedTools", *allowed]
    if cfg.get("permission_mode"):
        cmd += ["--permission-mode", str(cfg["permission_mode"])]
    cmd += _as_str_list(cfg.get("extra_args"))
    return cmd


def _invoke_codex(model: str, cfg: dict | None = None) -> list[str]:
    """Build the `codex exec` argv. NOTE: codex does NOT read `.mcp.json`; its
    MCP servers live in ~/.codex/config.toml [mcp_servers] (or via -c overrides).
    Recognized keys: sandbox -> --sandbox; extra_args -> appended verbatim
    (use this for `-c mcp_servers...`, `--profile`, etc.)."""
    cfg = cfg or {}
    cmd = ["codex", "exec", "--model", model, "--json"]
    if cfg.get("sandbox"):
        cmd += ["--sandbox", str(cfg["sandbox"])]
    cmd += _as_str_list(cfg.get("extra_args"))
    return cmd


def _invoke_gemini(model: str, cfg: dict | None = None) -> list[str]:
    """Build the `gemini` argv. NOTE: gemini does NOT read `.mcp.json`; its MCP
    servers live in .gemini/settings.json (manage via `gemini mcp`). Recognized
    keys: mcp_server_names -> --allowed-mcp-server-names; allowed_tools ->
    --allowed-tools; approval_mode -> --approval-mode; extra_args -> appended."""
    cfg = cfg or {}
    cmd = ["gemini", "--model", model, "--output", "json"]
    names = _as_str_list(cfg.get("mcp_server_names"))
    if names:
        cmd += ["--allowed-mcp-server-names", *names]
    allowed = _as_str_list(cfg.get("allowed_tools"))
    if allowed:
        cmd += ["--allowed-tools", *allowed]
    if cfg.get("approval_mode"):
        cmd += ["--approval-mode", str(cfg["approval_mode"])]
    cmd += _as_str_list(cfg.get("extra_args"))
    return cmd


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


def _loads_json_object_from_text(text: str) -> dict:
    stripped = text.strip()
    candidates = [stripped]
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1] == "```":
            candidates.append("\n".join(lines[1:-1]).strip())

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = _json.loads(candidate)
        except _json.JSONDecodeError as e:
            last_error = e
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            return _loads_json_object_from_text(parsed)
        raise ValueError("role output must be a JSON object")

    # Last-resort extraction for CLIs/models that wrap the JSON in prose.
    # JSONDecoder.raw_decode preserves normal JSON string escaping rules, so
    # braces inside string values do not confuse the scan once a candidate starts.
    decoder = _json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[i:])
        except _json.JSONDecodeError as e:
            last_error = e
            continue
        if isinstance(parsed, dict):
            return parsed
    if last_error is not None:
        raise last_error
    raise ValueError("role output did not contain a JSON object")


def _loads_role_json(stdout: bytes) -> dict:
    """Parse a CLI response into the role JSON object.

    Claude Code's `--output-format json` returns an execution envelope whose
    `result` field contains the assistant's text. The harness validators expect
    the assistant's role JSON, not the CLI envelope.
    """
    stdout_text = stdout.decode("utf-8", errors="replace")
    parsed = _loads_json_object_from_text(stdout_text)
    if (
        isinstance(parsed, dict)
        and parsed.get("type") == "result"
        and isinstance(parsed.get("result"), str)
    ):
        if parsed.get("is_error") is True:
            raise ValueError(
                "CLI returned error envelope: "
                f"{parsed.get('result') or parsed.get('api_error_status')}"
            )
        return _loads_json_object_from_text(parsed["result"])
    return parsed


def _append_diagnostic(existing_tail: str, diagnostic: str) -> str:
    return f"{existing_tail}\n{diagnostic}".strip() if existing_tail else diagnostic


def _extract_assistant_text(stdout: bytes) -> str:
    """Best-effort recovery of the assistant's own text from a CLI response, so
    the correction phase can show the model what it actually produced. Unwraps
    Claude Code's `{type: result, result: "..."}` envelope when present;
    otherwise returns the decoded stdout verbatim."""
    text = stdout.decode("utf-8", errors="replace")
    try:
        env = _json.loads(text.strip())
    except Exception:
        return text
    if (isinstance(env, dict) and env.get("type") == "result"
            and isinstance(env.get("result"), str)):
        return env["result"]
    return text


def _write_attempt_files(scratch_dir: Path, role: str, attempt: str,
                         result: _RunResult, stdin_text: str) -> None:
    """Persist the full LLM exchange for one attempt to scratch (gitignored).

    Writes three sibling files per attempt so a parse/validation failure can be
    reproduced and diagnosed entirely from disk:
      - {role}.{attempt}.stdin   the exact prompt sent (context + role/retry prompt)
      - {role}.{attempt}.stdout  the raw bytes the CLI returned
      - {role}.{attempt}.stderr  the captured stderr tail
    """
    (scratch_dir / f"{role}.{attempt}.stdin").write_text(
        stdin_text, encoding="utf-8", errors="replace",
    )
    (scratch_dir / f"{role}.{attempt}.stdout").write_bytes(result.stdout)
    (scratch_dir / f"{role}.{attempt}.stderr").write_text(
        result.stderr_tail, encoding="utf-8", errors="replace",
    )


def _untracked_nonignored(workspace_root: Path) -> set[str]:
    """Set of untracked, non-gitignored paths in the workspace. Best-effort:
    returns empty if git can't answer (e.g. a non-git workspace in a unit test),
    which makes the surrounding scrub a no-op."""
    out = subprocess.run(
        ["git", "-C", str(workspace_root), "ls-files", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, text=True)
    if out.returncode != 0:
        return set()
    return {p for p in out.stdout.split("\0") if p}


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
    """Spawn a role, then scrub any untracked, non-ignored file it left behind.

    Every role returns its result as JSON on stdout; the orchestrator does ALL
    legitimate file materialization from that parsed output AFTER this returns.
    So any untracked, non-ignored file that appears DURING the spawn is an agent
    side-effect — a model writing to its cwd=workspace_root under an arbitrary
    name (e.g. repo_adapter.json at the root) — and is removed here, at the
    source, so it can never dirty a later round's clean-worktree check. Files
    materialized by EARLIER phases are in the pre-spawn snapshot and preserved;
    gitignored scratch (CONTEXT.md, attempt dumps) is exempt via
    --exclude-standard. Scrubbing is best-effort and never masks the spawn
    result (it runs in `finally`, swallowing its own errors)."""
    before = _untracked_nonignored(workspace_root)
    try:
        return _spawn_role_impl(
            role, harness_config, context_md, prompt, workspace_root,
            round_id, variant_id, validator)
    finally:
        try:
            for rel in sorted(_untracked_nonignored(workspace_root) - before):
                target = workspace_root / rel
                try:
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink()
                except FileNotFoundError:
                    pass
        except Exception:
            pass


def _spawn_role_impl(
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
        "silence_timeout_seconds",
        run_cfg.get(
            "_silence_threshold_seconds_for_tests",
            spawn_timeout,
        ),
    )
    retry_sleep = run_cfg.get(
        "_retry_sleep_seconds_for_tests", _NONZERO_RETRY_SLEEP_SECONDS,
    )

    # Persist CONTEXT.md to scratch (for audit; gitignored)
    scratch_dir = workspace_root / "rounds" / round_id / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (scratch_dir / f"{role}.context.md").write_text(context_md)

    cmd = _TOOL_INVOKERS[tool](model, _effective_tool_cfg(role_cfg, workspace_root))
    stdin_text = context_md + "\n\n" + prompt

    # --- Pass 1: invoke ---
    result1 = _run_with_heartbeat(
        cmd, stdin_text, spawn_timeout, silence_threshold,
        cwd=workspace_root,
    )
    _write_attempt_files(scratch_dir, role, "attempt1", result1, stdin_text)
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
        _write_attempt_files(scratch_dir, role, "attempt2", result2, stdin_text)
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

    # --- Correction phase: a focused repair pass, NOT a redo ---
    # We hand the model back its OWN previous response plus the exact validation
    # error and ask it to fix only what's flagged, preserving everything else.
    # The old behavior re-ran the entire task (full CONTEXT.md + prompt), so the
    # model regenerated from scratch and tripped a DIFFERENT field each attempt
    # (missing id, then missing section_id, ...). We deliberately omit the large
    # round CONTEXT.md here to keep the model on the narrow repair task — its own
    # prior response already reflects that context.
    prev_text = _extract_assistant_text(parse_target)
    correction_stdin = (
        "[CORRECTION TASK]\n"
        "Your previous response had to be a SINGLE JSON object satisfying the "
        "schema described below, but it FAILED validation with:\n\n"
        f"{parse_error}\n\n"
        "Return your previous response again, CORRECTED so it passes. Fix ONLY "
        "what the error describes; preserve every other field exactly as it was. "
        "Do not add, drop, or rewrite unrelated content. Output ONLY the "
        "corrected JSON object — no prose, no markdown fences.\n\n"
        "===== SCHEMA / TASK (reference) =====\n"
        f"{prompt}\n\n"
        "===== YOUR PREVIOUS RESPONSE (verbatim) =====\n"
        f"{prev_text}\n"
    )
    result3 = _run_with_heartbeat(
        cmd, correction_stdin, spawn_timeout, silence_threshold,
        cwd=workspace_root,
    )
    _write_attempt_files(scratch_dir, role, "retry", result3, correction_stdin)
    elapsed += result3.elapsed_seconds
    stderr_tail = result3.stderr_tail
    first_parse_diagnostic = f"first parse/validation error: {parse_error}"
    if result3.verdict == "timeout":
        return RoleOutput(
            verdict="output-parse-fail",
            stderr_tail=_append_diagnostic(stderr_tail, first_parse_diagnostic),
            elapsed_seconds=elapsed, retry_count=1,
        )
    if result3.returncode != 0:
        return RoleOutput(
            verdict="output-parse-fail",
            stderr_tail=_append_diagnostic(stderr_tail, first_parse_diagnostic),
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
    except Exception as e:
        diagnostic = (
            f"{first_parse_diagnostic}\n"
            f"retry parse/validation error: {e}"
        )
        return RoleOutput(
            verdict="output-parse-fail",
            stderr_tail=_append_diagnostic(stderr_tail, diagnostic),
            elapsed_seconds=elapsed, retry_count=1,
        )
