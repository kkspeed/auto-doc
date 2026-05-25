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


if __name__ == "__main__":
    unittest.main()
