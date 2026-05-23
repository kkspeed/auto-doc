"""Claim graph module for the Design Doc Evolution Harness.

This module owns the claim graph data model and its mechanical operations:
schemas (dataclasses + validators), the append-only canonical slug registry,
decision registration, canonicalization application, and the three mechanical
detectors (position collisions, decisional asymmetry, stale proposals).

Public API consumed by the (future) orchestrator in harness/orchestrator.py:

  Schemas (each has from_dict / to_dict):
    - Claim           cl-*.json
    - Attack          at-*.json
    - Decision        goal.toml [[decision]] entry
    - DecisionProposalVerdict   reviewer.json decision_proposals[] entry
    - CanonicalSlugRegistry     derived/canonical_slug_registry.json

  Cross-field validators (raise SchemaError):
    - validate_claim_decision_id_resolution
    - validate_claim_position_not_vacuous
    - validate_claim_position_not_alias

  Registry mechanics (mutating):
    - add_canonical_position
    - register_alias
    - rewrite_position_to_canonical
    - apply_canonicalization

  Decision registry:
    - load_decisions_from_goal_toml
    - dump_decisions_to_json
    - detect_goal_toml_changes
    - register_decision

  Detectors (pure):
    - detect_position_collisions
    - detect_decisional_asymmetry
    - detect_stale_proposals

  Section walker:
    - retag_sections_for_retired_decisions

  Reviewer gating:
    - apply_reviewer_decision_proposals

  Morning brief renderers:
    - render_position_collisions_table
    - render_decisional_asymmetry_table
    - render_pending_registry_changes
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ----- Errors -----------------------------------------------------------------


class SchemaError(ValueError):
    """Raised when a dict fails schema validation."""


class RegistryInvariantError(RuntimeError):
    """Raised when an operation would violate an append-only registry invariant."""


# ----- Closed enums -----------------------------------------------------------


CLAIM_TYPES = frozenset({"decision", "observation", "inference", "out_of_scope", "unresolved"})
AT_TYPES = frozenset({"dispute_claim", "propose_decision_cut", "propose_canonicalization"})
CANONICALIZATION_KINDS = frozenset({"decision_id", "position"})
CONFIDENCES = frozenset({"high", "medium", "low"})
DECISION_STATUSES = frozenset({"open", "proposed", "retired"})
PROPOSAL_VERDICTS = frozenset({"approve", "reject"})

# Vacuous position slugs (Edge Case C); pre-commit blocklist
VACUOUS_POSITION_SLUGS = frozenset({
    "tbd", "unclear", "unknown", "not-decided", "not-yet-decided",
    "na", "none", "n-a", "tbd_", "unclear_", "unknown_",
    "not_decided", "not_yet_decided", "n_a",
})

# Kebab-case slug regex (also accepts 2-char minimum per spec §3.2)
SLUG_REGEX = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise SchemaError(msg)


def _require_enum(value: Any, allowed: frozenset, field_name: str) -> None:
    _require(value in allowed,
             f"{field_name} must be one of {sorted(allowed)}, got {value!r}")


def _require_slug(value: str, field_name: str) -> None:
    _require(isinstance(value, str), f"{field_name} must be a string")
    _require(SLUG_REGEX.match(value) is not None,
             f"{field_name} {value!r} is not kebab-case ASCII (regex ^[a-z][a-z0-9-]*[a-z0-9]$)")


# ----- Claim ------------------------------------------------------------------


@dataclass
class Claim:
    id: str
    section_id: str
    decision_id: str
    claim_type: str
    evidence_ids: list[str]
    assertion: str
    position: str | None = None
    out_of_scope_rationale: str | None = None
    proposed_decision: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        for required in ("id", "section_id", "decision_id", "claim_type",
                         "evidence_ids", "assertion"):
            _require(required in d, f"Claim missing required field {required!r}")
        _require_enum(d["claim_type"], CLAIM_TYPES, "claim_type")
        _require_slug(d["decision_id"], "decision_id")
        _require(isinstance(d["evidence_ids"], list),
                 "evidence_ids must be a list")
        claim = cls(
            id=d["id"],
            section_id=d["section_id"],
            decision_id=d["decision_id"],
            claim_type=d["claim_type"],
            evidence_ids=list(d["evidence_ids"]),
            assertion=d["assertion"],
            position=d.get("position"),
            out_of_scope_rationale=d.get("out_of_scope_rationale"),
            proposed_decision=(dict(d["proposed_decision"])
                               if d.get("proposed_decision") is not None else None),
        )
        # Conditional field requirements
        if claim.claim_type == "out_of_scope":
            _require(claim.out_of_scope_rationale is not None,
                     "out_of_scope claim must have out_of_scope_rationale")
            _require(claim.position is None,
                     "out_of_scope claim must NOT have position")
        elif claim.claim_type == "unresolved":
            _require(claim.position is None,
                     "unresolved claim must NOT have position")
        else:
            _require(claim.position is not None,
                     f"{claim.claim_type} claim must have position")
            _require_slug(claim.position, "position")
        return claim

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "section_id": self.section_id,
            "decision_id": self.decision_id,
            "claim_type": self.claim_type,
            "evidence_ids": list(self.evidence_ids),
            "assertion": self.assertion,
        }
        if self.position is not None:
            d["position"] = self.position
        if self.out_of_scope_rationale is not None:
            d["out_of_scope_rationale"] = self.out_of_scope_rationale
        if self.proposed_decision is not None:
            d["proposed_decision"] = dict(self.proposed_decision)
        return d


# ----- Attack -----------------------------------------------------------------


@dataclass
class Attack:
    id: str
    at_type: str
    # dispute_claim fields
    target_claim_id: str | None = None
    target_variant: str | None = None
    argument: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    # propose_decision_cut fields
    target_decision_id: str | None = None
    rationale: str | None = None
    # propose_canonicalization fields
    kind: str | None = None
    scope: str | None = None
    from_slug: str | None = None
    to_slug: str | None = None
    confidence: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Attack":
        _require("id" in d, "Attack missing 'id'")
        _require("at_type" in d, "Attack missing 'at_type'")
        _require_enum(d["at_type"], AT_TYPES, "at_type")
        at_type = d["at_type"]
        at = cls(id=d["id"], at_type=at_type)
        if at_type == "dispute_claim":
            for req in ("target_claim_id", "argument"):
                _require(req in d, f"dispute_claim missing {req!r}")
            at.target_claim_id = d["target_claim_id"]
            at.target_variant = d.get("target_variant")
            at.argument = d["argument"]
            at.evidence_ids = list(d.get("evidence_ids", []))
        elif at_type == "propose_decision_cut":
            for req in ("target_decision_id", "rationale"):
                _require(req in d, f"propose_decision_cut missing {req!r}")
            _require_slug(d["target_decision_id"], "target_decision_id")
            at.target_decision_id = d["target_decision_id"]
            at.rationale = d["rationale"]
        elif at_type == "propose_canonicalization":
            for req in ("kind", "from", "to", "confidence", "rationale"):
                _require(req in d, f"propose_canonicalization missing {req!r}")
            _require_enum(d["kind"], CANONICALIZATION_KINDS, "kind")
            _require_enum(d["confidence"], CONFIDENCES, "confidence")
            _require_slug(d["from"], "from")
            _require_slug(d["to"], "to")
            at.kind = d["kind"]
            at.from_slug = d["from"]
            at.to_slug = d["to"]
            at.confidence = d["confidence"]
            at.rationale = d["rationale"]
            if at.kind == "position":
                _require("scope" in d, "propose_canonicalization kind=position requires scope")
                _require_slug(d["scope"], "scope")
                at.scope = d["scope"]
        return at

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "at_type": self.at_type}
        if self.at_type == "dispute_claim":
            d["target_claim_id"] = self.target_claim_id
            if self.target_variant is not None:
                d["target_variant"] = self.target_variant
            d["argument"] = self.argument
            d["evidence_ids"] = list(self.evidence_ids)
        elif self.at_type == "propose_decision_cut":
            d["target_decision_id"] = self.target_decision_id
            d["rationale"] = self.rationale
        elif self.at_type == "propose_canonicalization":
            d["kind"] = self.kind
            if self.scope is not None:
                d["scope"] = self.scope
            d["from"] = self.from_slug
            d["to"] = self.to_slug
            d["confidence"] = self.confidence
            d["rationale"] = self.rationale
        return d


# ----- Decision ---------------------------------------------------------------


@dataclass
class Decision:
    id: str
    question: str
    status: str
    introduced_at: str   # goal_version when first registered, e.g. "g-01"

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        for req in ("id", "question", "status", "introduced_at"):
            _require(req in d, f"Decision missing {req!r}")
        _require_slug(d["id"], "id")
        _require_enum(d["status"], DECISION_STATUSES, "status")
        return cls(id=d["id"], question=d["question"],
                   status=d["status"], introduced_at=d["introduced_at"])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "status": self.status,
            "introduced_at": self.introduced_at,
        }


# ----- CanonicalSlugRegistry --------------------------------------------------


@dataclass
class CanonicalSlugRegistry:
    """Per-decision append-only registry of canonical position slugs + aliases."""
    # decision_id -> {"canonical": [slug, ...], "aliases": {alias_slug: canonical_slug}}
    data: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalSlugRegistry":
        out: dict[str, dict[str, Any]] = {}
        for decision_id, entry in d.items():
            _require_slug(decision_id, f"registry key {decision_id!r}")
            _require("canonical" in entry,
                     f"registry[{decision_id}] missing 'canonical'")
            _require("aliases" in entry,
                     f"registry[{decision_id}] missing 'aliases'")
            for slug in entry["canonical"]:
                _require_slug(slug, f"registry[{decision_id}].canonical entry")
            for alias_key, alias_val in entry["aliases"].items():
                _require_slug(alias_key, f"registry[{decision_id}].aliases key")
                _require_slug(alias_val, f"registry[{decision_id}].aliases value")
                _require(alias_val in entry["canonical"],
                         f"registry[{decision_id}].aliases[{alias_key}] = {alias_val!r} "
                         f"is not in canonical list (alias target must be canonical)")
            out[decision_id] = {
                "canonical": list(entry["canonical"]),
                "aliases": dict(entry["aliases"]),
            }
        return cls(data=out)

    def to_dict(self) -> dict:
        return {
            decision_id: {
                "canonical": list(entry["canonical"]),
                "aliases": dict(entry["aliases"]),
            }
            for decision_id, entry in self.data.items()
        }

    def ensure_decision(self, decision_id: str) -> None:
        """Ensure decision_id has an entry in the registry (empty if new)."""
        _require_slug(decision_id, "decision_id")
        if decision_id not in self.data:
            self.data[decision_id] = {"canonical": [], "aliases": {}}


# ----- Cross-field validators -------------------------------------------------


def validate_claim_decision_id_resolution(
    claim: Claim,
    decisions: dict[str, Decision],
) -> None:
    """Verify claim.decision_id resolves to a non-retired registered decision
    OR matches the claim's proposed_decision payload.

    Args:
        claim: the Claim to validate
        decisions: {decision_id: Decision} from derived/decisions.json
    Raises:
        SchemaError on validation failure.
    """
    if claim.decision_id in decisions:
        registered = decisions[claim.decision_id]
        if registered.status == "retired":
            raise SchemaError(
                f"Claim {claim.id} references retired decision {claim.decision_id!r}; "
                "new claims may not reference retired decisions"
            )
        # status open or proposed; OK
        return
    # Not in registry; must have proposed_decision payload with matching id
    if claim.proposed_decision is None:
        raise SchemaError(
            f"Claim {claim.id} decision_id {claim.decision_id!r} is not registered "
            "and no proposed_decision payload is present"
        )
    if claim.proposed_decision.get("id") != claim.decision_id:
        raise SchemaError(
            f"Claim {claim.id} proposed_decision.id "
            f"({claim.proposed_decision.get('id')!r}) does not match "
            f"decision_id ({claim.decision_id!r})"
        )


def validate_claim_position_not_vacuous(claim: Claim) -> None:
    """Reject vacuous position slugs (tbd, unclear, etc.).

    No-op for out_of_scope and unresolved claims (they have no position).
    """
    if claim.position is None:
        return
    if claim.position in VACUOUS_POSITION_SLUGS:
        raise SchemaError(
            f"Claim {claim.id} has vacuous position slug {claim.position!r}; "
            f"use claim_type=unresolved if you genuinely lack a position"
        )


def validate_claim_position_not_alias(
    claim: Claim,
    registry: CanonicalSlugRegistry,
) -> None:
    """Reject claims whose position slug is an alias key in the registry for the
    claim's decision_id (designer must use the canonical slug).

    No-op for claims with no position, or for decisions absent from the registry.
    """
    if claim.position is None:
        return
    entry = registry.data.get(claim.decision_id)
    if entry is None:
        return
    aliases = entry.get("aliases", {})
    if claim.position in aliases:
        canonical = aliases[claim.position]
        raise SchemaError(
            f"Claim {claim.id} position {claim.position!r} is an alias of "
            f"canonical {canonical!r} under decision {claim.decision_id!r}; "
            f"use the canonical slug"
        )


# ----- DecisionProposalVerdict ------------------------------------------------


@dataclass
class DecisionProposalVerdict:
    proposed_id: str
    verdict: str
    rationale: str

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionProposalVerdict":
        for req in ("proposed_id", "verdict", "rationale"):
            _require(req in d, f"DecisionProposalVerdict missing {req!r}")
        _require_enum(d["verdict"], PROPOSAL_VERDICTS, "verdict")
        _require_slug(d["proposed_id"], "proposed_id")
        return cls(proposed_id=d["proposed_id"],
                   verdict=d["verdict"],
                   rationale=d["rationale"])

    def to_dict(self) -> dict:
        return {
            "proposed_id": self.proposed_id,
            "verdict": self.verdict,
            "rationale": self.rationale,
        }


# ----- Canonical slug registry mechanics --------------------------------------


def add_canonical_position(
    registry: CanonicalSlugRegistry,
    decision_id: str,
    slug: str,
) -> None:
    """Append a slug to the canonical list for a decision.

    Idempotent (adding a slug that's already canonical is a no-op). The slug
    must not currently be an alias key for this decision — append-only aliases
    means a slug that has been aliased cannot return to canonical.
    """
    _require_slug(decision_id, "decision_id")
    _require_slug(slug, "slug")
    registry.ensure_decision(decision_id)
    entry = registry.data[decision_id]
    if slug in entry["aliases"]:
        raise RegistryInvariantError(
            f"Cannot add canonical slug {slug!r} under decision {decision_id!r}: "
            f"it is already an alias of {entry['aliases'][slug]!r}. "
            "Aliases are append-only; a slug never returns to canonical."
        )
    if slug not in entry["canonical"]:
        entry["canonical"].append(slug)


def register_alias(
    registry: CanonicalSlugRegistry,
    decision_id: str,
    from_slug: str,
    to_slug: str,
) -> None:
    """Register from_slug as an alias of to_slug for this decision.

    Invariants:
      - to_slug MUST currently be in canonical for decision_id (alias target
        must be canonical). Novel to_slug → RegistryInvariantError.
      - from_slug MUST currently be in canonical (nothing to alias otherwise).
      - from_slug MUST NOT already be an alias key (append-only aliases).

    Effect: removes from_slug from canonical, adds aliases[from_slug] = to_slug.
    """
    _require_slug(decision_id, "decision_id")
    _require_slug(from_slug, "from_slug")
    _require_slug(to_slug, "to_slug")
    if decision_id not in registry.data:
        raise RegistryInvariantError(
            f"Decision {decision_id!r} not in registry; cannot register alias"
        )
    entry = registry.data[decision_id]
    if to_slug not in entry["canonical"]:
        raise RegistryInvariantError(
            f"Alias target {to_slug!r} is not canonical for decision {decision_id!r}; "
            "canonicalization must target an existing canonical slug"
        )
    if from_slug in entry["aliases"]:
        raise RegistryInvariantError(
            f"Slug {from_slug!r} is already an alias of "
            f"{entry['aliases'][from_slug]!r}; aliases are append-only"
        )
    if from_slug not in entry["canonical"]:
        raise RegistryInvariantError(
            f"Slug {from_slug!r} is not canonical for decision {decision_id!r}; "
            "nothing to alias"
        )
    entry["canonical"].remove(from_slug)
    entry["aliases"][from_slug] = to_slug


def rewrite_position_to_canonical(
    registry: CanonicalSlugRegistry,
    decision_id: str,
    slug: str,
) -> str:
    """Return the canonical form of slug under decision_id.

    If slug is in aliases, returns aliases[slug]. Otherwise returns slug
    unchanged (canonical slugs are pass-through; unknown slugs are pass-through
    so callers can detect "new canonical candidate" via list comparison).
    """
    entry = registry.data.get(decision_id)
    if entry is None:
        return slug
    return entry["aliases"].get(slug, slug)


# ----- Decision registry (goal.toml <-> derived/decisions.json) ---------------

import json as _json   # late import so module header stays stdlib-only-spirit clean
import tomllib as _tomllib
from pathlib import Path as _Path


def load_decisions_from_goal_toml(path: _Path) -> tuple[dict[str, Decision], str]:
    """Read goal.toml and return ({decision_id: Decision}, goal_version).

    Raises SchemaError if goal_version missing or decisions malformed.
    """
    with path.open("rb") as f:
        data = _tomllib.load(f)
    goal = data.get("goal", {})
    if "goal_version" not in goal:
        raise SchemaError(f"{path}: [goal] table missing goal_version")
    goal_version = goal["goal_version"]
    decisions: dict[str, Decision] = {}
    for entry in data.get("decision", []):
        dec = Decision.from_dict(entry)
        if dec.id in decisions:
            raise SchemaError(f"{path}: duplicate decision id {dec.id!r}")
        decisions[dec.id] = dec
    return decisions, goal_version


def dump_decisions_to_json(
    decisions: dict[str, Decision],
    goal_version: str,
    path: _Path,
) -> None:
    """Write decisions + goal_version to a JSON file (sorted keys for stability)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "goal_version": goal_version,
        "decisions": {
            decision_id: dec.to_dict()
            for decision_id, dec in sorted(decisions.items())
        },
    }
    with path.open("w") as f:
        _json.dump(payload, f, indent=2, sort_keys=True)


