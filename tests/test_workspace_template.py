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


class WorkspaceTemplateHarnessTomlTest(unittest.TestCase):
    def test_harness_toml_exists_and_parses(self):
        path = TEMPLATE_DIR / "harness.toml"
        self.assertTrue(path.exists(), f"missing template: {path}")
        with path.open("rb") as f:
            data = tomllib.load(f)
        # Must have the three pinned sections
        self.assertIn("models", data)
        self.assertIn("run", data)
        self.assertIn("claim_graph", data)

    def test_harness_toml_models_block_has_all_four_roles(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for role in ("planner", "designer", "reviewer", "verifier_c"):
            self.assertIn(role, data["models"],
                          f"models.{role} missing from harness.toml")
            entry = data["models"][role]
            self.assertIn("tool", entry,
                          f"models.{role}.tool missing")
            self.assertIn("model", entry,
                          f"models.{role}.model missing")

    def test_harness_toml_run_block_has_required_keys(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for k in ("max_rounds", "max_wall_clock_hours", "verifier_c_every",
                  "patch_max_sections", "spawn_timeout_seconds"):
            self.assertIn(k, data["run"], f"run.{k} missing")

    def test_harness_toml_claim_graph_block_has_thresholds(self):
        path = TEMPLATE_DIR / "harness.toml"
        with path.open("rb") as f:
            data = tomllib.load(f)
        for k in ("stale_proposals_threshold_rounds",
                  "bootstrap_registry_size_threshold"):
            self.assertIn(k, data["claim_graph"], f"claim_graph.{k} missing")


class WorkspaceTemplateSeedDocTest(unittest.TestCase):
    def test_seed_doc_exists(self):
        path = TEMPLATE_DIR / "seed_doc.md"
        self.assertTrue(path.exists(), f"missing template: {path}")

    def test_seed_doc_documents_three_starting_states(self):
        path = TEMPLATE_DIR / "seed_doc.md"
        text = path.read_text()
        for keyword in ("EMPTY", "STUB", "DRAFTED"):
            self.assertIn(keyword, text,
                          f"seed_doc.md must document the {keyword} starting state")


class WorkspaceTemplateGitignoreTest(unittest.TestCase):
    def test_gitignore_exists(self):
        path = TEMPLATE_DIR / ".gitignore"
        self.assertTrue(path.exists(), f"missing template: {path}")

    def test_gitignore_covers_required_patterns(self):
        path = TEMPLATE_DIR / ".gitignore"
        text = path.read_text()
        for pattern in ("CONTEXT.md", "derived/", "rounds/*/scratch/",
                        "*.tmp", "repo/", "sources/*/cache/"):
            self.assertIn(pattern, text,
                          f".gitignore must include {pattern!r}")


if __name__ == "__main__":
    unittest.main()
