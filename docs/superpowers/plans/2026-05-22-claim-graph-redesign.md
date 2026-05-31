# Claim Graph Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the claim graph redesign from [2026-05-22-claim-graph-redesign-design.md](../specs/2026-05-22-claim-graph-redesign-design.md) as a self-contained Python module (`harness/claim_graph.py`) with comprehensive unit tests. Integration with the round state machine is documented but not wired (a separate plan covers the broader v0 orchestrator).

**Architecture:** Single module owning schemas (dataclasses), validators (hand-rolled, no `jsonschema`), the append-only canonical slug registry, three mechanical detectors, autonomous canonicalization mechanics, decision registration, and morning-brief render helpers. Python 3.11+ stdlib only (uses `tomllib`, `json`, `dataclasses`, `re`, `unicodedata`, `pathlib`). All operations on synthetic in-memory inputs in unit tests; filesystem operations isolated to a handful of functions that take `Path` arguments.

**Tech Stack:** Python 3.11+, stdlib only (`tomllib`, `json`, `dataclasses`, `re`, `pathlib`, `unicodedata`), `unittest` for tests, `git` for commits.

---

## File Structure

**Created in this plan:**
- `harness/__init__.py` — package marker, empty
- `harness/claim_graph.py` — the entire claim graph module (schemas, validators, registry, detectors, canonicalization, render helpers, decision registration)
- `tests/__init__.py` — test package marker, empty
- `tests/test_claim_graph_schemas.py` — dataclass roundtrip + type validation tests
- `tests/test_claim_graph_validators.py` — cross-field validators (decision_id resolution, vacuous slug blocklist, alias rewriting check)
- `tests/test_canonical_registry.py` — append-only invariants for canonical_slug_registry
- `tests/test_decision_registry.py` — goal.toml ↔ derived/decisions.json roundtrip + change detection
- `tests/test_decision_registration.py` — Flow A auto-registration (write to goal.toml + bump version)
- `tests/test_canonicalization_apply.py` — orchestrator pass: walk cl-*.json, rewrite slugs, update registry
- `tests/test_collision_detection.py` — Detector 1 (position collisions across variants)
- `tests/test_coverage_asymmetry.py` — Detectors 2 + 3 (decided/out-of-scope asymmetry, stale proposals)
- `tests/test_section_retag.py` — section retag walker (decided → unresolved on retired decision)
- `tests/test_reviewer_gating.py` — reviewer.json decision_proposals consumption
- `tests/test_morning_brief_render.py` — table renderers for the new morning_brief sections
- `tests/fixtures/claim_graph/` — fixture filesystem trees for integration-style tests
- `workspace_template/constitution.md` — constitution prose with the new claim graph guidance
- `workspace_template/goal.toml` — example with `[[decision]]` table
- `pyproject.toml` — minimal Python project metadata (just to make `python -m unittest discover` work cleanly)
- `.gitignore` — standard Python + project-specific (per parent spec §1)

**NOT created in this plan (deferred to broader v0 plan):**
- `harness/orchestrator.py`, `harness/cli_runner.py`, `harness/verifier_ab.py`, etc.
- `workspace/hooks/pre-commit`, `workspace/hooks/commit-msg`
- `harness init` CLI

The claim_graph module is designed so the future orchestrator imports `harness.claim_graph` and calls its functions. Module docstring documents the public interface.

---

## Task 1: Initialize project skeleton

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/.gitignore`
- Create: `/Users/liwen/develop/projects/auto_design_doc/pyproject.toml`
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/__init__.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/__init__.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_skeleton.py`

- [ ] **Step 1: Initialize git repository**

Run:
```bash
cd /Users/liwen/develop/projects/auto_design_doc
git init
git config core.hooksPath workspace/hooks/ 2>/dev/null || true   # placeholder for future hooks; ok if dir doesn't exist yet
```
Expected: `Initialized empty Git repository in /Users/liwen/develop/projects/auto_design_doc/.git/`

- [ ] **Step 2: Create .gitignore**

Write `/Users/liwen/develop/projects/auto_design_doc/.gitignore`:
```
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.pytest_cache/
.coverage
htmlcov/

# Harness project layout (per parent spec §1)
CONTEXT.md
derived/
rounds/*/scratch/
*.tmp
repo/
sources/*/cache/

# IDE
.vscode/
.idea/
*.swp
.DS_Store
```

- [ ] **Step 3: Create pyproject.toml**

Write `/Users/liwen/develop/projects/auto_design_doc/pyproject.toml`:
```toml
[project]
name = "auto-design-doc"
version = "0.0.1"
description = "Design Doc Evolution Harness — claim graph module"
requires-python = ">=3.11"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["harness*"]
```

- [ ] **Step 4: Create empty package markers**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/__init__.py`:
```python
```
(intentionally empty)

Write `/Users/liwen/develop/projects/auto_design_doc/tests/__init__.py`:
```python
```
(intentionally empty)

- [ ] **Step 5: Write skeleton test**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_skeleton.py`:
```python
import unittest


class SkeletonTest(unittest.TestCase):
    def test_python_version(self):
        import sys
        self.assertGreaterEqual(sys.version_info[:2], (3, 11),
                                "Requires Python 3.11+ for tomllib stdlib import")

    def test_tomllib_importable(self):
        import tomllib
        self.assertIsNotNone(tomllib)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run skeleton test**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest discover tests/ -v`
Expected: `Ran 2 tests in 0.00Xs / OK`

- [ ] **Step 7: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add .gitignore pyproject.toml harness/ tests/
git commit -m "chore: initialize project skeleton with Python 3.11 stdlib baseline"
```

---

## Task 2: Define dataclasses for all claim graph schemas

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_claim_graph_schemas.py`

- [ ] **Step 1: Write failing roundtrip tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_claim_graph_schemas.py`:
```python
import json
import unittest

from harness import claim_graph as cg


class ClaimRoundtripTest(unittest.TestCase):
    def test_claim_with_position_roundtrips(self):
        d = {
            "id": "cl-000001",
            "section_id": "retry-policy",
            "decision_id": "retry-policy",
            "position": "expo-backoff",
            "claim_type": "decision",
            "evidence_ids": ["ev-000001"],
            "assertion": "Use exponential backoff with full jitter.",
        }
        claim = cg.Claim.from_dict(d)
        self.assertEqual(claim.id, "cl-000001")
        self.assertEqual(claim.position, "expo-backoff")
        self.assertEqual(claim.to_dict(), d)

    def test_claim_with_out_of_scope_rationale(self):
        d = {
            "id": "cl-000002",
            "section_id": "auth-strategy",
            "decision_id": "auth-strategy",
            "claim_type": "out_of_scope",
            "out_of_scope_rationale": "Auth lives in a separate doc.",
            "evidence_ids": [],
            "assertion": "Auth is out of scope for this design.",
        }
        claim = cg.Claim.from_dict(d)
        self.assertEqual(claim.claim_type, "out_of_scope")
        self.assertEqual(claim.position, None)
        self.assertEqual(claim.to_dict(), d)

    def test_claim_with_proposed_decision(self):
        d = {
            "id": "cl-000003",
            "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker-policy",
            "position": "half-open-probing",
            "claim_type": "decision",
            "evidence_ids": ["ev-000010"],
            "assertion": "Use half-open probing after N failures.",
            "proposed_decision": {
                "id": "circuit-breaker-policy",
                "question": "When and how should the circuit breaker reset?",
                "rationale": "Not in goal.toml yet; raised by this round.",
            },
        }
        claim = cg.Claim.from_dict(d)
        self.assertEqual(claim.proposed_decision["id"], "circuit-breaker-policy")
        self.assertEqual(claim.to_dict(), d)

    def test_claim_unresolved_type(self):
        d = {
            "id": "cl-000004",
            "section_id": "rate-limit-policy",
            "decision_id": "rate-limit-policy",
            "claim_type": "unresolved",
            "evidence_ids": [],
            "assertion": "No defensible position yet on rate-limit window size.",
        }
        claim = cg.Claim.from_dict(d)
        self.assertEqual(claim.claim_type, "unresolved")
        self.assertIsNone(claim.position)
        self.assertEqual(claim.to_dict(), d)


class AttackRoundtripTest(unittest.TestCase):
    def test_dispute_claim_at_type(self):
        d = {
            "id": "at-000001",
            "at_type": "dispute_claim",
            "target_claim_id": "cl-000001",
            "target_variant": "v-001",
            "argument": "Evidence ev-000001 supports linear backoff, not exponential.",
            "evidence_ids": ["ev-000001"],
        }
        at = cg.Attack.from_dict(d)
        self.assertEqual(at.at_type, "dispute_claim")
        self.assertEqual(at.target_claim_id, "cl-000001")
        self.assertEqual(at.to_dict(), d)

    def test_propose_decision_cut_at_type(self):
        d = {
            "id": "at-000002",
            "at_type": "propose_decision_cut",
            "target_decision_id": "auth-strategy",
            "rationale": "Auth lives in a separate doc; this section is dead weight.",
        }
        at = cg.Attack.from_dict(d)
        self.assertEqual(at.at_type, "propose_decision_cut")
        self.assertEqual(at.target_decision_id, "auth-strategy")
        self.assertEqual(at.to_dict(), d)

    def test_propose_canonicalization_at_type(self):
        d = {
            "id": "at-000003",
            "at_type": "propose_canonicalization",
            "kind": "position",
            "scope": "retry-policy",
            "from": "exponential-backoff",
            "to": "expo-backoff",
            "confidence": "high",
            "rationale": "Both variants discuss the same scheme; expo-backoff appeared first.",
        }
        at = cg.Attack.from_dict(d)
        self.assertEqual(at.at_type, "propose_canonicalization")
        self.assertEqual(at.kind, "position")
        self.assertEqual(at.to_dict(), d)


