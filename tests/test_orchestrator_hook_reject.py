import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator, round_ledger
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
