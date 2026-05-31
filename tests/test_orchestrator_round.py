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
                 rejection=None):
    parsed = {
        "round": round_id, "variant": variant,
        "decision": decision, "rationale": "looks fine",
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


if __name__ == "__main__":
    unittest.main()