class DecisionRoundtripTest(unittest.TestCase):
    def test_decision_dataclass(self):
        d = {
            "id": "retry-policy",
            "question": "How should transient failures be retried?",
            "status": "open",
            "introduced_at": "g-01",
        }
        dec = cg.Decision.from_dict(d)
        self.assertEqual(dec.status, "open")
        self.assertEqual(dec.to_dict(), d)


class CanonicalSlugRegistryRoundtripTest(unittest.TestCase):
    def test_empty_registry_roundtrips(self):
        d = {}
        reg = cg.CanonicalSlugRegistry.from_dict(d)
        self.assertEqual(reg.to_dict(), d)

    def test_populated_registry_roundtrips(self):
        d = {
            "retry-policy": {
                "canonical": ["expo-backoff", "linear-no-backoff"],
                "aliases": {"exponential-backoff": "expo-backoff"},
            },
            "auth-strategy": {
                "canonical": ["oauth2-pkce"],
                "aliases": {},
            },
        }
        reg = cg.CanonicalSlugRegistry.from_dict(d)
        out = reg.to_dict()
        # canonical lists may differ in ordering; compare as sets
        self.assertEqual(set(out["retry-policy"]["canonical"]),
                         set(d["retry-policy"]["canonical"]))
        self.assertEqual(out["retry-policy"]["aliases"], d["retry-policy"]["aliases"])
        self.assertEqual(set(out["auth-strategy"]["canonical"]),
                         set(d["auth-strategy"]["canonical"]))


class ReviewerDecisionProposalsRoundtripTest(unittest.TestCase):
    def test_reviewer_proposal_verdict(self):
        d = {
            "proposed_id": "circuit-breaker-policy",
            "verdict": "approve",
            "rationale": "On-thesis; not duplicative.",
        }
        v = cg.DecisionProposalVerdict.from_dict(d)
        self.assertEqual(v.verdict, "approve")
        self.assertEqual(v.to_dict(), d)


class TypeValidationTest(unittest.TestCase):
    def test_claim_rejects_unknown_claim_type(self):
        d = {
            "id": "cl-000001",
            "section_id": "retry-policy",
            "decision_id": "retry-policy",
            "position": "expo-backoff",
            "claim_type": "speculation",   # not in the enum
            "evidence_ids": [],
            "assertion": "x",
        }
        with self.assertRaises(cg.SchemaError) as cm:
            cg.Claim.from_dict(d)
        self.assertIn("claim_type", str(cm.exception))

    def test_attack_rejects_unknown_at_type(self):
        d = {
            "id": "at-000001",
            "at_type": "complain_loudly",
            "target_claim_id": "cl-000001",
        }
        with self.assertRaises(cg.SchemaError):
            cg.Attack.from_dict(d)

    def test_decision_rejects_unknown_status(self):
        d = {"id": "x", "question": "y?", "status": "frozen", "introduced_at": "g-01"}
        with self.assertRaises(cg.SchemaError):
            cg.Decision.from_dict(d)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_claim_graph_schemas -v`
Expected: ModuleNotFoundError or AttributeError because `harness.claim_graph` doesn't have these symbols yet.

- [ ] **Step 3: Implement dataclasses in claim_graph.py**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_claim_graph_schemas -v`
Expected: All tests pass. `Ran 13 tests in 0.00Xs / OK`

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_claim_graph_schemas.py
git commit -m "feat(claim_graph): dataclasses for Claim, Attack, Decision, Registry, ProposalVerdict"
```

---

## Task 3: Cross-field validators (decision_id resolution, vacuous slugs, alias rewriting check)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append validators)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_claim_graph_validators.py`

- [ ] **Step 1: Write failing validator tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_claim_graph_validators.py`:
```python
import unittest

from harness import claim_graph as cg


def _make_registry(data=None):
    """Build a CanonicalSlugRegistry."""
    return cg.CanonicalSlugRegistry.from_dict(data or {})


def _make_decisions(*ids_and_statuses):
    """Build a {decision_id: Decision} dict. Each arg is (id, status)."""
    return {
        decision_id: cg.Decision.from_dict({
            "id": decision_id, "question": f"q for {decision_id}?",
            "status": status, "introduced_at": "g-01",
        })
        for decision_id, status in ids_and_statuses
    }


def _claim(**overrides):
    base = {
        "id": "cl-000001",
        "section_id": "retry-policy",
        "decision_id": "retry-policy",
        "position": "expo-backoff",
        "claim_type": "decision",
        "evidence_ids": ["ev-000001"],
        "assertion": "x",
    }
    base.update(overrides)
    return cg.Claim.from_dict(base)


class DecisionIdResolutionValidatorTest(unittest.TestCase):
    def test_registered_open_decision_passes(self):
        decisions = _make_decisions(("retry-policy", "open"))
        cg.validate_claim_decision_id_resolution(_claim(), decisions)   # no raise

    def test_registered_proposed_decision_passes(self):
        decisions = _make_decisions(("retry-policy", "proposed"))
        cg.validate_claim_decision_id_resolution(_claim(), decisions)   # no raise

    def test_registered_retired_decision_fails_for_new_claim(self):
        decisions = _make_decisions(("retry-policy", "retired"))
        with self.assertRaises(cg.SchemaError) as cm:
            cg.validate_claim_decision_id_resolution(_claim(), decisions)
        self.assertIn("retired", str(cm.exception))

    def test_unregistered_with_proposed_decision_payload_passes(self):
        decisions = _make_decisions()   # empty registry
        claim = _claim(
            decision_id="circuit-breaker-policy",
            proposed_decision={
                "id": "circuit-breaker-policy",
                "question": "When to reset?",
                "rationale": "Not in goal.toml yet.",
            },
        )
        cg.validate_claim_decision_id_resolution(claim, decisions)   # no raise

    def test_unregistered_without_proposed_decision_fails(self):
        decisions = _make_decisions()
        claim = _claim(decision_id="circuit-breaker-policy")   # no proposed_decision
        with self.assertRaises(cg.SchemaError) as cm:
            cg.validate_claim_decision_id_resolution(claim, decisions)
        self.assertIn("not registered", str(cm.exception).lower())

    def test_proposed_decision_id_mismatch_fails(self):
        decisions = _make_decisions()
        claim = _claim(
            decision_id="circuit-breaker-policy",
            proposed_decision={
                "id": "circuit-breaker-strategy",   # mismatch
                "question": "x",
                "rationale": "y",
            },
        )
        with self.assertRaises(cg.SchemaError) as cm:
            cg.validate_claim_decision_id_resolution(claim, decisions)
        self.assertIn("proposed_decision.id", str(cm.exception))


class VacuousPositionValidatorTest(unittest.TestCase):
    def test_substantive_slug_passes(self):
        cg.validate_claim_position_not_vacuous(_claim(position="expo-backoff"))

    def test_tbd_slug_fails(self):
        with self.assertRaises(cg.SchemaError) as cm:
            cg.validate_claim_position_not_vacuous(_claim(position="tbd"))
        self.assertIn("vacuous", str(cm.exception).lower())

    def test_unclear_slug_fails(self):
        with self.assertRaises(cg.SchemaError):
            cg.validate_claim_position_not_vacuous(_claim(position="unclear"))

    def test_not_decided_slug_fails(self):
        with self.assertRaises(cg.SchemaError):
            cg.validate_claim_position_not_vacuous(_claim(position="not-decided"))

    def test_out_of_scope_skips_check(self):
        # out_of_scope claims have no position; validator should be a no-op
        claim = _claim(
            claim_type="out_of_scope",
            out_of_scope_rationale="separate doc",
            position=None,
        )
        # Re-author dict properly because _claim() sets position
        d = claim.to_dict()
        d.pop("position", None)
        d["claim_type"] = "out_of_scope"
        d["out_of_scope_rationale"] = "separate doc"
        claim = cg.Claim.from_dict(d)
        cg.validate_claim_position_not_vacuous(claim)   # no raise

    def test_unresolved_skips_check(self):
        d = {
            "id": "cl-x", "section_id": "y", "decision_id": "z",
            "claim_type": "unresolved", "evidence_ids": [], "assertion": "x",
        }
        claim = cg.Claim.from_dict(d)
        cg.validate_claim_position_not_vacuous(claim)   # no raise


class AliasRewritingValidatorTest(unittest.TestCase):
    def test_canonical_slug_passes(self):
        registry = _make_registry({
            "retry-policy": {"canonical": ["expo-backoff"], "aliases": {}},
        })
        cg.validate_claim_position_not_alias(_claim(position="expo-backoff"), registry)

    def test_alias_slug_fails(self):
        registry = _make_registry({
            "retry-policy": {
                "canonical": ["expo-backoff"],
                "aliases": {"exponential-backoff": "expo-backoff"},
            },
        })
        with self.assertRaises(cg.SchemaError) as cm:
            cg.validate_claim_position_not_alias(
                _claim(position="exponential-backoff"), registry,
            )
        self.assertIn("alias", str(cm.exception).lower())
        self.assertIn("expo-backoff", str(cm.exception))

    def test_unknown_slug_for_known_decision_passes(self):
        # A brand-new position slug under a known decision is OK; it will become
        # a new canonical entry once the round commits.
        registry = _make_registry({
            "retry-policy": {"canonical": ["expo-backoff"], "aliases": {}},
        })
        cg.validate_claim_position_not_alias(_claim(position="adaptive-jitter"), registry)

    def test_unknown_decision_skips_check(self):
        registry = _make_registry({})
        cg.validate_claim_position_not_alias(_claim(position="anything"), registry)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_claim_graph_validators -v`
Expected: AttributeError — validators don't exist yet.

- [ ] **Step 3: Append validators to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_claim_graph_validators -v`
Expected: All 15 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_claim_graph_validators.py
git commit -m "feat(claim_graph): cross-field validators for decision_id resolution, vacuous slugs, alias rewrites"
```

---

## Task 4: Append-only canonical slug registry mechanics

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append registry ops)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonical_registry.py`

