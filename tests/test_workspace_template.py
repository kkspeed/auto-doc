import tomllib
import unittest
from pathlib import Path

from harness import claim_graph as cg


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "workspace_template"


class WorkspaceTemplateGoalTomlTest(unittest.TestCase):
    def test_template_goal_toml_parses(self):
        path = TEMPLATE_DIR / "goal.toml"
        self.assertTrue(path.exists(), f"missing template: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
        self.assertIn("goal", data)
        self.assertIn("goal_version", data["goal"])

    def test_template_goal_toml_decisions_validate(self):
        path = TEMPLATE_DIR / "goal.toml"
        decisions, _version = cg.load_decisions_from_goal_toml(path)
        # Template should have at least one example decision
        self.assertGreaterEqual(len(decisions), 1)


class WorkspaceTemplateConstitutionTest(unittest.TestCase):
    def test_constitution_exists_and_has_required_sections(self):
        path = TEMPLATE_DIR / "constitution.md"
        self.assertTrue(path.exists(), f"missing template: {path}")
        text = path.read_text()
        # Required section headers per design doc §6.1
        self.assertIn("## Judgment rules for all roles", text)
        self.assertIn("## Slug discipline", text)
        self.assertIn("## Reviewer posture", text)
        self.assertIn("## Verifier C posture", text)

    def test_constitution_mentions_new_concepts(self):
        path = TEMPLATE_DIR / "constitution.md"
        text = path.read_text()
        # The mechanism-specific terms the constitution should anchor on
        for keyword in ["decision_id", "position", "proposed_decision",
                        "out_of_scope", "unresolved", "propose_canonicalization",
                        "propose_decision_cut", "registry_size",
                        "decision_proposals"]:
            self.assertIn(keyword, text, f"missing concept anchor: {keyword}")


if __name__ == "__main__":
    unittest.main()
