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

    Returns an empty dict if derived/decisions.json is missing, malformed,
    or has a non-dict 'decisions' payload (defensive against upstream bugs).
    """
    p = workspace_root / "derived" / "decisions.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    decisions = data.get("decisions", {})
    return decisions if isinstance(decisions, dict) else {}


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


def _load_goal_meta(workspace_root: Path) -> tuple[str, str]:
    """Return (title, description) from goal.toml's [goal] table."""
    p = workspace_root / "goal.toml"
    if not p.exists():
        return ("", "")
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return ("", "")
    g = data.get("goal", {})
    return (g.get("title", ""), g.get("description", ""))


def _render_goal_and_pointers(title: str, description: str,
                              pointers: list[str]) -> str:
    desc_block = [description, ""] if description else []
    lines = ["## Goal", "", f"**{title}**", "", *desc_block,
             "## Read these first (on disk)", "",
             "Read every path below before answering; the summary tables are "
             "an index, not a substitute for the source.", ""]
    for ptr in pointers:
        lines.append(f"- `{ptr}`")
    lines.append("")
    return "\n".join(lines)


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
    title, description = _load_goal_meta(workspace_root)
    out.append(_render_goal_and_pointers(
        title, description, [
            "goal.toml",
            f"variants/nodes/{variant_id}/doc/",
            f"rounds/{round_id}/scratch/planner.json",
            "evidence/",
        ]))
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
        if not pd:
            continue
        pd_id = pd.get("id")
        if not isinstance(pd_id, str) or not pd_id:
            continue   # skip malformed proposals (no usable id)
        if pd_id not in decisions:
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
    'sha (canonicalize) subject' lines. Empty list on git failure or no
    commits.

    The "(canonicalize)" annotation is appended so the literal substring
    "canonicalize" (not "canonicalizations") appears in the rendered output
    — useful both for grepping audit logs and for downstream consumers that
    key on the action name rather than the section header."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_root), "log",
             "--grep=^Action: canonicalize", f"-n{limit}",
             "--format=%h (canonicalize) %s"],
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
    title, description = _load_goal_meta(workspace_root)
    out.append(_render_goal_and_pointers(
        title, description, [
            f"rounds/{round_id}/patch.diff",
            f"variants/nodes/{variant_id}/claims/",
            "evidence/",
            f"variants/nodes/{variant_id}/doc/",
        ]))
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
    try:
        data = json.loads(designer_json.read_text(
            encoding="utf-8", errors="replace"
        ))
        for c in data.get("claims", []):
            pd = c.get("proposed_decision")
            if pd:
                pending.append(pd)
    except (FileNotFoundError, json.JSONDecodeError):
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
    claim_graph_cfg = harness_cfg.get("claim_graph", {})
    if not isinstance(claim_graph_cfg, dict):
        claim_graph_cfg = {}
    threshold = claim_graph_cfg.get("bootstrap_registry_size_threshold", 5)
    # Count only non-retired decisions for the bootstrap-permissive threshold.
    # Retired decisions don't affect what's actively in play, so they
    # shouldn't push the reviewer out of the bootstrap-permissive posture.
    size = sum(
        1 for d in decisions.values()
        if d.get("status") in ("open", "proposed")
    )
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
    title, description = _load_goal_meta(workspace_root)
    out = [_header("verifier_c", round_id, variant_id, goal_version), ""]
    out.append(_render_goal_and_pointers(
        title, description, [
            f"rounds/{round_id}/patch.diff",
            f"variants/nodes/{variant_id}/claims/",
            "evidence/",
        ]))
    out.append(_render_registered_decisions(decisions))
    return "\n".join(out)
