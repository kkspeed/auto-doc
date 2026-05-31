"""Persistence + commit invocation primitives for the harness round loop.

Public API:
  - write_role_scratch(workspace_root, round_id, role, parsed) -> Path
  - write_rejection(workspace_root, round_id, variant_id, reason_class,
                    failed_phase, detail, reviewer_id=None) -> str   (rj_id)
  - append_actions_log(workspace_root, entry) -> None
  - commit_register_decision(workspace_root, new_decision_ids) -> None
  - commit_canonicalize(workspace_root, rewrites) -> None
  - commit_merge(workspace_root, round_id, variant_id, section_paths,
                 claim_paths, attack_paths, evidence_paths) -> None
  - commit_rejection(workspace_root, action, round_id, variant_id, rj_id,
                     reason, reviewer_id=None) -> None
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


_RJ_ID_RE = re.compile(r"^rj-(\d{6})\.md$")


def _toml_escape(s: str) -> str:
    """Escape a string for safe inclusion in a TOML double-quoted value.

    Backslash must be escaped before double-quote to avoid double-escaping.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_role_scratch(workspace_root: Path, round_id: str, role: str,
                       parsed: dict) -> Path:
    """Write rounds/<round_id>/scratch/<role>.json. Creates parent dirs."""
    scratch_dir = workspace_root / "rounds" / round_id / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    path = scratch_dir / f"{role}.json"
    path.write_text(json.dumps(parsed, indent=2, sort_keys=True))
    return path


def _next_rj_id(workspace_root: Path) -> str:
    rej_dir = workspace_root / "rejections"
    if not rej_dir.exists():
        return "rj-000001"
    max_n = 0
    for fp in rej_dir.glob("rj-*.md"):
        m = _RJ_ID_RE.match(fp.name)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"rj-{max_n + 1:06d}"


def write_rejection(
    workspace_root: Path,
    round_id: str,
    variant_id: str,
    reason_class: str,
    failed_phase: str,
    detail: str,
    reviewer_id: str | None = None,
) -> str:
    """Allocate the next rj-*.md id, write the file with TOML frontmatter +
    body, return the rj_id string."""
    rj_id = _next_rj_id(workspace_root)
    rej_dir = workspace_root / "rejections"
    rej_dir.mkdir(parents=True, exist_ok=True)

    fm_lines = [
        f'variant = "{_toml_escape(variant_id)}"',
        f'round_id = "{_toml_escape(round_id)}"',
        f'reason_class = "{_toml_escape(reason_class)}"',
        f'failed_phase = "{_toml_escape(failed_phase)}"',
    ]
    if reviewer_id is not None:
        fm_lines.append(f'reviewer_id = "{_toml_escape(reviewer_id)}"')

    text = "+++\n" + "\n".join(fm_lines) + "\n+++\n\n" + detail.rstrip() + "\n"
    (rej_dir / f"{rj_id}.md").write_text(text)
    return rj_id


def append_actions_log(workspace_root: Path, entry: dict) -> None:
    """Append one JSON line to workspace_root/actions.jsonl.

    Atomic w.r.t. SIGKILL at the line level: the write is followed by an
    explicit flush so partial trailing lines only occur if the process is
    killed mid-system-call. Sub-project 6's resume trims any partial line.
    """
    path = workspace_root / "actions.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()


# ----- Commit helpers --------------------------------------------------------


def _git_commit(workspace_root: Path, message: str) -> None:
    """Run git commit with the given message. Uses a baked-in harness identity
    so the call works on machines without a global user.name/user.email."""
    subprocess.check_call(
        ["git",
         "-c", "user.email=harness@localhost",
         "-c", "user.name=harness",
         "commit", "-q", "-m", message],
        cwd=workspace_root,
    )


def _git_add(workspace_root: Path, *paths: str) -> None:
    if not paths:
        return
    subprocess.check_call(
        ["git", "-C", str(workspace_root), "add", "-f", *paths],
    )


