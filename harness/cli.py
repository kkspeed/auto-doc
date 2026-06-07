"""Harness CLI. Entry point: `python -m harness <subcommand>`."""
from __future__ import annotations

import argparse
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from harness import bootstrap

HARNESS_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = HARNESS_ROOT / "workspace_template"


def cmd_init(target_dir: Path, reactivate: bool) -> int:
    if reactivate:
        return _reactivate(target_dir)

    # Validate target: must not exist, OR must be an empty directory.
    if target_dir.exists():
        if not target_dir.is_dir():
            print(f"harness init: refusing to clobber existing file {target_dir}",
                  file=sys.stderr)
            return 1
        if any(target_dir.iterdir()):
            print(f"harness init: refusing to clobber non-empty {target_dir}",
                  file=sys.stderr)
            return 1
    else:
        target_dir.mkdir(parents=True)

    # Copy template tree (files only; directories created as needed)
    for src in TEMPLATE_DIR.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(TEMPLATE_DIR)
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        # Preserve executable bit for hook scripts (rglob may strip them)
        if "hooks" in rel.parts:
            dst.chmod(0o755)

    # Initialize git and configure hooksPath.
    # Hooks live at <target>/hooks/ in the scaffolded workspace (flat layout
    # — v0 simplifies the parent design's project-root/workspace/ nesting,
    # which only matters once repo/ and sources/ adapters land in v0.2+).
    # Pass an explicit harness identity to the commit so the call doesn't
    # fail on machines without a global git user.name/user.email (common in
    # CI containers and fresh dev environments).
    commit_msg = "harness: scaffold workspace\n\nAction: init\n"
    try:
        subprocess.check_call(["git", "init", "-q"], cwd=target_dir)
        subprocess.check_call(
            ["git", "config", "core.hooksPath", "hooks/"],
            cwd=target_dir,
        )
        # Bootstrap derived state before the scaffold commit:
        #  - decisions.json: rebuildable cache (gitignored; not committed)
        #  - canonical_slug_registry.json: persisted append-only baseline
        bootstrap.rebuild_decisions_cache(target_dir)
        bootstrap.ensure_empty_registry(target_dir)
        subprocess.check_call(["git", "add", "."], cwd=target_dir)
        # Force-add the registry (derived/ is gitignored) so its baseline is
        # tracked. decisions.json stays ignored — it is rebuilt each run.
        subprocess.check_call(
            ["git", "-C", str(target_dir), "add", "-f",
             "derived/canonical_slug_registry.json"])
        subprocess.check_call(
            ["git",
             "-c", "user.email=harness@localhost",
             "-c", "user.name=harness",
             "commit", "-q", "-m", commit_msg],
            cwd=target_dir,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        # Half-initialized state isn't useful; remove it so a re-run isn't
        # blocked by the "refusing to clobber" guard. OSError covers the
        # bootstrap.* filesystem steps (e.g. permission denied on derived/).
        shutil.rmtree(target_dir, ignore_errors=True)
        print(f"harness init: git step failed: {exc}", file=sys.stderr)
        return 1

    print(f"harness init: workspace ready at {target_dir}")
    return 0


def _reactivate(target_dir: Path) -> int:
    if not (target_dir / ".git").exists():
        print(f"{target_dir} is not a git repository", file=sys.stderr)
        return 1
    subprocess.check_call(
        ["git", "config", "core.hooksPath", "hooks/"],
        cwd=target_dir,
    )
    print(f"harness init --reactivate: hooks reactivated at {target_dir}")
    return 0


def cmd_run(
    workspace: Path,
    max_rounds: int | None,
    max_hours: float | None,
    variants: int,
) -> int:
    """Run the harness loop. Requires at least one of max_rounds / max_hours."""
    import tomllib
    from harness.orchestrator import run_loop

    if max_rounds is None and max_hours is None:
        print("harness run: at least one of --rounds or --hours required",
              file=sys.stderr)
        return 2

    harness_toml = workspace / "harness.toml"
    if not harness_toml.exists():
        print(f"harness run: {harness_toml} not found "
              "(workspace not scaffolded?)", file=sys.stderr)
        return 1
    with harness_toml.open("rb") as f:
        harness_config = tomllib.load(f)

    try:
        outcomes = run_loop(
            workspace, harness_config,
            max_rounds=max_rounds,
            max_wall_clock_hours=max_hours,
            variant_count=variants,
        )
    except ValueError as e:
        print(f"harness run: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"harness run: unexpected error: {e}", file=sys.stderr)
        return 1

    # Summary
    for o in outcomes:
        print(f"{o.round_id} {o.variant_id} → {o.verdict}"
              f"{' (' + o.reason + ')' if o.reason else ''}")
        # On rejection, surface the phase + detail inline so the cause is
        # visible without digging into rejections/rj-*.md. reason is None only
        # on the accept path; failed_phase/detail are populated on every reject.
        if o.reason and (o.failed_phase or o.detail):
            if o.failed_phase:
                print(f"    phase: {o.failed_phase}"
                      f"{' (' + o.rj_id + ')' if o.rj_id else ''}")
            if o.detail:
                for line in o.detail.splitlines() or [o.detail]:
                    print(f"    {line}")
    print(f"Ran {len(outcomes)} round(s).")
    return 0


# Commit messages whose subjects mark the two reset anchors (see orchestrator/
# cli init). Matched literally against `git log --grep --fixed-strings`.
_SEED_MARKER = "harness: seed variant documents"
_SCAFFOLD_MARKER = "harness: scaffold workspace"

# Untracked (gitignored) runtime artifacts that `git reset --hard` cannot
# remove. Deleting rounds/ is what actually restarts numbering at round 1 —
# _next_round_number scans that directory, not git history.
_RUNTIME_ARTIFACTS = ("rounds", "derived/decisions.json",
                      "CONTEXT.md", "morning_brief.md")

# Explicit identity so commits work on machines without a global git user
# (CI, fresh dev boxes) — mirrors cmd_init.
_HARNESS_IDENTITY = ("-c", "user.email=harness@localhost",
                     "-c", "user.name=harness")


def _find_anchor_commit(workspace: Path, marker: str) -> str | None:
    """SHA of the most recent commit whose message contains `marker`, or None.
    Most-recent matters: after a reset-to-scaffold + re-run, a fresh seed commit
    is created, and we want the current epoch's anchor."""
    result = subprocess.run(
        ["git", "-C", str(workspace), "log", "--grep", marker,
         "--fixed-strings", "--format=%H", "-1"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    sha = result.stdout.strip().split("\n")[0].strip()
    return sha or None


def cmd_reset(workspace: Path, target: str, assume_yes: bool) -> int:
    """Reset the workspace to its seed (default) or scaffold anchor commit and
    delete untracked round artifacts so the next run restarts at round 1."""
    if not (workspace / ".git").exists():
        print(f"harness reset: {workspace} is not a git repository",
              file=sys.stderr)
        return 1
    marker = _SEED_MARKER if target == "seed" else _SCAFFOLD_MARKER
    sha = _find_anchor_commit(workspace, marker)
    if sha is None:
        print(f"harness reset: no commit matching {marker!r} found in "
              f"{workspace} — nothing to reset to", file=sys.stderr)
        return 1
    short = sha[:9]
    print(f"harness reset --to {target}: will hard-reset {workspace} to "
          f"{short} and delete round artifacts "
          f"({', '.join(_RUNTIME_ARTIFACTS)}).")
    print("This permanently discards every round and commit made after that "
          "point.")
    if not assume_yes:
        try:
            resp = input("Continue? [y/N] ")
        except EOFError:
            resp = ""
        if resp.strip().lower() not in ("y", "yes"):
            print("harness reset: aborted.")
            return 1
    reset = subprocess.run(
        ["git", "-C", str(workspace), "reset", "--hard", sha],
        capture_output=True, text=True,
    )
    if reset.returncode != 0:
        print(f"harness reset: git reset failed: {reset.stderr.strip()}",
              file=sys.stderr)
        return 1
    removed = []
    for rel in _RUNTIME_ARTIFACTS:
        p = workspace / rel
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(rel)
        elif p.exists():
            p.unlink()
            removed.append(rel)
    print(f"harness reset: reset to {short} ({target}).")
    if removed:
        print(f"  removed: {', '.join(removed)}")
    return 0


def _config_files_changed(workspace: Path, files: list[str]) -> list[str]:
    """Subset of `files` that differ from HEAD (staged or unstaged)."""
    changed = []
    for f in files:
        r = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--quiet", "HEAD", "--", f],
            capture_output=True,
        )
        if r.returncode == 1:  # --quiet implies --exit-code: 1 == differs
            changed.append(f)
    return changed


def cmd_commit_config(workspace: Path, message: str | None) -> int:
    """Commit manual goal.toml / harness.toml edits with the Action: init
    trailer the commit-msg hook requires. init is the only Action whose
    file-whitelist is unrestricted (so harness.toml is allowed) and it is
    present in every existing workspace's hook, so this needs no hook change."""
    if not (workspace / ".git").exists():
        print(f"harness commit-config: {workspace} is not a git repository",
              file=sys.stderr)
        return 1
    candidates = [f for f in ("goal.toml", "harness.toml")
                  if (workspace / f).exists()]
    if not candidates:
        print(f"harness commit-config: no goal.toml or harness.toml in "
              f"{workspace}", file=sys.stderr)
        return 1
    changed = _config_files_changed(workspace, candidates)
    if not changed:
        print("harness commit-config: no changes to goal.toml or harness.toml.")
        return 0
    msg = message or f"harness: update config ({', '.join(changed)})"
    full = f"{msg}\n\nAction: init\n"
    # Pathspec commit (`-- <files>`): commits ONLY these paths' working-tree
    # state, leaving any other staged changes untouched and out of the commit.
    commit = subprocess.run(
        ["git", "-C", str(workspace), *_HARNESS_IDENTITY,
         "commit", "-m", full, "--", *changed],
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        print("harness commit-config: commit failed (commit-msg hook?):\n"
              f"{(commit.stderr or commit.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"harness commit-config: committed {', '.join(changed)} "
          "(Action: init).")
    return 0


# Config keys that grant a role tool access (see harness/spawn.py invokers).
_TOOL_ACCESS_KEYS = ("allowed_tools", "mcp_config", "mcp_server_names",
                     "sandbox", "extra_args")
# Tool-name prefixes that can read a planted file (so the read-probe is valid).
_FILE_TOOL_PREFIXES = ("Read", "Bash", "Grep", "Glob", "Edit", "Write")


def _role_has_tools(cfg: dict) -> bool:
    return any(cfg.get(k) for k in _TOOL_ACCESS_KEYS)


def _role_has_file_tools(cfg: dict) -> bool:
    allowed = cfg.get("allowed_tools") or []
    if not isinstance(allowed, list):
        return False
    return any(str(t).split("(")[0] in _FILE_TOOL_PREFIXES for t in allowed)


def _mcp_list(tool: str, workspace: Path) -> tuple[int, str]:
    """Run `<tool> mcp list` from the workspace (so project MCP config is seen).
    Returns (returncode, combined output). returncode 127-ish style errors are
    surfaced as-is."""
    try:
        r = subprocess.run([tool, "mcp", "list"], cwd=str(workspace),
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"could not run `{tool} mcp list`: {e}"
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _probe_role(workspace: Path, harness_config: dict, role: str,
                nonce: str, rel_probe_path: str) -> tuple[str, str]:
    """Spawn one role with a sentinel-file read task. PASS only if the role
    actually read the planted nonce back — proving its tools execute (not just
    that the CLI ran). Returns (status, detail) with status in {ok, fail}."""
    from harness.spawn import spawn_role

    prompt = (
        f"PREFLIGHT PROBE. A file exists at `{rel_probe_path}` relative to your "
        "working directory. Use your tools (the Read tool, or `cat` via Bash) "
        "to read its entire contents, then output ONLY this JSON: "
        '{"nonce": "<the file contents, trimmed>"}. You MUST actually read the '
        "file with a tool — do not guess or echo this prompt.")

    def _validator(d: dict) -> None:
        if not isinstance(d.get("nonce"), str):
            raise ValueError("probe response missing string 'nonce'")

    result = spawn_role(
        role=role, harness_config=harness_config,
        context_md="# Doctor preflight probe\n", prompt=prompt,
        workspace_root=workspace, round_id="doctor", variant_id=None,
        validator=_validator)
    if result.verdict != "ok":
        return "fail", f"{result.verdict}: {result.stderr_tail or 'no detail'}"
    got = ((result.parsed or {}).get("nonce") or "").strip()
    if got == nonce:
        return "ok", "tools work (read the planted file)"
    return ("fail",
            "CLI ran but the file was NOT read back — tool likely denied in "
            f"this environment (expected the nonce, got {got!r:.40})")


def cmd_doctor(workspace: Path, run_probes: bool) -> int:
    """Preflight: verify each role's CLI is present, MCP servers resolve, and
    tool-enabled roles can actually execute tools — before a long run."""
    import tomllib

    harness_toml = workspace / "harness.toml"
    if not harness_toml.exists():
        print(f"harness doctor: {harness_toml} not found "
              "(workspace not scaffolded?)", file=sys.stderr)
        return 1
    with harness_toml.open("rb") as f:
        config = tomllib.load(f)
    models = config.get("models", {})
    if not isinstance(models, dict) or not models:
        print("harness doctor: no [models] configured", file=sys.stderr)
        return 1

    problems = 0
    warnings = 0
    print(f"harness doctor — {workspace}\n")

    # 1. CLI binaries
    print("CLIs:")
    tools = sorted({c.get("tool") for c in models.values()
                    if isinstance(c, dict) and c.get("tool")})
    for t in tools:
        path = shutil.which(t)
        if path:
            print(f"  {t:10} ok       {path}")
        else:
            print(f"  {t:10} MISSING  not on PATH")
            problems += 1

    # 2. MCP health (per tool with MCP configured in some role)
    mcp_tools = sorted({
        c["tool"] for c in models.values()
        if isinstance(c, dict) and c.get("tool")
        and (c.get("mcp_config") or c.get("mcp_server_names"))})
    if mcp_tools:
        print("\nMCP servers:")
        for t in mcp_tools:
            if not shutil.which(t):
                continue  # already flagged as missing above
            rc, out = _mcp_list(t, workspace)
            print(f"  `{t} mcp list` -> {'ok' if rc == 0 else 'ERROR'}")
            for line in (out.splitlines() or ["(no output)"])[:25]:
                print(f"    {line}")
            if rc != 0:
                problems += 1
            # `<tool> mcp list` often exits 0 even when servers need auth or are
            # unreachable — scan the output so those don't read as "all good".
            unhealthy = [ln for ln in out.splitlines()
                         if any(marker in ln.lower() for marker in
                                ("needs authentication", "not connected",
                                 "failed", "error", "unauthenticated",
                                 "timed out", "✗"))]
            if unhealthy:
                warnings += len(unhealthy)
                print(f"    ⚠ {len(unhealthy)} server(s) need attention "
                      "(auth/connection) — only matters if a role uses them")

    # 3. Live tool probes
    tool_roles = [(r, c) for r, c in models.items()
                  if isinstance(c, dict) and _role_has_tools(c)]
    if not tool_roles:
        print("\nNo tool-enabled roles configured — nothing to probe.")
    elif not run_probes:
        names = ", ".join(r for r, _ in tool_roles)
        print(f"\nTool probes skipped (--no-probe). Tool-enabled roles: {names}")
    else:
        print("\nTool probes (spawning each tool-enabled role once):")
        nonce = secrets.token_hex(8)
        rel = "rounds/doctor/probe.txt"
        (workspace / "rounds" / "doctor").mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(nonce)
        for role, cfg in tool_roles:
            tool = cfg.get("tool", "")
            if not shutil.which(tool):
                print(f"  {role:14} skip    {tool} not on PATH (see above)")
                continue
            if not _role_has_file_tools(cfg):
                print(f"  {role:14} n/a     no file tools to probe; rely on "
                      "MCP check above")
                continue
            status, detail = _probe_role(workspace, config, role, nonce, rel)
            print(f"  {role:14} {'ok  ' if status == 'ok' else 'FAIL'}    "
                  f"{detail}")
            if status != "ok":
                problems += 1

    print()
    if problems:
        print(f"{problems} problem(s) found"
              + (f", {warnings} warning(s)" if warnings else "")
              + " — fix before a long/overnight run.")
        return 1
    if warnings:
        print(f"All hard checks passed; {warnings} warning(s) above "
              "(MCP servers needing auth/connection — authenticate any your "
              "roles actually use).")
        return 0
    print("All checks passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Design Doc Evolution Harness orchestrator.",
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    init_p = subparsers.add_parser("init", help="Scaffold a new workspace")
    init_p.add_argument("dir", type=Path,
                        help="Target directory (must not exist or must be empty)")
    init_p.add_argument(
        "--reactivate", action="store_true",
        help="Re-run hook configuration on an existing workspace (e.g., a clone)",
    )

    run_p = subparsers.add_parser("run", help="Run the harness loop")
    run_p.add_argument("--rounds", type=int, default=None,
                       help="Max rounds cap (at least one of "
                            "--rounds / --hours required)")
    run_p.add_argument("--hours", type=float, default=None,
                       help="Max wall-clock hours cap")
    run_p.add_argument("--variants", type=int, default=2,
                       help="Number of variants to rotate across (default 2)")
    run_p.add_argument("--workspace", type=Path, default=Path.cwd(),
                       help="Workspace directory (default: cwd)")

    reset_p = subparsers.add_parser(
        "reset",
        help="Reset the workspace to its seed or scaffold state")
    reset_p.add_argument(
        "--to", choices=["seed", "scaffold"], default="seed",
        help="Reset target: 'seed' keeps the seeded docs + round-0 baseline "
             "and restarts at round 1 (default); 'scaffold' returns to a bare "
             "workspace that re-seeds on the next run (use after editing "
             "seed_doc.md / goal.toml)")
    reset_p.add_argument("--workspace", type=Path, default=Path.cwd(),
                         help="Workspace directory (default: cwd)")
    reset_p.add_argument("-y", "--yes", action="store_true",
                         help="Skip the confirmation prompt")

    cfg_p = subparsers.add_parser(
        "commit-config",
        help="Commit manual goal.toml / harness.toml edits with the required "
             "Action trailer")
    cfg_p.add_argument("-m", "--message", default=None,
                       help="Commit message (default: auto-generated)")
    cfg_p.add_argument("--workspace", type=Path, default=Path.cwd(),
                       help="Workspace directory (default: cwd)")

    doctor_p = subparsers.add_parser(
        "doctor",
        help="Preflight: check each role's CLI, MCP servers, and tool access "
             "before a run")
    doctor_p.add_argument("--workspace", type=Path, default=Path.cwd(),
                          help="Workspace directory (default: cwd)")
    doctor_p.add_argument(
        "--no-probe", action="store_true",
        help="Static checks only — skip spawning tool-enabled roles")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    if args.cmd == "run":
        return cmd_run(args.workspace, args.rounds, args.hours, args.variants)
    if args.cmd == "reset":
        return cmd_reset(args.workspace, args.to, args.yes)
    if args.cmd == "commit-config":
        return cmd_commit_config(args.workspace, args.message)
    if args.cmd == "doctor":
        return cmd_doctor(args.workspace, not args.no_probe)
    return 1


if __name__ == "__main__":
    sys.exit(main())