def detect_goal_toml_changes(
    goal_toml_path: _Path,
    decisions_json_path: _Path,
) -> str:
    """Compare goal.toml against the cached derived/decisions.json.

    Returns one of:
      - "unchanged":      goal_version matches AND decision content matches
      - "versioned-change": goal_version differs (run registry-sync)

    Raises SchemaError with "goal_version" + "bump" wording when goal.toml
    content differs but goal_version is unchanged (silent edit; human must
    bump version).
    """
    fresh, fresh_version = load_decisions_from_goal_toml(goal_toml_path)
    if not decisions_json_path.exists():
        return "versioned-change"
    with decisions_json_path.open() as f:
        cached = _json.load(f)
    cached_version = cached.get("goal_version")
    cached_decisions = {
        d_id: Decision.from_dict(d)
        for d_id, d in cached.get("decisions", {}).items()
    }
    if fresh_version != cached_version:
        return "versioned-change"
    # Same version; content must match exactly
    if {d_id: dec.to_dict() for d_id, dec in fresh.items()} != \
       {d_id: dec.to_dict() for d_id, dec in cached_decisions.items()}:
        raise SchemaError(
            f"{goal_toml_path}: content changed but goal_version is unchanged "
            f"({cached_version!r}); bump goal_version before resuming"
        )
    return "unchanged"