- [ ] **Step 1: Write failing registry tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonical_registry.py`:
```python
import unittest

from harness import claim_graph as cg


def _empty_registry():
    return cg.CanonicalSlugRegistry()


class AddCanonicalPositionTest(unittest.TestCase):
    def test_first_canonical_for_new_decision_creates_entry(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["expo-backoff"])
        self.assertEqual(reg.data["retry-policy"]["aliases"], {})

    def test_additional_canonical_for_existing_decision_appends(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "linear-no-backoff")
        self.assertEqual(set(reg.data["retry-policy"]["canonical"]),
                         {"expo-backoff", "linear-no-backoff"})

    def test_adding_duplicate_canonical_is_noop(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")   # dup
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["expo-backoff"])

    def test_adding_alias_key_as_canonical_fails(self):
        # If a slug exists as an alias key, it cannot be added as canonical.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        with self.assertRaises(cg.RegistryInvariantError):
            cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")


class RegisterAliasTest(unittest.TestCase):
    def test_register_alias_to_canonical_succeeds(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        # from slug moved out of canonical, into aliases
        self.assertNotIn("exponential-backoff", reg.data["retry-policy"]["canonical"])
        self.assertEqual(reg.data["retry-policy"]["aliases"]["exponential-backoff"],
                         "expo-backoff")
        self.assertIn("expo-backoff", reg.data["retry-policy"]["canonical"])

    def test_register_alias_to_non_canonical_fails(self):
        # 'to' MUST be in canonical list
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.register_alias(reg, "retry-policy",
                              "exponential-backoff", "novel-slug")
        self.assertIn("not canonical", str(cm.exception).lower())

    def test_register_alias_from_non_canonical_fails(self):
        # 'from' must currently be canonical (else nothing to rewrite)
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        with self.assertRaises(cg.RegistryInvariantError):
            cg.register_alias(reg, "retry-policy",
                              "never-existed", "expo-backoff")

    def test_alias_keys_are_append_only(self):
        # Once a slug is an alias key, it cannot be re-pointed.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "expo-bo")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        # Now try to re-point 'exponential-backoff' to 'expo-bo'
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.register_alias(reg, "retry-policy",
                              "exponential-backoff", "expo-bo")
        self.assertIn("already an alias", str(cm.exception).lower())

    def test_canonical_list_cannot_shrink_except_via_register_alias(self):
        # There is no remove_canonical operation; the only way out of canonical
        # is via register_alias. We test this by trying to call register_alias
        # with both from and to as canonical (legal) and verify the from is now
        # absent from canonical.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "a")
        cg.add_canonical_position(reg, "retry-policy", "b")
        cg.register_alias(reg, "retry-policy", "a", "b")
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["b"])
        # Verify no public remove method exists
        self.assertFalse(hasattr(cg, "remove_canonical_position"))


class RewritePositionTest(unittest.TestCase):
    def test_rewrite_alias_returns_canonical(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy",
                                             "exponential-backoff"),
            "expo-backoff",
        )

    def test_rewrite_canonical_returns_self(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy", "expo-backoff"),
            "expo-backoff",
        )

    def test_rewrite_unknown_slug_returns_self(self):
        reg = _empty_registry()
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy", "novel"),
            "novel",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_canonical_registry -v`
Expected: AttributeError — registry ops don't exist.

- [ ] **Step 3: Append registry operations to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_canonical_registry -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_canonical_registry.py
git commit -m "feat(claim_graph): append-only canonical slug registry mechanics"
```

---

## Task 5: Decision registry — load from goal.toml, dump to JSON, detect changes

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registry.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/fixtures/claim_graph/__init__.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/fixtures/claim_graph/__init__.py`:
```python
```

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registry.py`:
```python
import json
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


SAMPLE_GOAL_TOML = """\
[goal]
title = "Test Design Doc"
description = "Sample for tests."
goal_version = "g-01"

[[decision]]
id = "retry-policy"
question = "How should transient failures be retried?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "auth-strategy"
question = "What auth scheme should the API use?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "deprecated-thing"
question = "Should we use deprecated-thing?"
status = "retired"
introduced_at = "g-01"
"""


class LoadDecisionsFromGoalTomlTest(unittest.TestCase):
    def test_loads_all_decisions(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(SAMPLE_GOAL_TOML)
            path = Path(f.name)
        try:
            decisions, goal_version = cg.load_decisions_from_goal_toml(path)
            self.assertEqual(goal_version, "g-01")
            self.assertEqual(set(decisions.keys()),
                             {"retry-policy", "auth-strategy", "deprecated-thing"})
            self.assertEqual(decisions["retry-policy"].status, "open")
            self.assertEqual(decisions["deprecated-thing"].status, "retired")
        finally:
            path.unlink()

    def test_empty_decision_table_returns_empty_dict(self):
        toml = '[goal]\ntitle = "t"\ngoal_version = "g-01"\n'
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            path = Path(f.name)
        try:
            decisions, goal_version = cg.load_decisions_from_goal_toml(path)
            self.assertEqual(decisions, {})
            self.assertEqual(goal_version, "g-01")
        finally:
            path.unlink()

    def test_missing_goal_version_raises(self):
        toml = '[goal]\ntitle = "t"\n'
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            path = Path(f.name)
        try:
            with self.assertRaises(cg.SchemaError):
                cg.load_decisions_from_goal_toml(path)
        finally:
            path.unlink()


class DumpDecisionsToJsonTest(unittest.TestCase):
    def test_dump_and_reload_roundtrip(self):
        decisions = {
            "retry-policy": cg.Decision.from_dict({
                "id": "retry-policy",
                "question": "How to retry?",
                "status": "open",
                "introduced_at": "g-01",
            }),
        }
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "decisions.json"
            cg.dump_decisions_to_json(decisions, "g-01", out)
            self.assertTrue(out.exists())
            with out.open() as f:
                loaded = json.load(f)
            self.assertEqual(loaded["goal_version"], "g-01")
            self.assertEqual(loaded["decisions"]["retry-policy"]["status"], "open")


class DetectGoalTomlChangesTest(unittest.TestCase):
    def _setup(self, toml_text, decisions_json_text):
        td = Path(tempfile.mkdtemp())
        goal_path = td / "goal.toml"
        goal_path.write_text(toml_text)
        derived = td / "derived"
        derived.mkdir()
        decisions_path = derived / "decisions.json"
        decisions_path.write_text(decisions_json_text)
        return td, goal_path, decisions_path

    def test_no_changes_returns_unchanged(self):
        decisions_json = json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy",
                    "question": "How should transient failures be retried?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
                "auth-strategy": {
                    "id": "auth-strategy",
                    "question": "What auth scheme should the API use?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
                "deprecated-thing": {
                    "id": "deprecated-thing",
                    "question": "Should we use deprecated-thing?",
                    "status": "retired",
                    "introduced_at": "g-01",
                },
            },
        })
        _, goal_path, decisions_path = self._setup(SAMPLE_GOAL_TOML, decisions_json)
        verdict = cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertEqual(verdict, "unchanged")

    def test_goal_version_bump_returns_versioned_change(self):
        bumped = SAMPLE_GOAL_TOML.replace('goal_version = "g-01"',
                                          'goal_version = "g-02"')
        decisions_json = json.dumps({"goal_version": "g-01", "decisions": {}})
        _, goal_path, decisions_path = self._setup(bumped, decisions_json)
        verdict = cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertEqual(verdict, "versioned-change")

    def test_silent_change_raises(self):
        # goal.toml content differs but goal_version is unchanged
        changed = SAMPLE_GOAL_TOML + """

[[decision]]
id = "circuit-breaker"
question = "When does the breaker reset?"
status = "open"
introduced_at = "g-01"
"""
        decisions_json = json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {"id": "retry-policy",
                                 "question": "How should transient failures be retried?",
                                 "status": "open", "introduced_at": "g-01"},
                "auth-strategy": {"id": "auth-strategy",
                                  "question": "What auth scheme should the API use?",
                                  "status": "open", "introduced_at": "g-01"},
                "deprecated-thing": {"id": "deprecated-thing",
                                     "question": "Should we use deprecated-thing?",
                                     "status": "retired", "introduced_at": "g-01"},
            },
        })
        _, goal_path, decisions_path = self._setup(changed, decisions_json)
        with self.assertRaises(cg.SchemaError) as cm:
            cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertIn("goal_version", str(cm.exception))
        self.assertIn("bump", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_decision_registry -v`
Expected: AttributeError — decision-registry functions don't exist.

