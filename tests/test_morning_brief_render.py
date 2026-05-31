import shutil
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg
from harness import morning_brief


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


class RenderCanonicalizationsAppliedTest(unittest.TestCase):
    def test_empty_both_lists_renders_no_canonicalizations_line(self):
        out = cg.render_canonicalizations_applied([], [])
        self.assertIn("No canonicalizations applied", out)
        self.assertTrue(out.startswith("## Canonicalizations applied"))

    def test_position_rewrites_only_renders_position_subtable(self):
        position_rewrites = [{
            "path": "workspace/variants/nodes/v-001/claims/cl-000001.json",
            "claim_id": "cl-000001",
            "decision_id": "retry-policy",
            "from": "exponential-backoff",
            "to": "expo-backoff",
        }]
        out = cg.render_canonicalizations_applied(position_rewrites, [])
        self.assertIn("Position canonicalizations", out)
        self.assertIn("retry-policy", out)
        self.assertIn("exponential-backoff", out)
        self.assertIn("expo-backoff", out)
        self.assertIn("cl-000001", out)
        # Decision_id sub-table absent
        self.assertNotIn("Decision_id canonicalizations", out)

    def test_decision_id_rewrites_only_renders_decision_id_subtable(self):
        decision_id_rewrites = [{
            "from": "auth-policy",
            "to": "authentication-policy",
            "kind": "claim",
            "paths": [
                "workspace/variants/nodes/v-001/claims/cl-000002.json",
                "workspace/variants/nodes/v-002/claims/cl-000003.json",
            ],
        }]
        out = cg.render_canonicalizations_applied([], decision_id_rewrites)
        self.assertIn("Decision_id canonicalizations", out)
        self.assertIn("auth-policy", out)
        self.assertIn("authentication-policy", out)
        self.assertIn("cl-000002", out)
        self.assertIn("claim", out)
        # Position sub-table absent
        self.assertNotIn("Position canonicalizations", out)

    def test_both_kinds_render_both_subtables(self):
        position_rewrites = [{
            "path": "workspace/variants/nodes/v-001/claims/cl-000001.json",
            "claim_id": "cl-000001", "decision_id": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
        }]
        decision_id_rewrites = [{
            "from": "auth-policy", "to": "authentication-policy",
            "kind": "section",
            "paths": ["workspace/variants/nodes/v-001/doc/01-auth-policy.md"],
        }]
        out = cg.render_canonicalizations_applied(position_rewrites,
                                                  decision_id_rewrites)
        self.assertIn("Position canonicalizations", out)
        self.assertIn("Decision_id canonicalizations", out)
        self.assertIn("retry-policy", out)
        self.assertIn("auth-policy", out)
        self.assertIn("section", out)
        self.assertLess(out.index("Position canonicalizations"),
                        out.index("Decision_id canonicalizations"))

    def test_decision_id_rewrite_with_empty_paths_renders_none_cell(self):
        out = cg.render_canonicalizations_applied([], [{
            "from": "auth-policy", "to": "authentication-policy",
            "kind": "attack", "paths": [],
        }])
        self.assertIn("(none)", out)


class RenderMorningBriefTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.ws.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_empty_workspace_renders_all_section_headers(self):
        out = morning_brief.render_morning_brief(self.ws, since_sha=None)
        self.assertIn("# Morning brief", out)
        self.assertIn("## Position collisions", out)
        self.assertIn("## Decisional asymmetry", out)
        self.assertIn("## Pending registry changes", out)
        self.assertIn("## Canonicalizations applied", out)
        self.assertIn("## Stale proposals", out)
        self.assertIn("## Score trajectory", out)
        self.assertIn("## Still weak", out)
        self.assertIn("## Rejected this run", out)
        self.assertIn("## What I'd ask you to look at first", out)

    def test_sections_in_spec_order(self):
        out = morning_brief.render_morning_brief(self.ws, since_sha=None)
        order = ["## Position collisions", "## Decisional asymmetry",
                 "## Pending registry changes", "## Canonicalizations applied",
                 "## Stale proposals", "## Score trajectory", "## Still weak",
                 "## Rejected this run", "## What I'd ask you to look at first"]
        positions = [out.index(h) for h in order]
        self.assertEqual(positions, sorted(positions))


class StillWeakSectionTest(unittest.TestCase):
    def test_weak_verdicts_rendered(self):
        out = morning_brief.render_still_weak([
            {"claim_id": "cl-000001", "rationale": "thin evidence"},
        ])
        self.assertIn("## Still weak", out)
        self.assertIn("cl-000001", out)
        self.assertIn("thin evidence", out)

    def test_empty_still_weak_friendly_state(self):
        out = morning_brief.render_still_weak([])
        self.assertIn("No claims flagged weak", out)


if __name__ == "__main__":
    unittest.main()
