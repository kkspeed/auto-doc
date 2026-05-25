"""Integration tests for the pre-commit hook script.

Each test sets up a minimal workspace under tempdir (via `harness init`), stages
a known file set, runs the pre-commit script as a subprocess, asserts exit
code + stderr.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "workspace_template" / "hooks" / "pre-commit"


def _scaffold_workspace(target: Path):
    """Run `harness init` to scaffold a workspace at target."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + ":" + env.get("PYTHONPATH", "")
    subprocess.check_call(
        ["python3", "-m", "harness", "init", str(target)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _write_cl(workspace: Path, variant: str, claim_id: str, **fields):
    """Write a cl-*.json under variants/nodes/<variant>/claims/."""
    claims_dir = workspace / "variants" / "nodes" / variant / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "id": claim_id, "section_id": fields.get("decision_id", "retry-policy"),
        "decision_id": "retry-policy", "claim_type": "decision",
        "evidence_ids": [], "assertion": "x", "position": "expo-backoff",
    }
    base.update(fields)
    fp = claims_dir / f"{claim_id}.json"
    fp.write_text(json.dumps(base, indent=2))
    return fp


def _write_at(workspace: Path, variant: str, attack_id: str, **fields):
    attacks_dir = workspace / "variants" / "nodes" / variant / "attacks"
    attacks_dir.mkdir(parents=True, exist_ok=True)
    base = {
        "id": attack_id, "at_type": "dispute_claim",
        "target_claim_id": "cl-000001",
        "argument": "x", "evidence_ids": [],
    }
    base.update(fields)
    fp = attacks_dir / f"{attack_id}.json"
    fp.write_text(json.dumps(base, indent=2))
    return fp


def _write_registry(workspace: Path, data: dict):
    p = workspace / "derived" / "canonical_slug_registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _write_decisions(workspace: Path, decisions: dict):
    p = workspace / "derived" / "decisions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"goal_version": "g-01", "decisions": decisions},
                            indent=2))


def _stage_all(workspace: Path):
    subprocess.check_call(["git", "-C", str(workspace), "add", "-A"])


def _run_hook(workspace: Path):
    """Invoke the pre-commit script directly. Returns CompletedProcess."""
    return subprocess.run(
        ["python3", str(HOOK_PATH)],
        cwd=workspace,
        capture_output=True, text=True,
    )


class PreCommitSchemaTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        _scaffold_workspace(self.ws)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_passes_on_valid_claim_and_attack(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001")
        _write_at(self.ws, "v-001", "at-000001")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 0,
                         f"stderr: {result.stderr}")

    def test_rejects_invalid_claim_json(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", claim_type="speculation")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("claim_type", result.stderr)

    def test_rejects_invalid_attack_json(self):
        _write_at(self.ws, "v-001", "at-000001", at_type="complain")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("at_type", result.stderr)

    def test_rejects_vacuous_position_slug(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", position="tbd")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("vacuous", result.stderr.lower())

    def test_rejects_alias_slug_as_position(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_registry(self.ws, {
            "retry-policy": {"canonical": ["expo-backoff"],
                             "aliases": {"exponential-backoff": "expo-backoff"}},
        })
        _write_cl(self.ws, "v-001", "cl-000001", position="exponential-backoff")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("alias", result.stderr.lower())

    def test_rejects_decision_id_for_retired_decision(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "retired", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("retired", result.stderr.lower())

    def test_collects_multiple_errors_in_one_run(self):
        _write_decisions(self.ws, {"retry-policy": {
            "id": "retry-policy", "question": "?",
            "status": "open", "introduced_at": "g-01"}})
        _write_cl(self.ws, "v-001", "cl-000001", claim_type="speculation")
        _write_at(self.ws, "v-001", "at-000001", at_type="complain")
        _stage_all(self.ws)
        result = _run_hook(self.ws)
        self.assertEqual(result.returncode, 1)
        self.assertIn("claim_type", result.stderr)
        self.assertIn("at_type", result.stderr)


if __name__ == "__main__":
    unittest.main()
