"""Tests for the repo adapter: designer-issued repo queries resolved into
evidence by a read-only repo adapter (Phase 2a)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import orchestrator
from harness.spawn import RoleOutput
from tests.test_orchestrator_round import (
    _scaffold_workspace, _harness_config,
    _planner_ok, _designer_ok, _reviewer_ok, _verifier_c_ok,
)


def _adapter_ok(confidence="high", claim="c", excerpt="verbatim span",
                citations=None):
    return RoleOutput(verdict="ok", parsed={
        "confidence": confidence,
        "citations": citations or [
            {"source": "repo", "ref": "a.py:1-3", "lines": "1-3", "sha": ""}],
        "claim": claim, "excerpt": excerpt,
    }, elapsed_seconds=0.1)


def _designer_query_ok(queries, round_id="round-000001", variant="v-001"):
    return RoleOutput(verdict="ok", parsed={
        "round": round_id, "variant": variant, "repo_queries": queries,
    }, elapsed_seconds=0.1)


class ValidatorTest(unittest.TestCase):
    def test_designer_query_valid(self):
        orchestrator.validate_designer_query_json(
            {"round": "r", "variant": "v",
             "repo_queries": [{"id": "q1", "question": "What retries?"}]})

    def test_designer_query_empty_list_ok(self):
        orchestrator.validate_designer_query_json(
            {"round": "r", "variant": "v", "repo_queries": []})

    def test_designer_query_blank_question_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_designer_query_json(
                {"round": "r", "variant": "v",
                 "repo_queries": [{"id": "q1", "question": "  "}]})

    def test_repo_adapter_valid(self):
        orchestrator.validate_repo_adapter_json(
            {"confidence": "high", "citations": [], "claim": "c",
             "excerpt": "x"})

    def test_repo_adapter_bad_confidence_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_repo_adapter_json(
                {"confidence": "certain", "citations": [], "claim": "c",
                 "excerpt": "x"})

    def test_repo_adapter_empty_excerpt_raises(self):
        with self.assertRaises(ValueError):
            orchestrator.validate_repo_adapter_json(
                {"confidence": "low", "citations": [], "claim": "c",
                 "excerpt": "   "})


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_repo_head_sha_none_for_plain_dir(self):
        (self.ws / "repo").mkdir()
        self.assertIsNone(orchestrator._repo_head_sha(self.ws))

    def test_repo_head_sha_reads_git_head(self):
        repo = self.ws / "repo"
        repo.mkdir()
        subprocess.check_call(["git", "init", "-q"], cwd=repo)
        (repo / "f.txt").write_text("x")
        subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
        subprocess.check_call(
            ["git", "-C", str(repo), "-c", "user.email=a@b",
             "-c", "user.name=a", "commit", "-q", "-m", "init"])
        sha = orchestrator._repo_head_sha(self.ws)
        self.assertIsNotNone(sha)
        self.assertEqual(len(sha), 40)

    def test_question_hash_deterministic(self):
        a = orchestrator._question_hash("  What is X? ")
        b = orchestrator._question_hash("What is X?")
        self.assertEqual(a, b)  # trimmed
        self.assertEqual(len(a), 16)

    def test_materialize_adapter_evidence_writes_next_id(self):
        ev_id, _abs, rel = orchestrator._materialize_adapter_evidence(
            self.ws, "abc123",
            {"confidence": "high",
             "citations": [{"ref": "a.py:1"}],
             "claim": "the answer", "excerpt": "verbatim body"})
        self.assertEqual(ev_id, "ev-000001")
        self.assertEqual(rel, "evidence/ev-000001.md")
        text = (self.ws / "evidence" / "ev-000001.md").read_text()
        self.assertIn("verbatim body", text)
        self.assertIn('source = "repo"', text)
        self.assertIn('repo_sha = "abc123"', text)
        self.assertIn('ref = "a.py:1"', text)


class ResolveRepoQueriesTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        (self.ws / "repo").mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _q(self):
        return [{"id": "q1", "question": "What is the retry policy?"}]

    def test_materializes_evidence_and_returns_mapping(self):
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=_adapter_ok()):
            mat, paths, resolved = orchestrator.resolve_repo_queries(
                self.ws, _harness_config(), "round-000001", "v-001", self._q())
        self.assertEqual(paths, ["evidence/ev-000001.md"])
        self.assertEqual(resolved["q1"], "ev-000001")
        self.assertTrue((self.ws / "evidence" / "ev-000001.md").exists())
        self.assertEqual(len(mat), 1)

    def test_second_call_hits_cache_no_respawn(self):
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=_adapter_ok()):
            orchestrator.resolve_repo_queries(
                self.ws, _harness_config(), "round-000001", "v-001", self._q())
        with mock.patch("harness.orchestrator.spawn_role") as m2:
            mat, paths, resolved = orchestrator.resolve_repo_queries(
                self.ws, _harness_config(), "round-000002", "v-001", self._q())
        m2.assert_not_called()
        self.assertEqual(resolved["q1"], "ev-000001")
        self.assertEqual(paths, [])  # cache hit: nothing newly materialized

    def test_failed_adapter_spawn_skips_gracefully(self):
        with mock.patch("harness.orchestrator.spawn_role",
                        return_value=RoleOutput(verdict="spawn-failed",
                                                stderr_tail="boom")):
            mat, paths, resolved = orchestrator.resolve_repo_queries(
                self.ws, _harness_config(), "round-000001", "v-001", self._q())
        self.assertEqual((mat, paths, resolved), ([], [], {}))
        self.assertFalse((self.ws / "evidence" / "ev-000001.md").exists())

    def test_falls_back_to_designer_model_when_unconfigured(self):
        captured = {}

        def fake(*, role, harness_config, **kw):
            captured["model"] = harness_config["models"][role]
            return _adapter_ok()

        with mock.patch("harness.orchestrator.spawn_role", side_effect=fake):
            orchestrator.resolve_repo_queries(
                self.ws, _harness_config(), "round-000001", "v-001", self._q())
        self.assertEqual(captured["model"],
                         _harness_config()["models"]["designer"])


class RunRoundWithRepoTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        (self.ws / "repo").mkdir()
        derived = self.ws / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "decisions.json").write_text(json.dumps({
            "goal_version": "g-01",
            "decisions": {"retry-policy": {
                "id": "retry-policy", "question": "How to retry?",
                "status": "open", "introduced_at": "g-01"}}}))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_query_pass_resolves_evidence_and_round_merges(self):
        claims = [{"id": "cl-000001", "section_id": "retry-policy",
                   "decision_id": "retry-policy", "claim_type": "decision",
                   "evidence_ids": ["ev-000001"], "assertion": "Backoff.",
                   "position": "expo-backoff"}]
        seq = [
            _planner_ok(),
            _designer_query_ok([{"id": "q1", "question": "retry policy?"}]),
            _adapter_ok(),                       # resolves q1 -> ev-000001
            _designer_ok(claims=claims),         # author cites ev-000001
            _reviewer_ok(),
            _verifier_c_ok(),
        ]
        with mock.patch("harness.orchestrator.spawn_role", side_effect=seq):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge", outcome.detail)
        # Adapter evidence was materialized and committed (worktree clean).
        ev = self.ws / "evidence" / "ev-000001.md"
        self.assertTrue(ev.exists())
        tracked = subprocess.run(
            ["git", "-C", str(self.ws), "ls-files", "evidence/ev-000001.md"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(tracked, "evidence/ev-000001.md")
        status = subprocess.run(
            ["git", "-C", str(self.ws), "status", "--porcelain"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(status, "", f"worktree not clean: {status}")

    def test_designer_query_failure_degrades_not_rejects(self):
        # Query pass fails -> round proceeds without repo evidence, still merges.
        claims = [{"id": "cl-000001", "section_id": "retry-policy",
                   "decision_id": "retry-policy", "claim_type": "decision",
                   "evidence_ids": [], "assertion": "Backoff.",
                   "position": "expo-backoff"}]
        seq = [
            _planner_ok(),
            RoleOutput(verdict="spawn-failed", stderr_tail="x"),  # query pass
            _designer_ok(claims=claims),
            _reviewer_ok(),
            _verifier_c_ok(),
        ]
        with mock.patch("harness.orchestrator.spawn_role", side_effect=seq):
            outcome = orchestrator.run_round(
                self.ws, _harness_config(), "round-000001", "v-001")
        self.assertEqual(outcome.verdict, "merge", outcome.detail)


if __name__ == "__main__":
    unittest.main()
