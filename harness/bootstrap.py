"""Init-time and run-time workspace setup for the Design Doc Evolution Harness.

- rebuild_decisions_cache: regenerate the derived decision cache from goal.toml.
- ensure_empty_registry: create the persisted append-only canonical slug
  registry baseline if absent.
- seed_variant_docs: seed each active variant's document from seed_doc.md.
- assert_clean_worktree: refuse to operate on a dirty worktree (safety rail for
  the round-reset path). See spec
  docs/superpowers/specs/2026-05-31-harness-trustworthiness-remediation-design.md.
"""
from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

from harness import claim_graph as cg


class DirtyWorktreeError(RuntimeError):
    """Raised when the workspace has uncommitted changes at run/round start."""


def rebuild_decisions_cache(workspace_root: Path) -> None:
    """Regenerate derived/decisions.json from goal.toml's [[decision]] array.

    Deterministic, idempotent overwrite. derived/ is gitignored; both the
    context builders and the pre-commit hook read this file from the working
    tree, so it need not be committed.
    """
    goal_path = workspace_root / "goal.toml"
    decisions: dict[str, dict] = {}
    if goal_path.exists():
        try:
            data = tomllib.loads(
                goal_path.read_text(encoding="utf-8", errors="replace"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        for d in data.get("decision", []) or []:
            d_id = d.get("id")
            if not isinstance(d_id, str) or not d_id:
                continue
            decisions[d_id] = {
                "id": d_id,
                "question": d.get("question", ""),
                "status": d.get("status", "open"),
                "introduced_at": d.get("introduced_at", ""),
            }
    out_dir = workspace_root / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "decisions.json").write_text(
        json.dumps({"decisions": decisions}, indent=2, sort_keys=True))


def ensure_empty_registry(workspace_root: Path) -> None:
    """Create derived/canonical_slug_registry.json as an empty registry if it
    does not already exist. The registry is persisted append-only state (it
    carries alias history) and must never be clobbered."""
    p = workspace_root / "derived" / "canonical_slug_registry.json"
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        cg.CanonicalSlugRegistry().to_dict(), indent=2, sort_keys=True))


def assert_clean_worktree(workspace_root: Path) -> None:
    """Raise DirtyWorktreeError if the worktree has any modified/staged/
    untracked non-ignored path, OR if git status cannot be determined (a
    non-git or missing path must NOT be treated as clean — this guard protects
    a git reset --hard)."""
    out = subprocess.run(
        ["git", "-C", str(workspace_root), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise DirtyWorktreeError(
            f"cannot determine worktree status for {workspace_root}: "
            f"{out.stderr.strip() or 'git status failed'}")
    if out.stdout.strip():
        raise DirtyWorktreeError(
            "workspace has uncommitted changes — commit or discard before "
            f"running:\n{out.stdout.rstrip()}")
