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
            proposed_decision=d.get("proposed_decision"),
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
