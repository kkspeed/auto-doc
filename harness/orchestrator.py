"""Round state machine + run loop for the Design Doc Evolution Harness.

Public API:
  - RoundOutcome: frozen dataclass with verdict + spawn_counts + elapsed
  - run_round(workspace_root, harness_config, round_id, variant_id) -> RoundOutcome
  - run_loop(workspace_root, harness_config, max_rounds=None,
             max_wall_clock_hours=None, variant_count=2) -> list[RoundOutcome]

The round flow is a linear function with early returns on rejection. See
docs/superpowers/specs/2026-05-31-...-design.md §3.1 for the full phase
sequence.
"""
from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness import claim_graph as cg
from harness import context as context_mod
from harness import round_ledger
from harness import verifiers
from harness.spawn import RoleOutput, spawn_role


# ----- Dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class RoundOutcome:
    round_id: str
    variant_id: str
    verdict: str
    reason: str | None = None
    rj_id: str | None = None
    elapsed_seconds: float = 0.0
    spawn_counts: dict = field(default_factory=dict)


# ----- Validators -----------------------------------------------------------
#
# These are passed to spawn_role's validator parameter. They raise ValueError
# on shape mismatch so spawn_role's validate-retry contract fires.


def validate_planner_json(d: dict) -> None:
    for key in ("round", "variant", "stance", "intent", "target_sections"):
        if key not in d:
            raise ValueError(f"planner.json missing {key!r}")
    if not isinstance(d["target_sections"], list):
        raise ValueError("planner.json target_sections must be a list")


def validate_designer_json(d: dict) -> None:
    for key in ("round", "variant", "patch_diff", "evidence", "claims"):
        if key not in d:
            raise ValueError(f"designer.json missing {key!r}")
    if not isinstance(d["claims"], list):
        raise ValueError("designer.json claims must be a list")
    if not isinstance(d["evidence"], list):
        raise ValueError("designer.json evidence must be a list")
    # Each claim must roundtrip through Claim.from_dict
    for c in d["claims"]:
        cg.Claim.from_dict(c)


def validate_reviewer_json(d: dict) -> None:
    for key in ("round", "variant", "decision", "rationale"):
        if key not in d:
            raise ValueError(f"reviewer.json missing {key!r}")
    if d["decision"] not in ("accept", "reject"):
        raise ValueError(
            f"reviewer.json decision must be accept|reject, got {d['decision']!r}"
        )
    # decision_proposals and attacks roundtrip via their dataclass from_dict
    for v in d.get("decision_proposals", []) or []:
        cg.DecisionProposalVerdict.from_dict(v)
    for a in d.get("attacks", []) or []:
        cg.Attack.from_dict(a)


def validate_verifier_c_json(d: dict) -> None:
    for key in ("round", "variant", "verdict", "per_claim"):
        if key not in d:
            raise ValueError(f"verification.json missing {key!r}")
    if d["verdict"] not in ("confirm", "dispute"):
        raise ValueError(
            f"verification.json verdict must be confirm|dispute, got {d['verdict']!r}"
        )


# ----- Helpers --------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds",
    )


def _log(workspace_root: Path, event: str, **fields) -> None:
    entry = {"ts": _now_iso(), "event": event, **fields}
    round_ledger.append_actions_log(workspace_root, entry)


PLANNER_PROMPT = (
    "You are the planner. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, stance, intent, target_sections (list), "
    "rejection_log_reviewed (list of rj-ids you considered), "
    "rationale_against_known_rejections (text). Output ONLY valid JSON."
)

DESIGNER_PROMPT = (
    "You are the designer. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, patch_diff (unified-diff text or empty string), "
    "evidence (list of {id, confidence, citations, claim, excerpt, ...}), "
    "claims (list of cl-*.json dicts). Output ONLY valid JSON."
)

REVIEWER_PROMPT = (
    "You are the reviewer. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, decision (accept|reject), rationale, optional "
    "rejection {reason_class, ...} on reject, optional decision_proposals "
    "(list of {proposed_id, verdict (approve|reject), rationale}) when the "
    "designer proposed new decisions, optional attacks (list of at-*.json "
    "dicts). Output ONLY valid JSON."
)

VERIFIER_C_PROMPT = (
    "You are Verifier C. Read the CONTEXT.md above plus the doc patch and "
    "cited evidence; emit JSON with fields: round, variant, verdict "
    "(confirm|dispute), per_claim (list of {claim_id, verdict (confirm|"
    "weak|dispute), rationale}), candidate_collisions_confirmed (list), "
    "candidate_collisions_rejected (list). Output ONLY valid JSON."
)


