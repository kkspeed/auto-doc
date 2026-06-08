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
from harness import scorecard as scorecard_mod
from harness.spawn import RoleOutput


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _commit_setup(ws: Path, message: str = "test: seed round setup"):
    """Stage + commit any non-ignored setup files a test wrote after scaffold,
    so run_round's assert_clean_worktree guard sees a clean tree. derived/ is
    gitignored and intentionally excluded. Uses --no-verify because some tests
    deliberately seed a section the content hooks would reject (e.g. an
    intentional dangling cite that run_round's verifiers are meant to catch);
    the clean-worktree guard only requires a clean tree, not hook-passing
    content."""
    subprocess.check_call(["git", "-C", str(ws), "add", "-A"])
    if subprocess.run(
            ["git", "-C", str(ws), "diff", "--cached", "--quiet"]
    ).returncode != 0:
        subprocess.check_call(
            ["git", "-c", "user.email=harness@localhost",
             "-c", "user.name=harness", "-C", str(ws),
             "commit", "-q", "--no-verify",
             "-m", f"{message}\n\nAction: init\n"])


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
                 technical_correctness=0.7, groundedness=None,
                 completeness=None, coherence=None):
    parsed = {
        "round": round_id, "variant": variant,
        "decision": decision, "rationale": "looks fine",
        "goal_alignment": goal_alignment,
        "technical_correctness": technical_correctness,
    }
    for key, val in (("groundedness", groundedness),
                     ("completeness", completeness),
                     ("coherence", coherence)):
        if val is not None:
            parsed[key] = val
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

    def test_designer_short_claim_id_is_reassigned_and_round_merges(self):
        # Regression: the designer (an LLM) emits a non-6-digit id 'cl-001'.
        # The orchestrator must reassign it to cl-000001, not fail materialize
        # with "malformed/unsafe claim id".
        designer_output = _designer_ok(claims=[
            {"id": "cl-001", "section_id": "retry-policy",
             "decision_id": "retry-policy",
             "claim_type": "decision",
             "evidence_ids": [],
             "assertion": "Use expo-backoff.",
             "position": "expo-backoff"},
        ])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), designer_output, _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001",
            )
        self.assertEqual(outcome.verdict, "merge", outcome.detail)
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertTrue(cl_path.exists(),
                        "short id should be reassigned to cl-000001")
        body = json.loads(cl_path.read_text())
        self.assertEqual(body["id"], "cl-000001",
                         "on-disk body id must match the reassigned filename")
        # scratch designer.json must carry the reassigned id too
        scratch = json.loads(
            (self.ws / "rounds" / "round-000001" / "scratch"
             / "designer.json").read_text())
        self.assertEqual(scratch["claims"][0]["id"], "cl-000001",
                         "scratch copy must agree with the on-disk id")


