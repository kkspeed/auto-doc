"""Per-variant multi-dimensional scorecard for the Design Doc Evolution Harness.

Pure functions: no git, no global state. The orchestrator gathers inputs and
calls compute_dimensions at Phase 6.5; the gate functions decide whether the
round may merge. See docs/superpowers/specs/2026-05-31-morning-brief-and-
scorecard-design.md §3-4.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


DIMENSIONS = (
    "groundedness",
    "goal_alignment",
    "technical_correctness",
    "completeness",
    "coherence",
    "constitution_compliance",
)

_CITE_RE = re.compile(r"\[\^ev-(\d{6})\]")
_SECTION_ID_RE = re.compile(
    r'^\s*section_id\s*=\s*"([^"]+)"\s*$', re.MULTILINE,
)


# ----- Evidence resolution ---------------------------------------------------


# NOTE: intentionally mirrors verifiers._load_evidence_frontmatter + the
# superseded_by check; duplicated (not imported) to keep scorecard.py pure.
def _evidence_resolves_ok(evidence_root: Path, ev_id: str) -> bool:
    """True iff evidence/<ev_id>.md exists, parses, and is not superseded."""
    ev_path = evidence_root / f"{ev_id}.md"
    if not ev_path.exists():
        return False
    text = ev_path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("+++"):
        return False
    end = text.find("+++", 3)
    if end == -1:
        return False
    try:
        meta = tomllib.loads(text[3:end])
    except tomllib.TOMLDecodeError:
        return False
    return not meta.get("superseded_by")


# ----- Mechanical dimensions -------------------------------------------------


def compute_groundedness(variant_claims_dir: Path, evidence_root: Path) -> float:
    """Fraction of cl-*.json claims whose every evidence_id resolves to an
    existing, non-superseded evidence file. 0 claims -> 1.0."""
    if not variant_claims_dir.exists():
        return 1.0
    claim_files = sorted(variant_claims_dir.glob("cl-*.json"))
    if not claim_files:
        return 1.0
    grounded = 0
    for cf in claim_files:
        try:
            data = json.loads(cf.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # malformed claim is not grounded
        ev_ids = data.get("evidence_ids", []) or []
        if all(_evidence_resolves_ok(evidence_root, e) for e in ev_ids):
            grounded += 1
    return grounded / len(claim_files)


def _section_ids(variant_doc_dir: Path) -> set[str]:
    ids: set[str] = set()
    if not variant_doc_dir.exists():
        return ids
    for md in variant_doc_dir.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        m = _SECTION_ID_RE.search(text)
        if m:
            ids.add(m.group(1))
    return ids


def compute_completeness(decisions: list[dict], variant_doc_dir: Path) -> float:
    """Fraction of required decisions (status in {open, proposed}) that have a
    doc section whose section_id matches the decision id. 0 required -> 1.0."""
    required = [d["id"] for d in decisions
                if d.get("status") in ("open", "proposed")]
    if not required:
        return 1.0
    present = _section_ids(variant_doc_dir)
    covered = sum(1 for did in required if did in present)
    return covered / len(required)


def compute_coherence(variant_doc_dir: Path, evidence_root: Path) -> float:
    """1 - (dead [^ev-*] citations / total citations). 0 citations -> 1.0.
    A citation is dead if its evidence is missing or superseded."""
    total = 0
    dead = 0
    if variant_doc_dir.exists():
        for md in variant_doc_dir.glob("*.md"):
            body = md.read_text(encoding="utf-8", errors="replace")
            for m in _CITE_RE.finditer(body):
                total += 1
                if not _evidence_resolves_ok(evidence_root, f"ev-{m.group(1)}"):
                    dead += 1
    if total == 0:
        return 1.0
    return 1.0 - (dead / total)


def compute_constitution_compliance(round_actions: list[dict]) -> float:
    """1 - (denied actions / total actions). 0 actions -> 1.0."""
    if not round_actions:
        return 1.0
    denied = sum(1 for a in round_actions if a.get("denied"))
    return 1.0 - (denied / len(round_actions))


# ----- Judgment dimensions ---------------------------------------------------


def compute_vc_confirm_rate(vc_per_claim: list[dict]) -> float | None:
    """confirmed / (confirmed + weak) over Verifier-C per_claim verdicts.
    Returns None when there are no confirm/weak verdicts (VC absent or empty)."""
    confirm = sum(1 for p in vc_per_claim if p.get("verdict") == "confirm")
    weak = sum(1 for p in vc_per_claim if p.get("verdict") == "weak")
    denom = confirm + weak
    if denom == 0:
        return None
    return confirm / denom


def compute_technical_correctness(
    reviewer_score: float, vc_confirm_rate: float | None,
) -> float:
    """reviewer_score x vc_confirm_rate when VC ran, else reviewer_score."""
    if vc_confirm_rate is None:
        return reviewer_score
    return reviewer_score * vc_confirm_rate


# ----- Aggregate + gate ------------------------------------------------------


def compute_dimensions(
    *,
    variant_claims_dir: Path,
    variant_doc_dir: Path,
    evidence_root: Path,
    decisions: list[dict],
    round_actions: list[dict],
    reviewer_goal_alignment: float,
    reviewer_technical_correctness: float,
    vc_per_claim: list[dict],
) -> dict:
    """Compute all six dimensions. Returns {dim: float} keyed by DIMENSIONS."""
    vc_rate = compute_vc_confirm_rate(vc_per_claim)
    return {
        "groundedness": compute_groundedness(variant_claims_dir, evidence_root),
        "goal_alignment": reviewer_goal_alignment,
        "technical_correctness": compute_technical_correctness(
            reviewer_technical_correctness, vc_rate),
        "completeness": compute_completeness(decisions, variant_doc_dir),
        "coherence": compute_coherence(variant_doc_dir, evidence_root),
        "constitution_compliance": compute_constitution_compliance(
            round_actions),
    }


def evaluate_gate(
    prior_dimensions: dict | None,
    new_dimensions: dict,
    tolerance: float,
) -> tuple[bool, str]:
    """Merge gate (delta tolerance). Returns (passed, detail).

    Bootstrap (no prior) always passes. Otherwise: pass iff at least one shared
    dimension strictly improved AND no shared dimension dropped more than
    `tolerance` below its prior value.
    """
    if prior_dimensions is None:
        return True, "bootstrap"
    shared = [d for d in new_dimensions if d in prior_dimensions]
    improved = any(new_dimensions[d] > prior_dimensions[d] for d in shared)
    regressions = [
        d for d in shared
        if new_dimensions[d] < prior_dimensions[d] - tolerance
    ]
    if regressions:
        worst = ", ".join(
            f"{d}: {prior_dimensions[d]:.2f}->{new_dimensions[d]:.2f}"
            for d in regressions
        )
        return False, f"regressed beyond tolerance: {worst}"
    if not improved:
        return False, "no dimension improved"
    return True, "ok"


def format_score_delta(prior_dimensions: dict, new_dimensions: dict) -> str:
    """Signed two-decimal per-dimension delta, in DIMENSIONS order."""
    parts = []
    for d in DIMENSIONS:
        delta = new_dimensions.get(d, 0.0) - prior_dimensions.get(d, 0.0)
        parts.append(f"{d}={delta:+.2f}")
    return " ".join(parts)


# ----- scorecard.json I/O ----------------------------------------------------


def build_scorecard(variant_id: str, round_id: str, dimensions: dict) -> dict:
    """Assemble a scorecard dict for JSON serialization."""
    return {
        "variant": variant_id,
        "round": round_id,
        "dimensions": dimensions,
    }


def load_scorecard(scorecard_path: Path) -> dict | None:
    if not scorecard_path.exists():
        return None
    try:
        return json.loads(scorecard_path.read_text())
    except json.JSONDecodeError:
        return None  # malformed JSON -> treat as no prior baseline
    # OSError (unreadable existing file) intentionally propagates: a corrupt
    # scorecard must not silently disable the gate and overwrite the baseline.


def write_scorecard(scorecard_path: Path, scorecard: dict) -> None:
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        json.dumps(scorecard, indent=2, sort_keys=True))
