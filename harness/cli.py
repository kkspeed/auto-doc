"""Harness CLI. Entry point: `python -m harness <subcommand>`."""
from __future__ import annotations

import argparse
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

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    if args.cmd == "run":
        return cmd_run(args.workspace, args.rounds, args.hours, args.variants)
    if args.cmd == "reset":
        return cmd_reset(args.workspace, args.to, args.yes)
    if args.cmd == "commit-config":
        return cmd_commit_config(args.workspace, args.message)
    return 1


if __name__ == "__main__":
    sys.exit(main())
