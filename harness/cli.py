"""Harness CLI. Entry point: `python -m harness <subcommand>`."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

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
        subprocess.check_call(["git", "add", "."], cwd=target_dir)
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

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.dir, args.reactivate)
    return 1


if __name__ == "__main__":
    sys.exit(main())
