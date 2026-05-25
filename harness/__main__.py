"""Entry shim so `python -m harness <subcommand>` works."""
from harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
