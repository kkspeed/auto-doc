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
            "id": "cl-xx", "section_id": "yy", "decision_id": "zz",
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
