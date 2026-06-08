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
import shutil
import subprocess
import tomllib
from pathlib import Path

from harness import claim_graph as cg


class DirtyWorktreeError(RuntimeError):
    """Raised when the workspace has uncommitted changes at run/round start."""


def rebuild_decisions_cache(workspace_root: Path) -> None:
    """Regenerate derived/decisions.json from goal.toml via the canonical
    claim_graph loader. Missing goal.toml -> empty cache. A goal.toml that
    exists but is malformed (bad TOML, missing goal_version, invalid/duplicate
    decision) raises (SchemaError / TOMLDecodeError) — a trustworthiness
    bootstrap must fail loud rather than silently produce a false-empty
    registry. derived/ is gitignored; consumers read this file from the tree."""
    goal_path = workspace_root / "goal.toml"
    out_path = workspace_root / "derived" / "decisions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not goal_path.exists():
        out_path.write_text(
            json.dumps({"decisions": {}}, indent=2, sort_keys=True))
        return
    decisions, goal_version = cg.load_decisions_from_goal_toml(goal_path)
    cg.dump_decisions_to_json(decisions, goal_version, out_path)


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


# Recovery classification. The durable ledger is built EXCLUSIVELY from commits,
# so the rules are:
#   - An UNTRACKED file is never committed ledger state and is never operator
#     config (config below is tracked) — it is a leaked artifact (an LLM spawn
#     writing to its cwd=workspace_root under an arbitrary name like
#     repo_adapter.json, a round interrupted before its commit, a partial
#     materialize) and is safe to discard, wherever it lives. The ONE exception
#     is a known operator file the operator may have newly created but not yet
#     committed — we refuse to delete those.
#   - A TRACKED file with uncommitted changes is auto-restored ONLY when it is
#     part of the harness ledger; an edit to operator config or any other
#     tracked file must never be silently clobbered.
_LEDGER_DIR_PREFIXES = (
    "variants/", "evidence/", "rejections/", "rounds/", "derived/",
)
_LEDGER_FILES = ("actions.jsonl", "morning_brief.md", "CONTEXT.md")

# Operator-authored inputs. Tracked, so an *edit* to one shows up as a tracked
# modification (→ raise); but we also refuse to discard one if it appears
# untracked (a freshly-added .mcp.json the operator hasn't committed yet).
_OPERATOR_FILES = frozenset({
    "goal.toml", "harness.toml", "constitution.md", "seed_doc.md",
    ".mcp.json", ".gitignore",
})


def _is_ledger_owned(rel: str) -> bool:
    rel = rel.rstrip("/")
    return (rel in _LEDGER_FILES
            or any((rel + "/").startswith(p) for p in _LEDGER_DIR_PREFIXES))


def _is_operator_owned(rel: str) -> bool:
    return rel.rstrip("/") in _OPERATOR_FILES


def _parse_porcelain_z(raw: str) -> list[tuple[str, str]]:
    """Parse `git status --porcelain -z` into (xy, path) records. Rename/copy
    records carry a trailing NUL-separated source path which we consume and
    ignore (the destination path is what the worktree now holds)."""
    fields = raw.split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(fields):
        f = fields[i]
        if not f:
            i += 1
            continue
        xy, path = f[:2], f[3:]
        entries.append((xy, path))
        # R (rename) / C (copy) in either index or worktree slot → src follows.
        if "R" in xy or "C" in xy:
            i += 2
        else:
            i += 1
    return entries


def recover_worktree(workspace_root: Path) -> list[str]:
    """Restore a clean worktree by discarding leaked artifacts, then return the
    sorted list of paths discarded.

    The recoverable replacement for assert_clean_worktree at run/round start.
    Discards every untracked, non-ignored stray (an agent side-effect can land at
    any path/name, so this is NOT limited to known ledger directories) plus any
    uncommitted change to a tracked ledger file, resetting them to HEAD so a
    stray from a previous round or an interrupted run can never abort the next
    round. Raises DirtyWorktreeError — rather than clobbering — when an operator
    file is dirty (a tracked edit, or a not-yet-committed new operator file) or
    any other tracked file has uncommitted changes, and when git status cannot be
    determined (a non-git path must never look clean)."""
    out = subprocess.run(
        ["git", "-C", str(workspace_root), "-c", "core.quotePath=false",
         "status", "--porcelain", "-z"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise DirtyWorktreeError(
            f"cannot determine worktree status for {workspace_root}: "
            f"{out.stderr.strip() or 'git status failed'}")
    entries = _parse_porcelain_z(out.stdout)
    if not entries:
        return []

    to_discard: list[str] = []
    foreign: list[tuple[str, str]] = []
    for xy, path in entries:
        if xy == "??":
            # Untracked: discard unless it's a known operator file the operator
            # may have added but not yet committed.
            if _is_operator_owned(path):
                foreign.append((xy, path))
            else:
                to_discard.append(path)
        elif _is_ledger_owned(path):
            # Tracked ledger file with uncommitted changes → restore to HEAD.
            to_discard.append(path)
        else:
            # Tracked operator config or any other tracked file was edited →
            # never clobber it.
            foreign.append((xy, path))
    if foreign:
        listing = "\n".join(f"{xy} {p}" for xy, p in foreign)
        raise DirtyWorktreeError(
            "workspace has uncommitted changes that are operator-owned or an "
            "unrecognized tracked file — commit or discard before running:\n"
            f"{listing}")

    discarded: list[str] = []
    for path in to_discard:
        # Unstage first so a path staged by an interrupted commit is handled
        # uniformly with an unstaged one.
        subprocess.run(
            ["git", "-C", str(workspace_root), "reset", "-q", "--", path],
            capture_output=True, text=True)
        in_head = subprocess.run(
            ["git", "-C", str(workspace_root), "cat-file", "-e",
             f"HEAD:{path}"], capture_output=True, text=True).returncode == 0
        if in_head:
            # Tracked at HEAD → restore committed content (covers modify/delete).
            subprocess.run(
                ["git", "-C", str(workspace_root), "checkout", "-q", "HEAD",
                 "--", path], capture_output=True, text=True)
        else:
            # Not in HEAD → a new leaked file/dir → remove it.
            target = workspace_root / path.rstrip("/")
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
        discarded.append(path)
    return sorted(discarded)


def seed_variant_docs(workspace_root: Path, variant_count: int) -> list[str]:
    """Seed each active variant (v-001..v-{variant_count:03d}) that has no doc
    yet with seed_doc.md's body as a single overview section. Returns the list
    of relative paths created (empty if nothing was seeded). No-op when
    seed_doc.md is absent."""
    seed_path = workspace_root / "seed_doc.md"
    if not seed_path.exists():
        return []
    seed_body = seed_path.read_text(encoding="utf-8", errors="replace")
    created: list[str] = []
    for n in range(1, variant_count + 1):
        variant_id = f"v-{n:03d}"
        doc_dir = workspace_root / "variants" / "nodes" / variant_id / "doc"
        if doc_dir.exists() and any(doc_dir.glob("*.md")):
            continue  # already has a document; do not re-seed
        doc_dir.mkdir(parents=True, exist_ok=True)
        rel = f"variants/nodes/{variant_id}/doc/00-overview.md"
        frontmatter = (
            "+++\n"
            'section_id = "overview"\n'
            'created_round = "round-000000"\n'
            'tags = []\n'
            "+++\n\n"
        )
        (workspace_root / rel).write_text(
            frontmatter + seed_body, encoding="utf-8")
        created.append(rel)
    return created
