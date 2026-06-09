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
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from harness import claim_graph as cg
from harness import context as context_mod
from harness import round_ledger
from harness import verifiers
from harness import scorecard as scorecard_mod
from harness import morning_brief as morning_brief_mod
from harness import bootstrap
from harness.round_ledger import _ALLOWED_REASONS
from harness.spawn import RoleOutput, spawn_role


_ID_RE = re.compile(r"^(cl|ev|at)-\d{6}$")
_EV_ID_RE = re.compile(r"^ev-\d{6}$")


def _toml_basic_str_escape(s: str) -> str:
    """Escape a string for safe inclusion in a TOML double-quoted basic string.

    Per TOML spec, basic strings escape: backslash, double-quote, and
    control characters (\\b, \\t, \\n, \\f, \\r). Other characters appear
    literally.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\b", "\\b").replace("\t", "\\t").replace("\n", "\\n")
    s = s.replace("\f", "\\f").replace("\r", "\\r")
    return s


# ----- Dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class RoundOutcome:
    round_id: str
    variant_id: str
    verdict: str
    reason: str | None = None
    rj_id: str | None = None
    failed_phase: str | None = None
    detail: str | None = None
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
    # No patch_diff: the designer edits doc section files directly with its
    # Write/Edit tools; the orchestrator derives the change set from git. The
    # JSON payload carries only evidence + claims metadata.
    for key in ("round", "variant", "evidence", "claims"):
        if key not in d:
            raise ValueError(f"designer.json missing {key!r}")
    if not isinstance(d["claims"], list):
        raise ValueError("designer.json claims must be a list")
    if not isinstance(d["evidence"], list):
        raise ValueError("designer.json evidence must be a list")
    evidence_ids = set()
    for ev in d["evidence"]:
        if not isinstance(ev, dict):
            raise ValueError("designer.json evidence item must be an object")
        ev_id = ev.get("id")
        if not isinstance(ev_id, str) or not _EV_ID_RE.match(ev_id):
            raise ValueError(
                f"designer.json evidence id invalid: {ev_id!r}")
        for k in ("confidence", "citations", "claim", "excerpt"):
            if k not in ev:
                raise ValueError(
                    f"designer.json evidence {ev_id} missing {k!r}")
        if ev_id in evidence_ids:
            raise ValueError(
                f"designer.json duplicate evidence id {ev_id!r}")
        evidence_ids.add(ev_id)
    # Claim ids are assigned by the orchestrator (_assign_claim_ids overwrites
    # whatever the designer emits), so the designer need NOT supply one —
    # requiring a field we immediately discard is a needless failure mode.
    # Every OTHER required field is checked, and we aggregate all problems across
    # all claims into a single message so one correction pass can fix them all at
    # once instead of failing on one missing field at a time.
    claim_errors: list[str] = []
    for i, c in enumerate(d["claims"]):
        if not isinstance(c, dict):
            claim_errors.append(f"claims[{i}] must be a JSON object")
            continue
        missing = [f for f in ("section_id", "decision_id", "claim_type",
                               "evidence_ids", "assertion") if f not in c]
        if missing:
            claim_errors.append(
                f"claims[{i}] missing required field(s): {', '.join(missing)} "
                "(do NOT include 'id' — the harness assigns it)")
            continue
        try:
            # from_dict also enforces id presence; supply a placeholder for the
            # orchestrator-assigned id so the real checks (claim_type enum,
            # decision_id/position slugs, conditional fields) still run.
            cg.Claim.from_dict(c if "id" in c else {**c, "id": "cl-000000"})
        except Exception as e:
            claim_errors.append(f"claims[{i}]: {e}")
            continue
        for ref in c.get("evidence_ids", []) or []:
            # A claim may cite this pass's inline evidence OR an evidence id
            # already materialized this round by the repo adapter (designer
            # query pass). We can't see disk here, so accept any well-formed
            # ev id not in the inline set; Verifier A (cite-resolution) is the
            # authority that every cite resolves to a real evidence/ev-*.md.
            if ref not in evidence_ids and not _EV_ID_RE.match(ref or ""):
                claim_errors.append(
                    f"claims[{i}] cites {ref!r} which is neither this pass's "
                    "evidence nor a valid ev-NNNNNN id")
    if claim_errors:
        raise ValueError(
            f"designer.json has {len(claim_errors)} claim error(s):\n- "
            + "\n- ".join(claim_errors))


def validate_reviewer_json(d: dict) -> None:
    for key in ("round", "variant", "decision", "rationale",
                "goal_alignment", "technical_correctness"):
        if key not in d:
            raise ValueError(f"reviewer.json missing {key!r}")
    if d["decision"] not in ("accept", "reject"):
        raise ValueError(
            f"reviewer.json decision must be accept|reject, got {d['decision']!r}"
        )
    for key in ("goal_alignment", "technical_correctness"):
        v = d[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(
                f"reviewer.json {key} must be a float in [0,1], got {v!r}"
            )
    # LLM-judged quality dimensions. Optional for backward/forward
    # compatibility: when present they refine the mechanical score (see
    # scorecard._cap); when absent the scorecard falls back to the mechanical
    # value, so a flaky omission degrades gracefully instead of hard-failing
    # an overnight round. Validated only when supplied.
    for key in ("groundedness", "completeness", "coherence"):
        if key not in d:
            continue
        v = d[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(
                f"reviewer.json {key} must be a float in [0,1], got {v!r}"
            )
    # decision_proposals and attacks. Aggregate every problem into one message
    # (so a single correction pass fixes them all, not one field at a time), and
    # do NOT require an attack 'id' — the orchestrator assigns it
    # (_assign_attack_ids), so requiring a field we discard is a needless failure
    # mode.
    errors: list[str] = []
    for i, v in enumerate(d.get("decision_proposals", []) or []):
        try:
            cg.DecisionProposalVerdict.from_dict(v)
        except Exception as e:
            errors.append(f"decision_proposals[{i}]: {e}")
    for i, a in enumerate(d.get("attacks", []) or []):
        errors.extend(_attack_field_errors(i, a))
    if errors:
        raise ValueError(
            f"reviewer.json has {len(errors)} attack/proposal error(s):\n- "
            + "\n- ".join(errors))


# Required fields per attack type (mirrors cg.Attack.from_dict), EXCLUDING the
# orchestrator-assigned 'id'. propose_canonicalization also requires 'scope'
# when kind == "position" (handled below).
_ATTACK_REQUIRED_FIELDS = {
    "dispute_claim": ("target_claim_id", "argument"),
    "propose_decision_cut": ("target_decision_id", "rationale"),
    "propose_canonicalization": ("kind", "from", "to", "confidence",
                                 "rationale"),
}


def _attack_field_errors(i: int, a: dict) -> list[str]:
    """Return ALL schema problems for one attack (empty if valid), surfacing
    every missing required field at once rather than failing on the first."""
    if not isinstance(a, dict):
        return [f"attacks[{i}] must be a JSON object"]
    at_type = a.get("at_type")
    if at_type not in _ATTACK_REQUIRED_FIELDS:
        return [f"attacks[{i}] at_type must be one of "
                f"{sorted(_ATTACK_REQUIRED_FIELDS)}, got {at_type!r}"]
    required = list(_ATTACK_REQUIRED_FIELDS[at_type])
    if at_type == "propose_canonicalization" and a.get("kind") == "position":
        required.append("scope")
    missing = [f for f in required if f not in a]
    if missing:
        return [f"attacks[{i}] ({at_type}) missing required field(s): "
                f"{', '.join(missing)} (do NOT include 'id' — the harness "
                "assigns it)"]
    # All required fields present → run the dataclass for enum/slug checks,
    # supplying a placeholder for the orchestrator-assigned id.
    try:
        cg.Attack.from_dict(a if "id" in a else {**a, "id": "at-000000"})
    except Exception as e:
        return [f"attacks[{i}]: {e}"]
    return []


def validate_verifier_c_json(d: dict) -> None:
    for key in ("round", "variant", "verdict", "per_claim"):
        if key not in d:
            raise ValueError(f"verification.json missing {key!r}")
    if d["verdict"] not in ("confirm", "dispute"):
        raise ValueError(
            f"verification.json verdict must be confirm|dispute, got {d['verdict']!r}"
        )
    # Optional diff-scoped paragraph-grounding score (feeds the scorecard's
    # groundedness dimension). Optional-with-fallback: an omission degrades to
    # the mechanical value rather than hard-failing the round.
    if "groundedness" in d:
        g = d["groundedness"]
        if not isinstance(g, (int, float)) or not (0.0 <= g <= 1.0):
            raise ValueError(
                f"verification.json groundedness must be a float in [0,1], "
                f"got {g!r}")


def validate_seed_judge_json(d: dict) -> None:
    """The seed judge scores the seed doc on every scorecard dimension so the
    round-0 baseline reflects the doc's real quality rather than the mechanical
    'empty input -> 1.0' defaults. Every dimension is REQUIRED (no fallback):
    the whole point is that no metric is assumed perfect."""
    for key in scorecard_mod.DIMENSIONS:
        if key not in d:
            raise ValueError(f"seed_judge.json missing dimension {key!r}")
        v = d[key]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise ValueError(
                f"seed_judge.json {key} must be a float in [0,1], got {v!r}"
            )


def validate_designer_query_json(d: dict) -> None:
    """Designer repo-query pass: emits the repo questions to resolve before
    authoring. repo_queries may be empty (the designer needs nothing new)."""
    for key in ("round", "variant", "repo_queries"):
        if key not in d:
            raise ValueError(f"designer_query.json missing {key!r}")
    if not isinstance(d["repo_queries"], list):
        raise ValueError("designer_query.json repo_queries must be a list")
    for q in d["repo_queries"]:
        if not isinstance(q, dict):
            raise ValueError("designer_query.json repo_query must be an object")
        if not isinstance(q.get("question"), str) or not q["question"].strip():
            raise ValueError(
                "designer_query.json repo_query missing non-empty 'question'")


def validate_repo_adapter_json(d: dict) -> None:
    """Repo adapter: returns one Evidence-shaped record for a query."""
    for key in ("confidence", "citations", "claim", "excerpt"):
        if key not in d:
            raise ValueError(f"repo_adapter.json missing {key!r}")
    if d["confidence"] not in ("high", "medium", "low"):
        raise ValueError(
            f"repo_adapter.json confidence must be high|medium|low, "
            f"got {d['confidence']!r}")
    if not isinstance(d["citations"], list):
        raise ValueError("repo_adapter.json citations must be a list")
    if not isinstance(d["excerpt"], str) or not d["excerpt"].strip():
        raise ValueError("repo_adapter.json excerpt must be a non-empty string")


# ----- Helpers --------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds",
    )


def _log(workspace_root: Path, event: str, **fields) -> None:
    entry = {"ts": _now_iso(), "event": event, **fields}
    round_ledger.append_actions_log(workspace_root, entry)


# Rejection details can be long (excerpt diffs, multi-line stderr). The full
# text always lives in rejections/rj-*.md; actions.jsonl keeps a bounded copy
# so the cause is grep-able without opening the round's rejection file. The
# cap is generous enough to hold a full score-regression breakdown plus the
# preserved-artifact pointer (the most useful hint, which trails the detail).
_DETAIL_LOG_LIMIT = 2000


def _truncate_detail(detail: str) -> str:
    if len(detail) <= _DETAIL_LOG_LIMIT:
        return detail
    return detail[:_DETAIL_LOG_LIMIT] + f"… [+{len(detail) - _DETAIL_LOG_LIMIT} chars]"


def _current_head_sha(workspace_root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except subprocess.CalledProcessError:
        return None


def _preserved_artifact_pointer(workspace_root: Path, round_id: str) -> str:
    """Pointer to the round's preserved (gitignored) debug artifacts.

    On rejection, _reject discards the materialized doc/claim/evidence files,
    so a post-mortem `git diff` shows nothing. But the designer's exact output
    survives on disk because rounds/*/patch.diff and rounds/*/scratch/ are
    gitignored (and _discard_materialized never lists them): patch.diff is the
    literal doc mutation that was applied-then-rolled-back, and the scratch
    JSONs hold the raw role outputs (reviewer scores + rationale, VC verdicts).
    Returns a "see also" block naming the ones that exist, or "" if none do."""
    round_dir = workspace_root / "rounds" / round_id
    candidates = [
        round_dir / "patch.diff",
        round_dir / "scratch" / "designer.json",
        round_dir / "scratch" / "reviewer.json",
        round_dir / "scratch" / "verifier_c.json",
    ]
    present = [p for p in candidates if p.exists()]
    if not present:
        return ""
    lines = "\n".join(
        f"  - {p.relative_to(workspace_root)}" for p in present)
    return ("\n\nThe applied diff was rolled back on rejection. The designer's "
            "raw output survives on disk (gitignored) for inspection:\n" + lines)


def _read_round_actions(workspace_root: Path, round_id: str) -> list[dict]:
    """Return actions.jsonl entries tagged with this round_id."""
    path = workspace_root / "actions.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("round_id") == round_id or entry.get("round") == round_id:
            out.append(entry)
    return out


PLANNER_PROMPT = (
    "You are the planner. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, stance, intent, target_sections (list), "
    "rejection_log_reviewed (list of rj-ids you considered), "
    "rationale_against_known_rejections (text). Output ONLY valid JSON."
)

DESIGNER_PROMPT = (
    "You are the designer. Read the CONTEXT.md above, then make your doc edits "
    "by WRITING FILES DIRECTLY with your Write/Edit tools — do NOT emit a patch "
    "or diff; the harness derives the patch from git. "
    "You may ONLY create or modify Markdown section files under "
    "variants/nodes/<variant>/doc/ for THIS variant (e.g. "
    "variants/nodes/<variant>/doc/01-my-section.md); editing any other path "
    "(another variant, evidence/, claims/, goal.toml, repo files) rejects the "
    "whole round. Each section file MUST begin with a +++ TOML frontmatter fence "
    "(section_id, tags — include \"decided\" only when the section's claims are "
    "settled) and put [^ev-NNNNNN] citations inline in the prose, using the SAME "
    "evidence ids you list below. "
    "Then emit a SINGLE JSON object (your only stdout) with fields: round, "
    "variant, evidence (list), claims (list) — NO patch_diff. "
    "Each EVIDENCE item is an object with REQUIRED fields: id (string "
    "'ev-NNNNNN', six digits), confidence (high|medium|low), citations (list), "
    "claim (string), excerpt (string). "
    "Each CLAIM is an object with REQUIRED fields: section_id (slug of the doc "
    "section it supports), decision_id (slug), claim_type (exactly one of: "
    "decision, observation, inference, out_of_scope, unresolved), evidence_ids "
    "(list of 'ev-NNNNNN' strings citing this round's evidence — may be empty), "
    "assertion (string). Conditional fields: claim_type decision|observation|"
    "inference ALSO requires position (a slug); claim_type out_of_scope requires "
    "out_of_scope_rationale and MUST omit position; claim_type unresolved MUST "
    "omit position. Do NOT include a claim 'id' — the harness assigns it. "
    "Before answering, read every path listed under 'Read these first (on "
    "disk)' in the CONTEXT above; do not rely on the summary tables alone. "
    "Output ONLY valid JSON."
)

REVIEWER_PROMPT = (
    "You are the reviewer. Read the CONTEXT.md above and emit JSON with "
    "fields: round, variant, decision (accept|reject), rationale, optional "
    "rejection {reason_class, ...} on reject, optional decision_proposals "
    "(list of {proposed_id, verdict (approve|reject), rationale}) when the "
    "designer proposed new decisions, optional attacks (list). "
    "Each ATTACK is an object whose REQUIRED fields depend on its at_type "
    "(exactly one of dispute_claim, propose_decision_cut, "
    "propose_canonicalization). Do NOT include an attack 'id' — the harness "
    "assigns it. Required fields by type: "
    "dispute_claim → target_claim_id (a cl-NNNNNN id), argument (string); "
    "optionally target_variant, evidence_ids. "
    "propose_decision_cut → target_decision_id (slug), rationale (string). "
    "propose_canonicalization → kind, from (slug), to (slug), confidence "
    "(high|medium|low), rationale; plus scope (slug) when kind is 'position'. "
    "Also emit five quality scores, each a float in [0,1], judged from the "
    "doc + claims + cited evidence you read: "
    "goal_alignment (how well this round's doc serves the stated goal); "
    "technical_correctness (how technically correct the cited claims are); "
    "groundedness (how well each claim is actually supported by its cited "
    "evidence — not merely that the cite resolves); "
    "completeness (how fully the doc covers the open/proposed decisions); "
    "coherence (how clearly and consistently the doc reads as a whole). "
    "Use the full continuous range — reserve 0.0 and 1.0 for genuine extremes, "
    "not as defaults. "
    "Before answering, read every path listed under 'Read these first (on "
    "disk)' in the CONTEXT above; do not rely on the summary tables alone. "
    "Output ONLY valid JSON."
)

VERIFIER_C_PROMPT = (
    "You are Verifier C. Read the CONTEXT.md above plus the round's patch "
    "(rounds/<round>/patch.diff), the FULL text of each changed section file, "
    "and the cited evidence; emit JSON with fields: round, variant, verdict "
    "(confirm|dispute), per_claim (list of {claim_id, verdict (confirm|"
    "weak|dispute), rationale}), candidate_collisions_confirmed (list), "
    "candidate_collisions_rejected (list), groundedness (float in [0,1]). "
    "groundedness judges ONLY the paragraphs THIS round changed (per the patch) "
    "in sections tagged 'decided': does each changed paragraph's FACTUAL content "
    "trace to its cited evidence? Connective/transition/framing sentences need "
    "NO citation and must not lower the score. Do NOT judge untouched paragraphs "
    "or non-decided sections. Use the full continuous range; reserve 0.0/1.0 for "
    "genuine extremes. "
    "Before answering, read every path listed under 'Read these first (on "
    "disk)' in the CONTEXT above; do not rely on the summary tables alone. "
    "Output ONLY valid JSON."
)

SEED_JUDGE_PROMPT = (
    "You are the seed judge. This is round 0: score the EXISTING seed document "
    "as-is to establish the quality baseline that all later rounds must "
    "improve on. Read the seed doc, the goal, and the constitution listed "
    "under 'Read these first (on disk)'. Emit JSON with exactly these float "
    "fields, each in [0,1], judged from what the seed doc actually contains: "
    "groundedness (how well its assertions are supported), goal_alignment (how "
    "well it serves the stated goal), technical_correctness (how technically "
    "sound its content is), completeness (how fully it covers the open/proposed "
    "decisions), coherence (how clearly and consistently it reads), "
    "constitution_compliance (how well it honors the constitution). "
    "Score what is actually there: an empty or stub seed should score LOW "
    "across the board, a thorough drafted seed higher. Do NOT assume any "
    "dimension is 1.0 — reserve 1.0 for a genuinely complete, polished doc and "
    "0.0 for absent. Output ONLY valid JSON."
)

DESIGNER_QUERY_PROMPT = (
    "You are the designer, in the repo-query pass. Before authoring, decide "
    "what you need to learn from the codebase under repo/. Read the CONTEXT.md "
    "above (goal, current doc, planner intent, existing evidence) and emit JSON "
    "with fields: round, variant, repo_queries (list of {id, question}). Each "
    "question must be a single focused, answerable fact about the repo that "
    "will ground a claim you intend to make. Ask nothing the existing evidence "
    "already answers. Emit an empty repo_queries list if you need nothing new. "
    "Output ONLY valid JSON."
)

REPO_ADAPTER_PROMPT = (
    "You are the repo adapter. A read-only copy of the codebase is at repo/. "
    "Answer the QUERY in the CONTEXT.md above by reading the actual files. "
    "Emit JSON with fields: confidence (high|medium|low), citations (list of "
    "{source, ref, lines, sha}), claim (one-sentence answer), excerpt (a "
    "VERBATIM span copied from a real file — never paraphrased). Set "
    "citations[].ref to the file path (with line range) the excerpt came from. "
    "If the repo does not answer the query, return confidence \"low\" and say "
    "so in claim rather than inventing. Output ONLY valid JSON."
)


_CLAIM_SEQ_RE = re.compile(r"^cl-(\d{6})$")


def _max_existing_claim_seq(workspace_root: Path) -> int:
    """Highest cl-NNNNNN sequence number across every variant's claims dir
    (0 if none exist yet). Scanned globally — claim ids are an append-only,
    workspace-wide ledger (attacks reference target_claim_id without a variant
    qualifier), so ids must be unique across variants, not just within one."""
    max_seq = 0
    nodes = workspace_root / "variants" / "nodes"
    if not nodes.exists():
        return 0
    for cl_path in nodes.glob("*/claims/cl-*.json"):
        m = _CLAIM_SEQ_RE.match(cl_path.stem)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq


def _assign_claim_ids(workspace_root: Path, parsed: dict) -> None:
    """Reassign every designer claim a fresh, globally-unique, append-only
    cl-NNNNNN id, mutating parsed['claims'] in place.

    The designer is an LLM and cannot reliably mint zero-padded, collision-free
    sequential ids — it emits e.g. 'cl-001', which fails the materialize id
    regex, and even a well-formatted guess risks colliding with an existing
    claim (append-only violation). The orchestrator owns this allocation so
    claim materialize can never fail on id format or collision. Safe because a
    claim's id is only its own filename + body field; claims are never
    cross-referenced (the doc cites evidence, not claims), so overwriting the
    designer's chosen id breaks nothing. Must run BEFORE the scratch write so
    the scratch copy and the on-disk claim files share the assigned ids."""
    claims = parsed.get("claims")
    if not isinstance(claims, list):
        return
    next_seq = _max_existing_claim_seq(workspace_root) + 1
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim["id"] = f"cl-{next_seq:06d}"
        next_seq += 1


_ATTACK_SEQ_RE = re.compile(r"^at-(\d{6})$")


def _max_existing_attack_seq(workspace_root: Path) -> int:
    """Highest at-NNNNNN sequence number across every variant's attacks dir
    (0 if none exist yet). Scanned globally — attack ids are an append-only,
    workspace-wide ledger, so ids must be unique across variants."""
    max_seq = 0
    nodes = workspace_root / "variants" / "nodes"
    if not nodes.exists():
        return 0
    for at_path in nodes.glob("*/attacks/at-*.json"):
        m = _ATTACK_SEQ_RE.match(at_path.stem)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq


def _assign_attack_ids(workspace_root: Path, parsed: dict) -> None:
    """Reassign every reviewer attack a fresh, globally-unique, append-only
    at-NNNNNN id, mutating parsed['attacks'] in place — for the same reason as
    _assign_claim_ids. An attack's id is only its own filename + a report label;
    attacks are enumerated by glob and matched by content (target_claim_id,
    target_decision_id, scope), never looked up by their own id, so overwriting
    the reviewer's chosen id breaks nothing. Must run BEFORE the reviewer scratch
    write so scratch and the on-disk at-*.json files share the assigned ids."""
    attacks = parsed.get("attacks")
    if not isinstance(attacks, list):
        return
    next_seq = _max_existing_attack_seq(workspace_root) + 1
    for attack in attacks:
        if not isinstance(attack, dict):
            continue
        attack["id"] = f"at-{next_seq:06d}"
        next_seq += 1


_EV_SEQ_RE = re.compile(r"^ev-(\d{6})$")
_CITE_RE = re.compile(r"\[\^ev-(\d{6})\]")


def _max_existing_evidence_seq(workspace_root: Path) -> int:
    """Highest ev-NNNNNN sequence number in the (global, single) evidence dir
    (0 if none exist yet). Evidence is a workspace-wide append-only ledger."""
    max_seq = 0
    ev_dir = workspace_root / "evidence"
    if not ev_dir.exists():
        return 0
    for ev_path in ev_dir.glob("ev-*.md"):
        m = _EV_SEQ_RE.match(ev_path.stem)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq


def _assign_evidence_ids(workspace_root: Path, parsed: dict) -> dict[str, str]:
    """Reassign each NEW evidence item a fresh, globally-unique ev-NNNNNN id and
    remap every in-payload reference to it, mutating parsed in place. Returns the
    {old_id: new_id} remap so the caller can also rewrite the [^ev-*] citations
    in the doc files the designer wrote (those are remapped in
    _materialize_designer_output, not here, since the designer authors them on
    disk rather than in a patch string).

    Unlike claim ids, evidence id *format* is already enforced by
    validate_designer_json, so the failure this prevents is COLLISION: the
    designer (an LLM) proposing an id that already exists on disk, which trips
    the append-only "already exists" guard at materialize. The orchestrator
    allocates fresh ids above the global max so a collision is impossible.

    Evidence IS cross-referenced, so after reassigning we remap claim.evidence_ids
    here (validate_designer_json guarantees every claim ref is in THIS round's
    evidence set). evidence.citations is a {source, ref, lines, sha} list (not
    ev-id refs) and is not materialized, so it is left untouched.

    Must run BEFORE _assign_claim_ids and the scratch write so the remap reaches
    the scratch copy and the on-disk files. Keyed by the designer's original id,
    which validate_designer_json guarantees is unique within the payload, so
    even a reordering/swap of ids remaps correctly."""
    evidence = parsed.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return {}
    next_seq = _max_existing_evidence_seq(workspace_root) + 1
    remap: dict[str, str] = {}
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        old = ev.get("id")
        new = f"ev-{next_seq:06d}"
        next_seq += 1
        ev["id"] = new
        if isinstance(old, str) and old and old != new:
            remap[old] = new
    if not remap:
        return {}
    for claim in parsed.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        refs = claim.get("evidence_ids")
        if isinstance(refs, list):
            claim["evidence_ids"] = [remap.get(r, r) for r in refs]
    return remap


# ----- Repo adapter (designer-issued repo queries) ----------------------------


def _repo_head_sha(workspace_root: Path) -> str | None:
    """HEAD sha of the user-provided repo/ if it is a git repo, else None.
    The repo is read as-is (no harness-managed worktree); the sha is recorded
    for the audit trail and keys the query cache so it invalidates when the
    user swaps in a different repo state."""
    repo = workspace_root / "repo"
    if not repo.exists():
        return None
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()[:16]


def _repo_cache_path(workspace_root: Path) -> Path:
    # derived/ is gitignored: the cache is a local, rebuildable accelerator,
    # not part of the committed ledger.
    return workspace_root / "derived" / "repo_query_cache.json"


def _load_repo_cache(workspace_root: Path) -> dict:
    p = _repo_cache_path(workspace_root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_repo_cache(workspace_root: Path, cache: dict) -> None:
    p = _repo_cache_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _materialize_adapter_evidence(
    workspace_root: Path, repo_sha: str | None, parsed: dict,
) -> tuple[str, Path, str]:
    """Write one evidence/ev-*.md from a repo adapter's Evidence JSON, assigning
    the next global ev id. Returns (ev_id, abs_path, rel_path). Raises
    RuntimeError if the id collides on disk (append-only invariant)."""
    ev_id = f"ev-{_max_existing_evidence_seq(workspace_root) + 1:06d}"
    evidence_dir = workspace_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    ev_path = evidence_dir / f"{ev_id}.md"
    if ev_path.exists():
        raise RuntimeError(
            f"adapter evidence id {ev_id!r} already exists on disk")
    # First citation ref (if any) + repo_sha go into the frontmatter for audit;
    # the verbatim excerpt is the body (Verifier B matches the doc quote to it).
    citations = parsed.get("citations") or []
    ref = ""
    if citations and isinstance(citations[0], dict):
        ref = str(citations[0].get("ref", ""))
    fm = {
        "id": ev_id,
        "confidence": str(parsed.get("confidence", "")),
        "source": "repo",
        "ref": ref,
        "repo_sha": repo_sha or "",
        "claim": str(parsed.get("claim", "")),
    }
    fm_lines = [f'{k} = "{_toml_basic_str_escape(v)}"'
                for k, v in fm.items() if v != ""]
    text = ("+++\n" + "\n".join(fm_lines) + "\n+++\n\n"
            + str(parsed.get("excerpt", "")) + "\n")
    ev_path.write_text(text)
    return ev_id, ev_path, f"evidence/{ev_id}.md"


def resolve_repo_queries(
    workspace_root: Path,
    harness_config: dict,
    round_id: str,
    variant_id: str,
    queries: list[dict],
) -> tuple[list[Path], list[str], dict]:
    """Resolve designer repo queries into materialized evidence/ev-*.md via the
    repo adapter, with (repo_sha, question_hash) caching.

    Returns (materialized_abs_paths, evidence_rel_paths, query_id -> ev_id).
    Degrades gracefully: a query whose adapter spawn fails or returns a non-ok
    verdict is skipped (logged), never aborting the round — repo grounding is
    best-effort enrichment, not a gate."""
    models = harness_config.get("models", {})
    if "repo_adapter" not in models:
        # Fall back to the designer model (closest analogue); skip if neither.
        if "designer" not in models:
            _log(workspace_root, "repo_query_skip",
                 detail="no repo_adapter or designer model configured")
            return [], [], {}
        harness_config = {**harness_config,
                          "models": {**models,
                                     "repo_adapter": models["designer"]}}
    repo_sha = _repo_head_sha(workspace_root)
    cache = _load_repo_cache(workspace_root)
    materialized: list[Path] = []
    evidence_paths: list[str] = []
    resolved: dict[str, str] = {}
    for q in queries:
        question = (q.get("question") or "").strip()
        if not question:
            continue
        qid = str(q.get("id") or _question_hash(question))
        cache_key = f"{repo_sha or 'nosha'}:{_question_hash(question)}"
        cached_ev = cache.get(cache_key)
        if cached_ev and (workspace_root / "evidence"
                          / f"{cached_ev}.md").exists():
            resolved[qid] = cached_ev
            _log(workspace_root, "repo_query_cache_hit",
                 round_id=round_id, query_id=qid, ev_id=cached_ev)
            continue
        ctx = context_mod.build_repo_adapter_context(
            workspace_root, round_id, variant_id, question)
        result = spawn_role(
            role="repo_adapter", harness_config=harness_config,
            context_md=ctx, prompt=REPO_ADAPTER_PROMPT,
            workspace_root=workspace_root, round_id=round_id,
            variant_id=variant_id, validator=validate_repo_adapter_json)
        if result.verdict != "ok":
            _log(workspace_root, "repo_query_skip",
                 round_id=round_id, query_id=qid, verdict=result.verdict,
                 detail=result.stderr_tail or result.verdict)
            continue
        try:
            ev_id, ev_abs, ev_rel = _materialize_adapter_evidence(
                workspace_root, repo_sha, result.parsed)
        except RuntimeError as e:
            _log(workspace_root, "repo_query_skip",
                 round_id=round_id, query_id=qid, detail=str(e))
            continue
        materialized.append(ev_abs)
        evidence_paths.append(ev_rel)
        resolved[qid] = ev_id
        cache[cache_key] = ev_id
        _log(workspace_root, "repo_query_resolved",
             round_id=round_id, query_id=qid, ev_id=ev_id,
             confidence=result.parsed.get("confidence"))
    _save_repo_cache(workspace_root, cache)
    return materialized, evidence_paths, resolved


def _designer_changed_paths(workspace_root: Path) -> list[str]:
    """The set of individual non-ignored files the designer changed in the
    worktree — untracked (new) plus tracked modifications/staged — as rel-paths.

    Files are listed individually (not collapsed to directories), so a brand-new
    section in an as-yet-untracked doc dir is still seen. Best-effort: a git
    failure yields an empty set, leaving the round a no-op rather than crashing.
    """
    paths: set[str] = set()
    for args in (
        ["ls-files", "--others", "--exclude-standard", "-z"],   # untracked
        ["diff", "--name-only", "-z"],                          # unstaged mods
        ["diff", "--cached", "--name-only", "-z"],              # staged
    ):
        out = subprocess.run(
            ["git", "-C", str(workspace_root), "-c", "core.quotePath=false",
             *args], capture_output=True, text=True)
        if out.returncode == 0:
            paths.update(p for p in out.stdout.split("\0") if p)
    return sorted(paths)


def _write_patch_diff_record(
    workspace_root: Path, round_id: str, section_paths: list[str],
) -> None:
    """Write rounds/<round_id>/patch.diff as a git-DERIVED unified diff of the
    designer's doc edits (gitignored; Reviewer/Verifier-C read it). New files are
    surfaced via a transient `git add -N` that is immediately undone with
    `git reset`, so the index is left exactly as it was."""
    round_dir = workspace_root / "rounds" / round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    patch_text = ""
    if section_paths:
        subprocess.run(
            ["git", "-C", str(workspace_root), "add", "-N", "--",
             *section_paths], capture_output=True, text=True)
        diff = subprocess.run(
            ["git", "-C", str(workspace_root), "-c", "core.quotePath=false",
             "diff", "--", *section_paths], capture_output=True, text=True)
        patch_text = diff.stdout
        subprocess.run(
            ["git", "-C", str(workspace_root), "reset", "-q", "--",
             *section_paths], capture_output=True, text=True)
    (round_dir / "patch.diff").write_text(patch_text, encoding="utf-8")


def _remap_cites_in_text(text: str, ev_remap: dict[str, str]) -> str:
    """Rewrite [^ev-NNNNNN] citations per the evidence-id remap."""
    def _sub(m: "re.Match") -> str:
        ev_id = f"ev-{m.group(1)}"
        return f"[^{ev_remap[ev_id]}]" if ev_id in ev_remap else m.group(0)
    return _CITE_RE.sub(_sub, text)


def _materialize_designer_output(
    workspace_root: Path, variant_id: str, round_id: str, parsed: dict,
    ev_remap: dict[str, str] | None = None,
) -> tuple[list[Path], list[str], list[str], list[str], list[str]]:
    """Materialize the designer's output to disk.

    The designer edits doc section files DIRECTLY with its Write/Edit tools
    during its spawn (see DESIGNER_PROMPT); this function derives the changed
    section set from git — never from LLM-authored diff text — so there is no
    `git apply` and no malformed-patch failure mode. It then remaps the evidence
    citations the designer wrote (the orchestrator reassigned the ids), records a
    git-derived patch.diff, and writes evidence + claims from the parsed JSON.

    ATOMIC: if any step raises, everything written or detected by this call —
    including the designer's own doc edits — is discarded before the exception
    propagates, so a rejected round leaves a clean worktree.

    Returns (materialized_paths_for_rollback, section_paths, claim_paths,
    attack_paths, evidence_paths) — the latter four are relative-to-workspace
    strings suitable for git add.
    """
    materialized: list[Path] = []
    section_paths: list[str] = []
    claim_paths: list[str] = []
    attack_paths: list[str] = []
    evidence_paths: list[str] = []

    try:
        # ---- Doc sections: derive from git, enforce scope, remap cites ----
        # Run BEFORE writing evidence/claims below, so the only dirty paths are
        # the designer's doc edits plus orchestrator-owned in-flight ledger
        # (repo-adapter evidence/, the appended actions.jsonl). Anything else the
        # designer touched is an out-of-scope edit and rejects the round.
        allowed_prefix = f"variants/nodes/{variant_id}/doc/"
        variant_prefix = f"variants/nodes/{variant_id}/"

        def _orchestrator_owned(rel: str) -> bool:
            # Ledger paths the orchestrator (not the designer) writes: the global
            # evidence dir + the round log, plus THIS variant's claims/attacks/
            # scorecard, plus gitignored scratch/derived/rounds. A change here at
            # doc-detection time is repo-adapter evidence, a prior-round file, or
            # this materialize's own output — never a designer scope violation.
            return (rel == "actions.jsonl"
                    or rel.startswith("evidence/")
                    or rel.startswith("rounds/")
                    or rel.startswith("derived/")
                    or rel.startswith(variant_prefix + "claims/")
                    or rel.startswith(variant_prefix + "attacks/")
                    or rel == variant_prefix + "scorecard.json")

        out_of_scope: list[str] = []
        for rel in _designer_changed_paths(workspace_root):
            if rel.startswith(allowed_prefix):
                p = workspace_root / rel
                if not p.is_file():
                    continue
                if ev_remap:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    new_txt = _remap_cites_in_text(txt, ev_remap)
                    if new_txt != txt:
                        p.write_text(new_txt, encoding="utf-8")
                section_paths.append(rel)
                materialized.append(p)
            elif _orchestrator_owned(rel):
                continue
            else:
                out_of_scope.append(rel)
        if out_of_scope:
            # Roll the stray edits back before rejecting, so the round leaves a
            # clean tree. The post-spawn scrub already removes untracked strays;
            # this additionally restores any TRACKED file the designer modified
            # outside its scope (e.g. goal.toml), which the scrub does not touch
            # and which _discard_materialized would otherwise miss.
            for rel in out_of_scope:
                in_head = subprocess.run(
                    ["git", "-C", str(workspace_root), "cat-file", "-e",
                     f"HEAD:{rel}"], capture_output=True).returncode == 0
                if in_head:
                    subprocess.run(
                        ["git", "-C", str(workspace_root), "checkout", "HEAD",
                         "--", rel], capture_output=True)
                else:
                    try:
                        (workspace_root / rel).unlink()
                    except (FileNotFoundError, IsADirectoryError):
                        pass
            raise RuntimeError(
                "designer modified files outside its doc scope "
                f"({allowed_prefix}): {', '.join(sorted(out_of_scope))}")
        _write_patch_diff_record(workspace_root, round_id, section_paths)

        # Evidence
        evidence_dir = workspace_root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for ev in parsed.get("evidence", []) or []:
            ev_id = ev.get("id", "")
            if not ev_id or not _ID_RE.match(ev_id):
                raise RuntimeError(
                    f"materialize: malformed/unsafe evidence id {ev_id!r}")
            # Build TOML frontmatter using basic strings with proper escapes.
            # Triple-quoted strings can't be safely escaped if the content
            # contains literal `"""`, so we use single-line basic strings
            # with newlines escaped as \\n.
            fm_lines = []
            for key in ("id", "confidence", "claim", "excerpt", "match"):
                val = ev.get(key)
                if val is None:
                    continue
                escaped = _toml_basic_str_escape(str(val))
                fm_lines.append(f'{key} = "{escaped}"')
            text = "+++\n" + "\n".join(fm_lines) + "\n+++\n\n" + \
                   str(ev.get("excerpt", "")) + "\n"
            ev_path = evidence_dir / f"{ev_id}.md"
            if ev_path.exists():
                raise RuntimeError(
                    f"materialize: evidence id {ev_id!r} already exists on "
                    "disk (append-only ledger violation)")
            ev_path.write_text(text)
            materialized.append(ev_path)
            evidence_paths.append(f"evidence/{ev_id}.md")

        # Claims
        claims_dir = (workspace_root / "variants" / "nodes" / variant_id
                      / "claims")
        claims_dir.mkdir(parents=True, exist_ok=True)
        seen_claim_ids: set[str] = set()
        for claim in parsed.get("claims", []) or []:
            cl_id = claim.get("id", "")
            if not cl_id or not _ID_RE.match(cl_id):
                raise RuntimeError(
                    f"materialize: malformed/unsafe claim id {cl_id!r}")
            if cl_id in seen_claim_ids:
                raise RuntimeError(
                    f"materialize: duplicate claim id {cl_id!r} in round")
            seen_claim_ids.add(cl_id)
            cl_path = claims_dir / f"{cl_id}.json"
            if cl_path.exists():
                raise RuntimeError(
                    f"materialize: claim id {cl_id!r} already exists on disk "
                    "(append-only ledger violation)")
            cl_path.write_text(json.dumps(claim, indent=2, sort_keys=True))
            materialized.append(cl_path)
            claim_paths.append(
                f"variants/nodes/{variant_id}/claims/{cl_id}.json")

    except Exception:
        _discard_materialized(workspace_root, materialized)
        raise

    return (materialized, section_paths, claim_paths, attack_paths,
            evidence_paths)


def _materialize_reviewer_attacks(
    workspace_root: Path, variant_id: str, parsed: dict,
) -> tuple[list[Path], list[str]]:
    """Materialize reviewer's attacks (at-*.json) to disk. ATOMIC: discards
    any file written by this call if a later item raises. Returns
    (materialized_paths, attack_paths_for_git_add)."""
    attacks_dir = (workspace_root / "variants" / "nodes" / variant_id
                   / "attacks")
    materialized: list[Path] = []
    attack_paths: list[str] = []
    try:
        for at in parsed.get("attacks", []) or []:
            at_id = at.get("id", "")
            if not at_id or not _ID_RE.match(at_id):
                raise RuntimeError(
                    f"materialize: malformed/unsafe attack id {at_id!r}")
            attacks_dir.mkdir(parents=True, exist_ok=True)
            at_path = attacks_dir / f"{at_id}.json"
            if at_path.exists():
                raise RuntimeError(
                    f"materialize: attack id {at_id!r} already exists on disk "
                    "(append-only ledger violation)")
            at_path.write_text(json.dumps(at, indent=2, sort_keys=True))
            materialized.append(at_path)
            attack_paths.append(
                f"variants/nodes/{variant_id}/attacks/{at_id}.json")
    except Exception:
        _discard_materialized(workspace_root, materialized)
        raise
    return materialized, attack_paths


def _discard_materialized(workspace_root: Path,
                          paths: list[Path]) -> None:
    """Remove new files or git-checkout HEAD for modified files."""
    for p in paths:
        if not p.exists():
            continue
        # Was it tracked by git at HEAD?
        rel = p.relative_to(workspace_root)
        ls = subprocess.run(
            ["git", "-C", str(workspace_root), "ls-files", "--error-unmatch",
             str(rel)],
            capture_output=True, text=True,
        )
        if ls.returncode == 0:
            # Modified tracked file → restore from HEAD
            subprocess.check_call(
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
    """Execute one round, wrapped in a best-effort SIGTERM/SIGINT cleanup guard.

    The round materializes a doc section to disk (via `git apply`) well before
    it commits — the reviewer and verifier-C spawns run in between, a multi-
    minute window. A kill landing there would orphan the applied section in the
    worktree. This wrapper installs handlers for the two *catchable* kill signals
    (SIGTERM from a wrapper/`kill`, SIGINT from Ctrl-C) that discard any
    uncommitted ledger dirt before re-raising, so a graceful shutdown leaves a
    clean tree. SIGKILL is uncatchable; the start-of-round recover_worktree
    remains the backstop for that and for anything this misses.

    Handlers are installed only from the main thread (a Python constraint) and
    restored in `finally` so they never leak across rounds or into tests."""
    prev_handlers: dict = {}

    def _interrupt_cleanup(signum, _frame):
        # Best-effort: discard in-flight uncommitted ledger files. recover_
        # worktree raises on operator-owned dirt — swallow it (a signal handler
        # must not raise, and refusing to clobber operator edits is correct).
        try:
            bootstrap.recover_worktree(workspace_root)
        except Exception:
            pass
        # Restore the prior disposition and re-raise so normal signal-exit
        # semantics (and any outer handler) still apply.
        try:
            signal.signal(signum, prev_handlers.get(signum, signal.SIG_DFL))
        except (ValueError, OSError):
            pass
        os.kill(os.getpid(), signum)

    can_handle = threading.current_thread() is threading.main_thread()
    if can_handle:
        for _sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev_handlers[_sig] = signal.signal(_sig, _interrupt_cleanup)
            except (ValueError, OSError):
                pass
    try:
        return _run_round_impl(
            workspace_root, harness_config, round_id, variant_id)
    finally:
        for _sig, _handler in prev_handlers.items():
            try:
                signal.signal(_sig, _handler)
            except (ValueError, OSError):
                pass


def _run_round_impl(
    workspace_root: Path,
    harness_config: dict,
    round_id: str,
    variant_id: str,
) -> RoundOutcome:
    """Execute one round on one variant. Linear flow with early returns
    on rejection paths. See spec §3.1 for full phase semantics."""
    start_ts = time.monotonic()
    spawn_counts: dict[str, int] = {}
    materialized: list[Path] = []
    # Recover, don't abort. A previous round (or an interrupted run, or an LLM
    # spawn writing directly to its cwd=workspace_root) can leave uncommitted
    # ledger artifacts behind; those are never part of the durable commit ledger
    # and are discarded here so a stray file can't abort this round. Operator
    # edits outside the ledger still raise DirtyWorktreeError.
    recovered = bootstrap.recover_worktree(workspace_root)
    if recovered:
        _log(workspace_root, "worktree_recovered",
             round_id=round_id, variant_id=variant_id, discarded=recovered)
    round_start_sha = subprocess.check_output(
        ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
        text=True).strip()

    _log(workspace_root, "round_start",
         round_id=round_id, variant_id=variant_id)

    variants_root = workspace_root / "variants" / "nodes"
    evidence_root = workspace_root / "evidence"

    def _reject(action: str, reason_class: str, failed_phase: str,
                detail: str, reviewer_id: str | None = None) -> RoundOutcome:
        # Append a pointer to the preserved (gitignored) designer output so the
        # rejection is debuggable even though the applied diff is rolled back
        # below. No-op before the designer phase (no scratch/patch.diff yet).
        detail = detail + _preserved_artifact_pointer(workspace_root, round_id)
        _discard_materialized(workspace_root, materialized)
        # Map verdicts that aren't in the commit-msg hook's ALLOWED_ACTIONS
        # to their natural sibling. "timeout" comes from spawn_role's
        # heartbeat trip; semantically it's a spawn-level failure, so we
        # commit it as spawn-failed. The rj-*.md frontmatter preserves the
        # original verdict for audit.
        commit_action = "spawn-failed" if action == "timeout" else action
        rj_id = round_ledger.write_rejection(
            workspace_root, round_id, variant_id,
            reason_class=reason_class, failed_phase=failed_phase,
            detail=detail, reviewer_id=reviewer_id,
        )
        _log(workspace_root, "rejection",
             round_id=round_id, rj_id=rj_id,
             reason_class=reason_class, failed_phase=failed_phase,
             detail=_truncate_detail(detail))
        # Emit the commit/round_end log lines BEFORE commit_rejection so they
        # are staged into the same commit (which includes actions.jsonl),
        # leaving the worktree clean for the next round's start guard.
        _log(workspace_root, "commit", round_id=round_id, action=action)
        _log(workspace_root, "round_end",
             round_id=round_id, verdict=action)
        round_ledger.commit_rejection(
            workspace_root, action=commit_action,
            round_id=round_id, variant_id=variant_id,
            rj_id=rj_id, reason=reason_class, reviewer_id=reviewer_id,
        )
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict=action, reason=reason_class, rj_id=rj_id,
            failed_phase=failed_phase, detail=detail,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts,
        )

    def _commit_reject(exc: subprocess.CalledProcessError) -> RoundOutcome:
        # A commit failed its hooks. Reset the round to its start (erasing any
        # partial register-decision/canonicalize commits + materialized files),
        # then record a single hook-rejected rejection. Ignored files (the
        # derived/ cache, scratch/) are intentionally preserved by `clean -fd`.
        reset = subprocess.run(["git", "-C", str(workspace_root), "reset",
                                "--hard", round_start_sha],
                               capture_output=True, text=True)
        if reset.returncode != 0:
            raise RuntimeError(
                "_commit_reject: git reset --hard failed; workspace may be "
                f"corrupt: {(reset.stderr or '').strip()}")
        clean = subprocess.run(["git", "-C", str(workspace_root), "clean",
                                "-fd"], capture_output=True, text=True)
        if clean.returncode != 0:
            raise RuntimeError(
                "_commit_reject: git clean -fd failed; workspace may be "
                f"corrupt: {(clean.stderr or '').strip()}")
        # The reset rolls back goal.toml; the derived/decisions.json cache is now
        # inconsistent with it (deleted if register_decision force-committed it
        # this round, or stale if it was an ignored write). Re-derive the cache
        # from the rolled-back goal.toml so the next round validates against the
        # true registry.
        bootstrap.rebuild_decisions_cache(workspace_root)
        detail = (exc.stderr or "").strip() or "commit failed"
        rj_id = round_ledger.write_rejection(
            workspace_root, round_id, variant_id,
            reason_class="hook-rejected", failed_phase="commit",
            detail=detail)
        _log(workspace_root, "rejection", round_id=round_id, rj_id=rj_id,
             reason_class="hook-rejected", failed_phase="commit",
             detail=_truncate_detail(detail))
        # Emit the commit/round_end log lines BEFORE commit_rejection so they
        # are captured by the same commit (which stages actions.jsonl); this
        # leaves the worktree clean for the next round's assert_clean_worktree.
        _log(workspace_root, "commit", round_id=round_id,
             action="hook-rejected")
        _log(workspace_root, "round_end", round_id=round_id,
             verdict="hook-rejected")
        round_ledger.commit_rejection(
            workspace_root, action="hook-rejected", round_id=round_id,
            variant_id=variant_id, rj_id=rj_id, reason="hook-rejected")
        return RoundOutcome(
            round_id=round_id, variant_id=variant_id,
            verdict="hook-rejected", reason="hook-rejected", rj_id=rj_id,
            failed_phase="commit", detail=detail,
            elapsed_seconds=time.monotonic() - start_ts,
            spawn_counts=spawn_counts)

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
        return _reject(
            action=planner_result.verdict,
            reason_class=planner_result.verdict,
            failed_phase="planner",
            detail=f"planner: {planner_result.stderr_tail or planner_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "planner", planner_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="planner",
         verdict=planner_result.verdict,
         retry_count=planner_result.retry_count,
         elapsed_seconds=planner_result.elapsed_seconds)

    # ---- Phase 2a: Designer repo-query pass + repo resolve ----
    # Only when a user-provided repo/ is present. The designer names the repo
    # facts it needs; the repo adapter resolves each into evidence/ev-*.md that
    # the author pass cites. Entirely best-effort: a failed query pass or a
    # failed adapter spawn degrades to "no extra evidence", never aborting the
    # round (repo grounding is enrichment, not a gate).
    repo_evidence_paths: list[str] = []
    repo_materialized: list[Path] = []
    if (workspace_root / "repo").exists():
        dq_ctx = context_mod.build_designer_query_context(
            workspace_root, round_id, variant_id)
        dq_result = spawn_role(
            role="designer", harness_config=harness_config,
            context_md=dq_ctx, prompt=DESIGNER_QUERY_PROMPT,
            workspace_root=workspace_root, round_id=round_id,
            variant_id=variant_id, validator=validate_designer_query_json)
        if dq_result.verdict != "ok":
            _log(workspace_root, "designer_query_skip", round_id=round_id,
                 verdict=dq_result.verdict,
                 detail=dq_result.stderr_tail or dq_result.verdict)
        else:
            spawn_counts["designer_query"] = 1 + dq_result.retry_count
            round_ledger.write_role_scratch(
                workspace_root, round_id, "designer_query", dq_result.parsed)
            queries = dq_result.parsed.get("repo_queries", []) or []
            if queries:
                repo_materialized, repo_evidence_paths, _resolved = \
                    resolve_repo_queries(
                        workspace_root, harness_config, round_id, variant_id,
                        queries)
                # Track for rollback now; re-attached after the designer
                # materialize rebinds `materialized` below.
                materialized.extend(repo_materialized)
                spawn_counts["repo_adapter"] = len(queries)
                _log(workspace_root, "repo_resolve_complete",
                     round_id=round_id, requested=len(queries),
                     resolved=len(repo_evidence_paths))

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
        # The designer authors doc sections by writing files directly; exempt
        # its variant's doc tree from the post-spawn stray scrub so those edits
        # survive to be detected and committed.
        exempt_untracked_prefixes=[f"variants/nodes/{variant_id}/doc/"],
    )
    spawn_counts["designer"] = 1 + designer_result.retry_count
    if designer_result.verdict != "ok":
        return _reject(
            action=designer_result.verdict,
            reason_class=designer_result.verdict,
            failed_phase="designer",
            detail=f"designer: {designer_result.stderr_tail or designer_result.verdict}",
        )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="designer",
         verdict=designer_result.verdict,
         retry_count=designer_result.retry_count,
         elapsed_seconds=designer_result.elapsed_seconds)
    dparsed = designer_result.parsed
    if dparsed.get("round") != round_id or dparsed.get("variant") != variant_id:
        return _reject(
            action="phase-a-fail",
            reason_class="cross-field-fail",
            failed_phase="designer",
            detail=(f"designer round/variant mismatch: got "
                    f"round={dparsed.get('round')!r} variant="
                    f"{dparsed.get('variant')!r}, expected "
                    f"round={round_id!r} variant={variant_id!r}"))
    # The orchestrator owns ledger id allocation: the designer (an LLM) cannot
    # mint collision-free, append-only ids. Evidence first (it remaps
    # claim.evidence_ids and returns the id remap), then claim ids. Both mutate
    # dparsed in place BEFORE the scratch write so scratch + on-disk files agree.
    # The returned remap rewrites the [^ev-*] citations in the doc files the
    # designer wrote (applied inside _materialize_designer_output).
    ev_remap = _assign_evidence_ids(workspace_root, dparsed)
    _assign_claim_ids(workspace_root, dparsed)
    round_ledger.write_role_scratch(
        workspace_root, round_id, "designer", dparsed,
    )
    try:
        materialized, section_paths, claim_paths, _att_unused, evidence_paths = \
            _materialize_designer_output(
                workspace_root, variant_id, round_id, designer_result.parsed,
                ev_remap=ev_remap,
            )
    except RuntimeError as e:
        return _reject(
            action="phase-a-fail",
            reason_class="cross-field-fail",
            failed_phase="designer",
            detail=f"materialize failure: {e}",
        )
    # _materialize_designer_output rebinds `materialized` to its own list; re-add
    # the repo-adapter evidence from Phase 2a so it is both rolled back on a
    # later reject and committed (via evidence_paths) on merge.
    materialized.extend(repo_materialized)
    evidence_paths = evidence_paths + repo_evidence_paths
    _log(workspace_root, "materialize",
         round_id=round_id,
         evidence_count=len(evidence_paths),
         claim_count=len(claim_paths),
         attack_count=0,
         section_count=len(section_paths))

    # ---- Phase 3: Verifier A (mechanical: frontmatter + cite resolution) ----
    r_frontmatter = verifiers.verify_frontmatter_wellformed(variants_root)
    r_resolution = verifiers.verify_cite_resolution(
        variants_root, evidence_root,
    )
    failure_count_a = len(r_frontmatter.failures) + len(r_resolution.failures)
    _log(workspace_root, "verifier_complete",
         round_id=round_id, verifier="a",
         failure_count=failure_count_a,
         verdict="pass" if failure_count_a == 0 else "fail")
    if failure_count_a > 0:
        if r_frontmatter.failures:
            reason = "cross-field-fail"
            failures = r_frontmatter.failures
        else:
            reason = "dangling-evidence"
            failures = r_resolution.failures
        detail_lines = [
            f"{f.variant} {f.section_path}: {f.detail}"
            for f in failures[:20]
        ]
        return _reject(
            action="phase-a-fail",
            reason_class=reason,
            failed_phase="verifier_a",
            detail="\n".join(detail_lines),
        )

    # ---- Phase 4: Verifier B (excerpt match) ----
    excerpt_threshold = harness_config.get("verifiers", {}).get(
        "excerpt_match_threshold", 0.6)
    r_excerpt = verifiers.verify_excerpt_match(
        variants_root, evidence_root, threshold=excerpt_threshold,
    )
    failure_count_b = len(r_excerpt.failures)
    _log(workspace_root, "verifier_complete",
         round_id=round_id, verifier="b",
         failure_count=failure_count_b,
         verdict="pass" if failure_count_b == 0 else "fail")
    if failure_count_b > 0:
        detail_lines = [
            f"{f.variant} {f.section_path}: {f.detail}\n{f.excerpt_diff or ''}"
            for f in r_excerpt.failures[:10]
        ]
        return _reject(
            action="phase-b-fail",
            reason_class="cross-field-fail",
            failed_phase="verifier_b",
            detail="\n\n".join(detail_lines),
        )

    # ---- Phase 5: Reviewer ----
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
        return _reject(
            action=reviewer_result.verdict,
            reason_class=reviewer_result.verdict,
            failed_phase="reviewer",
            detail=f"reviewer: {reviewer_result.stderr_tail or reviewer_result.verdict}",
        )
    # The orchestrator owns attack id allocation (the reviewer, an LLM, cannot
    # mint collision-free append-only ids). Assign before the scratch write so
    # scratch + on-disk at-*.json agree.
    _assign_attack_ids(workspace_root, reviewer_result.parsed)
    round_ledger.write_role_scratch(
        workspace_root, round_id, "reviewer", reviewer_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="reviewer",
         verdict=reviewer_result.verdict,
         retry_count=reviewer_result.retry_count,
         elapsed_seconds=reviewer_result.elapsed_seconds)

    if reviewer_result.parsed.get("decision") == "reject":
        rej = reviewer_result.parsed.get("rejection") or {}
        # Fallback to "cross-field-fail" if reviewer omits reason_class or uses
        # a value not in the commit-msg hook's ALLOWED_REASONS — Reason is
        # REQUIRED for the reviewer-rejected action and must pass the hook's
        # closed-vocab check. "cross-field-fail" is a generic catch-all.
        raw_reason = rej.get("reason_class")
        reason_class = (
            raw_reason if raw_reason in _ALLOWED_REASONS
            else "cross-field-fail"
        )
        detail = (
            f"reviewer rejected: {reviewer_result.parsed.get('rationale', '')}\n"
            f"supersedable_by: {rej.get('supersedable_by', '')}"
        )
        return _reject(
            action="reviewer-rejected",
            reason_class=reason_class,
            failed_phase="reviewer",
            detail=detail,
            reviewer_id=variant_id,
        )

    # Phase 5.5: Flow A gating (decision_proposals).
    # Only extract proposals for decision IDs that are NOT already registered
    # (per spec §3.1). If a designer re-emits a proposed_decision for an
    # already-registered ID (e.g., after crash-recovery), skip it — otherwise
    # cg.register_decision would raise SchemaError("duplicate id").
    decisions_json_path = workspace_root / "derived" / "decisions.json"
    if decisions_json_path.exists():
        try:
            existing_ids = set(
                json.loads(decisions_json_path.read_text()).get("decisions", {}).keys()
            )
        except (json.JSONDecodeError, OSError):
            existing_ids = set()
    else:
        existing_ids = set()
    proposed_payloads = []
    seen_in_round: set[str] = set()
    for c in designer_result.parsed.get("claims", []) or []:
        pd = c.get("proposed_decision")
        if not (pd and isinstance(pd, dict)):
            continue
        pd_id = pd.get("id")
        if not isinstance(pd_id, str) or not pd_id:
            continue   # malformed proposal — silently skip per validator scope
        if pd_id in existing_ids:
            continue   # already registered — don't re-propose
        if pd_id in seen_in_round:
            continue   # duplicate within this round — keep the first
        seen_in_round.add(pd_id)
        proposed_payloads.append(pd)
    approved_proposals: list[dict] = []
    if proposed_payloads:
        verdicts_raw = reviewer_result.parsed.get("decision_proposals", []) or []
        try:
            verdicts = [cg.DecisionProposalVerdict.from_dict(v)
                        for v in verdicts_raw]
            outcome_dict = cg.apply_reviewer_decision_proposals(
                proposed_payloads, verdicts,
            )
        except cg.SchemaError as e:
            return _reject(
                action="reviewer-rejected",
                reason_class="proposal-rejected",
                failed_phase="reviewer",
                detail=f"decision_proposals validation failed: {e}",
                reviewer_id=variant_id,
            )
        if outcome_dict["status"] == "any-rejected":
            rej_lines = [
                f"{r['proposed_id']}: {r['rationale']}"
                for r in outcome_dict["rejected"]
            ]
            return _reject(
                action="reviewer-rejected",
                reason_class="proposal-rejected",
                failed_phase="reviewer",
                detail="\n".join(rej_lines),
                reviewer_id=variant_id,
            )
        approved_proposals = outcome_dict["approved"]
    # Materialize attacks (deferred until after Phase 5.5 gating)
    try:
        att_materialized, attack_paths = _materialize_reviewer_attacks(
            workspace_root, variant_id, reviewer_result.parsed,
        )
    except RuntimeError as e:
        return _reject(
            action="reviewer-rejected",
            reason_class="cross-field-fail",
            failed_phase="reviewer",
            detail=f"attack materialize failure: {e}",
            reviewer_id=variant_id,
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
        return _reject(
            action=vc_result.verdict,
            reason_class=vc_result.verdict,
            failed_phase="verifier_c",
            detail=f"verifier_c: {vc_result.stderr_tail or vc_result.verdict}",
        )
    round_ledger.write_role_scratch(
        workspace_root, round_id, "verifier_c", vc_result.parsed,
    )
    _log(workspace_root, "spawn_complete",
         round_id=round_id, role="verifier_c",
         verdict=vc_result.verdict,
         retry_count=vc_result.retry_count,
         elapsed_seconds=vc_result.elapsed_seconds)

    vc_parsed = vc_result.parsed
    has_per_claim_dispute = any(
        pc.get("verdict") == "dispute"
        for pc in vc_parsed.get("per_claim", [])
    )
    if vc_parsed.get("verdict") == "dispute" or has_per_claim_dispute:
        disputed = [
            f"{pc.get('claim_id', '?')}: {pc.get('rationale', '?')}"
            for pc in vc_parsed.get("per_claim", [])
            if pc.get("verdict") == "dispute"
        ]
        return _reject(
            action="phase-c-dispute",
            reason_class="cross-field-fail",
            failed_phase="verifier_c",
            detail="Verifier C disputed claims:\n" + "\n".join(disputed),
        )

    # ---- Phase 6.5: Scorecard merge gate ----
    variant_claims_dir = variants_root / variant_id / "claims"
    variant_doc_dir = variants_root / variant_id / "doc"
    goal_toml_path = workspace_root / "goal.toml"
    decisions_list: list[dict] = []
    if goal_toml_path.exists():
        try:
            with goal_toml_path.open("rb") as f:
                decisions_list = tomllib.load(f).get("decision", []) or []
        except (tomllib.TOMLDecodeError, OSError):
            decisions_list = []
    round_actions = _read_round_actions(workspace_root, round_id)
    new_dimensions = scorecard_mod.compute_dimensions(
        variant_claims_dir=variant_claims_dir,
        variant_doc_dir=variant_doc_dir,
        evidence_root=evidence_root,
        decisions=decisions_list,
        round_actions=round_actions,
        reviewer_goal_alignment=reviewer_result.parsed["goal_alignment"],
        reviewer_technical_correctness=reviewer_result.parsed[
            "technical_correctness"],
        vc_per_claim=vc_parsed.get("per_claim", []),
        reviewer_groundedness=reviewer_result.parsed.get("groundedness"),
        reviewer_completeness=reviewer_result.parsed.get("completeness"),
        reviewer_coherence=reviewer_result.parsed.get("coherence"),
    )
    sc_path = variants_root / variant_id / "scorecard.json"
    sc_rel = f"variants/nodes/{variant_id}/scorecard.json"
    prior = scorecard_mod.load_scorecard(sc_path)
    prior_dims = prior["dimensions"] if prior else None
    tolerance = harness_config.get("scorecard", {}).get(
        "regression_tolerance", 0.05)
    passed, gate_detail = scorecard_mod.evaluate_gate(
        prior_dims, new_dimensions, tolerance)
    full_delta = (
        None if prior_dims is None
        else scorecard_mod.format_score_delta(prior_dims, new_dimensions)
    )
    _log(workspace_root, "scorecard", round_id=round_id,
         variant_id=variant_id, passed=passed, detail=gate_detail,
         dimensions=new_dimensions, prior_dimensions=prior_dims,
         tolerance=tolerance, delta=full_delta)
    if not passed:
        # Spell out the full before->after for every dimension (not just the
        # regressed ones) plus the reviewer-supplied scores, so the cause of a
        # gate failure is visible without re-running. _reject appends a pointer
        # to the preserved designer diff/scratch on top of this.
        dim_lines = "\n".join(
            f"  {d}: {(prior_dims or {}).get(d, 0.0):.2f} -> "
            f"{new_dimensions[d]:.2f}"
            for d in scorecard_mod.DIMENSIONS
        )
        detail = (
            f"scorecard gate failed: {gate_detail} (tolerance={tolerance})\n"
            f"dimensions (prior -> new):\n{dim_lines}\n"
            f"full delta: {full_delta if full_delta is not None else 'n/a'}\n"
            f"reviewer scores: goal_alignment="
            f"{reviewer_result.parsed['goal_alignment']}, "
            f"technical_correctness="
            f"{reviewer_result.parsed['technical_correctness']}"
        )
        return _reject(
            action="score-regression",
            reason_class="score-regression",
            failed_phase="scorecard",
            detail=detail,
        )
    scorecard_mod.write_scorecard(
        sc_path,
        scorecard_mod.build_scorecard(variant_id, round_id, new_dimensions),
    )
    materialized.append(sc_path)
    score_delta = (
        None if prior_dims is None
        else scorecard_mod.format_score_delta(prior_dims, new_dimensions)
    )

    # ---- Phase 7a: Flow A — register-decision ----
    if approved_proposals:
        decisions_json_path = workspace_root / "derived" / "decisions.json"
        cg.register_decision(
            goal_toml_path,
            new_decisions=approved_proposals,
            decisions_json_path=decisions_json_path,
        )
        try:
            round_ledger.commit_register_decision(
                workspace_root,
                new_decision_ids=[p["id"] for p in approved_proposals],
            )
        except subprocess.CalledProcessError as exc:
            return _commit_reject(exc)
        _log(workspace_root, "commit", round_id=round_id,
             action="register-decision")

    # ---- Phase 7b: Flow C — apply_canonicalization (high-confidence only) ----
    canon_proposals = [
        a for a in reviewer_result.parsed.get("attacks", []) or []
        if a.get("at_type") == "propose_canonicalization"
        and a.get("kind") == "position"
        and a.get("confidence") == "high"
    ]
    if canon_proposals:
        registry_path = (workspace_root / "derived"
                         / "canonical_slug_registry.json")
        if registry_path.exists():
            registry = cg.CanonicalSlugRegistry.from_dict(
                json.loads(registry_path.read_text()),
            )
        else:
            registry = cg.CanonicalSlugRegistry()
        all_rewrites: list[dict] = []
        for at in canon_proposals:
            entry = registry.data.get(at["scope"])
            if entry is None or at["to"] not in entry.get("canonical", []):
                # to_slug not canonical — skip, log, continue
                _log(workspace_root, "canonicalize_skip",
                     round_id=round_id,
                     reject_reason="invalid-canonicalization-target",
                     scope=at["scope"], from_slug=at["from"], to_slug=at["to"])
                continue
            try:
                rewrites = cg.apply_canonicalization(
                    variants_root, registry, at["scope"],
                    from_slug=at["from"], to_slug=at["to"],
                )
            except cg.RegistryInvariantError as e:
                _log(workspace_root, "canonicalize_skip",
                     round_id=round_id,
                     reject_reason=str(e),
                     scope=at["scope"], from_slug=at["from"], to_slug=at["to"])
                continue
            all_rewrites.extend(rewrites)
        if all_rewrites:
            # Persist updated registry
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text(json.dumps(
                registry.to_dict(), indent=2, sort_keys=True,
            ))
            try:
                round_ledger.commit_canonicalize(workspace_root, all_rewrites)
            except subprocess.CalledProcessError as exc:
                return _commit_reject(exc)
            _log(workspace_root, "commit", round_id=round_id,
                 action="canonicalize")

    # ---- Phase 7c: registry-sync — append authored position slugs ----
    # Runs after 7b canonicalize (which targets prior-round canonicals) so the
    # slugs this round authored become canonical for future rounds.
    reg_sync_path = workspace_root / "derived" / "canonical_slug_registry.json"
    if reg_sync_path.exists():
        reg_sync = cg.CanonicalSlugRegistry.from_dict(
            json.loads(reg_sync_path.read_text()))
    else:
        reg_sync = cg.CanonicalSlugRegistry()
    before = json.dumps(reg_sync.to_dict(), sort_keys=True)
    for claim in designer_result.parsed.get("claims", []) or []:
        if claim.get("claim_type") != "decision":
            continue
        decision_id = claim.get("decision_id")
        position = claim.get("position")
        if not decision_id or not position:
            continue
        try:
            cg.add_canonical_position(reg_sync, decision_id, position)
        except cg.RegistryInvariantError as e:
            _log(workspace_root, "registry_sync_skip", round_id=round_id,
                 decision_id=decision_id, slug=position, reason=str(e))
    if json.dumps(reg_sync.to_dict(), sort_keys=True) != before:
        reg_sync_path.parent.mkdir(parents=True, exist_ok=True)
        reg_sync_path.write_text(json.dumps(
            reg_sync.to_dict(), indent=2, sort_keys=True))
        try:
            round_ledger.commit_registry_sync(workspace_root)
        except subprocess.CalledProcessError as exc:
            return _commit_reject(exc)
        _log(workspace_root, "commit", round_id=round_id,
             action="registry-sync")

    # ---- Phase 8: Final merge commit ----
    # Log terminal events BEFORE the commit so they're staged into the same
    # merge commit (which includes actions.jsonl), leaving the worktree clean
    # for the next round's start guard. If commit_merge raises, _commit_reject's
    # `git reset --hard round_start_sha` erases these premature merge logs
    # before re-logging hook-rejected, so the audit trail stays correct.
    _log(workspace_root, "commit", round_id=round_id, action="merge")
    _log(workspace_root, "round_end", round_id=round_id, verdict="merge")
    try:
        round_ledger.commit_merge(
            workspace_root, round_id=round_id, variant_id=variant_id,
            section_paths=section_paths, claim_paths=claim_paths,
            attack_paths=attack_paths, evidence_paths=evidence_paths,
            score_delta=score_delta, scorecard_path=sc_rel,
        )
    except subprocess.CalledProcessError as exc:
        return _commit_reject(exc)

    return RoundOutcome(
        round_id=round_id, variant_id=variant_id, verdict="merge",
        elapsed_seconds=time.monotonic() - start_ts,
        spawn_counts=spawn_counts,
    )


_SEEDED_VARIANT_RE = re.compile(r"^variants/nodes/(v-\d{3})/doc/")


def score_seed_docs(
    workspace_root: Path,
    harness_config: dict,
    seeded_paths: list[str],
) -> list[str]:
    """Round-0 quality eval: judge each freshly-seeded variant doc and write a
    baseline scorecard.json so round 1+ is gated against the seed's real
    quality instead of bootstrapping into the mechanical 'empty -> 1.0'
    defaults (which made every later round a regression).

    Returns the list of scorecard.json rel-paths written (for git add). Degrades
    gracefully: a variant whose judge spawn fails or returns a non-ok verdict
    is skipped (logged), leaving it to bootstrap on round 1 — a flaky judge must
    not block the whole overnight run."""
    # seed_judge is a recent role; an older workspace's harness.toml may not
    # configure it. Fall back to the reviewer's model (closest analogue — a
    # quality judgment) so seed scoring still runs. If even reviewer is absent,
    # skip entirely rather than KeyError out of the whole run.
    models = harness_config.get("models", {})
    if "seed_judge" not in models:
        if "reviewer" not in models:
            _log(workspace_root, "seed_score_skip",
                 detail="no seed_judge or reviewer model configured")
            return []
        harness_config = {**harness_config,
                          "models": {**models,
                                     "seed_judge": models["reviewer"]}}
    variant_ids: list[str] = []
    for rel in seeded_paths:
        m = _SEEDED_VARIANT_RE.match(rel)
        if m and m.group(1) not in variant_ids:
            variant_ids.append(m.group(1))
    written: list[str] = []
    for variant_id in variant_ids:
        ctx = context_mod.build_seed_judge_context(workspace_root, variant_id)
        result = spawn_role(
            role="seed_judge", harness_config=harness_config,
            context_md=ctx, prompt=SEED_JUDGE_PROMPT,
            workspace_root=workspace_root, round_id="round-000000",
            variant_id=variant_id,
            validator=validate_seed_judge_json,
        )
        if result.verdict != "ok":
            _log(workspace_root, "seed_score_skip",
                 variant_id=variant_id, verdict=result.verdict,
                 detail=result.stderr_tail or result.verdict)
            continue
        dims = {d: float(result.parsed[d]) for d in scorecard_mod.DIMENSIONS}
        sc_path = (workspace_root / "variants" / "nodes" / variant_id
                   / "scorecard.json")
        scorecard_mod.write_scorecard(
            sc_path,
            scorecard_mod.build_scorecard(variant_id, "round-000000", dims),
        )
        written.append(f"variants/nodes/{variant_id}/scorecard.json")
        _log(workspace_root, "seed_score",
             variant_id=variant_id, dimensions=dims)
    return written


_ROUND_DIR_RE = re.compile(r"^round-(\d{6})$")


def _next_round_number(workspace_root: Path) -> int:
    rounds_root = workspace_root / "rounds"
    if not rounds_root.exists():
        return 1
    max_n = 0
    for d in rounds_root.iterdir():
        if not d.is_dir():
            continue
        m = _ROUND_DIR_RE.match(d.name)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n + 1


def run_loop(
    workspace_root: Path,
    harness_config: dict,
    max_rounds: int | None = None,
    max_wall_clock_hours: float | None = None,
    variant_count: int = 2,
) -> list[RoundOutcome]:
    """Drive rounds in sequence with variant rotation. Stops at whichever cap
    fires first. At least one of max_rounds / max_wall_clock_hours required.

    Variant rotation: round N → v-{((N-1) % variant_count) + 1:03d}.
    Round-id allocation: discovers max existing rounds/round-* dir, starts
    at max+1 (so resume across runs is natural)."""
    if max_rounds is None and max_wall_clock_hours is None:
        raise ValueError(
            "run_loop requires at least one of max_rounds or "
            "max_wall_clock_hours"
        )

    # Recover the worktree before bootstrapping. A run killed mid-round (or one
    # whose last spawn wrote directly to the workspace) leaves uncommitted
    # ledger artifacts behind; discard them so seeding/bootstrap start clean
    # instead of aborting. Surfaced on stderr — the durable per-round audit line
    # is emitted by run_round, whose own start-of-round recovery would otherwise
    # roll back an actions.jsonl entry written here before the first commit.
    recovered = bootstrap.recover_worktree(workspace_root)
    if recovered:
        print(f"harness: recovered worktree, discarded {len(recovered)} "
              f"stray ledger path(s): {', '.join(recovered)}", file=sys.stderr)
    bootstrap.rebuild_decisions_cache(workspace_root)
    bootstrap.ensure_empty_registry(workspace_root)
    # Rebuild the derived decision cache, ensure the registry baseline, and
    # seed each variant's document from seed_doc.md before the first round —
    # all on a worktree we've asserted is clean.
    seeded = bootstrap.seed_variant_docs(workspace_root, variant_count)
    if seeded:
        # Score each seed doc (round 0) so round 1+ is gated against the seed's
        # real quality, not the mechanical 'empty -> 1.0' baseline. Commit the
        # baseline scorecards together with the seed docs as a single init.
        seed_scorecards = score_seed_docs(
            workspace_root, harness_config, seeded)
        # score_seed_docs appends seed_score/seed_score_skip lines to
        # actions.jsonl; stage it in the same init commit so the worktree is
        # clean before round 1's assert_clean_worktree.
        to_stage = list(seeded) + seed_scorecards
        if (workspace_root / "actions.jsonl").exists():
            to_stage.append("actions.jsonl")
        try:
            round_ledger._git_add(workspace_root, *to_stage)
            round_ledger._git_commit(
                workspace_root,
                "harness: seed variant documents\n\nAction: init\n")
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "seed-variant commit failed (check hooks/commit-msg): "
                f"{(exc.stderr or '').strip()}") from exc

    loop_start = time.monotonic()
    outcomes: list[RoundOutcome] = []
    next_n = _next_round_number(workspace_root)
    start_sha = _current_head_sha(workspace_root)

    while True:
        if max_rounds is not None and len(outcomes) >= max_rounds:
            break
        if max_wall_clock_hours is not None and \
           time.monotonic() - loop_start >= max_wall_clock_hours * 3600:
            break
        round_id = f"round-{next_n:06d}"
        variant_n = ((next_n - 1) % variant_count) + 1
        variant_id = f"v-{variant_n:03d}"
        outcome = run_round(
            workspace_root, harness_config, round_id, variant_id,
        )
        outcomes.append(outcome)
        if outcome.verdict == "merge":
            bootstrap.rebuild_decisions_cache(workspace_root)
        next_n += 1

    brief = morning_brief_mod.render_morning_brief(workspace_root, start_sha)
    (workspace_root / "morning_brief.md").write_text(brief)

    return outcomes
