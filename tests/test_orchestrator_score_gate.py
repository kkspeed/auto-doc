"""Tests for Phase 6.5 scorecard merge gate in run_round.

Reuses the mock-spawn harness from test_orchestrator_round (helper factories
+ scaffold) and exercises the three gate outcomes: bootstrap merge, improving
merge with Score-Delta, regressing rejection.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator
from harness import scorecard as scorecard_mod
from tests.test_orchestrator_round import (
    _scaffold_workspace,
    _harness_config,
    _planner_ok,
    _designer_ok,
    _reviewer_ok,
    _verifier_c_ok,
)


def _seed_baseline(ws: Path, dims: dict):
    """Write + commit a baseline scorecard.json as Action: init."""
    sc_path = ws / "variants" / "nodes" / "v-001" / "scorecard.json"
    scorecard_mod.write_scorecard(
        sc_path, scorecard_mod.build_scorecard("v-001", "round-000000", dims))
    subprocess.check_call(
        ["git", "-C", str(ws), "add", "-f",
         "variants/nodes/v-001/scorecard.json"])
    subprocess.check_call(
        ["git", "-C", str(ws), "-c", "user.email=h@l", "-c", "user.name=h",
         "commit", "-q", "-m", "seed baseline\n\nAction: init\n"])


def _last_commit_body(ws: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ws), "log", "-1", "--format=%B"], text=True)


def _full_log(ws: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ws), "log", "--format=%B---END---"], text=True)


class ScoreGateTest(unittest.TestCase):
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
                    "id": "retry-policy", "question": "How to retry?",
                    "status": "open", "introduced_at": "g-01",
                },
            },
        }, indent=2))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _claims(self):
        return [{
            "id": "cl-000001", "section_id": "retry-policy",
            "decision_id": "retry-policy", "claim_type": "decision",
            "evidence_ids": [], "assertion": "Use expo-backoff.",
            "position": "expo-backoff",
        }]

    def test_bootstrap_round_merges_and_writes_scorecard(self):
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=self._claims()),
            _reviewer_ok(),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
        sc_path = self.ws / "variants" / "nodes" / "v-001" / "scorecard.json"
        self.assertTrue(sc_path.exists(),
                        "scorecard.json should be written on bootstrap merge")
        body = _last_commit_body(self.ws)
        self.assertIn("Action: merge", body)
        self.assertNotIn("Score-Delta", body)

    def test_improving_round_merges_with_score_delta(self):
        # Baseline deliberately low so the round improves. completeness
        # computes to 0.0 (no doc section for the open decision), so set the
        # baseline completeness to 0.0 to avoid a spurious completeness
        # regression; goal_alignment 0.1 -> 0.8 is the genuine improvement.
        low = {d: 0.1 for d in scorecard_mod.DIMENSIONS}
        low["completeness"] = 0.0
        _seed_baseline(self.ws, low)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=self._claims()),
            _reviewer_ok(goal_alignment=0.8, technical_correctness=0.7),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge")
        body = _last_commit_body(self.ws)
        self.assertIn("Action: merge", body)
        self.assertIn("Score-Delta:", body)

    def test_regressing_round_rejects_with_score_regression(self):
        # Baseline HIGH so a low-scoring round regresses goal_alignment past
        # tolerance with nothing improving.
        high = {d: 0.9 for d in scorecard_mod.DIMENSIONS}
        # completeness computes to 0.0 here too; keep it at 0.0 in baseline so
        # the regression is unambiguously goal_alignment (and not masked by an
        # improvement). Nothing in a clean round exceeds 0.9.
        high["completeness"] = 0.0
        _seed_baseline(self.ws, high)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=self._claims()),
            _reviewer_ok(goal_alignment=0.2, technical_correctness=0.2),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "score-regression")
        body = _last_commit_body(self.ws)
        self.assertIn("Action: score-regression", body)
        # The most recent commit is the rejection, not a merge.
        self.assertNotIn("Action: merge", body)
        # Designer's materialized claim was discarded on rejection.
        cl_path = (self.ws / "variants" / "nodes" / "v-001" / "claims"
                   / "cl-000001.json")
        self.assertFalse(cl_path.exists(),
                         "materialized claim should be discarded on gate fail")


if __name__ == "__main__":
    unittest.main()
