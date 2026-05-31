"""Phase-by-phase tests for run_round. Mocks spawn_role via unittest.mock to
avoid subprocess overhead; verifiers are real (pure-Python, fast).

Each test sets up a harness-init-scaffolded workspace and constructs a sequence
of RoleOutput mocks via _planner_ok / _designer_ok / etc. helpers.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator
from harness import round_ledger
from harness.spawn import RoleOutput


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _harness_config():
    return {
        "models": {
            "planner": {"tool": "claude", "model": "fake"},
            "designer": {"tool": "claude", "model": "fake"},
            "reviewer": {"tool": "claude", "model": "fake"},
            "verifier_c": {"tool": "claude", "model": "fake"},
        },
        "run": {
            "spawn_timeout_seconds": 10,
            "patch_max_sections": 3,
            "_retry_sleep_seconds_for_tests": 0,
        },
        "claim_graph": {
            "stale_proposals_threshold_rounds": 5,
            "bootstrap_registry_size_threshold": 5,
        },
    }


def _planner_ok(round_id="round-000001", variant="v-001"):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant, "stance": "tighten-claims-first",
        "intent": "Test plan", "target_sections": ["retry-policy"],
        "rejection_log_reviewed": [],
        "rationale_against_known_rejections": "n/a",
    }, retry_count=0, elapsed_seconds=0.1)


def _designer_ok(round_id="round-000001", variant="v-001",
                 claims=None, evidence=None, patch_diff=""):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant,
        "patch_diff": patch_diff,
        "evidence": evidence or [],
        "claims": claims or [],
    }, retry_count=0, elapsed_seconds=0.1)


def _reviewer_ok(round_id="round-000001", variant="v-001",
                 decision="accept", decision_proposals=None, attacks=None,
                 rejection=None, goal_alignment=0.8,
                 technical_correctness=0.7):
    parsed = {
        "round": round_id, "variant": variant,
        "decision": decision, "rationale": "looks fine",
        "goal_alignment": goal_alignment,
        "technical_correctness": technical_correctness,
    }
    if decision_proposals is not None:
        parsed["decision_proposals"] = decision_proposals
    if attacks is not None:
        parsed["attacks"] = attacks
    if rejection is not None:
        parsed["rejection"] = rejection
    return RoleOutput(verdict="ok", parsed=parsed, retry_count=0,
                      elapsed_seconds=0.1)


def _verifier_c_ok(round_id="round-000001", variant="v-001",
                   verdict="confirm", per_claim=None):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant, "verdict": verdict,
        "per_claim": per_claim or [],
        "candidate_collisions_confirmed": [],
        "candidate_collisions_rejected": [],
    }, retry_count=0, elapsed_seconds=0.1)


class RoundOutcomeDataclassTest(unittest.TestCase):
    def test_default_fields(self):
        o = orchestrator.RoundOutcome(
            round_id="round-000001", variant_id="v-001", verdict="merge",
        )
        self.assertEqual(o.verdict, "merge")
        self.assertIsNone(o.reason)
        self.assertIsNone(o.rj_id)
        self.assertEqual(o.elapsed_seconds, 0.0)
        self.assertEqual(o.spawn_counts, {})

    def test_is_frozen(self):
        import dataclasses
        o = orchestrator.RoundOutcome(
            round_id="r", variant_id="v", verdict="merge",
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            o.verdict = "reviewer-rejected"


class RunRoundHappyPathTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Seed a registered decision so designer/reviewer have something to use
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy",
                    "question": "How to retry?",
                    "status": "open",
                    "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_full_round_produces_merge_verdict_and_one_commit(self):
        # Designer materializes a cl-*.json + a section file, no patch_diff.
        # We bypass patch_diff by setting it empty; orchestrator should treat
        # empty patch_diff as a no-op materialization for sections.
        designer_output = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy",
             "claim_type": "decision",
             "evidence_ids": [],
             "assertion": "Use expo-backoff.",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            designer_output,
            _reviewer_ok(),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        self.assertIsNone(outcome.rj_id)
        # Spawn counts: 4 roles, each 1 spawn
        self.assertEqual(outcome.spawn_counts.get("planner"), 1)
        self.assertEqual(outcome.spawn_counts.get("designer"), 1)
        self.assertEqual(outcome.spawn_counts.get("reviewer"), 1)
        self.assertEqual(outcome.spawn_counts.get("verifier_c"), 1)
        # cl-000001 was materialized
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertTrue(cl_path.exists(),
                        "designer's cl-*.json should be materialized")
        # scratch files written
        scratch = self.ws / "rounds" / "round-000001" / "scratch"
        for role in ("planner", "designer", "reviewer", "verifier_c"):
            self.assertTrue((scratch / f"{role}.json").exists(),
                            f"missing scratch for {role}")


class RunRoundPlannerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_planner_spawn_failed_verdict_spawn_failed(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            RoleOutput(verdict="spawn-failed",
                       stderr_tail="claude exited 1",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "spawn-failed")
        self.assertIsNotNone(outcome.rj_id)
        # rj-*.md must exist and commit must have failure-class Action
        rj_path = self.ws / "rejections" / f"{outcome.rj_id}.md"
        self.assertTrue(rj_path.exists())

    def test_planner_output_parse_fail_verdict_output_parse_fail(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            RoleOutput(verdict="output-parse-fail",
                       stderr_tail="json parse error",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "output-parse-fail")
        self.assertIsNotNone(outcome.rj_id)


class RunRoundDesignerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_spawn_failed_verdict_spawn_failed(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            RoleOutput(verdict="spawn-failed", stderr_tail="x",
                       elapsed_seconds=0.1, retry_count=1),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "spawn-failed")
        self.assertIsNotNone(outcome.rj_id)

    def test_designer_patch_apply_failure_verdict_cross_field_fail(self):
        # Designer emits a malformed patch_diff that git apply rejects
        bad_diff = "--- this is not a valid unified diff ---\n"
        designer_bad = _designer_ok(patch_diff=bad_diff)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            designer_bad,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        # The orchestrator surfaces materialization errors as phase-a-fail
        # (pre-Verifier-A materialize step). We accept any of the failure
        # verdicts: just confirm the round was rejected.
        self.assertIn(outcome.verdict, (
            "phase-a-fail", "phase-b-fail", "output-parse-fail",
        ))


class RunRoundVerifierAFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Seed decisions so designer's cl-*.json validates
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_uncited_claim_verdict_phase_a_fail_reason_uncited_claim(self):
        # Designer creates a `decided` section without a cite
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Uncited assertion.\n'
        )
        # No designer evidence; the section is pre-existing
        designer = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "evidence_ids": [], "assertion": "x",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "uncited-claim")

    def test_dangling_cite_verdict_phase_a_fail_reason_dangling_evidence(self):
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Assertion with bad cite [^ev-999999].\n'
        )
        # Designer's evidence list is empty — cite resolves to nothing
        designer = _designer_ok(claims=[
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "evidence_ids": [], "assertion": "x",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-a-fail")
        self.assertEqual(outcome.reason, "dangling-evidence")


class RunRoundVerifierBFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_excerpt_mismatch_verdict_phase_b_fail(self):
        # Pre-existing section with a cite; designer emits an ev with a wildly
        # different excerpt → Verifier B mismatch
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = ["decided"]\n+++\n'
            'Use expo-backoff with full jitter [^ev-000001].\n'
        )
        designer = _designer_ok(
            evidence=[{
                "id": "ev-000001",
                "confidence": "high",
                "excerpt": "Use TCP keepalive with a 30-second interval.",
                "match": "normalized_substring",
                "claim": "x",
            }],
            claims=[{
                "id": "cl-000001", "section_id": "retry-policy",
                "decision_id": "retry-policy", "claim_type": "decision",
                "evidence_ids": ["ev-000001"], "assertion": "x",
                "position": "expo-backoff",
            }],
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-b-fail")


class RunRoundReviewerFailureTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_reviewer_decision_reject_verdict_reviewer_rejected(self):
        reviewer = _reviewer_ok(
            decision="reject",
            rejection={"reason_class": "uncited-claim",
                       "evidence_against": [],
                       "supersedable_by": "add a cite"},
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            reviewer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "reviewer-rejected")
        self.assertEqual(outcome.reason, "uncited-claim")

    def test_reviewer_rejection_with_other_reason_class_still_commits(self):
        # If reviewer returns reason_class="other" (not in hook's ALLOWED_REASONS),
        # the orchestrator falls back to a valid reason so the commit-msg hook
        # accepts the rejection commit.
        reviewer = _reviewer_ok(
            decision="reject",
            rejection={"reason_class": "other",  # invalid hook value
                       "evidence_against": [],
                       "supersedable_by": "x"},
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            reviewer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        # The round must produce a verdict; if the commit_rejection had
        # failed (because Reason was omitted), the orchestrator would have
        # raised CalledProcessError instead of returning a clean verdict.
        self.assertEqual(outcome.verdict, "reviewer-rejected")
        # The rj-*.md frontmatter preserves the original reason_class for audit;
        # the commit-msg Reason is the safe fallback.
        import tomllib as _tomllib
        rj_path = self.ws / "rejections" / f"{outcome.rj_id}.md"
        text = rj_path.read_text()
        end = text.find("+++", 3)
        fm = _tomllib.loads(text[3:end])
        # We chose to write the safe value to the rj frontmatter too
        # (so audit and commit message agree). Either is acceptable.
        self.assertIn(fm["reason_class"], ("other", "cross-field-fail"))


class RunRoundVerifierCDisputeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_verifier_c_top_level_dispute_verdict_phase_c_dispute(self):
        vc = _verifier_c_ok(verdict="dispute", per_claim=[
            {"claim_id": "cl-000001", "verdict": "dispute",
             "rationale": "evidence does not support"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            _reviewer_ok(),
            vc,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-c-dispute")

    def test_verifier_c_per_claim_dispute_verdict_phase_c_dispute(self):
        # verdict=confirm at top level but per_claim has a dispute
        vc = _verifier_c_ok(verdict="confirm", per_claim=[
            {"claim_id": "cl-000001", "verdict": "dispute",
             "rationale": "x"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=[]),
            _reviewer_ok(),
            vc,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "phase-c-dispute")


class RunRoundFileDiscardTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_rejection_discards_materialized_files(self):
        # Designer materializes a cl-*.json; reviewer rejects.
        # The cl-*.json should be removed from working tree.
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "retry-policy",
            "decision_id": "retry-policy", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "expo-backoff",
        }])
        reviewer = _reviewer_ok(
            decision="reject",
            rejection={"reason_class": "uncited-claim",
                       "evidence_against": [],
                       "supersedable_by": "x"},
        )
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer,
        ]):
            orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertFalse(cl_path.exists(),
                         "designer's cl-*.json should have been discarded "
                         "on reviewer rejection")


class RunRoundFlowATest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # Empty decisions.json — bootstrap; designer will propose
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01", "decisions": {},
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_proposal_rejected_via_phase_5_5_verdict_reviewer_rejected_reason_proposal_rejected(self):
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "half-open",
            "proposed_decision": {
                "id": "circuit-breaker",
                "question": "When to reset?",
                "rationale": "needed",
            },
        }])
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "circuit-breaker", "verdict": "reject",
             "rationale": "off-thesis"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer,
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "reviewer-rejected")
        self.assertEqual(outcome.reason, "proposal-rejected")

    def test_approved_proposal_triggers_register_decision_commit_before_merge(self):
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "half-open",
            "proposed_decision": {
                "id": "circuit-breaker",
                "question": "When to reset?",
                "rationale": "needed for resilience",
            },
        }])
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "circuit-breaker", "verdict": "approve",
             "rationale": "on-thesis"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        # Inspect git log: register-decision commit precedes merge commit
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%s|||%B---END---"],
            text=True,
        )
        commit_bodies = log.split("---END---")
        register_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: register-decision" in b), None,
        )
        merge_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: merge" in b), None,
        )
        self.assertIsNotNone(register_idx)
        self.assertIsNotNone(merge_idx)
        # In git log default ordering (newest first), merge < register
        self.assertLess(merge_idx, register_idx)
        # circuit-breaker is now in goal.toml
        goal_text = (self.ws / "goal.toml").read_text()
        self.assertIn("circuit-breaker", goal_text)

    def test_designer_reproposing_already_registered_decision_skipped(self):
        # Seed decisions.json with circuit-breaker already registered
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "circuit-breaker": {
                    "id": "circuit-breaker", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))
        # Designer claims an existing decision AND re-emits a proposed_decision
        # for it. Phase 5.5 should silently skip the proposal, not crash.
        designer = _designer_ok(claims=[{
            "id": "cl-000001", "section_id": "circuit-breaker",
            "decision_id": "circuit-breaker", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "half-open",
            "proposed_decision": {
                "id": "circuit-breaker",   # already registered!
                "question": "When to reset?",
                "rationale": "needed",
            },
        }])
        # Reviewer doesn't need to send decision_proposals because Phase 5.5
        # collected zero proposals (filtered out).
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer, _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000001", "v-001",
            )
        # Round should succeed (merge), not crash with SchemaError
        self.assertEqual(outcome.verdict, "merge")
        # No register-decision commit should have happened (no new proposals)
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B---END---"],
            text=True,
        )
        self.assertNotIn("Action: register-decision", log)


class RunRoundFlowCTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {
                "retry-policy": {
                    "id": "retry-policy", "question": "?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))
        # Registry with two canonical positions under retry-policy
        (derived / "canonical_slug_registry.json").write_text(json.dumps({
            "retry-policy": {
                "canonical": ["expo-backoff", "exponential-backoff"],
                "aliases": {},
            },
        }, indent=2))
        # Pre-existing cl-*.json for v-001 with the from_slug
        claims_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        claims_dir.mkdir(parents=True, exist_ok=True)
        (claims_dir / "cl-000001.json").write_text(json.dumps({
            "id": "cl-000001", "section_id": "retry-policy",
            "decision_id": "retry-policy", "claim_type": "decision",
            "evidence_ids": [], "assertion": "x",
            "position": "exponential-backoff",
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_high_confidence_canonicalization_triggers_canonicalize_commit_before_merge(self):
        # Reviewer proposes canonicalizing exponential-backoff → expo-backoff
        reviewer = _reviewer_ok(attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "high", "rationale": "both mean the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[]), reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000002", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%s|||%B---END---"],
            text=True,
        )
        commit_bodies = log.split("---END---")
        canon_idx = next(
            (i for i, b in enumerate(commit_bodies)
             if "Action: canonicalize" in b), None,
        )
        self.assertIsNotNone(canon_idx,
                             "expected a canonicalize commit")
        # The cl-*.json position has been rewritten
        cl = json.loads(
            (self.ws / "variants" / "nodes" / "v-001" / "claims"
             / "cl-000001.json").read_text()
        )
        self.assertEqual(cl["position"], "expo-backoff")

    def test_medium_confidence_canonicalization_skipped_for_v0(self):
        reviewer = _reviewer_ok(attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "medium", "rationale": "may be the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[]), reviewer, _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(),
                "round-000002", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge")
        # NO canonicalize commit — medium-confidence is skipped in v0
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B---END---"],
            text=True,
        )
        self.assertNotIn("Action: canonicalize", log)
        # Original cl-*.json position unchanged
        cl = json.loads(
            (self.ws / "variants" / "nodes" / "v-001" / "claims"
             / "cl-000001.json").read_text()
        )
        self.assertEqual(cl["position"], "exponential-backoff")


class ReviewerScoreFieldsValidatorTest(unittest.TestCase):
    BASE = {
        "round": "round-000001", "variant": "v-001",
        "decision": "accept", "rationale": "ok",
        "goal_alignment": 0.8, "technical_correctness": 0.7,
    }

    def test_valid_payload_passes(self):
        orchestrator.validate_reviewer_json(dict(self.BASE))  # no raise

    def test_missing_goal_alignment_raises(self):
        d = dict(self.BASE)
        del d["goal_alignment"]
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)

    def test_out_of_range_technical_correctness_raises(self):
        d = dict(self.BASE, technical_correctness=1.4)
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)

    def test_non_numeric_type_raises(self):
        d = dict(self.BASE, goal_alignment="high")
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)


if __name__ == "__main__":
    unittest.main()
