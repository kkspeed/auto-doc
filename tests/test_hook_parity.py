"""Parity test: the inlined validators in workspace_template/hooks/pre-commit
must produce identical verdicts to the canonical validators in
harness/claim_graph.py for every fixture.
"""
import unittest
from pathlib import Path

from harness import claim_graph as cg

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "pre-commit"


def _load_hook_namespace():
    """Exec the hook script in a namespace where __name__ != '__main__' so
    the guarded main() does not auto-run. Returns the namespace dict."""
    ns: dict = {"__name__": "pre_commit_hook"}
    exec(HOOK_PATH.read_text(), ns)
    return ns


# Fixtures: each is (label, dict, should_raise) for claim/attack validation.

VALID_CLAIM = {
    "id": "cl-000001",
    "section_id": "retry-policy",
    "decision_id": "retry-policy",
    "claim_type": "decision",
    "evidence_ids": ["ev-000001"],
    "assertion": "Use exponential backoff.",
    "position": "expo-backoff",
}

CLAIM_FIXTURES = [
    ("valid_decision_claim", VALID_CLAIM, False),
    ("missing_id", {**VALID_CLAIM, "id": None} if False else {k: v for k, v in VALID_CLAIM.items() if k != "id"}, True),
    ("bad_claim_type_enum", {**VALID_CLAIM, "claim_type": "speculation"}, True),
    ("missing_position_on_decision_claim",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"}, True),
    ("position_on_out_of_scope",
     {**VALID_CLAIM, "claim_type": "out_of_scope", "position": "x",
      "out_of_scope_rationale": "elsewhere"}, True),
    ("valid_out_of_scope",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"} |
     {"claim_type": "out_of_scope", "out_of_scope_rationale": "elsewhere"}, False),
    ("valid_unresolved",
     {k: v for k, v in VALID_CLAIM.items() if k != "position"} |
     {"claim_type": "unresolved"}, False),
    ("bad_decision_id_slug", {**VALID_CLAIM, "decision_id": "Bad_Slug"}, True),
    ("bad_position_slug", {**VALID_CLAIM, "position": "Bad_Slug"}, True),
    ("evidence_ids_not_list", {**VALID_CLAIM, "evidence_ids": "ev-1"}, True),
]


VALID_DISPUTE = {
    "id": "at-000001", "at_type": "dispute_claim",
    "target_claim_id": "cl-000001",
    "argument": "Evidence does not support this.",
    "evidence_ids": ["ev-000001"],
}

VALID_CUT = {
    "id": "at-000002", "at_type": "propose_decision_cut",
    "target_decision_id": "auth-strategy",
    "rationale": "Lives in another doc.",
}

VALID_CANON = {
    "id": "at-000003", "at_type": "propose_canonicalization",
    "kind": "position", "scope": "retry-policy",
    "from": "exponential-backoff", "to": "expo-backoff",
    "confidence": "high", "rationale": "Both mean the same.",
}

ATTACK_FIXTURES = [
    ("valid_dispute", VALID_DISPUTE, False),
    ("valid_cut", VALID_CUT, False),
    ("valid_canon", VALID_CANON, False),
    ("bad_at_type", {**VALID_DISPUTE, "at_type": "complain"}, True),
    ("cut_missing_rationale",
     {k: v for k, v in VALID_CUT.items() if k != "rationale"}, True),
    ("canon_position_missing_scope",
     {k: v for k, v in VALID_CANON.items() if k != "scope"}, True),
]


def _make_decisions(registered):
    """Build {id: {"status": ...}} dict in the shape derived/decisions.json uses."""
    return {d_id: {"id": d_id, "question": "?", "status": status,
                   "introduced_at": "g-01"}
            for d_id, status in registered}


def _make_registry(data):
    return data  # registry is already the right shape


CROSS_FIELD_FIXTURES = [
    # (label, claim_dict, decisions, registry, should_raise)
    ("decision_id_resolves_to_open",
     VALID_CLAIM, _make_decisions([("retry-policy", "open")]), {}, False),
    ("decision_id_resolves_to_proposed",
     VALID_CLAIM, _make_decisions([("retry-policy", "proposed")]), {}, False),
    ("decision_id_retired_fails",
     VALID_CLAIM, _make_decisions([("retry-policy", "retired")]), {}, True),
    ("decision_id_unregistered_no_proposal_fails",
     VALID_CLAIM, _make_decisions([]), {}, True),
    ("decision_id_unregistered_with_proposal_passes",
     {**VALID_CLAIM, "decision_id": "circuit-breaker",
      "proposed_decision": {"id": "circuit-breaker",
                            "question": "?", "rationale": "x"}},
     _make_decisions([]), {}, False),
    ("vacuous_position_tbd",
     {**VALID_CLAIM, "position": "tbd"},
     _make_decisions([("retry-policy", "open")]), {}, True),
    ("vacuous_position_unclear",
     {**VALID_CLAIM, "position": "unclear"},
     _make_decisions([("retry-policy", "open")]), {}, True),
    ("position_is_alias_key",
     {**VALID_CLAIM, "position": "exponential-backoff"},
     _make_decisions([("retry-policy", "open")]),
     {"retry-policy": {"canonical": ["expo-backoff"],
                       "aliases": {"exponential-backoff": "expo-backoff"}}},
     True),
]


class ClaimValidatorParityTest(unittest.TestCase):
    def test_claim_validator_parity(self):
        ns = _load_hook_namespace()
        hook_validate = ns["validate_claim_dict"]
        for label, fixture, should_raise in CLAIM_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                try:
                    hook_validate(fixture)
                except Exception:
                    hook_raised = True
                try:
                    cg.Claim.from_dict(fixture)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


class AttackValidatorParityTest(unittest.TestCase):
    def test_attack_validator_parity(self):
        ns = _load_hook_namespace()
        hook_validate = ns["validate_attack_dict"]
        for label, fixture, should_raise in ATTACK_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                try:
                    hook_validate(fixture)
                except Exception:
                    hook_raised = True
                try:
                    cg.Attack.from_dict(fixture)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


class CrossFieldValidatorParityTest(unittest.TestCase):
    def test_cross_field_validator_parity(self):
        ns = _load_hook_namespace()
        hook_resolution = ns["validate_claim_decision_id_resolution"]
        hook_vacuous = ns["validate_claim_position_not_vacuous"]
        hook_alias = ns["validate_claim_position_not_alias"]
        for label, claim, decisions, registry, should_raise in CROSS_FIELD_FIXTURES:
            with self.subTest(fixture=label):
                hook_raised = False
                cg_raised = False
                # Hook side: run all three
                try:
                    hook_resolution(claim, decisions)
                    hook_vacuous(claim)
                    hook_alias(claim, registry)
                except Exception:
                    hook_raised = True
                # cg side: run the canonical Claim then the three
                try:
                    cg_claim = cg.Claim.from_dict(claim)
                    cg_decisions_typed = {
                        d_id: cg.Decision.from_dict(d)
                        for d_id, d in decisions.items()
                    }
                    cg_registry = cg.CanonicalSlugRegistry.from_dict(registry)
                    cg.validate_claim_decision_id_resolution(cg_claim, cg_decisions_typed)
                    cg.validate_claim_position_not_vacuous(cg_claim)
                    cg.validate_claim_position_not_alias(cg_claim, cg_registry)
                except Exception:
                    cg_raised = True
                self.assertEqual(hook_raised, cg_raised,
                                 f"{label}: hook raised={hook_raised}, "
                                 f"cg raised={cg_raised}")
                self.assertEqual(hook_raised, should_raise,
                                 f"{label}: expected raise={should_raise}, "
                                 f"got raise={hook_raised}")


if __name__ == "__main__":
    unittest.main()
