"""Assemble morning_brief.md from workspace state at run pause.

Imports the section renderers from claim_graph; adds run-level sections (score
trajectory, still-weak, rejected-this-run, look-at-first). Rendered once by
run_loop when the loop stops. See spec
docs/superpowers/specs/2026-05-31-morning-brief-and-scorecard-design.md §7.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harness import claim_graph as cg


_SCORE_DELTA_RE = re.compile(r"^Score-Delta:\s*(.+)$", re.MULTILINE)
_ROUND_RE = re.compile(r"^Round:\s*(round-\d{6})$", re.MULTILINE)
_VARIANT_RE = re.compile(r"^Variant:\s*(v-\d{3})$", re.MULTILINE)
_REASON_RE = re.compile(r"^Reason:\s*(\S+)$", re.MULTILINE)
_ACTION_RE = re.compile(r"^Action:\s*(\S+)$", re.MULTILINE)


def _git_log_messages(workspace_root: Path, since_sha: str | None) -> list[str]:
    """Return commit message bodies in since_sha..HEAD (or all history)."""
    rev = f"{since_sha}..HEAD" if since_sha else "HEAD"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workspace_root), "log", rev,
             "--format=%B%x00"],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
    except subprocess.CalledProcessError:
        return []
    return [m.strip() for m in out.split("\x00") if m.strip()]


# ----- Run-level section renderers -------------------------------------------


def render_score_trajectory(rows: list[dict]) -> str:
    """rows: [{"round","variant","score_delta"}], oldest first."""
    if not rows:
        return "## Score trajectory\n\nNo merges this run.\n"
    lines = ["## Score trajectory", "",
             "| Round | Variant | Score-Delta |", "|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['round']} | {r['variant']} | {r['score_delta']} |")
    lines.append("")
    return "\n".join(lines)


def render_still_weak(weak: list[dict]) -> str:
    """weak: [{"claim_id","rationale"}]."""
    if not weak:
        return "## Still weak\n\nNo claims flagged weak this run.\n"
    lines = ["## Still weak", "", "| Claim | Why weak |", "|---|---|"]
    for w in weak:
        lines.append(f"| {w['claim_id']} | {w['rationale']} |")
    lines.append("")
    return "\n".join(lines)


def render_rejected_this_run(by_reason: dict) -> str:
    """by_reason: {reason_class: count}."""
    if not by_reason:
        return "## Rejected this run\n\nNo rounds rejected this run.\n"
    lines = ["## Rejected this run", "", "| Reason class | Count |",
             "|---|---|"]
    for reason in sorted(by_reason):
        lines.append(f"| {reason} | {by_reason[reason]} |")
    lines.append("")
    return "\n".join(lines)


def render_look_at_first(items: list[str]) -> str:
    """items: list of freeform strings, one bullet per item."""
    if not items:
        return ("## What I'd ask you to look at first\n\n"
                "Nothing urgent - the run was clean.\n")
    lines = ["## What I'd ask you to look at first", ""]
    for it in items:
        lines.append(f"- {it}")
    lines.append("")
    return "\n".join(lines)


# ----- Gathering -------------------------------------------------------------


def _gather_trajectory(messages: list[str]) -> list[dict]:
    rows = []
    for msg in reversed(messages):  # oldest first
        action_m = _ACTION_RE.search(msg)
        if not action_m or action_m.group(1) != "merge":
            continue
        sd = _SCORE_DELTA_RE.search(msg)
        rnd = _ROUND_RE.search(msg)
        var = _VARIANT_RE.search(msg)
        if rnd and var:
            rows.append({
                "round": rnd.group(1), "variant": var.group(1),
                "score_delta": sd.group(1).strip() if sd else "(baseline)",
            })
    return rows


def _gather_rejected(messages: list[str]) -> dict:
    by_reason: dict[str, int] = {}
    reject_actions = {
        "reviewer-rejected", "phase-a-fail", "phase-b-fail",
        "phase-c-dispute", "spawn-failed", "output-parse-fail",
        "score-regression",
    }
    for msg in messages:
        action_m = _ACTION_RE.search(msg)
        if not action_m or action_m.group(1) not in reject_actions:
            continue
        reason_m = _REASON_RE.search(msg)
        reason = reason_m.group(1) if reason_m else action_m.group(1)
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return by_reason


def _gather_still_weak(workspace_root: Path) -> list[dict]:
    """Verifier-C weak verdicts from rounds/*/scratch/verifier_c.json."""
    weak: list[dict] = []
    rounds_root = workspace_root / "rounds"
    if not rounds_root.exists():
        return weak
    for vc in sorted(rounds_root.glob("round-*/scratch/verifier_c.json")):
        try:
            data = json.loads(vc.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for pc in data.get("per_claim", []):
            if pc.get("verdict") == "weak":
                weak.append({
                    "claim_id": pc.get("claim_id", "?"),
                    "rationale": pc.get("rationale", ""),
                })
    return weak


def _gather_look_at_first(workspace_root: Path, rejected: dict) -> list[str]:
    """Ranking heuristic (spec 5.3): contested decisions > decisional
    asymmetry > regressed scores > stale proposals. v0 surfaces the cheap,
    git-derivable signals; richer ranking is a v0.1 concern."""
    items: list[str] = []
    if rejected.get("score-regression"):
        items.append(
            f"{rejected['score-regression']} round(s) hit a score regression "
            "- review the rejections/ entries.")
    return items


# ----- Top-level assembly ----------------------------------------------------


def render_morning_brief(workspace_root: Path,
                         since_sha: str | None = None) -> str:
    messages = _git_log_messages(workspace_root, since_sha)
    rejected = _gather_rejected(messages)
    parts = [
        "# Morning brief\n",
        # Claim-graph sections: v0 passes empty data to the existing
        # renderers (live detector wiring is a documented deferred item;
        # the renderers emit friendly empty-states for []).
        cg.render_position_collisions_table([]),
        cg.render_decisional_asymmetry_table([]),
        cg.render_pending_registry_changes([], [], []),
        cg.render_canonicalizations_applied([], []),
        cg.render_stale_proposals_table([]),
        render_score_trajectory(_gather_trajectory(messages)),
        render_still_weak(_gather_still_weak(workspace_root)),
        render_rejected_this_run(rejected),
        render_look_at_first(_gather_look_at_first(workspace_root, rejected)),
        "\n_\"Survived adversarial review\" is deferred to v0.1._\n",
    ]
    return "\n".join(parts)
