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
    except subprocess.CalledProcessError as exc:
        # Half-initialized state isn't useful; remove it so a re-run isn't
        # blocked by the "refusing to clobber" guard.
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
    print(f"Ran {len(outcomes)} round(s).")
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

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    if args.cmd == "run":
        return cmd_run(args.workspace, args.rounds, args.hours, args.variants)
    return 1


if __name__ == "__main__":
    sys.exit(main())