def commit_register_decision(
    workspace_root: Path,
    new_decision_ids: list[str],
) -> None:
    """Stage goal.toml + derived/decisions.json + actions.jsonl, commit with
    Action: register-decision."""
    _git_add(
        workspace_root,
        "goal.toml", "derived/decisions.json", "actions.jsonl",
    )
    ids_str = ", ".join(new_decision_ids)
    message = (
        f"feat(harness): register decisions ({ids_str})\n\n"
        "Action: register-decision\n"
    )
    _git_commit(workspace_root, message)


def commit_canonicalize(
    workspace_root: Path,
    rewrites: list[dict],
) -> None:
    """Stage rewritten cl-*.json files + derived/canonical_slug_registry.json
    + actions.jsonl, commit with Action: canonicalize.

    Each rewrite dict has 'path' (relative to workspace_root)."""
    rel_paths = sorted({r["path"] for r in rewrites})
    _git_add(workspace_root, *rel_paths,
             "derived/canonical_slug_registry.json", "actions.jsonl")
    count = len(rewrites)
    message = (
        f"feat(harness): canonicalize {count} position(s)\n\n"
        "Action: canonicalize\n"
    )
    _git_commit(workspace_root, message)


def commit_merge(
    workspace_root: Path,
    round_id: str,
    variant_id: str,
    section_paths: list[str],
    claim_paths: list[str],
    attack_paths: list[str],
    evidence_paths: list[str],
    score_delta: str | None = None,
    scorecard_path: str | None = None,
) -> None:
    """Stage all materialized files + actions.jsonl (+ scorecard.json when
    given), commit with Action: merge + Variant + Round (+ Score-Delta when
    given) trailers."""
    all_paths = list(section_paths) + list(claim_paths) + \
                list(attack_paths) + list(evidence_paths)
    if scorecard_path is not None:
        all_paths.append(scorecard_path)
    _git_add(workspace_root, *all_paths, "actions.jsonl")
    lines = [
        f"feat(harness): {round_id} {variant_id}",
        "",
        "Action: merge",
        f"Variant: {variant_id}",
        f"Round: {round_id}",
    ]
    if score_delta is not None:
        lines.append(f"Score-Delta: {score_delta}")
    message = "\n".join(lines) + "\n"
    _git_commit(workspace_root, message)


# Closed-vocab Reason values accepted by the commit-msg hook.
# Actions "spawn-failed" and "output-parse-fail" do NOT require a Reason
# trailer per TRAILER_REQUIREMENTS; including an invalid Reason would fail
# the hook. We omit the Reason trailer when the value isn't in this set.
_ALLOWED_REASONS = frozenset({
    "uncited-claim", "cross-field-fail", "vacuous-position",
    "proposal-rejected", "scope-violation", "immutability-violation",
    "phantom-claim", "dangling-evidence", "silent-goal-toml-edit",
    "score-regression",
})


def commit_rejection(
    workspace_root: Path,
    action: str,
    round_id: str,
    variant_id: str,
    rj_id: str,
    reason: str,
    reviewer_id: str | None = None,
) -> None:
    """Stage rejections/<rj_id>.md + actions.jsonl, commit with the
    failure-class Action trailer + Variant + Round + Reason (when the
    reason is a valid hook-allowed value) + Reviewer (when applicable)."""
    _git_add(
        workspace_root,
        f"rejections/{rj_id}.md", "actions.jsonl",
    )
    lines = [
        f"chore(harness): {action} for {round_id} {variant_id}",
        "",
        f"Action: {action}",
        f"Variant: {variant_id}",
        f"Round: {round_id}",
    ]
    # Only include the Reason trailer when it's a valid hook-allowed value.
    # "spawn-failed" and "output-parse-fail" are action names, not reasons,
    # and those actions don't require a Reason trailer per TRAILER_REQUIREMENTS.
    if reason in _ALLOWED_REASONS:
        lines.append(f"Reason: {reason}")
    if reviewer_id is not None:
        lines.append(f"Reviewer: {reviewer_id}")
    message = "\n".join(lines) + "\n"
    _git_commit(workspace_root, message)
