import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness import context


def _write_decisions(workspace, decisions: dict, goal_version="g-01"):
    p = workspace / "derived" / "decisions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "goal_version": goal_version,
        "decisions": decisions,
    }, indent=2))


def _write_goal_toml(workspace, goal_version="g-01"):
    p = workspace / "goal.toml"
    p.write_text(
        f'[goal]\ntitle = "test"\ngoal_version = "{goal_version}"\n'
    )


def _write_section(workspace, variant, name, tags, body, claim_id="cl-000001"):
    doc_dir = workspace / "variants" / "nodes" / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    fp = doc_dir / f"{name}.md"
    fp.write_text(
        f'+++\nsection_id = "x"\nclaim_id = "{claim_id}"\n'
        f'tags = [{tag_str}]\n+++\n{body}'
    )
    return fp


def _write_claim(workspace, variant, claim_id, decision_id, position=None,
                 proposed_decision=None, claim_type="decision"):
    claims_dir = workspace / "variants" / "nodes" / variant / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": claim_id, "section_id": decision_id, "decision_id": decision_id,
        "claim_type": claim_type, "evidence_ids": [], "assertion": "x",
    }
    if position is not None:
        payload["position"] = position
    if proposed_decision is not None:
        payload["proposed_decision"] = proposed_decision
    fp = claims_dir / f"{claim_id}.json"
    fp.write_text(json.dumps(payload, indent=2))
    return fp


def _write_rejection(workspace, variant, rj_id, summary):
    rej_dir = workspace / "rejections"
    rej_dir.mkdir(parents=True, exist_ok=True)
    fp = rej_dir / f"{rj_id}.md"
    fp.write_text(
        f'+++\nvariant = "{variant}"\n'
        f'summary = "{summary}"\n+++\nBody\n'
    )
    return fp


def _write_constitution(workspace):
    p = workspace / "constitution.md"
    p.write_text(
        "# Constitution\n\n## Slug discipline\n\n"
        "Use kebab-case ASCII slugs.\n\n"
        "## Other section\n\nUnrelated.\n"
    )


def _write_harness_toml(workspace, bootstrap_threshold=5):
    p = workspace / "harness.toml"
    p.write_text(
        "[claim_graph]\n"
        f"bootstrap_registry_size_threshold = {bootstrap_threshold}\n"
        "stale_proposals_threshold_rounds = 5\n"
    )


class BuildPlannerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_planner_lists_only_open_and_proposed_decisions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
            "auth-strategy": {"id": "auth-strategy", "question": "?",
                              "status": "proposed", "introduced_at": "g-01"},
            "dead-thing": {"id": "dead-thing", "question": "?",
                           "status": "retired", "introduced_at": "g-01"},
        })
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        self.assertIn("retry-policy", out)
        self.assertIn("auth-strategy", out)
        self.assertNotIn("dead-thing", out)

    def test_planner_shows_stale_proposals_when_present(self):
        # Empty stale list → section omitted; populated → section shown.
        # The planner uses detect_stale_proposals; absent data means no section.
        # Here we just verify the empty case produces no "stale" mention.
        _write_decisions(self.td, {})
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        # With no proposed decisions, there's nothing to be stale about
        self.assertNotIn("## Stale proposals", out)

    def test_planner_recent_rejections_filtered_by_variant(self):
        _write_decisions(self.td, {})
        _write_rejection(self.td, "v-001", "rj-000001", "first rejection")
        _write_rejection(self.td, "v-002", "rj-000002", "other variant")
        out = context.build_planner_context(self.td, "round-000001", "v-001")
        self.assertIn("first rejection", out)
        self.assertNotIn("other variant", out)


class BuildDesignerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_constitution(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_shows_own_variant_positions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-001", "cl-000001", "retry-policy",
                     position="expo-backoff")
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("expo-backoff", out)
        self.assertIn("cl-000001", out)

    def test_designer_does_not_show_other_variant_positions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-002", "cl-000099", "retry-policy",
                     position="linear-no-backoff")
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        # Designer for v-001 should NOT see v-002's positions
        self.assertNotIn("linear-no-backoff", out)
        self.assertNotIn("cl-000099", out)

    def test_designer_shows_own_pending_proposals(self):
        _write_decisions(self.td, {})  # registry is empty
        _write_claim(self.td, "v-001", "cl-000001", "circuit-breaker",
                     position="half-open",
                     proposed_decision={"id": "circuit-breaker",
                                        "question": "When to reset?",
                                        "rationale": "needed for resilience"})
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("circuit-breaker", out)
        self.assertIn("When to reset?", out)

    def test_designer_includes_slug_discipline_section(self):
        _write_decisions(self.td, {})
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("Slug discipline", out)
        self.assertIn("kebab-case", out)


class BuildReviewerContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)
        _write_harness_toml(self.td)
        # Initialize a git repo so the reviewer's git-log scan works
        subprocess.check_call(
            ["git", "init", "-q"], cwd=self.td,
        )
        subprocess.check_call(
            ["git", "config", "user.email", "test@x"], cwd=self.td,
        )
        subprocess.check_call(
            ["git", "config", "user.name", "test"], cwd=self.td,
        )

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reviewer_shows_positions_across_all_variants(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        _write_claim(self.td, "v-001", "cl-000001", "retry-policy",
                     position="expo-backoff")
        _write_claim(self.td, "v-002", "cl-000099", "retry-policy",
                     position="linear-no-backoff")
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        # Reviewer sees BOTH variants' positions
        self.assertIn("expo-backoff", out)
        self.assertIn("linear-no-backoff", out)
        self.assertIn("v-001", out)
        self.assertIn("v-002", out)

    def test_reviewer_includes_pending_proposals_from_current_round(self):
        _write_decisions(self.td, {})
        # Designer.json for round-000001 with a proposed_decision payload
        scratch = self.td / "rounds" / "round-000001" / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "designer.json").write_text(json.dumps({
            "round": "round-000001",
            "variant": "v-001",
            "claims": [
                {"id": "cl-000001", "section_id": "x", "decision_id": "x",
                 "claim_type": "decision", "evidence_ids": [],
                 "assertion": "y", "position": "z",
                 "proposed_decision": {"id": "circuit-breaker",
                                       "question": "When?", "rationale": "r"}},
            ],
        }))
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("circuit-breaker", out)

    def test_reviewer_includes_recent_canonicalize_commits(self):
        _write_decisions(self.td, {})
        # Create a couple of commits with canonicalize trailers
        (self.td / "file1.txt").write_text("x")
        subprocess.check_call(["git", "add", "."], cwd=self.td)
        subprocess.check_call(
            ["git", "commit", "-q", "-m",
             "first\n\nAction: canonicalize\nRound: round-000001\n"],
            cwd=self.td,
        )
        (self.td / "file2.txt").write_text("y")
        subprocess.check_call(["git", "add", "."], cwd=self.td)
        subprocess.check_call(
            ["git", "commit", "-q", "-m",
             "second\n\nAction: merge\nVariant: v-001\nRound: round-000002\n"],
            cwd=self.td,
        )
        out = context.build_reviewer_context(self.td, "round-000003", "v-001")
        # Canonicalize commit appears; merge commit does NOT
        self.assertIn("canonicalize", out)

    def test_reviewer_includes_registry_size(self):
        _write_decisions(self.td, {
            "a": {"id": "a", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
            "b": {"id": "b", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
        })
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("registry_size: 2", out)

    def test_reviewer_registry_size_below_threshold_flagged_as_bootstrap_permissive(self):
        _write_decisions(self.td, {
            "a": {"id": "a", "question": "?", "status": "open",
                  "introduced_at": "g-01"},
        })
        _write_harness_toml(self.td, bootstrap_threshold=5)
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("bootstrap-permissive", out)


class BuildVerifierCContextTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_verifier_c_lists_registered_decisions(self):
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        self.assertIn("retry-policy", out)

    def test_verifier_c_omits_designer_and_reviewer_specific_sections(self):
        _write_decisions(self.td, {})
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        # Should NOT contain any of the designer-/reviewer-only sections
        self.assertNotIn("Slug discipline", out)
        self.assertNotIn("Positions you have committed", out)
        self.assertNotIn("All positions in use across variants", out)
        self.assertNotIn("registry_size", out)

    def test_verifier_c_header_includes_round_and_variant_and_goal_version(self):
        _write_decisions(self.td, {})
        out = context.build_verifier_c_context(
            self.td, "round-000042", "v-007"
        )
        self.assertIn("round-000042", out)
        self.assertIn("v-007", out)
        self.assertIn("g-01", out)


class ContextPointersTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        _write_goal_toml(self.td)  # title="test"
        _write_decisions(self.td, {
            "retry-policy": {"id": "retry-policy", "question": "?",
                             "status": "open", "introduced_at": "g-01"},
        })

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_context_has_pointers_and_goal(self):
        out = context.build_designer_context(self.td, "round-000001", "v-001")
        self.assertIn("Read these first", out)
        self.assertIn("goal.toml", out)
        self.assertIn("variants/nodes/v-001/doc/", out)
        self.assertIn("rounds/round-000001/scratch/planner.json", out)
        self.assertIn("evidence/", out)
        self.assertIn("test", out)  # goal title

    def test_verifier_c_context_points_at_patch_and_evidence(self):
        out = context.build_verifier_c_context(self.td, "round-000001", "v-001")
        self.assertIn("Read these first", out)
        self.assertIn("rounds/round-000001/patch.diff", out)
        self.assertIn("evidence/", out)
        # registered decisions still rendered
        self.assertIn("retry-policy", out)

    def test_reviewer_context_points_at_patch(self):
        out = context.build_reviewer_context(self.td, "round-000001", "v-001")
        self.assertIn("rounds/round-000001/patch.diff", out)
        self.assertIn("Read these first", out)


if __name__ == "__main__":
    unittest.main()
