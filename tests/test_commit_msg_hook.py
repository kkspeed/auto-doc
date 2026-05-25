"""Integration tests for the commit-msg hook script."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "commit-msg"


def _scaffold_workspace(target: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _write_msg(workspace: Path, text: str) -> Path:
    msg_path = workspace / ".git" / "MSG"
    msg_path.write_text(text)
    return msg_path


def _run_hook(workspace: Path, msg_path: Path):
    return subprocess.run(
        ["python3", str(HOOK_PATH), str(msg_path)],
        cwd=workspace,
        capture_output=True, text=True,
    )


class CommitMsgTrailerTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_valid_action_init_passes(self):
        msg = _write_msg(self.ws, "subject\n\nAction: init\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

    def test_missing_action_trailer_rejects(self):
        msg = _write_msg(self.ws, "some subject\n\nbody without trailers\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Action", result.stderr)

    def test_unknown_action_value_rejects(self):
        msg = _write_msg(self.ws, "subject\n\nAction: explode\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("explode", result.stderr)

    def test_unknown_trailer_key_rejects(self):
        msg = _write_msg(self.ws,
                         "subject\n\nAction: init\nBananas: yellow\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bananas", result.stderr)

    def test_action_merge_missing_variant_rejects(self):
        msg = _write_msg(self.ws,
                         "subject\n\nAction: merge\nRound: round-000001\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Variant", result.stderr)

    def test_action_reviewer_rejected_missing_reviewer_rejects(self):
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: reviewer-rejected\n"
            "Variant: v-001\nRound: round-000001\nReason: uncited-claim\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Reviewer", result.stderr)


def _stage_file(workspace: Path, rel_path: str, content: str = "x\n"):
    """Create a file at rel_path under workspace and force-stage it.

    Uses `git add -f` to bypass the workspace .gitignore (which excludes
    derived/, rounds/*/scratch/, etc.). The hook must still validate these
    when they're explicitly staged by the orchestrator.
    """
    p = workspace / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.check_call(["git", "-C", str(workspace), "add", "-f", rel_path])


class CommitMsgFileSetTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_action_register_decision_with_doc_file_rejects(self):
        _stage_file(self.ws, "variants/nodes/v-001/doc/01-retry.md",
                    "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(self.ws, "subject\n\nAction: register-decision\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("register-decision", result.stderr)
        self.assertIn("doc/01-retry.md", result.stderr)

    def test_action_canonicalize_with_at_file_rejects(self):
        _stage_file(self.ws, "variants/nodes/v-001/attacks/at-000001.json",
                    "{}\n")
        msg = _write_msg(self.ws, "subject\n\nAction: canonicalize\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("canonicalize", result.stderr)
        self.assertIn("at-000001.json", result.stderr)

    def test_action_registry_sync_allowed_files_pass(self):
        _stage_file(self.ws, "derived/decisions.json", "{}\n")
        _stage_file(self.ws, "derived/canonical_slug_registry.json", "{}\n")
        msg = _write_msg(self.ws, "subject\n\nAction: registry-sync\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_action_merge_allowed_files_pass(self):
        _stage_file(self.ws, "variants/nodes/v-001/doc/01-retry.md",
                    "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        _stage_file(self.ws, "variants/nodes/v-001/claims/cl-000001.json",
                    "{}\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_failure_action_with_evidence_file_rejects(self):
        _stage_file(self.ws, "evidence/ev-000001.md", "x\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: phase-b-fail\nVariant: v-001\n"
            "Round: round-000001\nReason: cross-field-fail\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("phase-b-fail", result.stderr)


if __name__ == "__main__":
    unittest.main()