- [ ] **Step 3: Append decision-registry functions to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_decision_registry -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_decision_registry.py tests/fixtures/
git commit -m "feat(claim_graph): decision registry I/O + content-based change detection"
```

---

## Task 6: Decision registration (Flow A auto-apply)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registration.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registration.py`:
```python
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


SEED_GOAL_TOML = """\
[goal]
title = "Test"
goal_version = "g-01"

[[decision]]
id = "retry-policy"
question = "How to retry?"
status = "open"
introduced_at = "g-01"
"""


class RegisterDecisionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.goal_path = self.td / "goal.toml"
        self.goal_path.write_text(SEED_GOAL_TOML)

    def test_register_appends_new_decision_and_bumps_version(self):
        new_decisions = [
            {
                "id": "circuit-breaker-policy",
                "question": "When does the breaker reset?",
                "rationale": "Discovered by round 5 designer.",
            },
        ]
        new_version = cg.register_decision(self.goal_path, new_decisions)
        self.assertEqual(new_version, "g-02")
        loaded, v = cg.load_decisions_from_goal_toml(self.goal_path)
        self.assertEqual(v, "g-02")
        self.assertIn("circuit-breaker-policy", loaded)
        self.assertEqual(loaded["circuit-breaker-policy"].introduced_at, "g-02")
        # Existing entries preserved
        self.assertIn("retry-policy", loaded)
        self.assertEqual(loaded["retry-policy"].introduced_at, "g-01")

    def test_register_multiple_decisions_one_version_bump(self):
        new_decisions = [
            {"id": "a-policy", "question": "?", "rationale": "x"},
            {"id": "b-policy", "question": "?", "rationale": "x"},
        ]
        new_version = cg.register_decision(self.goal_path, new_decisions)
        self.assertEqual(new_version, "g-02")
        loaded, _ = cg.load_decisions_from_goal_toml(self.goal_path)
        self.assertIn("a-policy", loaded)
        self.assertIn("b-policy", loaded)
        self.assertEqual(loaded["a-policy"].introduced_at, "g-02")
        self.assertEqual(loaded["b-policy"].introduced_at, "g-02")

    def test_register_duplicate_id_raises(self):
        # Trying to re-register an existing decision_id
        with self.assertRaises(cg.SchemaError) as cm:
            cg.register_decision(self.goal_path, [
                {"id": "retry-policy", "question": "?", "rationale": "x"},
            ])
        self.assertIn("retry-policy", str(cm.exception))

    def test_register_invalid_slug_raises(self):
        with self.assertRaises(cg.SchemaError):
            cg.register_decision(self.goal_path, [
                {"id": "Bad_Slug", "question": "?", "rationale": "x"},
            ])

    def test_version_bump_double_digit(self):
        # Manually bump goal.toml to g-09 then register
        text = self.goal_path.read_text().replace('goal_version = "g-01"',
                                                  'goal_version = "g-09"')
        self.goal_path.write_text(text)
        new_version = cg.register_decision(self.goal_path, [
            {"id": "x-policy", "question": "?", "rationale": "x"},
        ])
        self.assertEqual(new_version, "g-10")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_decision_registration -v`
Expected: AttributeError — `register_decision` doesn't exist.

