import json
import shutil
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

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

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
