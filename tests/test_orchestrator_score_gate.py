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
from harness.spawn import RoleOutput
from tests.test_orchestrator_round import (
    _scaffold_workspace,
    _harness_config,
    _planner_ok,
    _designer_ok,
    _reviewer_ok,
    _verifier_c_ok,
)


def _seed_judge_ok(**dims):
    parsed = {d: 0.3 for d in scorecard_mod.DIMENSIONS}
    parsed.update(dims)
    return RoleOutput(verdict="ok", parsed=parsed, elapsed_seconds=0.1)


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
        # Traceability: outcome.detail spells out the per-dimension before->after,
        # the reviewer scores, and points at the preserved designer output.
        self.assertIsNotNone(outcome.detail)
        self.assertIn("goal_alignment: 0.90 -> 0.20", outcome.detail)
        self.assertIn("reviewer scores", outcome.detail)
        self.assertIn("full delta", outcome.detail)
        self.assertIn("rounds/round-000001/scratch/designer.json",
                      outcome.detail)
        self.assertIn("rounds/round-000001/patch.diff", outcome.detail)
        # The preserved (gitignored) artifacts actually survive the rollback.
        self.assertTrue(
            (self.ws / "rounds" / "round-000001" / "scratch"
             / "reviewer.json").exists(),
            "reviewer scratch must survive rejection for post-mortem")
        # The rejection record on disk carries the same enriched detail.
        rj = (self.ws / "rejections" / f"{outcome.rj_id}.md").read_text()
        self.assertIn("goal_alignment: 0.90 -> 0.20", rj)

    def test_reviewer_judged_scores_flow_into_scorecard(self):
        # A mechanically-clean round: the reviewer's continuous groundedness/
        # completeness/coherence judgments must land in scorecard.json instead
        # of snapping to the mechanical 1.0/0.0.
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=self._claims()),
            _reviewer_ok(goal_alignment=0.8, technical_correctness=0.7,
                         groundedness=0.64, completeness=0.0, coherence=0.72),
            _verifier_c_ok(),
        ]):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge", outcome.detail)
        sc = json.loads(
            (self.ws / "variants" / "nodes" / "v-001"
             / "scorecard.json").read_text())
        dims = sc["dimensions"]
        # claim has empty evidence_ids -> mechanically grounded (1.0), so the
        # reviewer's 0.64 caps through; coherence has no cites -> 1.0 cap, 0.72
        # through. completeness is mechanically 0.0 (no doc section), capping
        # the reviewer's 0.0 to 0.0 either way.
        self.assertAlmostEqual(dims["groundedness"], 0.64)
        self.assertAlmostEqual(dims["coherence"], 0.72)

    def test_scorecard_log_records_prior_and_delta(self):
        high = {d: 0.9 for d in scorecard_mod.DIMENSIONS}
        high["completeness"] = 0.0
        _seed_baseline(self.ws, high)
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(),
            _designer_ok(claims=self._claims()),
            _reviewer_ok(goal_alignment=0.2, technical_correctness=0.2),
            _verifier_c_ok(),
        ]):
            orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        actions = [
            json.loads(ln) for ln in
            (self.ws / "actions.jsonl").read_text().splitlines() if ln.strip()
        ]
        sc = next(a for a in actions if a.get("event") == "scorecard")
        self.assertFalse(sc["passed"])
        self.assertIsNotNone(sc["prior_dimensions"],
                             "scorecard log must record prior dims for the delta")
        self.assertEqual(sc["prior_dimensions"]["goal_alignment"], 0.9)
        self.assertEqual(sc["dimensions"]["goal_alignment"], 0.2)
        self.assertIn("goal_alignment", sc["delta"])
        self.assertEqual(sc["tolerance"], 0.05)


class SeedScoringTest(unittest.TestCase):
    """Round-0 seed quality eval (score_seed_docs)."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        self.seeded = ["variants/nodes/v-001/doc/00-overview.md"]

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_writes_baseline_scorecard_from_judge(self):
        judged = dict(groundedness=0.2, goal_alignment=0.35,
                      technical_correctness=0.4, completeness=0.15,
                      coherence=0.5, constitution_compliance=0.6)
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=_seed_judge_ok(**judged)):
            written = orchestrator.score_seed_docs(
                self.ws, _harness_config(), self.seeded)
        self.assertEqual(written, ["variants/nodes/v-001/scorecard.json"])
        sc = json.loads(
            (self.ws / "variants" / "nodes" / "v-001"
             / "scorecard.json").read_text())
        self.assertEqual(sc["round"], "round-000000")
        # No dimension defaults to 1.0 — every value comes from the judge.
        self.assertEqual(sc["dimensions"], judged)
        self.assertTrue(all(v < 1.0 for v in sc["dimensions"].values()))

    def test_becomes_round1_prior_so_round1_is_gated_not_bootstrap(self):
        # A seed baseline must make round 1 a *gated* round: a round that
        # improves nothing over the seed is rejected (not auto-merged as
        # bootstrap would be).
        high = {d: 0.95 for d in scorecard_mod.DIMENSIONS}
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=_seed_judge_ok(**high)):
            orchestrator.score_seed_docs(
                self.ws, _harness_config(), self.seeded)
        prior = scorecard_mod.load_scorecard(
            self.ws / "variants" / "nodes" / "v-001" / "scorecard.json")
        self.assertIsNotNone(prior)
        passed, _ = scorecard_mod.evaluate_gate(
            prior["dimensions"], dict(high), 0.05)
        self.assertFalse(passed, "round matching the seed must not auto-pass")

    def test_falls_back_to_reviewer_model_when_seed_judge_unconfigured(self):
        # _harness_config() has no [models.seed_judge]; score_seed_docs must
        # reuse the reviewer model rather than KeyError out of the run.
        captured = {}

        def fake_spawn(*, role, harness_config, **kw):
            captured["role"] = role
            captured["model"] = harness_config["models"][role]
            return _seed_judge_ok()

        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=fake_spawn):
            written = orchestrator.score_seed_docs(
                self.ws, _harness_config(), self.seeded)
        self.assertEqual(written, ["variants/nodes/v-001/scorecard.json"])
        self.assertEqual(captured["role"], "seed_judge")
        self.assertEqual(captured["model"],
                         _harness_config()["models"]["reviewer"])

    def test_skips_gracefully_on_judge_failure(self):
        # A flaky judge must not block the run: no scorecard, round 1 bootstraps.
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=RoleOutput(verdict="spawn-failed",
                                                stderr_tail="boom")):
            written = orchestrator.score_seed_docs(
                self.ws, _harness_config(), self.seeded)
        self.assertEqual(written, [])
        self.assertFalse(
            (self.ws / "variants" / "nodes" / "v-001"
             / "scorecard.json").exists())


if __name__ == "__main__":
    unittest.main()
