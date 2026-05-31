import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from harness import round_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent


def _scaffold_workspace(target: Path):
    """Run `python -m harness init` to scaffold a workspace at target."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class WriteRoleScratchTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_writes_json_under_scratch_dir(self):
        path = round_ledger.write_role_scratch(
            self.td, "round-000001", "planner", {"ok": True},
        )
        self.assertEqual(path,
            self.td / "rounds" / "round-000001" / "scratch" / "planner.json")
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text()), {"ok": True})

    def test_creates_parent_dirs(self):
        # Workspace doesn't have rounds/ yet
        round_ledger.write_role_scratch(
            self.td, "round-000007", "designer", {"claims": []},
        )
        self.assertTrue(
            (self.td / "rounds" / "round-000007" / "scratch").is_dir()
        )


class WriteRejectionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_allocates_next_rj_id(self):
        # No existing rejections → first is rj-000001
        rj1 = round_ledger.write_rejection(
            self.td, "round-000001", "v-001",
            reason_class="uncited-claim",
            failed_phase="verifier_a",
            detail="missing cite in 01-retry-policy.md",
        )
        self.assertEqual(rj1, "rj-000001")
        # Next allocation should bump
        rj2 = round_ledger.write_rejection(
            self.td, "round-000002", "v-002",
            reason_class="spawn-failed",
            failed_phase="planner",
            detail="claude CLI exited 1",
        )
        self.assertEqual(rj2, "rj-000002")

    def test_frontmatter_includes_required_fields(self):
        rj = round_ledger.write_rejection(
            self.td, "round-000003", "v-001",
            reason_class="uncited-claim",
            failed_phase="verifier_a",
            detail="Some details here.",
        )
        fp = self.td / "rejections" / f"{rj}.md"
        text = fp.read_text()
        end = text.find("+++", 3)
        fm = tomllib.loads(text[3:end])
        self.assertEqual(fm["variant"], "v-001")
        self.assertEqual(fm["round_id"], "round-000003")
        self.assertEqual(fm["reason_class"], "uncited-claim")
        self.assertEqual(fm["failed_phase"], "verifier_a")
        self.assertNotIn("reviewer_id", fm)
        # Body should contain the detail
        body = text[end + 3:].strip()
        self.assertIn("Some details here.", body)

    def test_reviewer_id_included_when_present(self):
        rj = round_ledger.write_rejection(
            self.td, "round-000001", "v-001",
            reason_class="other",
            failed_phase="reviewer",
            detail="x",
            reviewer_id="v-002",
        )
        text = (self.td / "rejections" / f"{rj}.md").read_text()
        end = text.find("+++", 3)
        fm = tomllib.loads(text[3:end])
        self.assertEqual(fm["reviewer_id"], "v-002")


class AppendActionsLogTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_append_writes_newline_terminated_json(self):
        round_ledger.append_actions_log(self.td, {"event": "round_start"})
        path = self.td / "actions.jsonl"
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertTrue(content.endswith("\n"))
        self.assertEqual(json.loads(content.strip()), {"event": "round_start"})

    def test_multiple_appends_preserve_order(self):
        round_ledger.append_actions_log(self.td, {"n": 1})
        round_ledger.append_actions_log(self.td, {"n": 2})
        round_ledger.append_actions_log(self.td, {"n": 3})
        lines = (self.td / "actions.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([json.loads(l)["n"] for l in lines], [1, 2, 3])


class CommitHelpersTest(unittest.TestCase):
    """Verify each commit helper assembles the right Action trailer + file set.

    Uses a real harness-init workspace so the commit-msg hook is active.
    """
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        # actions.jsonl must exist for the commit helpers to stage it
        (self.ws / "actions.jsonl").write_text(
            '{"event": "init", "n": 1}\n'
        )

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _last_commit_message(self) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"],
            text=True,
        )

    def test_commit_register_decision_action_trailer_set(self):
        # Stage realistic register-decision change
        (self.ws / "derived").mkdir(parents=True, exist_ok=True)
        (self.ws / "derived" / "decisions.json").write_text(
            '{"goal_version": "g-02", "decisions": {}}\n'
        )
        # Bump goal.toml version too (real Flow A would do this via register_decision)
        goal_path = self.ws / "goal.toml"
        text = goal_path.read_text()
        text = text.replace('goal_version = "g-01"', 'goal_version = "g-02"')
        goal_path.write_text(text)
        round_ledger.commit_register_decision(
            self.ws, new_decision_ids=["circuit-breaker"],
        )
        msg = self._last_commit_message()
        self.assertIn("Action: register-decision", msg)

    def test_commit_merge_includes_variant_and_round_trailers(self):
        # Stage a doc section update (decided-section immutability requires
        # an existing section in HEAD; for this test we use a new section)
        doc_dir = self.ws / "variants" / "nodes" / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-retry.md").write_text(
            '+++\nsection_id = "retry-policy"\ntags = []\n+++\nbody\n'
        )
        round_ledger.commit_merge(
            self.ws, round_id="round-000001", variant_id="v-001",
            section_paths=["variants/nodes/v-001/doc/01-retry.md"],
            claim_paths=[], attack_paths=[], evidence_paths=[],
        )
        msg = self._last_commit_message()
        self.assertIn("Action: merge", msg)
        self.assertIn("Variant: v-001", msg)
        self.assertIn("Round: round-000001", msg)

    def test_commit_rejection_includes_reviewer_trailer_when_applicable(self):
        # Write an rj-*.md first
        rj_id = round_ledger.write_rejection(
            self.ws, "round-000005", "v-001",
            reason_class="uncited-claim",
            failed_phase="reviewer",
            detail="x", reviewer_id="v-002",
        )
        round_ledger.commit_rejection(
            self.ws, action="reviewer-rejected",
            round_id="round-000005", variant_id="v-001",
            rj_id=rj_id, reason="uncited-claim", reviewer_id="v-002",
        )
        msg = self._last_commit_message()
        self.assertIn("Action: reviewer-rejected", msg)
        self.assertIn("Variant: v-001", msg)
        self.assertIn("Round: round-000005", msg)
        self.assertIn("Reason: uncited-claim", msg)
        self.assertIn("Reviewer: v-002", msg)


class CommitMergeScoreDeltaTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)
        (self.ws / "actions.jsonl").write_text('{"event": "init"}\n')

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _make_section(self):
        doc = self.ws / "variants" / "nodes" / "v-001" / "doc" / "01-x.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text('+++\nsection_id = "x"\n+++\n## X\nbody\n')
        return "variants/nodes/v-001/doc/01-x.md"

    def _last_commit_msg(self):
        return subprocess.check_output(
            ["git", "-C", str(self.ws), "log", "-1", "--format=%B"]
        ).decode()

    def test_score_delta_appended_when_provided(self):
        sec = self._make_section()
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[],
            score_delta="groundedness=+0.04 completeness=-0.01",
        )
        msg = self._last_commit_msg()
        self.assertIn("Score-Delta: groundedness=+0.04 completeness=-0.01", msg)

    def test_no_score_delta_trailer_when_none(self):
        sec = self._make_section()
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[], score_delta=None,
        )
        self.assertNotIn("Score-Delta", self._last_commit_msg())

    def test_scorecard_path_staged_when_provided(self):
        sec = self._make_section()
        sc = self.ws / "variants" / "nodes" / "v-001" / "scorecard.json"
        sc.write_text('{"variant": "v-001"}')
        round_ledger.commit_merge(
            self.ws, round_id="round-000002", variant_id="v-001",
            section_paths=[sec], claim_paths=[], attack_paths=[],
            evidence_paths=[], score_delta="groundedness=+0.01",
            scorecard_path="variants/nodes/v-001/scorecard.json",
        )
        tracked = subprocess.check_output(
            ["git", "-C", str(self.ws), "ls-files",
             "variants/nodes/v-001/scorecard.json"]
        ).decode().strip()
        self.assertEqual(tracked, "variants/nodes/v-001/scorecard.json")


if __name__ == "__main__":
    unittest.main()
