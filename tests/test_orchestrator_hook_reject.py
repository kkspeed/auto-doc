import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator, round_ledger
from harness.spawn import RoleOutput
from tests.test_orchestrator_round import (
    _scaffold_workspace, _harness_config, _planner_ok, _designer_ok,
    _reviewer_ok, _verifier_c_ok,
)

_RETRY_CLAIM = {
    "id": "cl-000001", "section_id": "retry-policy",
    "decision_id": "retry-policy", "claim_type": "decision",
    "evidence_ids": [], "assertion": "Use expo-backoff.",
    "position": "expo-backoff",
}


class HookRejectResetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)  # init bootstraps the decision cache

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_merge_commit_failure_resets_and_records_hook_rejected(self):
        spawns = [_planner_ok(),
                  _designer_ok(claims=[dict(_RETRY_CLAIM)]),
                  _reviewer_ok(), _verifier_c_ok()]
        def boom(*a, **kw):
            raise subprocess.CalledProcessError(
                1, ["git", "commit"], stderr="hook said no")
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=spawns), \
             mock.patch("harness.round_ledger.commit_merge",
                        side_effect=boom):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "hook-rejected")
        msg = subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"]).decode()
        self.assertIn("Action: hook-rejected", msg)
        rj = sorted((self.ws / "rejections").glob("rj-*.md"))[-1].read_text()
        self.assertIn("hook said no", rj)
        st = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st.strip(), "")

    def test_merge_round_leaves_clean_tree_for_next_round(self):
        # Round 1 merges. The merge terminal path logs commit/round_end BEFORE
        # commit_merge, so actions.jsonl is staged into the merge commit and the
        # worktree is left clean. A second real round must therefore pass its
        # start-of-round assert_clean_worktree guard (no DirtyWorktreeError).
        claim1 = dict(_RETRY_CLAIM)
        claim2 = dict(_RETRY_CLAIM, id="cl-000002", position="expo-backoff")
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim1]),
            _reviewer_ok(), _verifier_c_ok()]):
            o1 = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(o1.verdict, "merge")
        st1 = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st1.strip(), "", "tree dirty after merge")
        # Second round runs to a terminal verdict without raising
        # DirtyWorktreeError on the start guard, and also leaves a clean tree.
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[claim2]),
            _reviewer_ok(), _verifier_c_ok()]):
            o2 = orchestrator.run_round(
                self.ws, _harness_config(), "round-000002", "v-001")
        self.assertIn(o2.verdict, {"merge", "score-regression"})
        st2 = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st2.strip(), "", "tree dirty after second round")

    def test_reject_round_then_next_round_clean(self):
        # A planner failure routes through _reject; the next round's clean
        # guard must still pass (actions.jsonl left clean).
        bad_planner = RoleOutput(verdict="spawn-failed", stderr_tail="boom")
        with mock.patch("harness.orchestrator.spawn_role",
                        side_effect=[bad_planner]):
            o1 = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(o1.verdict, "spawn-failed")
        st = subprocess.check_output(
            ["git", "-C", str(self.ws), "status", "--porcelain"]).decode()
        self.assertEqual(st.strip(), "", "tree dirty after _reject")
        # Next round runs without DirtyWorktreeError.
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
            _planner_ok(), _designer_ok(claims=[dict(_RETRY_CLAIM)]),
            _reviewer_ok(), _verifier_c_ok()]):
            o2 = orchestrator.run_round(
                self.ws, _harness_config(), "round-000002", "v-001")
        self.assertEqual(o2.verdict, "merge")


class CommitRejectRebuildsCacheTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_cache_rebuilt_to_match_goal_toml_after_reset(self):
        # Designer proposes a NEW decision (registered in Phase 7a, mutating
        # goal.toml + decisions.json); reviewer approves it; then the merge
        # commit is forced to fail. After _commit_reject's reset, decisions.json
        # must NOT contain the proposed decision (goal.toml rolled back -> cache
        # must too).
        import json as _json
        claim = {"id": "cl-000001", "section_id": "new-policy",
                 "decision_id": "new-policy", "claim_type": "decision",
                 "evidence_ids": [], "assertion": "x", "position": "some-pos",
                 "proposed_decision": {"id": "new-policy",
                                       "question": "New?",
                                       "rationale": "needed"}}
        reviewer = _reviewer_ok(decision_proposals=[
            {"proposed_id": "new-policy", "verdict": "approve",
             "rationale": "ok"}])
        def boom(*a, **kw):
            raise subprocess.CalledProcessError(
                1, ["git", "commit"], stderr="hook said no")
        with mock.patch("harness.orchestrator.spawn_role", side_effect=[
                _planner_ok(), _designer_ok(claims=[claim]),
                reviewer, _verifier_c_ok()]), \
             mock.patch("harness.round_ledger.commit_merge", side_effect=boom):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "hook-rejected")
        cache = _json.loads(
            (self.ws / "derived" / "decisions.json").read_text())
        self.assertNotIn("new-policy", cache["decisions"])
