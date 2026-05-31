"""Integration tests for the commit-msg hook script."""
import os
import re
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

    def test_crlf_message_parses_trailers(self):
        # CRLF line endings (Windows tools, some CI runners) must not cause
        # the trailer parser to lose the Action trailer.
        msg = _write_msg(self.ws, "subject\r\n\r\nAction: init\r\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

    def test_co_authored_by_case_insensitive_passes(self):
        # Project default per CLAUDE.md uses title-case Co-Authored-By;
        # also test lowercase. Git interpret-trailers is case-insensitive.
        for variant in ("Co-Authored-By", "co-authored-by", "Co-authored-by"):
            with self.subTest(trailer=variant):
                msg = _write_msg(
                    self.ws,
                    f"subject\n\nAction: init\n{variant}: Bot <bot@example.com>\n",
                )
                result = _run_hook(self.ws, msg)
                self.assertEqual(result.returncode, 0,
                                 f"trailer {variant!r} should pass; "
                                 f"stderr: {result.stderr}")


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


def _commit_initial_section(workspace: Path, variant: str, section_name: str,
                            tags: list[str], body: str = "body\n"):
    """Stage and commit (bypassing hooks) a section file as the initial state.

    Returns the file path. Uses --no-verify to skip our own hooks during
    test setup; the test then makes a NEW change to exercise the hook.
    """
    doc_dir = workspace / "variants" / "nodes" / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    fp = doc_dir / f"{section_name}.md"
    fp.write_text(f"+++\nsection_id = \"x\"\ntags = [{tag_str}]\n+++\n{body}")
    subprocess.check_call(["git", "-C", str(workspace), "add", str(fp)])
    subprocess.check_call(
        ["git", "-C", str(workspace), "commit", "--no-verify", "-q",
         "-m", "setup\n\nAction: init\n"],
    )
    return fp


def _modify_section(workspace: Path, fp: Path, new_tags: list[str] | None = None,
                    new_body: str | None = None):
    """Modify the section file. Re-reads original and selectively updates."""
    text = fp.read_text()
    if new_tags is not None:
        tag_str = ", ".join(f'"{t}"' for t in new_tags)
        text = re.sub(r"(tags\s*=\s*)\[[^\]]*\]", rf"\1[{tag_str}]", text)
    if new_body is not None:
        # Replace everything after the second +++
        end = text.find("+++", 3)
        text = text[: end + 3] + "\n" + new_body
    fp.write_text(text)


def _stage(workspace: Path, *paths: str):
    subprocess.check_call(["git", "-C", str(workspace), "add", *paths])


class CommitMsgScopeTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_action_merge_three_sections_passes(self):
        for i in range(1, 4):
            _stage_file(self.ws, f"variants/nodes/v-001/doc/0{i}-s.md",
                        "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_action_merge_four_sections_rejects(self):
        for i in range(1, 5):
            _stage_file(self.ws, f"variants/nodes/v-001/doc/0{i}-s.md",
                        "+++\nsection_id = \"x\"\ntags = []\n+++\nbody\n")
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000001\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("4 doc sections", result.stderr)
        self.assertIn("limit is 3", result.stderr)


class CommitMsgImmutabilityTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decided_section_body_change_with_goal_version_bump_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        # Modify the body
        _modify_section(self.ws, fp, new_body="updated body\n")
        # Modify goal.toml goal_version
        goal_path = self.ws / "goal.toml"
        text = goal_path.read_text()
        text = text.replace('goal_version = "g-01"', 'goal_version = "g-02"')
        goal_path.write_text(text)
        _stage(self.ws, str(fp), str(goal_path))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_decided_section_body_change_without_goal_version_bump_rejects(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_body="updated body\n")
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("immutability", result.stderr.lower())

    def test_decided_section_tag_only_change_on_registry_sync_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_tags=["unresolved"])
        _stage(self.ws, str(fp))
        msg = _write_msg(self.ws, "subject\n\nAction: registry-sync\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_decided_section_tag_only_change_on_merge_rejects(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_tags=["unresolved"])
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("registry-sync", result.stderr)

    def test_non_decided_section_body_change_passes(self):
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["unresolved"])
        _modify_section(self.ws, fp, new_body="updated body\n")
        _stage(self.ws, str(fp))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_decided_section_body_change_with_goal_toml_staged_but_version_unchanged_rejects(self):
        # Stage goal.toml WITHOUT bumping goal_version — only edit a comment
        # or some other field. The hook should still reject the section body
        # change because no goal_version bump occurred.
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        _modify_section(self.ws, fp, new_body="updated body\n")
        goal_path = self.ws / "goal.toml"
        text = goal_path.read_text()
        # Modify goal.toml without bumping goal_version (e.g., add a comment)
        text = text + "\n# trailing comment added without version bump\n"
        goal_path.write_text(text)
        _stage(self.ws, str(fp), str(goal_path))
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("immutability", result.stderr.lower())

    def test_decided_section_deletion_rejects(self):
        # Deleting a decided section should be rejected — it's a structural
        # change that the orchestrator's documented flows never perform.
        fp = _commit_initial_section(self.ws, "v-001", "01-s", ["decided"])
        # Stage the deletion of the file
        subprocess.check_call(
            ["git", "-C", str(self.ws), "rm", "-q", str(fp.relative_to(self.ws))],
        )
        msg = _write_msg(
            self.ws,
            "subject\n\nAction: merge\nVariant: v-001\nRound: round-000002\n",
        )
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot delete decided", result.stderr)


class ScoreRegressionActionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_score_regression_with_required_trailers_passes(self):
        # Stage a rejection file so the file-whitelist check is satisfied.
        rej = self.ws / "rejections" / "rj-000001.md"
        rej.parent.mkdir(parents=True, exist_ok=True)
        rej.write_text("+++\n+++\nbody\n")
        subprocess.check_call(["git", "-C", str(self.ws), "add", "-f",
                               "rejections/rj-000001.md"])
        msg = _write_msg(self.ws,
            "chore: score-regression for round-000002 v-001\n\n"
            "Action: score-regression\nVariant: v-001\n"
            "Round: round-000002\nReason: score-regression\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

    def test_score_regression_missing_reason_rejects(self):
        msg = _write_msg(self.ws,
            "chore: score-regression\n\n"
            "Action: score-regression\nVariant: v-001\nRound: round-000002\n")
        result = _run_hook(self.ws, msg)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Reason", result.stderr)


if __name__ == "__main__":
    unittest.main()
