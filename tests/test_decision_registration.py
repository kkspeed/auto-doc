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
