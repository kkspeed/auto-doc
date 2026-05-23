import json
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


SAMPLE_GOAL_TOML = """\
[goal]
title = "Test Design Doc"
description = "Sample for tests."
goal_version = "g-01"

[[decision]]
id = "retry-policy"
question = "How should transient failures be retried?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "auth-strategy"
question = "What auth scheme should the API use?"
status = "open"
introduced_at = "g-01"

[[decision]]
id = "deprecated-thing"
question = "Should we use deprecated-thing?"
status = "retired"
introduced_at = "g-01"
"""


class LoadDecisionsFromGoalTomlTest(unittest.TestCase):
    def test_loads_all_decisions(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(SAMPLE_GOAL_TOML)
            path = Path(f.name)
        try:
            decisions, goal_version = cg.load_decisions_from_goal_toml(path)
            self.assertEqual(goal_version, "g-01")
            self.assertEqual(set(decisions.keys()),
                             {"retry-policy", "auth-strategy", "deprecated-thing"})
            self.assertEqual(decisions["retry-policy"].status, "open")
            self.assertEqual(decisions["deprecated-thing"].status, "retired")
        finally:
            path.unlink()

    def test_empty_decision_table_returns_empty_dict(self):
        toml = '[goal]\ntitle = "t"\ngoal_version = "g-01"\n'
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            path = Path(f.name)
        try:
            decisions, goal_version = cg.load_decisions_from_goal_toml(path)
            self.assertEqual(decisions, {})
            self.assertEqual(goal_version, "g-01")
        finally:
            path.unlink()

    def test_missing_goal_version_raises(self):
        toml = '[goal]\ntitle = "t"\n'
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            path = Path(f.name)
        try:
            with self.assertRaises(cg.SchemaError):
                cg.load_decisions_from_goal_toml(path)
        finally:
            path.unlink()


class DumpDecisionsToJsonTest(unittest.TestCase):
    def test_dump_and_reload_roundtrip(self):
        decisions = {
            "retry-policy": cg.Decision.from_dict({
                "id": "retry-policy",
                "question": "How to retry?",
                "status": "open",
                "introduced_at": "g-01",
            }),
        }
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "decisions.json"
            cg.dump_decisions_to_json(decisions, "g-01", out)
            self.assertTrue(out.exists())
            with out.open() as f:
                loaded = json.load(f)
            self.assertEqual(loaded["goal_version"], "g-01")
            self.assertEqual(loaded["decisions"]["retry-policy"]["status"], "open")


class DetectGoalTomlChangesTest(unittest.TestCase):
    def _setup(self, toml_text, decisions_json_text):
        td = Path(tempfile.mkdtemp())
        goal_path = td / "goal.toml"
        goal_path.write_text(toml_text)
        derived = td / "derived"
        derived.mkdir()
        decisions_path = derived / "decisions.json"
        decisions_path.write_text(decisions_json_text)
        return td, goal_path, decisions_path

    def test_no_changes_returns_unchanged(self):
        decisions_json = json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy",
                    "question": "How should transient failures be retried?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
                "auth-strategy": {
                    "id": "auth-strategy",
                    "question": "What auth scheme should the API use?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
                "deprecated-thing": {
                    "id": "deprecated-thing",
                    "question": "Should we use deprecated-thing?",
                    "status": "retired",
                    "introduced_at": "g-01",
                },
            },
        })
        _, goal_path, decisions_path = self._setup(SAMPLE_GOAL_TOML, decisions_json)
        verdict = cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertEqual(verdict, "unchanged")

    def test_goal_version_bump_returns_versioned_change(self):
        bumped = SAMPLE_GOAL_TOML.replace('goal_version = "g-01"',
                                          'goal_version = "g-02"')
        decisions_json = json.dumps({"goal_version": "g-01", "decisions": {}})
        _, goal_path, decisions_path = self._setup(bumped, decisions_json)
        verdict = cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertEqual(verdict, "versioned-change")

    def test_silent_change_raises(self):
        # goal.toml content differs but goal_version is unchanged
        changed = SAMPLE_GOAL_TOML + """

[[decision]]
id = "circuit-breaker"
question = "When does the breaker reset?"
status = "open"
introduced_at = "g-01"
"""
        decisions_json = json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {"id": "retry-policy",
                                 "question": "How should transient failures be retried?",
                                 "status": "open", "introduced_at": "g-01"},
                "auth-strategy": {"id": "auth-strategy",
                                  "question": "What auth scheme should the API use?",
                                  "status": "open", "introduced_at": "g-01"},
                "deprecated-thing": {"id": "deprecated-thing",
                                     "question": "Should we use deprecated-thing?",
                                     "status": "retired", "introduced_at": "g-01"},
            },
        })
        _, goal_path, decisions_path = self._setup(changed, decisions_json)
        with self.assertRaises(cg.SchemaError) as cm:
            cg.detect_goal_toml_changes(goal_path, decisions_path)
        self.assertIn("goal_version", str(cm.exception))
        self.assertIn("bump", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
