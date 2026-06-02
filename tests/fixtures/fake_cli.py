#!/usr/bin/env python3
"""Scenario-driven fake CLI for spawn-wrapper tests.

Invocation: python3 fake_cli.py --scenario <name> [--marker-file <path>]

Scenarios:
  ok            — read stdin, write {"ok": true, "echo_len": N} to stdout, exit 0
  nonzero       — write "boom" to stderr, exit 1
  nonjson       — write "not json output" to stdout, exit 0
  slow N        — emit heartbeat lines every 100ms for N seconds (default 1),
                  then {"ok": true} and exit 0
  hang          — read stdin, then sleep forever
  validate_fail — write {"wrong_field": "x"} to stdout, exit 0
  claude_json_envelope
                — write a Claude Code --output-format json envelope whose
                  result field contains the assistant JSON text
  transient     — first invocation exit 1; second invocation exit 0 with {"ok": true}.
                  Uses --marker-file to track invocation count across runs.
"""
import argparse
import hashlib
import json
import os
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True)
    p.add_argument("--scenario-arg", default=None,
                   help="Numeric arg for scenarios like 'slow N'")
    p.add_argument("--marker-file", default=None,
                   help="Per-test marker file for the 'transient' scenario")
    # Accept and ignore any --model / -p / --output-format / etc. so the same
    # script can pretend to be claude, codex, or gemini under any argv shape.
    args, _ = p.parse_known_args()

    if args.scenario == "ok":
        stdin_text = sys.stdin.read()
        out = {"ok": True, "echo_len": len(stdin_text),
               "echo_sha": hashlib.sha256(stdin_text.encode()).hexdigest()[:16]}
        print(json.dumps(out))
        sys.exit(0)
    elif args.scenario == "nonzero":
        sys.stderr.write("boom\n")
        sys.exit(1)
    elif args.scenario == "nonjson":
        print("not json output")
        sys.exit(0)
    elif args.scenario == "slow":
        n = int(args.scenario_arg or "1")
        end = time.monotonic() + n
        while time.monotonic() < end:
            sys.stderr.write("heartbeat\n")
            sys.stderr.flush()
            time.sleep(0.1)
        print(json.dumps({"ok": True}))
        sys.exit(0)
    elif args.scenario == "hang":
        sys.stdin.read()    # consume whatever the parent sends
        while True:
            time.sleep(60)
    elif args.scenario == "validate_fail":
        print(json.dumps({"wrong_field": "x"}))
        sys.exit(0)
    elif args.scenario == "claude_json_envelope":
        role_json = {
            "round": "round-000001",
            "variant": "v-001",
            "stance": "test",
            "intent": "test",
            "target_sections": [],
        }
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(role_json),
        }
        print(json.dumps(envelope))
        sys.exit(0)
    elif args.scenario == "transient":
        if not args.marker_file:
            sys.stderr.write("transient scenario requires --marker-file\n")
            sys.exit(2)
        if os.path.exists(args.marker_file):
            # Second invocation: succeed
            print(json.dumps({"ok": True, "retry": True}))
            sys.exit(0)
        else:
            # First invocation: drop marker and fail
            with open(args.marker_file, "w") as f:
                f.write("first")
            sys.stderr.write("transient-fail\n")
            sys.exit(1)
    else:
        sys.stderr.write(f"unknown scenario: {args.scenario}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