# ----- Decision registration (Flow A) -----------------------------------------


_GOAL_VERSION_RE = re.compile(r'^(\s*goal_version\s*=\s*")g-(\d+)("\s*)$',
                              re.MULTILINE)


def _bump_goal_version(text: str) -> tuple[str, str]:
    """Increment the goal_version in goal.toml text. Returns (new_text, new_version)."""
    m = _GOAL_VERSION_RE.search(text)
    if m is None:
        raise SchemaError("goal.toml has no parseable goal_version line")
    current_num = int(m.group(2))
    new_num = current_num + 1
    new_version = f"g-{new_num:02d}"
    new_text = _GOAL_VERSION_RE.sub(rf'\1{new_version}\3', text, count=1)
    return new_text, new_version


def register_decision(
    goal_toml_path: _Path,
    new_decisions: list[dict],
) -> str:
    """Append new_decisions to goal.toml, bump goal_version, return new version.

    Each entry in new_decisions: {"id": "...", "question": "...", "rationale": "..."}.
    The rationale is preserved as a comment in goal.toml above the [[decision]] block.

    Raises SchemaError on: empty new_decisions list, duplicate id (existing or
    within batch), missing required field, invalid slug, or unparseable goal_version.
    """
    if not new_decisions:
        raise SchemaError("register_decision: new_decisions list cannot be empty")
    text = goal_toml_path.read_text()
    existing, _ = load_decisions_from_goal_toml(goal_toml_path)
    seen_ids = set(existing.keys())
    # Validate ALL entries before any mutation
    for entry in new_decisions:
        for req in ("id", "question", "rationale"):
            if req not in entry:
                raise SchemaError(f"register_decision entry missing {req!r}")
        _require_slug(entry["id"], "id")
        if entry["id"] in seen_ids:
            raise SchemaError(
                f"Cannot register {entry['id']!r}: duplicate id "
                "(already in goal.toml or earlier in this batch)"
            )
        seen_ids.add(entry["id"])
    # All validated; now bump version and build blocks
    new_text, new_version = _bump_goal_version(text)
    appended_blocks: list[str] = []
    for entry in new_decisions:
        question_escaped = entry["question"].replace('\\', '\\\\').replace('"', '\\"')
        rationale_escaped = entry["rationale"].replace('\n', ' ')
        block = (
            f'\n# Rationale: {rationale_escaped}\n'
            f'[[decision]]\n'
            f'id = "{entry["id"]}"\n'
            f'question = "{question_escaped}"\n'
            f'status = "open"\n'
            f'introduced_at = "{new_version}"\n'
        )
        appended_blocks.append(block)
    new_text = new_text.rstrip() + "\n" + "".join(appended_blocks)
    goal_toml_path.write_text(new_text)
    return new_version