def _materialize_designer_output(
    workspace_root: Path, variant_id: str, parsed: dict,
) -> tuple[list[Path], list[str], list[str], list[str], list[str]]:
    """Materialize designer's parsed output to disk.

    Returns (materialized_paths_for_rollback, section_paths, claim_paths,
    attack_paths, evidence_paths) — the latter four are relative-to-workspace
    strings suitable for git add.
    """
    materialized: list[Path] = []
    section_paths: list[str] = []
    claim_paths: list[str] = []
    attack_paths: list[str] = []
    evidence_paths: list[str] = []

    # Evidence
    evidence_dir = workspace_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for ev in parsed.get("evidence", []) or []:
        ev_id = ev.get("id", "")
        if not ev_id:
            continue
        # Build TOML frontmatter from the evidence dict.
        fm_lines = []
        for key in ("id", "confidence", "claim", "excerpt", "match"):
            val = ev.get(key)
            if val is None:
                continue
            # Use triple-quoted string for multi-line safety
            escaped = str(val).replace('"""', '\\"\\"\\"')
            fm_lines.append(f'{key} = """{escaped}"""')
        text = "+++\n" + "\n".join(fm_lines) + "\n+++\n\n" + \
               str(ev.get("excerpt", "")) + "\n"
        ev_path = evidence_dir / f"{ev_id}.md"
        ev_path.write_text(text)
        materialized.append(ev_path)
        evidence_paths.append(f"evidence/{ev_id}.md")

    # Claims
    claims_dir = workspace_root / "variants" / "nodes" / variant_id / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    for claim in parsed.get("claims", []) or []:
        cl_id = claim.get("id", "")
        if not cl_id:
            continue
        cl_path = claims_dir / f"{cl_id}.json"
        cl_path.write_text(json.dumps(claim, indent=2, sort_keys=True))
        materialized.append(cl_path)
        claim_paths.append(
            f"variants/nodes/{variant_id}/claims/{cl_id}.json"
        )

    # patch_diff: if non-empty, apply with `git apply`. For v0, empty patch_diff
    # is a no-op.
    patch_diff = parsed.get("patch_diff", "") or ""
    if patch_diff.strip():
        import subprocess as _sp
        result = _sp.run(
            ["git", "-C", str(workspace_root), "apply", "--whitespace=nowarn"],
            input=patch_diff, text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            # Caller treats as cross-field-fail; clean up evidence + claims
            # we already wrote.
            for p in materialized:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            raise RuntimeError(
                f"git apply failed: {result.stderr.strip()}"
            )
        # Extract section paths from the patch_diff (lines starting with
        # `+++ b/`).
        for line in patch_diff.split("\n"):
            if line.startswith("+++ b/"):
                rel = line[len("+++ b/"):].strip()
                if rel.startswith(f"variants/nodes/{variant_id}/doc/"):
                    section_paths.append(rel)
                    materialized.append(workspace_root / rel)

    return materialized, section_paths, claim_paths, attack_paths, evidence_paths


def _materialize_reviewer_attacks(
    workspace_root: Path, variant_id: str, parsed: dict,
) -> tuple[list[Path], list[str]]:
    """Materialize reviewer's attacks (at-*.json) to disk. Returns
    (materialized_paths, attack_paths_for_git_add)."""
    attacks_dir = workspace_root / "variants" / "nodes" / variant_id / "attacks"
    materialized: list[Path] = []
    attack_paths: list[str] = []
    for at in parsed.get("attacks", []) or []:
        at_id = at.get("id", "")
        if not at_id:
            continue
        attacks_dir.mkdir(parents=True, exist_ok=True)
        at_path = attacks_dir / f"{at_id}.json"
        at_path.write_text(json.dumps(at, indent=2, sort_keys=True))
        materialized.append(at_path)
        attack_paths.append(
            f"variants/nodes/{variant_id}/attacks/{at_id}.json"
        )
    return materialized, attack_paths


def _discard_materialized(workspace_root: Path,
                          paths: list[Path]) -> None:
    """Remove new files or git-checkout HEAD for modified files."""
    import subprocess as _sp
    for p in paths:
        if not p.exists():
            continue
        # Was it tracked by git at HEAD?
        rel = p.relative_to(workspace_root)
        ls = _sp.run(
            ["git", "-C", str(workspace_root), "ls-files", "--error-unmatch",
             str(rel)],
            capture_output=True, text=True,
        )
        if ls.returncode == 0:
            # Modified tracked file → restore from HEAD
            _sp.check_call(
                ["git", "-C", str(workspace_root), "checkout", "HEAD",
                 "--", str(rel)],
            )
        else:
            # New file → unlink
            try:
                p.unlink()
            except FileNotFoundError:
                pass


# ----- run_round ------------------------------------------------------------


def run_round(
    workspace_root: Path,
    harness_config: dict,
    round_id: str,
    variant_id: str,
) -> RoundOutcome:
    """Execute one round on one variant. Happy-path-only in Task 2; failure
    branches and Flow A/C land in Tasks 3 and 4."""
    start_ts = time.monotonic()
    spawn_counts: dict[str, int] = {}
    _log(workspace_root, "round_start", round_id=round_id, variant_id=variant_id)

    # ---- Phase 1: Planner ----
    planner_ctx = context_mod.build_planner_context(
        workspace_root, round_id, variant_id,
    )
    planner_result = spawn_role(
        role="planner", harness_config=harness_config,
        context_md=planner_ctx, prompt=PLANNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_planner_json,
    )
    spawn_counts["planner"] = 1 + planner_result.retry_count
    if planner_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=planner_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "planner", planner_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="planner",
         verdict=planner_result.verdict,
         retry_count=planner_result.retry_count,
         elapsed_seconds=planner_result.elapsed_seconds)

    # ---- Phase 2: Designer ----
    designer_ctx = context_mod.build_designer_context(
        workspace_root, round_id, variant_id,
    )
    designer_result = spawn_role(
        role="designer", harness_config=harness_config,
        context_md=designer_ctx, prompt=DESIGNER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_designer_json,
    )
    spawn_counts["designer"] = 1 + designer_result.retry_count
    if designer_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=designer_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "designer", designer_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="designer",
         verdict=designer_result.verdict,
         retry_count=designer_result.retry_count,
         elapsed_seconds=designer_result.elapsed_seconds)
    materialized, section_paths, claim_paths, _att_unused, evidence_paths = \
        _materialize_designer_output(
            workspace_root, variant_id, designer_result.parsed,
        )
    _log(workspace_root, "materialize",
         round_id=round_id,
         evidence_count=len(evidence_paths),
         claim_count=len(claim_paths),
         attack_count=0,
         section_count=len(section_paths))

    # ---- Phase 5: Reviewer (Phases 3-4 verifiers added in Task 3) ----
    reviewer_ctx = context_mod.build_reviewer_context(
        workspace_root, round_id, variant_id,
    )
    reviewer_result = spawn_role(
        role="reviewer", harness_config=harness_config,
        context_md=reviewer_ctx, prompt=REVIEWER_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_reviewer_json,
    )
    spawn_counts["reviewer"] = 1 + reviewer_result.retry_count
    if reviewer_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=reviewer_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "reviewer", reviewer_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="reviewer",
         verdict=reviewer_result.verdict,
         retry_count=reviewer_result.retry_count,
         elapsed_seconds=reviewer_result.elapsed_seconds)
    att_materialized, attack_paths = _materialize_reviewer_attacks(
        workspace_root, variant_id, reviewer_result.parsed,
    )
    materialized.extend(att_materialized)

    # ---- Phase 6: Verifier C ----
    vc_ctx = context_mod.build_verifier_c_context(
        workspace_root, round_id, variant_id,
    )
    vc_result = spawn_role(
        role="verifier_c", harness_config=harness_config,
        context_md=vc_ctx, prompt=VERIFIER_C_PROMPT,
        workspace_root=workspace_root, round_id=round_id,
        variant_id=variant_id,
        validator=validate_verifier_c_json,
    )
    spawn_counts["verifier_c"] = 1 + vc_result.retry_count
    if vc_result.verdict != "ok":
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=vc_result.verdict,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "verifier_c", vc_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="verifier_c",
         verdict=vc_result.verdict,
         retry_count=vc_result.retry_count,
         elapsed_seconds=vc_result.elapsed_seconds)

    # ---- Phase 8: Final merge commit ----
    round_ledger.commit_merge(
        workspace_root, round_id=round_id, variant_id=variant_id,
        section_paths=section_paths, claim_paths=claim_paths,
        attack_paths=attack_paths, evidence_paths=evidence_paths,
    )
    _log(workspace_root, "commit", round_id=round_id, action="merge")
    _log(workspace_root, "round_end", round_id=round_id, verdict="merge")

    return RoundOutcome(
        round_id=round_id, variant_id=variant_id, verdict="merge",
        elapsed_seconds=time.monotonic() - start_ts,
        spawn_counts=spawn_counts,
    )


def run_loop(*args, **kwargs):
    """run_loop is implemented in Task 5."""
    raise NotImplementedError("run_loop lands in Task 5")
