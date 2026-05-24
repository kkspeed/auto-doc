import json
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

    def test_register_within_batch_duplicate_id_raises(self):
        with self.assertRaises(cg.SchemaError) as cm:
            cg.register_decision(self.goal_path, [
                {"id": "new-policy", "question": "?", "rationale": "x"},
                {"id": "new-policy", "question": "?", "rationale": "y"},
            ])
        self.assertIn("new-policy", str(cm.exception))
        # File must NOT have been modified
        loaded, v = cg.load_decisions_from_goal_toml(self.goal_path)
        self.assertEqual(v, "g-01")
        self.assertNotIn("new-policy", loaded)

    def test_register_empty_list_raises(self):
        with self.assertRaises(cg.SchemaError) as cm:
            cg.register_decision(self.goal_path, [])
        self.assertIn("empty", str(cm.exception).lower())
        # File unchanged
        _, v = cg.load_decisions_from_goal_toml(self.goal_path)
        self.assertEqual(v, "g-01")

    def test_register_duplicate_id_does_not_modify_file(self):
        # Atomicity: failed call leaves file unchanged
        original = self.goal_path.read_text()
        with self.assertRaises(cg.SchemaError):
            cg.register_decision(self.goal_path, [
                {"id": "retry-policy", "question": "?", "rationale": "x"},
            ])
        self.assertEqual(self.goal_path.read_text(), original)


class RegisterDecisionAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.goal_path = self.td / "goal.toml"
        self.goal_path.write_text(SEED_GOAL_TOML)

    def test_path_argument_omitted_preserves_existing_behavior(self):
        # No decisions_json_path → behaves exactly like before: no derived file written
        derived_dir = self.td / "derived"
        new_version = cg.register_decision(self.goal_path, [
            {"id": "circuit-breaker-policy",
             "question": "When does the breaker reset?",
             "rationale": "x"},
        ])
        self.assertEqual(new_version, "g-02")
        self.assertFalse(derived_dir.exists(),
                         "derived/ must not be created when path omitted")

    def test_path_argument_provided_writes_decisions_json(self):
        decisions_json = self.td / "derived" / "decisions.json"
        new_version = cg.register_decision(
            self.goal_path,
            [{"id": "circuit-breaker-policy",
              "question": "When does the breaker reset?",
              "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        self.assertEqual(new_version, "g-02")
        self.assertTrue(decisions_json.exists())
        with decisions_json.open() as f:
            payload = json.load(f)
        self.assertEqual(payload["goal_version"], "g-02")
        self.assertIn("circuit-breaker-policy", payload["decisions"])
        self.assertEqual(payload["decisions"]["circuit-breaker-policy"]["status"],
                         "open")

    def test_decisions_json_matches_freshly_loaded_goal_toml(self):
        # decisions.json content equals what load_decisions_from_goal_toml produces
        decisions_json = self.td / "derived" / "decisions.json"
        cg.register_decision(
            self.goal_path,
            [{"id": "a-policy", "question": "?", "rationale": "x"},
             {"id": "b-policy", "question": "?", "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        loaded_from_toml, version_from_toml = cg.load_decisions_from_goal_toml(self.goal_path)
        with decisions_json.open() as f:
            from_json = json.load(f)
        self.assertEqual(from_json["goal_version"], version_from_toml)
        self.assertEqual(
            set(from_json["decisions"].keys()),
            set(loaded_from_toml.keys()),
        )
        for d_id, d in loaded_from_toml.items():
            self.assertEqual(from_json["decisions"][d_id], d.to_dict())

    def test_decisions_json_path_parent_created_if_missing(self):
        # decisions_json_path lives under a non-existent directory
        decisions_json = self.td / "deep" / "nested" / "decisions.json"
        self.assertFalse(decisions_json.parent.exists())
        cg.register_decision(
            self.goal_path,
            [{"id": "x-policy", "question": "?", "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        self.assertTrue(decisions_json.exists())


if __name__ == "__main__":
    unittest.main()