# ----- Canonicalization apply (Flow C high-confidence) ------------------------


def apply_canonicalization(
    variants_nodes_root: _Path,
    registry: CanonicalSlugRegistry,
    decision_id: str,
    from_slug: str,
    to_slug: str,
) -> list[dict]:
    """Apply a high-confidence canonicalization across all variants.

    Walks all v-*/claims/cl-*.json files under variants_nodes_root and rewrites
    `position: from_slug → to_slug` where `decision_id` matches. Then updates
    the registry via register_alias (from_slug becomes alias of to_slug).

    Returns a list of {"path": ..., "claim_id": ..., "from": ..., "to": ...,
    "decision_id": ...} entries — one per file rewritten. Empty list if no
    files matched. Suitable for the Action: canonicalize commit trailer.

    Raises RegistryInvariantError if the registry invariants reject the alias
    (caller should not have attempted with invalid to_slug).
    """
    # Validate the registry transition BEFORE touching files, so a bad call
    # leaves the filesystem alone.
    entry = registry.data.get(decision_id)
    if entry is None:
        raise RegistryInvariantError(
            f"Decision {decision_id!r} not in registry"
        )
    if to_slug not in entry["canonical"]:
        raise RegistryInvariantError(
            f"Alias target {to_slug!r} is not canonical for {decision_id!r}"
        )
    if from_slug not in entry["canonical"]:
        raise RegistryInvariantError(
            f"Slug {from_slug!r} is not canonical for {decision_id!r}"
        )
    if from_slug in entry["aliases"]:
        raise RegistryInvariantError(
            f"Slug {from_slug!r} is already an alias"
        )

    rewrites: list[dict] = []
    if variants_nodes_root.exists():
        for variant_dir in sorted(variants_nodes_root.iterdir()):
            if not variant_dir.is_dir():
                continue
            claims_dir = variant_dir / "claims"
            if not claims_dir.exists():
                continue
            for cl_file in sorted(claims_dir.glob("cl-*.json")):
                with cl_file.open() as f:
                    data = _json.load(f)
                if data.get("decision_id") != decision_id:
                    continue
                if data.get("position") != from_slug:
                    continue
                data["position"] = to_slug
                with cl_file.open("w") as f:
                    _json.dump(data, f, indent=2, sort_keys=True)
                rewrites.append({
                    "path": str(cl_file.relative_to(variants_nodes_root.parent.parent)),
                    "claim_id": data["id"],
                    "decision_id": decision_id,
                    "from": from_slug,
                    "to": to_slug,
                })

    # Commit the registry change AFTER rewriting files (so files+registry stay
    # in sync; if rewrite fails partway, no registry mutation has occurred).
    register_alias(registry, decision_id, from_slug, to_slug)
    return rewrites
