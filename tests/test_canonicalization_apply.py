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
        # v-002's cl-000002 unchanged (already canonical)
        d2 = json.loads(f2.read_text())
        self.assertEqual(d2["position"], "expo-backoff")
        # Registry: 'exponential-backoff' moved to aliases
        self.assertNotIn("exponential-backoff", registry.data["retry-policy"]["canonical"])
        self.assertEqual(registry.data["retry-policy"]["aliases"]["exponential-backoff"],
                         "expo-backoff")
        # Rewrites list reports each file changed; path is relative to workspace root
        # (variants_nodes_root.parent.parent), which in this test is self.td
        self.assertEqual(len(rewrites), 1)
        self.assertEqual(rewrites[0]["path"], str(f1.relative_to(self.td)))
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