- [ ] **Step 3: Append `register_decision` to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


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

    Raises SchemaError on duplicate id, invalid slug, or unparseable goal_version.
    """
    text = goal_toml_path.read_text()
    existing, _ = load_decisions_from_goal_toml(goal_toml_path)
    new_text, new_version = _bump_goal_version(text)
    appended_blocks: list[str] = []
    for entry in new_decisions:
        for req in ("id", "question", "rationale"):
            if req not in entry:
                raise SchemaError(f"register_decision entry missing {req!r}")
        _require_slug(entry["id"], "id")
        if entry["id"] in existing:
            raise SchemaError(
                f"Cannot register {entry['id']!r}: already in goal.toml"
            )
        # Build a TOML [[decision]] block; quote-escape question by replacing " with \"
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_decision_registration -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_decision_registration.py
git commit -m "feat(claim_graph): Flow A decision registration with goal_version bump"
```

---

## Task 7: Auto-apply canonicalization (Flow C high-confidence path)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonicalization_apply.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonicalization_apply.py`:
```python
import json
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str, position: str):
    p = variant_dir / "claims"
    p.mkdir(parents=True, exist_ok=True)
    fp = p / f"{claim_id}.json"
    fp.write_text(json.dumps({
        "id": claim_id,
        "section_id": decision_id,
        "decision_id": decision_id,
        "claim_type": "decision",
        "evidence_ids": [],
        "assertion": "x",
        "position": position,
    }, indent=2))
    return fp


class ApplyCanonicalizationTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"
        self.v2 = self.variants / "v-002"

    def test_apply_rewrites_slug_in_cl_files(self):
        f1 = _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        f2 = _write_cl(self.v2, "cl-000002", "retry-policy", "expo-backoff")
        # Both slugs are canonical before applying
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        rewrites = cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        # v-001's cl-000001 should now use expo-backoff
        d1 = json.loads(f1.read_text())
        self.assertEqual(d1["position"], "expo-backoff")
        # v-002's cl-000002 unchanged
        d2 = json.loads(f2.read_text())
        self.assertEqual(d2["position"], "expo-backoff")
        # Registry: 'exponential-backoff' moved to aliases
        self.assertNotIn("exponential-backoff", registry.data["retry-policy"]["canonical"])
        self.assertEqual(registry.data["retry-policy"]["aliases"]["exponential-backoff"],
                         "expo-backoff")
        # Rewrites list reports each file changed
        self.assertEqual(len(rewrites), 1)
        self.assertEqual(rewrites[0]["path"], str(f1.relative_to(self.td.parent)))
        self.assertEqual(rewrites[0]["from"], "exponential-backoff")
        self.assertEqual(rewrites[0]["to"], "expo-backoff")

    def test_apply_does_not_touch_other_decisions(self):
        # A claim under a DIFFERENT decision with same slug should not be rewritten
        _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        other = _write_cl(self.v1, "cl-000002", "auth-strategy", "exponential-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        cg.add_canonical_position(registry, "auth-strategy", "exponential-backoff")
        cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        # auth-strategy claim is unchanged
        d = json.loads(other.read_text())
        self.assertEqual(d["position"], "exponential-backoff")

    def test_apply_invalid_target_raises(self):
        _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        # 'expo-backoff' is NOT canonical yet
        with self.assertRaises(cg.RegistryInvariantError):
            cg.apply_canonicalization(
                self.variants, registry, "retry-policy",
                from_slug="exponential-backoff", to_slug="expo-backoff",
            )

    def test_apply_no_matching_claims_succeeds_with_empty_rewrites(self):
        # Registry has both slugs but no cl-*.json files use the 'from' slug
        _write_cl(self.v1, "cl-000001", "retry-policy", "expo-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        rewrites = cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        self.assertEqual(rewrites, [])
        # Registry still updated
        self.assertNotIn("exponential-backoff",
                         registry.data["retry-policy"]["canonical"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_canonicalization_apply -v`
Expected: AttributeError — `apply_canonicalization` doesn't exist.

- [ ] **Step 3: Append `apply_canonicalization` to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_canonicalization_apply -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_canonicalization_apply.py
git commit -m "feat(claim_graph): apply_canonicalization walks cl-*.json and rewrites slugs"
```

---

## Task 8: Detector 1 — Position collisions across variants

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_collision_detection.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_collision_detection.py`:
```python
import json
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str,
              position: str | None = None, claim_type: str = "decision",
              evidence_ids: list[str] | None = None,
              out_of_scope_rationale: str | None = None):
    claims = variant_dir / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": claim_id, "section_id": decision_id,
        "decision_id": decision_id, "claim_type": claim_type,
        "evidence_ids": evidence_ids or [],
        "assertion": "x",
    }
    if position is not None:
        payload["position"] = position
    if out_of_scope_rationale is not None:
        payload["out_of_scope_rationale"] = out_of_scope_rationale
    (claims / f"{claim_id}.json").write_text(json.dumps(payload, indent=2))


class DetectPositionCollisionsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"
        self.v2 = self.variants / "v-002"

    def test_same_position_same_decision_no_collision(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff", evidence_ids=["ev-1"])
        _write_cl(self.v2, "cl-002", "retry-policy", "expo-backoff", evidence_ids=["ev-1"])
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(collisions, [])

    def test_different_position_same_decision_yields_collision(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff", evidence_ids=["ev-1"])
        _write_cl(self.v2, "cl-002", "retry-policy", "linear-no-backoff",
                  evidence_ids=["ev-2"])
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(len(collisions), 1)
        c = collisions[0]
        self.assertEqual(c["decision_id"], "retry-policy")
        self.assertEqual(c["confirmed"], False)
        self.assertEqual(len(c["per_variant"]), 2)
        positions = {v["position"] for v in c["per_variant"]}
        self.assertEqual(positions, {"expo-backoff", "linear-no-backoff"})

    def test_three_distinct_positions_yield_one_collision_record(self):
        v3 = self.variants / "v-003"
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", "linear-no-backoff")
        _write_cl(v3, "cl-003", "retry-policy", "no-retry")
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(len(collisions), 1)
        self.assertEqual(len(collisions[0]["per_variant"]), 3)

    def test_out_of_scope_does_not_trigger_position_collision(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", claim_type="out_of_scope",
                  out_of_scope_rationale="separate doc")
        collisions = cg.detect_position_collisions(self.variants)
        # Position collision requires both variants to have positions
        self.assertEqual(collisions, [])

    def test_unresolved_does_not_trigger_position_collision(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", claim_type="unresolved")
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(collisions, [])

    def test_single_variant_does_not_trigger(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(collisions, [])

    def test_multiple_decisions_each_yield_their_own_record(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", "linear-no-backoff")
        _write_cl(self.v1, "cl-003", "auth-strategy", "oauth2")
        _write_cl(self.v2, "cl-004", "auth-strategy", "mtls")
        collisions = cg.detect_position_collisions(self.variants)
        self.assertEqual(len(collisions), 2)
        decision_ids = {c["decision_id"] for c in collisions}
        self.assertEqual(decision_ids, {"retry-policy", "auth-strategy"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_collision_detection -v`
Expected: AttributeError — `detect_position_collisions` doesn't exist.

- [ ] **Step 3: Append detector to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


# ----- Detectors --------------------------------------------------------------


def _walk_claims(variants_nodes_root: _Path) -> dict[str, dict[str, list[dict]]]:
    """Return {decision_id: {variant_name: [claim_dict, ...]}}.

    Reads every variants/nodes/v-*/claims/cl-*.json. Ignores malformed files
    (logged-only behavior; orchestrator-level validation catches schema errors
    earlier).
    """
    out: dict[str, dict[str, list[dict]]] = {}
    if not variants_nodes_root.exists():
        return out
    for variant_dir in sorted(variants_nodes_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        claims_dir = variant_dir / "claims"
        if not claims_dir.exists():
            continue
        for cl_file in sorted(claims_dir.glob("cl-*.json")):
            try:
                with cl_file.open() as f:
                    data = _json.load(f)
            except (_json.JSONDecodeError, OSError):
                continue
            decision_id = data.get("decision_id")
            if not decision_id:
                continue
            out.setdefault(decision_id, {}).setdefault(variant_dir.name, []).append(data)
    return out


def detect_position_collisions(variants_nodes_root: _Path) -> list[dict]:
    """Find decisions where multiple variants chose different position slugs.

    For each decision_id with positions from >=2 variants, if the set of
    positions has size >= 2, produce a collision record. Out_of_scope and
    unresolved claims have no position; they do not contribute.

    Returns a list of {"decision_id": ..., "per_variant": [{...}], "confirmed": False}.
    """
    grouped = _walk_claims(variants_nodes_root)
    collisions: list[dict] = []
    for decision_id, per_variant in sorted(grouped.items()):
        # For each variant, pick the LATEST claim with a position (highest id).
        per_variant_latest: dict[str, dict] = {}
        for variant_name, claim_list in per_variant.items():
            with_position = [c for c in claim_list if c.get("position")]
            if not with_position:
                continue
            latest = max(with_position, key=lambda c: c["id"])
            per_variant_latest[variant_name] = latest
        if len(per_variant_latest) < 2:
            continue
        distinct_positions = {c["position"] for c in per_variant_latest.values()}
        if len(distinct_positions) < 2:
            continue
        collisions.append({
            "decision_id": decision_id,
            "per_variant": sorted([
                {
                    "variant": variant_name,
                    "claim_id": claim["id"],
                    "position": claim["position"],
                    "evidence_ids": list(claim.get("evidence_ids", [])),
                }
                for variant_name, claim in per_variant_latest.items()
            ], key=lambda x: x["variant"]),
            "confirmed": False,
        })
    return collisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_collision_detection -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_collision_detection.py
git commit -m "feat(claim_graph): Detector 1 — position collisions across variants"
```

---

## Task 9: Detectors 2 + 3 — Decisional asymmetry + stale proposals

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_coverage_asymmetry.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_coverage_asymmetry.py`:
```python
import json
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str,
              position: str | None = None, claim_type: str = "decision",
              out_of_scope_rationale: str | None = None):
    claims = variant_dir / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": claim_id, "section_id": decision_id,
        "decision_id": decision_id, "claim_type": claim_type,
        "evidence_ids": [], "assertion": "x",
    }
    if position is not None:
        payload["position"] = position
    if out_of_scope_rationale is not None:
        payload["out_of_scope_rationale"] = out_of_scope_rationale
    (claims / f"{claim_id}.json").write_text(json.dumps(payload, indent=2))


def _make_decisions(*ids_and_statuses):
    return {
        decision_id: cg.Decision.from_dict({
            "id": decision_id, "question": "?", "status": status,
            "introduced_at": "g-01",
        })
        for decision_id, status in ids_and_statuses
    }


class DetectDecisionalAsymmetryTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"
        self.v2 = self.variants / "v-002"

    def test_both_decided_no_asymmetry(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", "linear-no-backoff")
        decisions = _make_decisions(("retry-policy", "open"))
        entries = cg.detect_decisional_asymmetry(self.variants, decisions)
        # Both variants decided; that's a position collision, not asymmetry
        self.assertEqual(entries, [])

    def test_one_decided_one_out_of_scope_yields_asymmetry(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_cl(self.v2, "cl-002", "retry-policy", claim_type="out_of_scope",
                  out_of_scope_rationale="separate doc")
        decisions = _make_decisions(("retry-policy", "open"))
        entries = cg.detect_decisional_asymmetry(self.variants, decisions)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["decision_id"], "retry-policy")
        statuses = {v["status"]: v for v in e["per_variant"]}
        self.assertIn("decided", statuses)
        self.assertIn("out_of_scope", statuses)
        self.assertEqual(statuses["decided"]["position"], "expo-backoff")
        self.assertEqual(statuses["out_of_scope"]["out_of_scope_rationale"],
                         "separate doc")

    def test_one_decided_one_unaddressed_no_asymmetry(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        # v-002 has nothing
        self.v2.mkdir(parents=True)
        decisions = _make_decisions(("retry-policy", "open"))
        entries = cg.detect_decisional_asymmetry(self.variants, decisions)
        # Unaddressed silence does NOT trigger
        self.assertEqual(entries, [])

    def test_both_out_of_scope_no_asymmetry(self):
        _write_cl(self.v1, "cl-001", "retry-policy", claim_type="out_of_scope",
                  out_of_scope_rationale="r1")
        _write_cl(self.v2, "cl-002", "retry-policy", claim_type="out_of_scope",
                  out_of_scope_rationale="r2")
        decisions = _make_decisions(("retry-policy", "open"))
        entries = cg.detect_decisional_asymmetry(self.variants, decisions)
        self.assertEqual(entries, [])

    def test_only_considers_registered_decisions(self):
        _write_cl(self.v1, "cl-001", "unknown-decision", "x")
        _write_cl(self.v2, "cl-002", "unknown-decision",
                  claim_type="out_of_scope",
                  out_of_scope_rationale="not registered yet")
        decisions = _make_decisions(("retry-policy", "open"))  # different decision
        entries = cg.detect_decisional_asymmetry(self.variants, decisions)
        self.assertEqual(entries, [])


class DetectStaleProposalsTest(unittest.TestCase):
    def test_open_decision_never_stale(self):
        decisions = _make_decisions(("retry-policy", "open"))
        introduced_round = {"retry-policy": 1}
        stale = cg.detect_stale_proposals(decisions, introduced_round,
                                          current_round=20, threshold=5)
        self.assertEqual(stale, [])

    def test_proposed_decision_under_threshold_not_stale(self):
        decisions = _make_decisions(("circuit-breaker", "proposed"))
        introduced_round = {"circuit-breaker": 10}
        stale = cg.detect_stale_proposals(decisions, introduced_round,
                                          current_round=13, threshold=5)
        self.assertEqual(stale, [])

    def test_proposed_decision_over_threshold_stale(self):
        decisions = _make_decisions(("circuit-breaker", "proposed"))
        introduced_round = {"circuit-breaker": 10}
        stale = cg.detect_stale_proposals(decisions, introduced_round,
                                          current_round=20, threshold=5)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["decision_id"], "circuit-breaker")
        self.assertEqual(stale[0]["rounds_since_proposal"], 10)

    def test_retired_decision_never_stale(self):
        decisions = _make_decisions(("dead-thing", "retired"))
        introduced_round = {"dead-thing": 1}
        stale = cg.detect_stale_proposals(decisions, introduced_round,
                                          current_round=100, threshold=5)
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_coverage_asymmetry -v`
Expected: AttributeError.

- [ ] **Step 3: Append Detectors 2 + 3 to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


def detect_decisional_asymmetry(
    variants_nodes_root: _Path,
    decisions: dict[str, Decision],
) -> list[dict]:
    """Find decisions where >=1 variant is `decided` and >=1 is `out_of_scope`.

    Considers ONLY registered (non-retired) decisions. Unaddressed silence
    does NOT trigger (per spec §5.2 Detector 2).

    Returns entries: {"decision_id": ..., "per_variant": [{variant, status,
    position|out_of_scope_rationale}, ...]}.
    """
    grouped = _walk_claims(variants_nodes_root)
    entries: list[dict] = []
    for decision_id, per_variant in sorted(grouped.items()):
        if decision_id not in decisions:
            continue
        if decisions[decision_id].status == "retired":
            continue
        # Per-variant latest claim
        per_variant_status: list[dict] = []
        for variant_name, claim_list in per_variant.items():
            latest = max(claim_list, key=lambda c: c["id"])
            ct = latest.get("claim_type")
            if ct == "out_of_scope":
                per_variant_status.append({
                    "variant": variant_name, "status": "out_of_scope",
                    "claim_id": latest["id"],
                    "out_of_scope_rationale": latest.get("out_of_scope_rationale"),
                })
            elif ct in ("decision", "observation", "inference") and latest.get("position"):
                per_variant_status.append({
                    "variant": variant_name, "status": "decided",
                    "claim_id": latest["id"],
                    "position": latest["position"],
                })
            # unresolved or no-position: don't count as either status for asymmetry
        statuses = {v["status"] for v in per_variant_status}
        if "decided" in statuses and "out_of_scope" in statuses:
            entries.append({
                "decision_id": decision_id,
                "per_variant": sorted(per_variant_status, key=lambda v: v["variant"]),
            })
    return entries


def detect_stale_proposals(
    decisions: dict[str, Decision],
    introduced_round: dict[str, int],
    current_round: int,
    threshold: int = 5,
) -> list[dict]:
    """Find `proposed` decisions older than `threshold` rounds.

    Args:
        decisions: full registry from derived/decisions.json
        introduced_round: {decision_id: round_number_when_first_proposed}
        current_round: the current round number
        threshold: max rounds a proposal may remain `proposed` before stale
    """
    stale: list[dict] = []
    for decision_id, dec in sorted(decisions.items()):
        if dec.status != "proposed":
            continue
        intro = introduced_round.get(decision_id)
        if intro is None:
            continue
        age = current_round - intro
        if age >= threshold:
            stale.append({
                "decision_id": decision_id,
                "question": dec.question,
                "rounds_since_proposal": age,
                "introduced_round": intro,
            })
    return stale
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_coverage_asymmetry -v`
Expected: All 9 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_coverage_asymmetry.py
git commit -m "feat(claim_graph): Detectors 2 + 3 — decisional asymmetry, stale proposals"
```

---

## Task 10: Section retag walker (handles retired decisions)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_section_retag.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_section_retag.py`:
```python
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


SECTION_TEMPLATE = """+++
section_id = "{section_id}"
created_round = "round-000001"
created_role = "designer"
goal_version = "g-01"
evidence_ids = []
claim_id = "{claim_id}"
tags = [{tags}]
history = []
+++
## {section_id}
Section body.
"""


def _write_section(variant_doc_dir: Path, section_id: str, claim_id: str,
                   tags: list[str]):
    variant_doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    text = SECTION_TEMPLATE.format(section_id=section_id, claim_id=claim_id,
                                   tags=tag_str)
    (variant_doc_dir / f"01-{section_id}.md").write_text(text)


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str,
              position: str):
    claims = variant_dir / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    import json
    (claims / f"{claim_id}.json").write_text(json.dumps({
        "id": claim_id, "section_id": decision_id,
        "decision_id": decision_id, "claim_type": "decision",
        "evidence_ids": [], "assertion": "x", "position": position,
    }, indent=2))


class RetagSectionsForRetiredDecisionsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"

    def test_decided_section_retired_decision_flips_to_unresolved(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["decided"])
        retired = {"retry-policy"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(len(retagged), 1)
        self.assertEqual(retagged[0]["section_id"], "retry-policy")
        text = (self.v1 / "doc" / "01-retry-policy.md").read_text()
        self.assertIn('tags = ["unresolved"]', text)
        self.assertNotIn('"decided"', text)

    def test_decided_section_non_retired_decision_untouched(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["decided"])
        retired = {"some-other-decision"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(retagged, [])
        text = (self.v1 / "doc" / "01-retry-policy.md").read_text()
        self.assertIn('"decided"', text)

    def test_unresolved_section_untouched(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["unresolved"])
        retired = {"retry-policy"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(retagged, [])

    def test_missing_claim_file_logs_and_skips(self):
        # Section references a claim_id that doesn't exist; walker is defensive
        _write_section(self.v1 / "doc", "retry-policy", "cl-missing", ["decided"])
        retired = {"retry-policy"}
        # Should not raise
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        # Behavior: still retag if the section's frontmatter section_id matches
        # the retired decision_id. (claim_id is informational here.)
        self.assertEqual(len(retagged), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_section_retag -v`
Expected: AttributeError.

- [ ] **Step 3: Append walker to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


# ----- Section retag walker ---------------------------------------------------


# Matches TOML frontmatter `tags = ["decided", ...]` and similar arrays.
_TAGS_LINE_RE = re.compile(r'^(\s*tags\s*=\s*)(\[[^\]]*\])(\s*)$', re.MULTILINE)


def _section_tags(frontmatter_text: str) -> list[str]:
    """Extract the tags array from a section's TOML frontmatter."""
    m = _TAGS_LINE_RE.search(frontmatter_text)
    if m is None:
        return []
    inner = m.group(2)[1:-1]   # strip [ ]
    tags: list[str] = []
    for piece in inner.split(","):
        piece = piece.strip().strip('"').strip("'").strip()
        if piece:
            tags.append(piece)
    return tags


