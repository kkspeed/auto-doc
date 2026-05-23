import json
import shutil
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

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

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
