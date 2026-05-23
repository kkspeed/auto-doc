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