def _section_decision_id(frontmatter_text: str) -> str | None:
    """Extract section_id from a TOML frontmatter block. For v0 we treat
    section_id as equivalent to decision_id when retagging."""
    m = re.search(r'^\s*section_id\s*=\s*"([^"]+)"\s*$',
                  frontmatter_text, re.MULTILINE)
    return m.group(1) if m else None


def _set_section_tags(frontmatter_text: str, new_tags: list[str]) -> str:
    """Rewrite the tags = [...] line to contain only new_tags."""
    replacement = "[" + ", ".join(f'"{t}"' for t in new_tags) + "]"
    new_text, n = _TAGS_LINE_RE.subn(rf'\1{replacement}\3', frontmatter_text,
                                     count=1)
    if n == 0:
        # No existing tags line; insert one before the closing +++
        new_text = frontmatter_text.replace(
            "+++\n",
            f"tags = {replacement}\n+++\n",
            1,
        )
    return new_text


def retag_sections_for_retired_decisions(
    variants_nodes_root: _Path,
    retired_decision_ids: set[str],
) -> list[dict]:
    """Walk variants/*/doc/*.md, find sections whose section_id is in
    retired_decision_ids AND whose tags include 'decided', flip to 'unresolved'.

    Returns a list of {"variant": ..., "section_id": ..., "path": ...,
    "prior_tags": [...], "new_tags": [...]} for each section retagged.
    """
    retagged: list[dict] = []
    if not variants_nodes_root.exists():
        return retagged
    for variant_dir in sorted(variants_nodes_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        doc_dir = variant_dir / "doc"
        if not doc_dir.exists():
            continue
        for md in sorted(doc_dir.glob("*.md")):
            text = md.read_text()
            if not text.startswith("+++"):
                continue
            end = text.find("+++", 3)
            if end == -1:
                continue
            frontmatter = text[3:end]
            body = text[end:]
            section_id = _section_decision_id(frontmatter)
            if section_id is None or section_id not in retired_decision_ids:
                continue
            tags = _section_tags(frontmatter)
            if "decided" not in tags:
                continue
            new_tags = ["unresolved" if t == "decided" else t for t in tags]
            new_frontmatter = _set_section_tags(frontmatter, new_tags)
            md.write_text("+++" + new_frontmatter + body)
            retagged.append({
                "variant": variant_dir.name,
                "section_id": section_id,
                "path": str(md.relative_to(variants_nodes_root.parent.parent)),
                "prior_tags": tags,
                "new_tags": new_tags,
            })
    return retagged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_section_retag -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_section_retag.py
git commit -m "feat(claim_graph): section retag walker for retired-decision cascade"
```

---

## Task 11: Reviewer decision_proposals gating

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_reviewer_gating.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_reviewer_gating.py`:
```python
import unittest

from harness import claim_graph as cg


def _verdict(proposed_id: str, verdict: str, rationale: str = "x"):
    return cg.DecisionProposalVerdict.from_dict({
        "proposed_id": proposed_id, "verdict": verdict, "rationale": rationale,
    })


def _proposed(decision_id: str, question: str = "?", rationale: str = "x"):
    return {"id": decision_id, "question": question, "rationale": rationale}


class ApplyReviewerDecisionProposalsTest(unittest.TestCase):
    def test_all_approve_returns_approved_list(self):
        proposals = [_proposed("a-policy"), _proposed("b-policy")]
        verdicts = [_verdict("a-policy", "approve"), _verdict("b-policy", "approve")]
        outcome = cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertEqual(outcome["status"], "all-approved")
        self.assertEqual(len(outcome["approved"]), 2)
        self.assertEqual(outcome["rejected"], [])

    def test_any_reject_fails_round(self):
        proposals = [_proposed("a-policy"), _proposed("b-policy")]
        verdicts = [_verdict("a-policy", "approve"),
                    _verdict("b-policy", "reject", "off-thesis")]
        outcome = cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertEqual(outcome["status"], "any-rejected")
        self.assertEqual(outcome["round_fail_reason"], "proposal-rejected")
        self.assertEqual(len(outcome["rejected"]), 1)
        self.assertEqual(outcome["rejected"][0]["proposed_id"], "b-policy")

    def test_no_proposals_no_verdicts_ok(self):
        outcome = cg.apply_reviewer_decision_proposals([], [])
        self.assertEqual(outcome["status"], "all-approved")
        self.assertEqual(outcome["approved"], [])

    def test_mismatched_verdict_count_raises(self):
        proposals = [_proposed("a-policy")]
        verdicts = []   # missing
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertIn("missing verdict", str(cm.exception).lower())

    def test_verdict_for_unknown_proposal_raises(self):
        proposals = [_proposed("a-policy")]
        verdicts = [_verdict("z-policy", "approve")]
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertIn("z-policy", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_reviewer_gating -v`
Expected: AttributeError.

- [ ] **Step 3: Append reviewer-gating to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


# ----- Reviewer decision_proposals gating (Flow A step 5) ---------------------


def apply_reviewer_decision_proposals(
    proposed_decisions: list[dict],
    verdicts: list[DecisionProposalVerdict],
) -> dict:
    """Match designer-proposed new decisions against reviewer verdicts.

    Args:
        proposed_decisions: each {"id": ..., "question": ..., "rationale": ...}
            extracted from cl-*.json `proposed_decision` payloads.
        verdicts: one DecisionProposalVerdict per proposed_decisions entry.

    Returns:
        {
          "status": "all-approved" | "any-rejected",
          "approved": [proposed_decision_dict, ...],
          "rejected": [{"proposed_id": ..., "rationale": ...}, ...],
          "round_fail_reason": "proposal-rejected" | None,
        }

    Raises SchemaError if verdicts count != proposals count or if a verdict
    references an unknown proposed_id.
    """
    proposed_ids = {p["id"] for p in proposed_decisions}
    if len(verdicts) != len(proposed_decisions):
        raise SchemaError(
            f"missing verdict: {len(proposed_decisions)} proposals but "
            f"{len(verdicts)} verdicts"
        )
    for v in verdicts:
        if v.proposed_id not in proposed_ids:
            raise SchemaError(
                f"verdict references unknown proposed_id {v.proposed_id!r}"
            )
    verdicts_by_id = {v.proposed_id: v for v in verdicts}
    approved: list[dict] = []
    rejected: list[dict] = []
    for p in proposed_decisions:
        v = verdicts_by_id[p["id"]]
        if v.verdict == "approve":
            approved.append(p)
        else:
            rejected.append({"proposed_id": p["id"], "rationale": v.rationale})
    if rejected:
        return {
            "status": "any-rejected",
            "approved": approved,
            "rejected": rejected,
            "round_fail_reason": "proposal-rejected",
        }
    return {
        "status": "all-approved",
        "approved": approved,
        "rejected": [],
        "round_fail_reason": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_reviewer_gating -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_reviewer_gating.py
git commit -m "feat(claim_graph): reviewer decision_proposals gating for Flow A"
```

---

## Task 12: morning_brief.md render helpers

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append)
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py`:
```python
import unittest

from harness import claim_graph as cg


class RenderPositionCollisionsTableTest(unittest.TestCase):
    def test_empty_collisions_renders_no_collisions_line(self):
        out = cg.render_position_collisions_table([])
        self.assertIn("No position collisions", out)

    def test_single_collision_renders_table_row(self):
        collisions = [{
            "decision_id": "retry-policy",
            "per_variant": [
                {"variant": "v-001", "claim_id": "cl-001",
                 "position": "expo-backoff", "evidence_ids": ["ev-1"]},
                {"variant": "v-002", "claim_id": "cl-002",
                 "position": "linear-no-backoff", "evidence_ids": ["ev-2"]},
            ],
            "confirmed": False,
        }]
        out = cg.render_position_collisions_table(collisions)
        self.assertIn("retry-policy", out)
        self.assertIn("expo-backoff", out)
        self.assertIn("linear-no-backoff", out)
        self.assertIn("v-001", out)
        self.assertIn("v-002", out)
        self.assertIn("ev-1", out)


class RenderDecisionalAsymmetryTableTest(unittest.TestCase):
    def test_empty_renders_no_asymmetry_line(self):
        out = cg.render_decisional_asymmetry_table([])
        self.assertIn("No decisional asymmetry", out)

    def test_single_entry_renders(self):
        entries = [{
            "decision_id": "retry-policy",
            "per_variant": [
                {"variant": "v-001", "status": "decided",
                 "claim_id": "cl-001", "position": "expo-backoff"},
                {"variant": "v-002", "status": "out_of_scope",
                 "claim_id": "cl-002",
                 "out_of_scope_rationale": "separate doc"},
            ],
        }]
        out = cg.render_decisional_asymmetry_table(entries)
        self.assertIn("retry-policy", out)
        self.assertIn("decided", out)
        self.assertIn("out_of_scope", out)
        self.assertIn("separate doc", out)


class RenderPendingRegistryChangesTest(unittest.TestCase):
    def test_empty_returns_empty_string(self):
        out = cg.render_pending_registry_changes(
            cuts_proposed=[], canonicalizations_pending=[],
            decision_id_canonicalizations=[],
        )
        self.assertIn("No pending registry changes", out)

    def test_cuts_section_rendered(self):
        out = cg.render_pending_registry_changes(
            cuts_proposed=[{
                "target_decision_id": "auth-strategy",
                "rationale": "lives in a separate doc",
            }],
            canonicalizations_pending=[],
            decision_id_canonicalizations=[],
        )
        self.assertIn("Cuts proposed", out)
        self.assertIn("auth-strategy", out)
        self.assertIn("lives in a separate doc", out)

    def test_canonicalizations_section_rendered(self):
        out = cg.render_pending_registry_changes(
            cuts_proposed=[],
            canonicalizations_pending=[{
                "scope": "retry-policy", "from": "expo-backoff",
                "to": "exponential-backoff", "confidence": "medium",
                "rationale": "designer judgment call",
            }],
            decision_id_canonicalizations=[],
        )
        self.assertIn("Position canonicalizations", out)
        self.assertIn("expo-backoff", out)
        self.assertIn("medium", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_morning_brief_render -v`
Expected: AttributeError.

- [ ] **Step 3: Append render helpers to claim_graph.py**

Append to `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:
```python


# ----- morning_brief.md render helpers ----------------------------------------


def render_position_collisions_table(collisions: list[dict]) -> str:
    """Render the Position collisions section of morning_brief.md."""
    if not collisions:
        return "## Position collisions\n\nNo position collisions this run.\n"
    lines = ["## Position collisions", ""]
    lines.append("| Decision | Variants × Positions | Evidence cited per variant |")
    lines.append("|---|---|---|")
    for c in collisions:
        variants_positions = "; ".join(
            f"{v['variant']}={v['position']} (cl={v['claim_id']})"
            for v in c["per_variant"]
        )
        evidence = "; ".join(
            f"{v['variant']}: {','.join(v['evidence_ids']) or '(none)'}"
            for v in c["per_variant"]
        )
        lines.append(f"| {c['decision_id']} | {variants_positions} | {evidence} |")
    lines.append("")
    return "\n".join(lines)


def render_decisional_asymmetry_table(entries: list[dict]) -> str:
    """Render the Decisional asymmetry section of morning_brief.md."""
    if not entries:
        return "## Decisional asymmetry\n\nNo decisional asymmetry this run.\n"
    lines = ["## Decisional asymmetry", ""]
    lines.append("| Decision | Variants × Status (position or rationale) |")
    lines.append("|---|---|")
    for e in entries:
        cells = []
        for v in e["per_variant"]:
            if v["status"] == "decided":
                cells.append(f"{v['variant']}=decided ({v['position']})")
            else:
                cells.append(f"{v['variant']}=out_of_scope ({v['out_of_scope_rationale']})")
        lines.append(f"| {e['decision_id']} | {'; '.join(cells)} |")
    lines.append("")
    return "\n".join(lines)


def render_pending_registry_changes(
    cuts_proposed: list[dict],
    canonicalizations_pending: list[dict],
    decision_id_canonicalizations: list[dict],
) -> str:
    """Render the Pending registry changes section of morning_brief.md.

    Returns a string covering three sub-sections:
      - Cuts proposed (Flow B)
      - Position canonicalizations (medium/low) (Flow C medium/low)
      - Decision_id canonicalizations (Flow D)
    """
    if not (cuts_proposed or canonicalizations_pending or
            decision_id_canonicalizations):
        return ("## Pending registry changes\n\n"
                "No pending registry changes; human ritual is empty.\n")
    out = ["## Pending registry changes", ""]
    if cuts_proposed:
        out.extend(["### Cuts proposed", ""])
        out.append("| Decision | Rationale |")
        out.append("|---|---|")
        for c in cuts_proposed:
            out.append(f"| {c['target_decision_id']} | {c['rationale']} |")
        out.append("")
    if canonicalizations_pending:
        out.extend(["### Position canonicalizations (medium/low)", ""])
        out.append("| Scope (decision) | From | To | Confidence | Rationale |")
        out.append("|---|---|---|---|---|")
        for c in canonicalizations_pending:
            out.append(
                f"| {c['scope']} | {c['from']} | {c['to']} | "
                f"{c['confidence']} | {c['rationale']} |"
            )
        out.append("")
    if decision_id_canonicalizations:
        out.extend(["### Decision_id canonicalizations", ""])
        out.append("| From | To | Confidence | Rationale |")
        out.append("|---|---|---|---|")
        for c in decision_id_canonicalizations:
            out.append(
                f"| {c['from']} | {c['to']} | "
                f"{c['confidence']} | {c['rationale']} |"
            )
        out.append("")
    return "\n".join(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_morning_brief_render -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_morning_brief_render.py
git commit -m "feat(claim_graph): morning_brief.md render helpers (collisions, asymmetry, pending)"
```

---

## Task 13: Constitution + goal.toml workspace templates

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/constitution.md`
- Create: `/Users/liwen/develop/projects/auto_design_doc/workspace_template/goal.toml`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_workspace_template.py`

- [ ] **Step 1: Write failing template smoke test**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_workspace_template.py`:
```python
import tomllib
import unittest
from pathlib import Path

from harness import claim_graph as cg


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "workspace_template"


class WorkspaceTemplateGoalTomlTest(unittest.TestCase):
    def test_template_goal_toml_parses(self):
        path = TEMPLATE_DIR / "goal.toml"
        self.assertTrue(path.exists(), f"missing template: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
        self.assertIn("goal", data)
        self.assertIn("goal_version", data["goal"])

    def test_template_goal_toml_decisions_validate(self):
        path = TEMPLATE_DIR / "goal.toml"
        decisions, _version = cg.load_decisions_from_goal_toml(path)
        # Template should have at least one example decision
        self.assertGreaterEqual(len(decisions), 1)


class WorkspaceTemplateConstitutionTest(unittest.TestCase):
    def test_constitution_exists_and_has_required_sections(self):
        path = TEMPLATE_DIR / "constitution.md"
        self.assertTrue(path.exists(), f"missing template: {path}")
        text = path.read_text()
        # Required section headers per design doc §6.1
        self.assertIn("## Judgment rules for all roles", text)
        self.assertIn("## Slug discipline", text)
        self.assertIn("## Reviewer posture", text)
        self.assertIn("## Verifier C posture", text)

    def test_constitution_mentions_new_concepts(self):
        path = TEMPLATE_DIR / "constitution.md"
        text = path.read_text()
        # The mechanism-specific terms the constitution should anchor on
        for keyword in ["decision_id", "position", "proposed_decision",
                        "out_of_scope", "unresolved", "propose_canonicalization",
                        "propose_decision_cut", "registry_size",
                        "decision_proposals"]:
            self.assertIn(keyword, text, f"missing concept anchor: {keyword}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_workspace_template -v`
Expected: FAIL — template files don't exist.

- [ ] **Step 3: Write workspace_template/goal.toml**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/goal.toml`:
```toml
[goal]
title = "Example: API resilience design"
description = "An example goal.toml shipped with `harness init`. Edit before running."
goal_version = "g-01"

# Decisions the doc must address. The orchestrator validates designer claims
# against this list. Designer may propose new decisions via `proposed_decision`
# on a cl-*.json; reviewer gates approval.
#
# Slug rules: kebab-case ASCII, regex ^[a-z][a-z0-9-]*[a-z0-9]$.
# Status values: open | proposed | retired.

[[decision]]
id = "retry-policy"
question = "How should transient failures be retried?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "circuit-breaker-policy"
question = "When does the circuit breaker open, and how does it reset?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "rate-limit-policy"
question = "What rate-limit window/burst should the API enforce, and how?"
status = "open"
introduced_at = "g-01"
```

- [ ] **Step 4: Write workspace_template/constitution.md**

Write `/Users/liwen/develop/projects/auto_design_doc/workspace_template/constitution.md`:
````markdown
# Constitution (v0)

This file is loaded into every CONTEXT.md and re-injected mid-session for long
agent sessions. It defines the judgment rules that govern designer, reviewer,
and Verifier C behavior throughout the harness's autonomous overnight runs.

## Judgment rules for all roles

- Weight evidence by source provenance. Evidence with `confidence: high` and an
  authoritative source dominates `confidence: low` excerpts from informal
  sources.
- Distinguish observation, inference, and decision. Tag every claim accordingly
  via `claim_type`.
- Never invent APIs, file paths, function names, or behaviors that aren't in
  cited evidence.
- Prefer reducing scope to speculating. If you cannot ground a claim, mark
  the section `unresolved` and produce a cl-*.json with `claim_type:
  unresolved`. Do not paper over.
- Surface conflicts. If two pieces of evidence disagree, write both into the
  doc with a `## Conflict` block; do not pick one silently.
- Citation discipline: every assertion of fact in a `decided` section has a
  `[^ev-NNNNNN]` cite. No exceptions. Hooks will reject otherwise.
- Authoring cl-*.json:
  - For every `decided`-tagged section: produce one cl-*.json with a
    `decision_id` from the registered decisions list AND a `position` slug
    capturing this variant's answer.
  - If the section resolves a question not in the registry: set decision_id
    to a new kebab-case slug AND include `proposed_decision: {id, question,
    rationale}`. Reviewer will gate.
  - If the question is out of scope: set `claim_type: out_of_scope` and
    provide `out_of_scope_rationale`. Silence is uninformative; explicit
    out_of_scope is the strongest cross-variant scope signal.
  - Read `derived/decisions.json` before authoring. Match existing
    decision_ids and positions when you mean the same concept; invent new
    slugs only when you genuinely differ.

## Slug discipline (decision_ids and positions)

- Slugs are kebab-case ASCII: `^[a-z][a-z0-9-]*[a-z0-9]$`. Hook rejects
  otherwise.
- Position slugs MUST be substantive — they describe the variant's actual
  answer. `tbd` or `unclear` is a designer failure; use `claim_type:
  unresolved` if you genuinely lack a position yet.
- If a slug already appears in `derived/decisions.json` or in another variant's
  claims under the same decision_id, MATCH IT when you mean the same concept.
  Don't invent variations.
- Slug drift across variants is a designer failure, not a stylistic preference.
  Reviewers will flag it as `propose_canonicalization`. Repeated drift signals
  weak registry hygiene in the designer prompt or in CONTEXT.md construction.

## Reviewer posture

- Default toward rejection when evidence is thin.
- Target a 30-70% accept rate over a run; sustained accept rates outside that
  band mean either the designer is degenerate or the reviewer is rubber-stamping.
- Write at-*.json `at_type: dispute_claim` when you disagree with another
  variant's claim that cites overlapping evidence. State the alternative
  inference clearly.
- Spot slug drift across variants. If you see "expo-backoff" in one variant and
  "exponential-backoff" in another under the same decision_id, write at-*.json
  `at_type: propose_canonicalization`. Mark `confidence: high` ONLY when:
  - (a) you are certain both slugs mean the same concept, AND
  - (b) the canonical slug ("to") is already in the canonical list elsewhere
    in the registry for this decision.

  Otherwise mark medium or low. High-confidence proposals auto-apply
  overnight; medium/low queue for the morning human. Conservative > aggressive.
- Spot off-thesis decisions. If a registered decision shouldn't be in the doc
  (out of scope, redundant, dead-end), write at-*.json `at_type:
  propose_decision_cut`. Always human-gated; queue for review.
- Gate designer-proposed new decisions strictly via `decision_proposals` in
  reviewer.json. Default deny EXCEPT during bootstrap (registry_size < 5),
  where default permissive: approve unless the proposal is clearly off-thesis
  or duplicates an existing registered decision. Approved proposals
  auto-register overnight — say yes only when you mean it.

## Verifier C posture

- Read the doc patch and the cited evidence files only. You do NOT see the
  designer's plan or reasoning.
- For each claim_id in the round, output a verdict (`confirm | weak | dispute`)
  and a one-line rationale.
- Confirm a candidate collision only if you agree both citing claims could be
  drawing on overlapping reasoning to reach incompatible conclusions.
- v0: do NOT attempt to confirm collisions or canonicalizations. That's v0.1.
````

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest tests.test_workspace_template -v`
Expected: All 4 tests pass.

- [ ] **Step 6: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python -m unittest discover tests/ -v`
Expected: All tests from all task files pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add workspace_template/ tests/test_workspace_template.py
git commit -m "feat: workspace_template/goal.toml + constitution.md with claim graph guidance"
```

---

## Spec coverage check

Going through the spec to confirm each requirement maps to a task:

| Spec section | Requirement | Implemented in |
|---|---|---|
| §3.1 goal.toml | `[[decision]]` table with id/question/status/introduced_at | Task 5 (load), Task 6 (register), Task 13 (template) |
| §3.2 cl-*.json | decision_id required | Task 2 (dataclass) |
| §3.2 cl-*.json | position required when not out_of_scope/unresolved | Task 2 (conditional check) |
| §3.2 cl-*.json | out_of_scope_rationale required for out_of_scope | Task 2 |
| §3.2 cl-*.json | proposed_decision optional | Task 2 |
| §3.2 cl-*.json | claim_type adds out_of_scope, unresolved | Task 2 (enum) |
| §3.2 cross-field | decision_id resolves or proposed | Task 3 |
| §3.2 cross-field | position not in vacuous blocklist | Task 3 |
| §3.2 cross-field | position not in alias map | Task 3 |
| §3.3 at-*.json | at_type enum | Task 2 |
| §3.3 at-*.json | dispute_claim payload | Task 2 |
| §3.3 at-*.json | propose_decision_cut payload | Task 2 |
| §3.3 at-*.json | propose_canonicalization payload | Task 2 |
| §3.4 reviewer.json | decision_proposals array | Task 2 (dataclass) + Task 11 (gating) |
| §4.1 Flow A | designer proposes, reviewer gates, auto-register | Task 6 (register) + Task 11 (gating) |
| §4.2 Flow B | reviewer cut → pending_goal_changes.md | Task 2 (at-*.json) + Task 12 (render) |
| §4.2 Flow B | section retag walker | Task 10 |
| §4.3-§4.4 Flow C | canonicalization with confidence gates | Task 7 (auto-apply at high conf), Task 12 (render medium/low) |
| §4.5 Flow D | decision_id canonicalization (human-gated) | Task 12 (render) |
| §4.6 Flow C | single pending_goal_changes.md file | Task 12 (render) |
| §5.1 registry | append-only invariants | Task 4 |
| §5.1 registry | incoming alias rewriting | Task 3 (validator) + Task 7 (apply) |
| §5.2 Detector 1 | position collisions | Task 8 |
| §5.2 Detector 2 | decisional asymmetry | Task 9 |
| §5.2 Detector 3 | stale proposals | Task 9 |
| §5.3 morning_brief | Position collisions section | Task 12 |
| §5.3 morning_brief | Decisional asymmetry section | Task 12 |
| §5.3 morning_brief | Pending registry changes section | Task 12 |
| §6.1 Constitution | new prose | Task 13 |
| §6.2 CONTEXT.md additions | template population | NOT in plan — orchestrator concern |
| §7 Edge A | round-state-machine ordering | NOT in plan — orchestrator concern |
| §7 Edge B | append-only registry | Task 4 |
| §7 Edge C | vacuous slug blocklist | Task 3 |
| §7 Edge D | bootstrap registry_size | NOT in plan — orchestrator concern (passes flag in CONTEXT.md) |
| §11 Q3 | content-based change detection | Task 5 |

**Gaps that are deliberately out-of-scope for this plan (orchestrator concerns):**
- CONTEXT.md template population (§6.2): orchestrator builds CONTEXT.md from `derived/` files this module produces; the rendering belongs in `context_builder.py`, separate plan.
- Round state machine ordering (§7 Edge A): owned by `orchestrator.py`, separate plan.
- Bootstrap registry_size (§7 Edge D): orchestrator counts and passes the flag; the constitution prose written in Task 13 covers the reviewer-facing behavior.
- Pre-commit hook (`workspace/hooks/pre-commit`): separate plan covers the hook duplication of these validators per the inline-duplication-over-import learning.
- Actions: `register-decision`, `canonicalize`, `registry-sync` — these are commit trailer values used by `orchestrator.py` when it calls into this module; not implemented here.

All in-scope requirements have a task.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", "implement later" entries in plan body (only as test cases of the vacuous-slug blocklist, which is intentional).
- All function names used across tasks match definitions:
  - `validate_claim_decision_id_resolution`, `validate_claim_position_not_vacuous`, `validate_claim_position_not_alias` (Task 3)
  - `add_canonical_position`, `register_alias`, `rewrite_position_to_canonical` (Task 4)
  - `load_decisions_from_goal_toml`, `dump_decisions_to_json`, `detect_goal_toml_changes` (Task 5)
  - `register_decision` (Task 6)
  - `apply_canonicalization` (Task 7)
  - `detect_position_collisions` (Task 8)
  - `detect_decisional_asymmetry`, `detect_stale_proposals` (Task 9)
  - `retag_sections_for_retired_decisions` (Task 10)
  - `apply_reviewer_decision_proposals` (Task 11)
  - `render_position_collisions_table`, `render_decisional_asymmetry_table`, `render_pending_registry_changes` (Task 12)
- Dataclass names match across tasks: `Claim`, `Attack`, `Decision`, `CanonicalSlugRegistry`, `DecisionProposalVerdict`.
- Exception classes: `SchemaError`, `RegistryInvariantError` defined in Task 2, used throughout.
- Module-level constants (`CLAIM_TYPES`, `AT_TYPES`, `VACUOUS_POSITION_SLUGS`, etc.) defined in Task 2, referenced in later tasks.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-22-claim-graph-redesign.md`.
