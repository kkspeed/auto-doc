"""Tests for `harness doctor` preflight."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import cli
from harness.spawn import RoleOutput


def _write_harness_toml(ws: Path, models: dict):
    lines = []
    for role, cfg in models.items():
        lines.append(f"[models.{role}]")
        for k, v in cfg.items():
            if isinstance(v, list):
                lines.append(f"{k} = {json.dumps(v)}")
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")
    (ws / "harness.toml").write_text("\n".join(lines))


def _run_doctor(ws, run_probes=True):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.cmd_doctor(ws, run_probes)
    return rc, buf.getvalue()


# A fake spawn that "reads" the planted probe file via the workspace path.
def _fake_spawn_reads_probe(*, workspace_root, **kw):
    content = (workspace_root / "rounds" / "doctor" / "probe.txt").read_text()
    return RoleOutput(verdict="ok", parsed={"nonce": content.strip()})


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ws = self.td / "ws"
        self.ws.mkdir()
        self.tool_models = {
            "planner": {"tool": "claude", "model": "m"},
            "repo_adapter": {
                "tool": "claude", "model": "m",
                "allowed_tools": ["Read", "Bash(gh:*)"],
                "mcp_config": [".mcp.json"]},
        }

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_missing_harness_toml(self):
        rc, _ = _run_doctor(self.ws)
        self.assertEqual(rc, 1)

    def test_missing_cli_is_a_problem(self):
        _write_harness_toml(self.ws, self.tool_models)
        with mock.patch("harness.cli.shutil.which", return_value=None):
            rc, out = _run_doctor(self.ws, run_probes=False)
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", out)

    def test_probe_pass_when_tool_reads_file(self):
        _write_harness_toml(self.ws, self.tool_models)
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, "gdrive ok")), \
             mock.patch("harness.spawn.spawn_role",
                        side_effect=_fake_spawn_reads_probe):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("repo_adapter", out)
        self.assertIn("All checks passed", out)

    def test_probe_fail_when_tool_denied(self):
        _write_harness_toml(self.ws, self.tool_models)
        denied = RoleOutput(verdict="ok", parsed={"nonce": ""})  # didn't read
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, "")), \
             mock.patch("harness.spawn.spawn_role", return_value=denied):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 1)
        self.assertIn("tool likely denied", out)

    def test_probe_fail_on_spawn_failure(self):
        _write_harness_toml(self.ws, self.tool_models)
        failed = RoleOutput(verdict="spawn-failed", stderr_tail="boom")
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, "")), \
             mock.patch("harness.spawn.spawn_role", return_value=failed):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 1)
        self.assertIn("spawn-failed", out)

    def test_no_probe_skips_spawn(self):
        _write_harness_toml(self.ws, self.tool_models)
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, "")), \
             mock.patch("harness.spawn.spawn_role") as m:
            rc, out = _run_doctor(self.ws, run_probes=False)
        m.assert_not_called()
        self.assertEqual(rc, 0, out)
        self.assertIn("skipped", out)

    def test_mcp_list_error_is_a_problem(self):
        _write_harness_toml(self.ws, self.tool_models)
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list",
                        return_value=(1, "auth required")), \
             mock.patch("harness.spawn.spawn_role",
                        side_effect=_fake_spawn_reads_probe):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 1)
        self.assertIn("auth required", out)

    def test_unhealthy_mcp_server_warns_but_passes(self):
        # `mcp list` exits 0 but a server needs auth -> warning, not a hard fail.
        _write_harness_toml(self.ws, self.tool_models)
        listing = ("Google Drive: ... - ! Needs authentication\n"
                   "Glean: ... - connected")
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, listing)), \
             mock.patch("harness.spawn.spawn_role",
                        side_effect=_fake_spawn_reads_probe):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 0, out)            # warning, not a problem
        self.assertIn("need attention", out)
        self.assertIn("warning(s)", out)

    def test_no_tool_roles_nothing_to_probe(self):
        _write_harness_toml(self.ws, {"planner": {"tool": "claude",
                                                  "model": "m"}})
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"):
            rc, out = _run_doctor(self.ws)
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing to probe", out)

    def test_mcp_only_role_skips_file_probe(self):
        models = {"src": {"tool": "claude", "model": "m",
                          "mcp_config": [".mcp.json"]}}  # no file tools
        _write_harness_toml(self.ws, models)
        with mock.patch("harness.cli.shutil.which", return_value="/bin/claude"), \
             mock.patch("harness.cli._mcp_list", return_value=(0, "ok")), \
             mock.patch("harness.spawn.spawn_role") as m:
            rc, out = _run_doctor(self.ws)
        m.assert_not_called()  # no file tools -> no file probe
        self.assertEqual(rc, 0, out)
        self.assertIn("no file tools", out)


if __name__ == "__main__":
    unittest.main()
