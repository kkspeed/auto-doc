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