class ClaimIdAllocationTest(unittest.TestCase):
    """Unit tests for orchestrator-owned claim-id allocation."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _seed_claim(self, variant_id, seq):
        d = self.ws / "variants" / "nodes" / variant_id / "claims"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"cl-{seq:06d}.json").write_text("{}")

    def test_overwrites_malformed_ids_sequentially_from_one(self):
        parsed = {"claims": [
            {"id": "cl-001"}, {"id": "garbage"}, {"id": "cl-7"},
        ]}
        orchestrator._assign_claim_ids(self.ws, parsed)
        self.assertEqual([c["id"] for c in parsed["claims"]],
                         ["cl-000001", "cl-000002", "cl-000003"])

    def test_continues_past_global_max_across_variants(self):
        # ids are a workspace-wide ledger: allocation continues past the max
        # of ALL variants, not just the one being written.
        self._seed_claim("v-001", 3)
        self._seed_claim("v-002", 7)
        parsed = {"claims": [{"id": "cl-001"}, {"id": "cl-002"}]}
        orchestrator._assign_claim_ids(self.ws, parsed)
        self.assertEqual([c["id"] for c in parsed["claims"]],
                         ["cl-000008", "cl-000009"])

    def test_noop_when_claims_missing_or_not_a_list(self):
        for parsed in ({}, {"claims": None}, {"claims": "nope"}):
            orchestrator._assign_claim_ids(self.ws, parsed)  # must not raise

    def test_max_seq_ignores_non_matching_filenames(self):
        self._seed_claim("v-001", 5)
        d = self.ws / "variants" / "nodes" / "v-001" / "claims"
        (d / "cl-bad.json").write_text("{}")
        (d / "cl-1234567.json").write_text("{}")  # 7 digits, not a match
        self.assertEqual(orchestrator._max_existing_claim_seq(self.ws), 5)


class EvidenceIdAllocationTest(unittest.TestCase):
    """Unit tests for orchestrator-owned evidence-id allocation + remap."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _seed_evidence(self, seq):
        d = self.ws / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"ev-{seq:06d}.md").write_text("+++\n+++\n")

    def test_reassigns_above_global_max_and_remaps_claim_refs(self):
        self._seed_evidence(4)  # existing on disk → next is ev-000005
        parsed = {
            "patch_diff": "",
            "evidence": [{"id": "ev-000001", "confidence": "high",
                          "citations": [], "claim": "c", "excerpt": "e"}],
            "claims": [{"id": "cl-000001", "evidence_ids": ["ev-000001"]}],
        }
        orchestrator._assign_evidence_ids(self.ws, parsed)
        self.assertEqual(parsed["evidence"][0]["id"], "ev-000005")
        self.assertEqual(parsed["claims"][0]["evidence_ids"], ["ev-000005"],
                         "claim ref must follow the reassigned evidence id")

    def test_remaps_doc_citations_in_patch_diff(self):
        parsed = {
            "patch_diff": "+++ b/doc/s.md\n+Body text.[^ev-000001] More.\n",
            "evidence": [{"id": "ev-000001"}],
            "claims": [],
        }
        orchestrator._assign_evidence_ids(self.ws, parsed)
        # max=0 → first id is ev-000001, which equals the proposed id, so the
        # citation is unchanged (no remap needed).
        self.assertEqual(parsed["evidence"][0]["id"], "ev-000001")
        self.assertIn("[^ev-000001]", parsed["patch_diff"])

    def test_remaps_doc_citations_when_collision_forces_new_id(self):
        self._seed_evidence(1)  # ev-000001 exists → proposed ev-000001 collides
        parsed = {
            "patch_diff": "+Body.[^ev-000001] and again [^ev-000001].\n",
            "evidence": [{"id": "ev-000001"}],
            "claims": [{"id": "cl-000001", "evidence_ids": ["ev-000001"]}],
        }
        orchestrator._assign_evidence_ids(self.ws, parsed)
        self.assertEqual(parsed["evidence"][0]["id"], "ev-000002")
        self.assertNotIn("[^ev-000001]", parsed["patch_diff"])
        self.assertEqual(parsed["patch_diff"].count("[^ev-000002]"), 2)
        self.assertEqual(parsed["claims"][0]["evidence_ids"], ["ev-000002"])

    def test_swapped_ids_remap_correctly(self):
        # Designer emits ids out of order; remap keyed by original id, so a swap
        # resolves each ref to the right item. max=0 → assign 1,2 in order.
        parsed = {
            "patch_diff": "+a [^ev-000007] b [^ev-000006]\n",
            "evidence": [{"id": "ev-000007"}, {"id": "ev-000006"}],
            "claims": [
                {"id": "cl-1", "evidence_ids": ["ev-000007"]},
                {"id": "cl-2", "evidence_ids": ["ev-000006"]},
            ],
        }
        orchestrator._assign_evidence_ids(self.ws, parsed)
        # item #1 (orig ev-000007) → ev-000001; item #2 (orig ev-000006) → ev-000002
        self.assertEqual([e["id"] for e in parsed["evidence"]],
                         ["ev-000001", "ev-000002"])
        self.assertEqual(parsed["claims"][0]["evidence_ids"], ["ev-000001"])
        self.assertEqual(parsed["claims"][1]["evidence_ids"], ["ev-000002"])
        self.assertEqual(parsed["patch_diff"], "+a [^ev-000001] b [^ev-000002]\n")

    def test_noop_when_no_evidence(self):
        for parsed in ({}, {"evidence": []}, {"evidence": None}):
            orchestrator._assign_evidence_ids(self.ws, parsed)  # must not raise


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
        _commit_setup(self.ws)
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
        _commit_setup(self.ws)
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
        _commit_setup(self.ws)
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
        _commit_setup(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_high_confidence_canonicalization_triggers_canonicalize_commit_before_merge(self):
        # Reviewer proposes canonicalizing exponential-backoff → expo-backoff
        reviewer = _reviewer_ok(round_id="round-000002", attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "high", "rationale": "both mean the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(round_id="round-000002"),
            _designer_ok(round_id="round-000002", claims=[]),
            reviewer,
            _verifier_c_ok(round_id="round-000002"),
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
        reviewer = _reviewer_ok(round_id="round-000002", attacks=[{
            "id": "at-000001",
            "at_type": "propose_canonicalization",
            "kind": "position", "scope": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
            "confidence": "medium", "rationale": "may be the same",
        }])
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(round_id="round-000002"),
            _designer_ok(round_id="round-000002", claims=[]),
            reviewer,
            _verifier_c_ok(round_id="round-000002"),
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

    def test_optional_judged_dims_pass_when_valid(self):
        d = dict(self.BASE, groundedness=0.6, completeness=0.3, coherence=0.9)
        orchestrator.validate_reviewer_json(d)  # no raise

    def test_optional_judged_dim_out_of_range_raises(self):
        d = dict(self.BASE, completeness=1.5)
        with self.assertRaises(ValueError):
            orchestrator.validate_reviewer_json(d)


class SeedJudgeValidatorTest(unittest.TestCase):
    BASE = {d: 0.5 for d in scorecard_mod.DIMENSIONS}

    def test_all_dimensions_present_passes(self):
        orchestrator.validate_seed_judge_json(dict(self.BASE))  # no raise

    def test_missing_dimension_raises(self):
        d = dict(self.BASE)
        del d["coherence"]
        with self.assertRaises(ValueError):
            orchestrator.validate_seed_judge_json(d)

    def test_out_of_range_raises(self):
        d = dict(self.BASE, groundedness=1.2)
        with self.assertRaises(ValueError):
            orchestrator.validate_seed_judge_json(d)


class PromptReadInstructionTest(unittest.TestCase):
    def test_prompts_compel_reading(self):
        for p in (orchestrator.DESIGNER_PROMPT, orchestrator.REVIEWER_PROMPT,
                  orchestrator.VERIFIER_C_PROMPT):
            self.assertIn("Read these first", p)


class RunRoundSignalGuardTest(unittest.TestCase):
    """The run_round wrapper installs best-effort SIGTERM/SIGINT cleanup so a
    graceful kill mid-round discards in-flight uncommitted ledger files instead
    of orphaning them, then restores the prior handlers."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        _commit_setup(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_installs_during_and_restores_after(self):
        import signal
        orig = {s: signal.getsignal(s)
                for s in (signal.SIGTERM, signal.SIGINT)}
        seen = {}

        def fake_impl(ws, cfg, rid, vid):
            seen[signal.SIGTERM] = signal.getsignal(signal.SIGTERM)
            seen[signal.SIGINT] = signal.getsignal(signal.SIGINT)
            return orchestrator.RoundOutcome(
                round_id=rid, variant_id=vid, verdict="merge")

        with mock.patch("harness.orchestrator._run_round_impl",
                        side_effect=fake_impl):
            orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        # A handler was installed while the round ran...
        self.assertNotEqual(seen[signal.SIGTERM], orig[signal.SIGTERM])
        self.assertNotEqual(seen[signal.SIGINT], orig[signal.SIGINT])
        # ...and the originals were restored afterward.
        self.assertEqual(signal.getsignal(signal.SIGTERM),
                         orig[signal.SIGTERM])
        self.assertEqual(signal.getsignal(signal.SIGINT),
                         orig[signal.SIGINT])

    def test_sigterm_mid_round_discards_in_flight_section(self):
        import signal
        stray = (self.ws / "variants" / "nodes" / "v-001" / "doc"
                 / "99-in-flight.md")

        def fake_impl(ws, cfg, rid, vid):
            # Emulate the vulnerable window: a section is on disk (git-applied)
            # but not yet committed when a graceful kill arrives.
            stray.parent.mkdir(parents=True, exist_ok=True)
            stray.write_text("applied, not yet committed\n")
            signal.raise_signal(signal.SIGTERM)
            return orchestrator.RoundOutcome(
                round_id=rid, variant_id=vid, verdict="merge")

        # Neutralize only the handler's RE-RAISE (orchestrator.os.kill) so the
        # test process survives; signal.raise_signal still delivers for real, so
        # the handler genuinely runs and performs the discard.
        with mock.patch("harness.orchestrator._run_round_impl",
                        side_effect=fake_impl), \
                mock.patch.object(orchestrator.os, "kill") as kill_mock:
            orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertFalse(stray.exists())
        self.assertTrue(kill_mock.called)  # handler attempted to re-raise


class MaterializeFailLoudTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_malformed_evidence_id_raises(self):
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [{"id": "../etc/passwd"}],
                  "claims": []}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_duplicate_claim_id_raises(self):
        c = {"id": "cl-000001", "decision_id": "retry-policy",
             "section_id": "retry-policy", "claim_type": "decision",
             "position": "expo", "evidence_ids": []}
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [],
                  "claims": [dict(c), dict(c)]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_patch_diff_file_written(self):
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [], "claims": []}
        orchestrator._materialize_designer_output(self.ws, "v-001", "round-000001", parsed)
        self.assertTrue(
            (self.ws / "rounds" / "round-000001" / "patch.diff").exists())

    def test_malformed_attack_id_raises(self):
        parsed = {"attacks": [{"id": "../evil"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_reviewer_attacks(
                self.ws, "v-001", parsed)

    def test_patch_diff_empty_content(self):
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [], "claims": []}
        orchestrator._materialize_designer_output(self.ws, "v-001", "round-000001", parsed)
        content = (self.ws / "rounds" / "round-000001"
                   / "patch.diff").read_text()
        self.assertEqual(content, "")

    # --- patch_diff staging must equal what git apply actually writes ---------
    # Regression guard: a section authored via patch_diff that the path-capture
    # misses is neither committed (merge) nor discarded (reject), leaving the
    # worktree dirty so the next round's assert_clean_worktree aborts the run
    # with "workspace has uncommitted changes".

    def _git_status(self):
        return subprocess.run(
            ["git", "-C", str(self.ws), "status", "--porcelain"],
            capture_output=True, text=True).stdout.strip()

    def test_in_scope_new_section_is_captured_for_staging(self):
        _commit_setup(self.ws)
        diff = (
            "diff --git a/variants/nodes/v-001/doc/01-x.md "
            "b/variants/nodes/v-001/doc/01-x.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/variants/nodes/v-001/doc/01-x.md\n"
            "@@ -0,0 +1 @@\n+hi\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": diff, "evidence": [], "claims": []}
        _materialized, section_paths, _c, _a, _e = \
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        self.assertIn("variants/nodes/v-001/doc/01-x.md", section_paths)
        self.assertTrue(
            (self.ws / "variants/nodes/v-001/doc/01-x.md").exists())

    def test_out_of_scope_path_raises_and_leaves_tree_clean(self):
        _commit_setup(self.ws)
        diff = (
            "diff --git a/NOTES.md b/NOTES.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/NOTES.md\n"
            "@@ -0,0 +1 @@\n+oops\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": diff, "evidence": [], "claims": []}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        # numstat is a dry run — nothing should have been written to the tree.
        self.assertEqual(self._git_status(), "")
        self.assertFalse((self.ws / "NOTES.md").exists())

    def test_missing_ab_prefix_resolving_out_of_scope_raises(self):
        _commit_setup(self.ws)
        # No `a/`/`b/` prefixes: git's -p stripping resolves this to
        # "nodes/v-001/doc/01-x.md", outside the variant doc scope.
        diff = (
            "--- /dev/null\n"
            "+++ variants/nodes/v-001/doc/01-x.md\n"
            "@@ -0,0 +1 @@\n+hi\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": diff, "evidence": [], "claims": []}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        self.assertEqual(self._git_status(), "")

    def test_wrong_variant_doc_path_raises(self):
        _commit_setup(self.ws)
        diff = (
            "diff --git a/variants/nodes/v-002/doc/01-x.md "
            "b/variants/nodes/v-002/doc/01-x.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/variants/nodes/v-002/doc/01-x.md\n"
            "@@ -0,0 +1 @@\n+hi\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": diff, "evidence": [], "claims": []}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        self.assertEqual(self._git_status(), "")


class ValidateDesignerStrictTest(unittest.TestCase):
    def test_non_string_patch_diff_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": 123,
                "evidence": [], "claims": []})

    def test_claim_evidence_id_not_in_round_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": "",
                "evidence": [{"id": "ev-000001", "confidence": "high",
                              "citations": [], "claim": "c", "excerpt": "e"}],
                "claims": [{"id": "cl-000001", "decision_id": "d",
                            "section_id": "d", "claim_type": "decision",
                            "position": "p", "evidence_ids": ["ev-999999"]}]})

    def test_duplicate_evidence_id_raises(self):
        ev = {"id": "ev-000001", "confidence": "high", "citations": [],
              "claim": "c", "excerpt": "e"}
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": "",
                "evidence": [dict(ev), dict(ev)], "claims": []})

    def test_claim_without_id_is_accepted(self):
        # The orchestrator assigns claim ids (_assign_claim_ids), so a designer
        # claim that omits 'id' must validate cleanly — requiring a discarded
        # field was the reported failure mode.
        orchestrator.validate_designer_json({
            "round": "r", "variant": "v", "patch_diff": "", "evidence": [],
            "claims": [{"section_id": "retry-policy",
                        "decision_id": "retry-policy",
                        "claim_type": "decision", "position": "expo-backoff",
                        "evidence_ids": [], "assertion": "a"}]})

    def test_aggregates_all_missing_claim_fields(self):
        # All missing required fields are surfaced together (not one at a time)
        # so a single correction pass can fix them.
        with self.assertRaises(ValueError) as cm:
            orchestrator.validate_designer_json({
                "round": "r", "variant": "v", "patch_diff": "", "evidence": [],
                "claims": [{"claim_type": "decision"}]})
        msg = str(cm.exception)
        for field in ("section_id", "decision_id", "evidence_ids", "assertion"):
            self.assertIn(field, msg)


class BadAttackRejectsNotCrashTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        from harness import bootstrap
        bootstrap.rebuild_decisions_cache(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_malformed_attack_id_rejects(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "expo"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(attacks=[{"id": "../evil", "at_type": "dispute_claim"}]),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "reviewer-rejected")


class FreshInitRoundReachesMergeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)  # init bootstraps decisions.json; NO manual seeding

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_round_merges_without_manual_decision_seeding(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "Use expo-backoff.",
                 "position": "expo-backoff"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")


class RegistryMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)  # init bootstraps cache + empty registry

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_authored_position_appended_to_registry(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "Use expo-backoff.",
                 "position": "expo-backoff"}
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim]),
            _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
        reg = json.loads((self.ws / "derived"
                          / "canonical_slug_registry.json").read_text())
        self.assertIn("expo-backoff",
                      reg.get("retry-policy", {}).get("canonical", []))
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B"]).decode()
        self.assertIn("Action: registry-sync", log)

    def test_noop_round_produces_no_registry_sync_commit(self):
        # A designer that authors no decision claim must not create a
        # registry-sync commit (before == after).
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[]),
            _reviewer_ok(), _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
        log = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "--format=%B"]).decode()
        self.assertNotIn("Action: registry-sync", log)


class DesignerRoundVariantGuardTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_wrong_round_is_rejected(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "expo"}
        bad = _designer_ok(claims=[claim])
        bad.parsed["round"] = "round-999999"
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=[_planner_ok(), bad]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertNotEqual(outcome.verdict, "merge")
        self.assertEqual(outcome.reason, "cross-field-fail")

    def test_wrong_variant_is_rejected(self):
        claim = {"id": "cl-000001", "section_id": "retry-policy",
                 "decision_id": "retry-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "expo"}
        bad = _designer_ok(claims=[claim])
        bad.parsed["variant"] = "v-999"
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=[_planner_ok(), bad]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertNotEqual(outcome.verdict, "merge")
        self.assertEqual(outcome.reason, "cross-field-fail")


class MaterializePatchDiffRoundTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_patch_diff_written_under_trusted_round_id(self):
        parsed = {"round": "round-999999", "variant": "v-001",
                  "patch_diff": "", "evidence": [], "claims": []}
        orchestrator._materialize_designer_output(
            self.ws, "v-001", "round-000007", parsed)
        self.assertTrue(
            (self.ws / "rounds" / "round-000007" / "patch.diff").exists())
        self.assertFalse(
            (self.ws / "rounds" / "round-999999" / "patch.diff").exists())


class MaterializeNoOverwriteTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_existing_evidence_id_raises(self):
        ev_dir = self.ws / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "ev-000001.md").write_text("+++\nid = \"ev-000001\"\n+++\n")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "claims": [],
                  "evidence": [{"id": "ev-000001", "confidence": "high",
                                "citations": [], "claim": "c", "excerpt": "e"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_existing_claim_id_raises(self):
        cl_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        cl_dir.mkdir(parents=True, exist_ok=True)
        (cl_dir / "cl-000001.json").write_text("{}")
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [],
                  "claims": [{"id": "cl-000001", "section_id": "retry-policy",
                              "decision_id": "retry-policy",
                              "claim_type": "decision", "position": "expo",
                              "evidence_ids": []}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)

    def test_existing_attack_id_raises(self):
        at_dir = self.ws / "variants" / "nodes" / "v-001" / "attacks"
        at_dir.mkdir(parents=True, exist_ok=True)
        (at_dir / "at-000001.json").write_text("{}")
        parsed = {"attacks": [{"id": "at-000001", "at_type": "dispute_claim"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_reviewer_attacks(
                self.ws, "v-001", parsed)


class MaterializeAtomicTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_designer_midbatch_raise_leaves_no_orphan(self):
        # First claim is fresh; second collides with a pre-existing on-disk id.
        cl_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        cl_dir.mkdir(parents=True, exist_ok=True)
        (cl_dir / "cl-000002.json").write_text("{}")  # pre-existing collision
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "evidence": [],
                  "claims": [
                    {"id": "cl-000001", "section_id": "retry-policy",
                     "decision_id": "retry-policy", "claim_type": "decision",
                     "position": "expo", "evidence_ids": []},
                    {"id": "cl-000002", "section_id": "retry-policy",
                     "decision_id": "retry-policy", "claim_type": "decision",
                     "position": "linear", "evidence_ids": []}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        # The fresh cl-000001 written before the raise must NOT be left behind.
        self.assertFalse((cl_dir / "cl-000001.json").exists(),
                         "orphan cl-000001.json left after mid-batch raise")
        # The pre-existing collision file must be untouched.
        self.assertTrue((cl_dir / "cl-000002.json").exists())

    def test_designer_evidence_midbatch_raise_leaves_no_orphan(self):
        ev_dir = self.ws / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "ev-000002.md").write_text("+++\n+++\n")  # collision
        parsed = {"round": "round-000001", "variant": "v-001",
                  "patch_diff": "", "claims": [],
                  "evidence": [
                    {"id": "ev-000001", "confidence": "high", "citations": [],
                     "claim": "c", "excerpt": "e"},
                    {"id": "ev-000002", "confidence": "high", "citations": [],
                     "claim": "c", "excerpt": "e"}]}
        with self.assertRaises(RuntimeError):
            orchestrator._materialize_designer_output(
                self.ws, "v-001", "round-000001", parsed)
        self.assertFalse((ev_dir / "ev-000001.md").exists(),
                         "orphan ev-000001.md left after mid-batch raise")

    def test_round_with_colliding_claim_leaves_clean_tree(self):
        # End-to-end: a round whose designer batch collides mid-way rejects AND
        # leaves a clean worktree (so the next round's guard would pass).
        cl_dir = self.ws / "variants" / "nodes" / "v-001" / "claims"
        cl_dir.mkdir(parents=True, exist_ok=True)
        # Commit a pre-existing cl-000002 so it's tracked/committed (a prior round).
        (cl_dir / "cl-000002.json").write_text("{}")
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "variants/nodes/v-001/claims/cl-000002.json"])
        subprocess.check_call(["git", "-C", str(self.ws),
                               "-c", "user.email=h@l", "-c", "user.name=h",
                               "commit", "-q", "--no-verify",
                               "-m", "seed\n\nAction: init\n"])
        claims = [
            {"id": "cl-000001", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "position": "expo", "evidence_ids": []},
            {"id": "cl-000002", "section_id": "retry-policy",
             "decision_id": "retry-policy", "claim_type": "decision",
             "position": "linear", "evidence_ids": []}]
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
                _planner_ok(), _designer_ok(claims=claims),
                _reviewer_ok(), _verifier_c_ok()]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertNotEqual(outcome.verdict, "merge")
        st = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st.strip(), "", f"tree dirty after rejection: {st!r}")


if __name__ == "__main__":
    unittest.main()
