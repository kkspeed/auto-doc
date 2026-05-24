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


class RenderStaleProposalsTableTest(unittest.TestCase):
    def test_empty_renders_no_stale_proposals_line(self):
        out = cg.render_stale_proposals_table([])
        self.assertIn("No stale proposals", out)
        self.assertTrue(out.startswith("## Stale proposals"))

    def test_single_stale_renders_row_with_rounds_since_proposal(self):
        stale = [{
            "decision_id": "circuit-breaker-policy",
            "question": "When does the breaker reset?",
            "rounds_since_proposal": 12,
            "introduced_round": 8,
        }]
        out = cg.render_stale_proposals_table(stale)
        self.assertIn("## Stale proposals", out)
        self.assertIn("circuit-breaker-policy", out)
        self.assertIn("When does the breaker reset?", out)
        self.assertIn("12", out)
        self.assertIn("8", out)

    def test_multiple_stale_render_ordered_by_decision_id(self):
        stale = [
            {"decision_id": "z-policy", "question": "?",
             "rounds_since_proposal": 6, "introduced_round": 1},
            {"decision_id": "a-policy", "question": "?",
             "rounds_since_proposal": 9, "introduced_round": 2},
        ]
        out = cg.render_stale_proposals_table(stale)
        # a-policy must appear before z-policy in the rendered output
        self.assertLess(out.index("a-policy"), out.index("z-policy"))


if __name__ == "__main__":
    unittest.main()
