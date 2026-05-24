# Claim Graph Drift Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three gaps between `harness/claim_graph.py` and [2026-05-22-claim-graph-redesign-design.md](../specs/2026-05-22-claim-graph-redesign-design.md) per the drift closure spec at [2026-05-24-claim-graph-drift-closure-design.md](../specs/2026-05-24-claim-graph-drift-closure-design.md): add `apply_decision_id_canonicalization` (Flow D §4.5), add `render_canonicalizations_applied` + `render_stale_proposals_table` (§5.3), and make `register_decision` atomically regenerate `derived/decisions.json` (§4.1 step 6).

**Architecture:** All additions land in the existing `harness/claim_graph.py` module — no new modules, no schema changes, no new dataclasses. Tests extend three existing topic-grouped test files (`test_canonicalization_apply.py`, `test_morning_brief_render.py`, `test_decision_registration.py`); no new test files. Total: ~130 LOC runtime + ~310 LOC tests.

**Tech Stack:** Python 3.11+ stdlib only (`re`, `json`, `pathlib`), `unittest` for tests, `git` for commits. Matches the existing module's conventions (`from __future__ import annotations`, `_Path`/`_json` late-import aliases, `_require`/`_require_enum`/`_require_slug` validation helpers).

---

## File Structure

**Modified in this plan:**
- `harness/claim_graph.py` — appended: one helper regex `_SECTION_ID_LINE_RE`, one helper `_set_section_id`, one new function `apply_decision_id_canonicalization`, two new render functions; one in-place modification: `register_decision` gains an optional `decisions_json_path` parameter.
- `tests/test_canonicalization_apply.py` — extended with `ApplyDecisionIdCanonicalizationTest` class (~14 tests) plus inline `_write_at` and `_write_section` helpers.
- `tests/test_morning_brief_render.py` — extended with `RenderCanonicalizationsAppliedTest` (~4 tests) and `RenderStaleProposalsTableTest` (~3 tests).
- `tests/test_decision_registration.py` — extended with `RegisterDecisionAtomicityTest` (~4 tests).

**NOT modified (out of scope):**
- The orchestrator (does not exist yet).
- Any other test file, the workspace template, the constitution, the existing dataclasses or enums.

---

## Task 1: `register_decision` atomicity (optional `decisions_json_path` argument)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py:624-670` (the existing `register_decision` definition)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registration.py` (append new test class)

- [ ] **Step 1: Append failing atomicity tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_decision_registration.py` (before the `if __name__ == "__main__":` line at the bottom):

```python
class RegisterDecisionAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.goal_path = self.td / "goal.toml"
        self.goal_path.write_text(SEED_GOAL_TOML)

    def test_path_argument_omitted_preserves_existing_behavior(self):
        # No decisions_json_path → behaves exactly like before: no derived file written
        derived_dir = self.td / "derived"
        new_version = cg.register_decision(self.goal_path, [
            {"id": "circuit-breaker-policy",
             "question": "When does the breaker reset?",
             "rationale": "x"},
        ])
        self.assertEqual(new_version, "g-02")
        self.assertFalse(derived_dir.exists(),
                         "derived/ must not be created when path omitted")

    def test_path_argument_provided_writes_decisions_json(self):
        decisions_json = self.td / "derived" / "decisions.json"
        new_version = cg.register_decision(
            self.goal_path,
            [{"id": "circuit-breaker-policy",
              "question": "When does the breaker reset?",
              "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        self.assertEqual(new_version, "g-02")
        self.assertTrue(decisions_json.exists())
        with decisions_json.open() as f:
            payload = json.load(f)
        self.assertEqual(payload["goal_version"], "g-02")
        self.assertIn("circuit-breaker-policy", payload["decisions"])
        self.assertEqual(payload["decisions"]["circuit-breaker-policy"]["status"],
                         "open")

    def test_decisions_json_matches_freshly_loaded_goal_toml(self):
        # decisions.json content equals what load_decisions_from_goal_toml produces
        decisions_json = self.td / "derived" / "decisions.json"
        cg.register_decision(
            self.goal_path,
            [{"id": "a-policy", "question": "?", "rationale": "x"},
             {"id": "b-policy", "question": "?", "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        loaded_from_toml, version_from_toml = cg.load_decisions_from_goal_toml(self.goal_path)
        with decisions_json.open() as f:
            from_json = json.load(f)
        self.assertEqual(from_json["goal_version"], version_from_toml)
        self.assertEqual(
            set(from_json["decisions"].keys()),
            set(loaded_from_toml.keys()),
        )
        for d_id, d in loaded_from_toml.items():
            self.assertEqual(from_json["decisions"][d_id], d.to_dict())

    def test_decisions_json_path_parent_created_if_missing(self):
        # decisions_json_path lives under a non-existent directory
        decisions_json = self.td / "deep" / "nested" / "decisions.json"
        self.assertFalse(decisions_json.parent.exists())
        cg.register_decision(
            self.goal_path,
            [{"id": "x-policy", "question": "?", "rationale": "x"}],
            decisions_json_path=decisions_json,
        )
        self.assertTrue(decisions_json.exists())
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_decision_registration.RegisterDecisionAtomicityTest -v`
Expected: `TypeError: register_decision() got an unexpected keyword argument 'decisions_json_path'` on the three tests that pass the new kwarg; the omitted-arg test will pass with `derived/` absent (existing behavior).

- [ ] **Step 3: Add the optional parameter + post-write reload-and-dump branch**

Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`. Replace the existing `register_decision` definition with:

```python
def register_decision(
    goal_toml_path: _Path,
    new_decisions: list[dict],
    decisions_json_path: _Path | None = None,
) -> str:
    """Append new_decisions to goal.toml, bump goal_version, return new version.

    Each entry in new_decisions: {"id": "...", "question": "...", "rationale": "..."}.
    The rationale is preserved as a comment in goal.toml above the [[decision]] block.

    When `decisions_json_path` is provided, regenerates the derived decisions
    JSON file atomically after writing goal.toml: reloads (decisions, version)
    from the freshly-written goal.toml and dumps them via dump_decisions_to_json.
    Reloading rather than building the in-memory dict avoids drift between the
    TOML text just written and the dataclass shapes constructed in-process.

    Raises SchemaError on: empty new_decisions list, duplicate id (existing or
    within batch), missing required field, invalid slug, or unparseable goal_version.
    """
    if not new_decisions:
        raise SchemaError("register_decision: new_decisions list cannot be empty")
    text = goal_toml_path.read_text()
    existing, _ = load_decisions_from_goal_toml(goal_toml_path)
    seen_ids = set(existing.keys())
    # Validate ALL entries before any mutation
    for entry in new_decisions:
        for req in ("id", "question", "rationale"):
            if req not in entry:
                raise SchemaError(f"register_decision entry missing {req!r}")
        _require_slug(entry["id"], "id")
        if entry["id"] in seen_ids:
            raise SchemaError(
                f"Cannot register {entry['id']!r}: duplicate id "
                "(already in goal.toml or earlier in this batch)"
            )
        seen_ids.add(entry["id"])
    # All validated; now bump version and build blocks
    new_text, new_version = _bump_goal_version(text)
    appended_blocks: list[str] = []
    for entry in new_decisions:
        question_escaped = entry["question"].replace('\\', '\\\\').replace('"', '\\"')
        rationale_escaped = entry["rationale"].replace('\n', ' ')
        block = (
            f'\n# Rationale: {rationale_escaped}\n'
            f'[[decision]]\n'
            f'id = "{entry["id"]}"\n'
            f'question = "{question_escaped}"\n'
            f'status = "open"\n'
            f'introduced_at = "{new_version}"\n'
        )
        appended_blocks.append(block)
    new_text = new_text.rstrip() + "\n" + "".join(appended_blocks)
    goal_toml_path.write_text(new_text)
    if decisions_json_path is not None:
        fresh_decisions, fresh_version = load_decisions_from_goal_toml(goal_toml_path)
        dump_decisions_to_json(fresh_decisions, fresh_version, decisions_json_path)
    return new_version
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_decision_registration.RegisterDecisionAtomicityTest -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ -v 2>&1 | tail -5`
Expected: `Ran 105 tests / OK` (101 existing + 4 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_decision_registration.py
git commit -m "feat(claim_graph): register_decision optional decisions_json_path for atomicity"
```

---

## Task 2: `render_stale_proposals_table`

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append after `render_pending_registry_changes`)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py` (append new test class)

- [ ] **Step 1: Append failing renderer tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py` (before the `if __name__ == "__main__":` line):

```python
class RenderStaleProposalsTableTest(unittest.TestCase):
    def test_empty_renders_no_stale_proposals_line(self):
        out = cg.render_stale_proposals_table([])
        self.assertIn("No stale proposals", out)
        self.assertTrue(out.startswith("## Stale proposals"))

    def test_single_stale_renders_row_with_rounds_since_proposal(self):
        stale = [{
            "decision_id": "circuit-breaker-policy",
            "question": "When does the breaker reset?",
            "rounds_since_proposal": 12,
            "introduced_round": 8,
        }]
        out = cg.render_stale_proposals_table(stale)
        self.assertIn("## Stale proposals", out)
        self.assertIn("circuit-breaker-policy", out)
        self.assertIn("When does the breaker reset?", out)
        self.assertIn("12", out)

    def test_multiple_stale_render_ordered_by_decision_id(self):
        stale = [
            {"decision_id": "z-policy", "question": "?",
             "rounds_since_proposal": 6, "introduced_round": 1},
            {"decision_id": "a-policy", "question": "?",
             "rounds_since_proposal": 9, "introduced_round": 2},
        ]
        out = cg.render_stale_proposals_table(stale)
        # a-policy must appear before z-policy in the rendered output
        self.assertLess(out.index("a-policy"), out.index("z-policy"))
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_morning_brief_render.RenderStaleProposalsTableTest -v`
Expected: `AttributeError: module 'harness.claim_graph' has no attribute 'render_stale_proposals_table'` for all 3 tests.

- [ ] **Step 3: Append the renderer to claim_graph.py**

Use Edit to append at the END of `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:

```python


def render_stale_proposals_table(stale: list[dict]) -> str:
    """Render the Stale proposals section of morning_brief.md.

    Input shape from detect_stale_proposals: each entry is
    {"decision_id", "question", "rounds_since_proposal", "introduced_round"}.
    Returns the friendly empty-state line when stale is empty.
    """
    if not stale:
        return "## Stale proposals\n\nNo stale proposals this run.\n"
    lines = ["## Stale proposals", ""]
    lines.append("| Decision | Question | Rounds since proposal | Introduced round |")
    lines.append("|---|---|---|---|")
    for s in sorted(stale, key=lambda e: e["decision_id"]):
        lines.append(
            f"| {s['decision_id']} | {s['question']} | "
            f"{s['rounds_since_proposal']} | {s['introduced_round']} |"
        )
    lines.append("")
    return "\n".join(lines)
```

Also add `render_stale_proposals_table` to the Morning brief renderers list in the module docstring (`/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py:45-48`). Use Edit:

Old:
```
  Morning brief renderers:
    - render_position_collisions_table
    - render_decisional_asymmetry_table
    - render_pending_registry_changes
```

New:
```
  Morning brief renderers:
    - render_position_collisions_table
    - render_decisional_asymmetry_table
    - render_pending_registry_changes
    - render_stale_proposals_table
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_morning_brief_render.RenderStaleProposalsTableTest -v`
Expected: All 3 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ -v 2>&1 | tail -5`
Expected: `Ran 108 tests / OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_morning_brief_render.py
git commit -m "feat(claim_graph): render_stale_proposals_table for §5.3 morning_brief section"
```

---

## Task 3: `render_canonicalizations_applied`

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append after `render_stale_proposals_table`)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py` (append new test class)

- [ ] **Step 1: Append failing renderer tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_morning_brief_render.py` (before the `if __name__ == "__main__":` line):

```python
class RenderCanonicalizationsAppliedTest(unittest.TestCase):
    def test_empty_both_lists_renders_no_canonicalizations_line(self):
        out = cg.render_canonicalizations_applied([], [])
        self.assertIn("No canonicalizations applied", out)
        self.assertTrue(out.startswith("## Canonicalizations applied"))

    def test_position_rewrites_only_renders_position_subtable(self):
        position_rewrites = [{
            "path": "workspace/variants/nodes/v-001/claims/cl-000001.json",
            "claim_id": "cl-000001",
            "decision_id": "retry-policy",
            "from": "exponential-backoff",
            "to": "expo-backoff",
        }]
        out = cg.render_canonicalizations_applied(position_rewrites, [])
        self.assertIn("Position canonicalizations", out)
        self.assertIn("retry-policy", out)
        self.assertIn("exponential-backoff", out)
        self.assertIn("expo-backoff", out)
        self.assertIn("cl-000001", out)
        # Decision_id sub-table absent
        self.assertNotIn("Decision_id canonicalizations", out)

    def test_decision_id_rewrites_only_renders_decision_id_subtable(self):
        decision_id_rewrites = [{
            "from": "auth-policy",
            "to": "authentication-policy",
            "kind": "claim",
            "paths": [
                "workspace/variants/nodes/v-001/claims/cl-000002.json",
                "workspace/variants/nodes/v-002/claims/cl-000003.json",
            ],
        }]
        out = cg.render_canonicalizations_applied([], decision_id_rewrites)
        self.assertIn("Decision_id canonicalizations", out)
        self.assertIn("auth-policy", out)
        self.assertIn("authentication-policy", out)
        self.assertIn("cl-000002", out)
        self.assertIn("claim", out)
        # Position sub-table absent
        self.assertNotIn("Position canonicalizations", out)

    def test_both_kinds_render_both_subtables(self):
        position_rewrites = [{
            "path": "workspace/variants/nodes/v-001/claims/cl-000001.json",
            "claim_id": "cl-000001", "decision_id": "retry-policy",
            "from": "exponential-backoff", "to": "expo-backoff",
        }]
        decision_id_rewrites = [{
            "from": "auth-policy", "to": "authentication-policy",
            "kind": "section",
            "paths": ["workspace/variants/nodes/v-001/doc/01-auth-policy.md"],
        }]
        out = cg.render_canonicalizations_applied(position_rewrites,
                                                  decision_id_rewrites)
        self.assertIn("Position canonicalizations", out)
        self.assertIn("Decision_id canonicalizations", out)
        self.assertIn("retry-policy", out)
        self.assertIn("auth-policy", out)
        self.assertIn("section", out)
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_morning_brief_render.RenderCanonicalizationsAppliedTest -v`
Expected: `AttributeError: module 'harness.claim_graph' has no attribute 'render_canonicalizations_applied'` for all 4 tests.

- [ ] **Step 3: Append the renderer to claim_graph.py**

Use Edit to append at the END of `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:

```python


def render_canonicalizations_applied(
    position_rewrites: list[dict],
    decision_id_rewrites: list[dict],
) -> str:
    """Render the Canonicalizations applied this round section of morning_brief.md.

    Inputs:
      position_rewrites — records produced by apply_canonicalization, each
        {"path", "claim_id", "decision_id", "from", "to"}.
      decision_id_rewrites — orchestrator-synthesized from
        apply_decision_id_canonicalization's return value, each
        {"from", "to", "kind", "paths"} where kind ∈ {"claim", "attack", "section"}
        and paths is the list of touched files for that (from, to, kind) group.

    Returns the friendly empty-state line when both lists are empty. Otherwise
    renders two sub-tables under a level-2 header, omitting any sub-table whose
    list is empty.
    """
    if not (position_rewrites or decision_id_rewrites):
        return ("## Canonicalizations applied this round\n\n"
                "No canonicalizations applied this run.\n")
    out = ["## Canonicalizations applied this round", ""]
    if position_rewrites:
        out.extend(["### Position canonicalizations", ""])
        out.append("| Decision | From | To | Claim | Path |")
        out.append("|---|---|---|---|---|")
        for r in position_rewrites:
            out.append(
                f"| {r['decision_id']} | {r['from']} | {r['to']} | "
                f"{r['claim_id']} | {r['path']} |"
            )
        out.append("")
    if decision_id_rewrites:
        out.extend(["### Decision_id canonicalizations", ""])
        out.append("| From | To | Kind | Paths |")
        out.append("|---|---|---|---|")
        for r in decision_id_rewrites:
            paths_cell = "; ".join(r["paths"]) if r["paths"] else "(none)"
            out.append(
                f"| {r['from']} | {r['to']} | {r['kind']} | {paths_cell} |"
            )
        out.append("")
    return "\n".join(out)
```

Also extend the Morning brief renderers list in the module docstring with the new entry. Use Edit:

Old:
```
  Morning brief renderers:
    - render_position_collisions_table
    - render_decisional_asymmetry_table
    - render_pending_registry_changes
    - render_stale_proposals_table
```

New:
```
  Morning brief renderers:
    - render_position_collisions_table
    - render_decisional_asymmetry_table
    - render_pending_registry_changes
    - render_stale_proposals_table
    - render_canonicalizations_applied
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_morning_brief_render.RenderCanonicalizationsAppliedTest -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ -v 2>&1 | tail -5`
Expected: `Ran 112 tests / OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_morning_brief_render.py
git commit -m "feat(claim_graph): render_canonicalizations_applied for §5.3 morning_brief section"
```

---

## Task 4: `apply_decision_id_canonicalization` (Flow D apply)

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py` (append: helper regex, helper function, the apply function; also docstring update)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonicalization_apply.py` (append new test class + helpers)

- [ ] **Step 1: Append failing Flow D tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_canonicalization_apply.py` (before the `if __name__ == "__main__":` line):

```python
# ----- Flow D helpers -----

SECTION_TEMPLATE = """+++
section_id = "{section_id}"
created_round = "round-000001"
created_role = "designer"
goal_version = "g-01"
evidence_ids = []
claim_id = "{claim_id}"
tags = [{tags}]
history = []
+++
## {section_id}
Section body.
"""


def _write_cl_flow_d(variant_dir: Path, claim_id: str, decision_id: str,
                     position: str | None = None, section_id: str | None = None,
                     claim_type: str = "decision",
                     proposed_decision: dict | None = None,
                     out_of_scope_rationale: str | None = None):
    """Wider _write_cl that supports independent section_id and proposed_decision."""
    p = variant_dir / "claims"
    p.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": claim_id,
        "section_id": section_id if section_id is not None else decision_id,
        "decision_id": decision_id,
        "claim_type": claim_type,
        "evidence_ids": [],
        "assertion": "x",
    }
    if position is not None:
        payload["position"] = position
    if proposed_decision is not None:
        payload["proposed_decision"] = proposed_decision
    if out_of_scope_rationale is not None:
        payload["out_of_scope_rationale"] = out_of_scope_rationale
    fp = p / f"{claim_id}.json"
    fp.write_text(json.dumps(payload, indent=2))
    return fp


def _write_at(variant_dir: Path, attack_id: str, at_type: str, **fields):
    """Write an at-*.json with arbitrary fields beyond id + at_type."""
    p = variant_dir / "attacks"
    p.mkdir(parents=True, exist_ok=True)
    payload = {"id": attack_id, "at_type": at_type, **fields}
    fp = p / f"{attack_id}.json"
    fp.write_text(json.dumps(payload, indent=2))
    return fp


def _write_section(variant_dir: Path, section_id: str, claim_id: str,
                   tags: list[str]):
    doc = variant_dir / "doc"
    doc.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    text = SECTION_TEMPLATE.format(section_id=section_id, claim_id=claim_id,
                                   tags=tag_str)
    fp = doc / f"01-{section_id}.md"
    fp.write_text(text)
    return fp


def _make_decisions(*ids):
    """Build {id: Decision} dict; all open, introduced_at g-01."""
    return {
        d_id: cg.Decision.from_dict({
            "id": d_id, "question": "?", "status": "open", "introduced_at": "g-01",
        })
        for d_id in ids
    }


class ApplyDecisionIdCanonicalizationTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"
        self.v2 = self.variants / "v-002"

    # ----- File-walk rewrite tests -----

    def test_rewrites_decision_id_in_cl_files(self):
        f1 = _write_cl_flow_d(self.v1, "cl-001", "auth-policy", position="oauth2")
        f2 = _write_cl_flow_d(self.v2, "cl-002", "auth-policy", position="mtls")
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")  # to_id registered
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        for fp in (f1, f2):
            d = json.loads(fp.read_text())
            self.assertEqual(d["decision_id"], "authentication-policy")
        self.assertEqual(len(report["claims_rewritten"]), 2)
        # Each record carries the field that was changed
        decision_id_records = [r for r in report["claims_rewritten"]
                               if r["field"] == "decision_id"]
        self.assertEqual(len(decision_id_records), 2)

    def test_rewrites_section_id_in_cl_files(self):
        # cl-*.json has section_id == from_id; it must be rewritten too
        f1 = _write_cl_flow_d(self.v1, "cl-001", "auth-policy",
                              section_id="auth-policy", position="oauth2")
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        d = json.loads(f1.read_text())
        self.assertEqual(d["section_id"], "authentication-policy")
        section_id_records = [r for r in report["claims_rewritten"]
                              if r["field"] == "section_id"]
        self.assertEqual(len(section_id_records), 1)

    def test_rewrites_proposed_decision_id_in_cl_files(self):
        f1 = _write_cl_flow_d(self.v1, "cl-001", "auth-policy",
                              position="oauth2",
                              proposed_decision={"id": "auth-policy",
                                                 "question": "?", "rationale": "x"})
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        d = json.loads(f1.read_text())
        self.assertEqual(d["proposed_decision"]["id"], "authentication-policy")

    def test_rewrites_target_decision_id_in_at_propose_decision_cut(self):
        f1 = _write_at(self.v1, "at-001", "propose_decision_cut",
                       target_decision_id="auth-policy",
                       rationale="lives elsewhere")
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        d = json.loads(f1.read_text())
        self.assertEqual(d["target_decision_id"], "authentication-policy")
        self.assertEqual(len(report["attacks_rewritten"]), 1)
        self.assertEqual(report["attacks_rewritten"][0]["field"],
                         "target_decision_id")

    def test_rewrites_scope_in_at_propose_canonicalization(self):
        f1 = _write_at(self.v1, "at-002", "propose_canonicalization",
                       kind="position", scope="auth-policy",
                       **{"from": "oauth", "to": "oauth2"},
                       confidence="medium", rationale="x")
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        d = json.loads(f1.read_text())
        self.assertEqual(d["scope"], "authentication-policy")

    def test_rewrites_section_id_in_doc_frontmatter(self):
        f1 = _write_section(self.v1, "auth-policy", "cl-001", ["decided"])
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        text = f1.read_text()
        self.assertIn('section_id = "authentication-policy"', text)
        self.assertNotIn('"auth-policy"', text)
        # Tag NOT changed — Flow D is rename, not retire
        self.assertIn('"decided"', text)
        self.assertEqual(len(report["sections_rewritten"]), 1)

    # ----- Registry mutation tests -----

    def test_moves_registry_entry_from_id_to_to_id(self):
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "auth-policy", "oauth2")
        cg.add_canonical_position(registry, "auth-policy", "exponential")
        cg.register_alias(registry, "auth-policy", "exponential", "oauth2")
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        self.assertNotIn("auth-policy", registry.data)
        self.assertIn("authentication-policy", registry.data)
        self.assertEqual(registry.data["authentication-policy"]["canonical"],
                         ["oauth2"])
        self.assertEqual(registry.data["authentication-policy"]["aliases"],
                         {"exponential": "oauth2"})
        self.assertTrue(report["registry_moved"])

    def test_overwrites_empty_to_id_registry_entry(self):
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "auth-policy", "oauth2")
        registry.ensure_decision("authentication-policy")   # empty pre-created entry
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        self.assertNotIn("auth-policy", registry.data)
        self.assertEqual(registry.data["authentication-policy"]["canonical"],
                         ["oauth2"])
        self.assertTrue(report["registry_moved"])

    def test_raises_when_to_id_registry_entry_non_empty(self):
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "auth-policy", "oauth2")
        cg.add_canonical_position(registry, "authentication-policy", "mtls")
        decisions = _make_decisions("authentication-policy")
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.apply_decision_id_canonicalization(
                self.variants, registry, decisions,
                from_id="auth-policy", to_id="authentication-policy",
            )
        self.assertIn("non-empty", str(cm.exception).lower())

    # ----- Pre-flight rail tests -----

    def test_raises_when_from_equals_to(self):
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("auth-policy")
        with self.assertRaises(cg.SchemaError):
            cg.apply_decision_id_canonicalization(
                self.variants, registry, decisions,
                from_id="auth-policy", to_id="auth-policy",
            )

    def test_raises_when_to_id_not_registered(self):
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions()  # nothing registered
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_decision_id_canonicalization(
                self.variants, registry, decisions,
                from_id="auth-policy", to_id="authentication-policy",
            )
        self.assertIn("not registered", str(cm.exception).lower())

    def test_raises_when_from_id_still_registered(self):
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("auth-policy", "authentication-policy")
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_decision_id_canonicalization(
                self.variants, registry, decisions,
                from_id="auth-policy", to_id="authentication-policy",
            )
        self.assertIn("still registered", str(cm.exception).lower())

    # ----- Idempotency + isolation tests -----

    def test_idempotent_when_from_id_absent_everywhere(self):
        # No cl, no at, no section, no registry entry — return empty report
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy")
        report = cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        self.assertEqual(report["claims_rewritten"], [])
        self.assertEqual(report["attacks_rewritten"], [])
        self.assertEqual(report["sections_rewritten"], [])
        self.assertFalse(report["registry_moved"])

    def test_does_not_touch_other_decision_ids(self):
        # cl-*.json under a DIFFERENT decision_id should not be rewritten
        f_other = _write_cl_flow_d(self.v1, "cl-other", "retry-policy",
                                   position="expo-backoff")
        _write_cl_flow_d(self.v1, "cl-target", "auth-policy",
                         position="oauth2")
        registry = cg.CanonicalSlugRegistry()
        decisions = _make_decisions("authentication-policy", "retry-policy")
        cg.apply_decision_id_canonicalization(
            self.variants, registry, decisions,
            from_id="auth-policy", to_id="authentication-policy",
        )
        d = json.loads(f_other.read_text())
        self.assertEqual(d["decision_id"], "retry-policy")
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_canonicalization_apply.ApplyDecisionIdCanonicalizationTest -v`
Expected: `AttributeError: module 'harness.claim_graph' has no attribute 'apply_decision_id_canonicalization'` on each of the 14 tests.

- [ ] **Step 3: Append the helper regex, helper function, and apply function**

Use Edit to append at the END of `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py`:

```python


# ----- Flow D: decision_id canonicalization apply -----------------------------


_SECTION_ID_LINE_RE = re.compile(
    r'^(\s*section_id\s*=\s*")([^"]+)("\s*)$', re.MULTILINE,
)


def _set_section_id(frontmatter_text: str, new_id: str) -> str:
    """Rewrite the first section_id = "..." line in a frontmatter block."""
    return _SECTION_ID_LINE_RE.sub(rf'\1{new_id}\3', frontmatter_text, count=1)


def apply_decision_id_canonicalization(
    variants_nodes_root: _Path,
    registry: CanonicalSlugRegistry,
    decisions: dict[str, Decision],
    from_id: str,
    to_id: str,
) -> dict:
    """Cascade a decision_id rename across the whole ledger (Flow D apply).

    The caller has observed the human's goal.toml edit + goal_version bump,
    reloaded `decisions` from the new goal.toml, and resolved (from_id, to_id)
    from a queued Flow D canonicalization proposal in pending_goal_changes.md.

    Execution order (rails-first):
      1. Pre-flight rails (raise SchemaError; no filesystem mutation):
         - from_id != to_id
         - both slugs valid
         - to_id in decisions (human's edit completed)
         - from_id not in decisions (human removed the old entry)
      2. Walk variants/nodes/v-*/{claims,attacks}/*.json and v-*/doc/*.md;
         rewrite every field where the old slug appears.
      3. Registry move: registry.data[from_id] → registry.data[to_id].
         Raises RegistryInvariantError if to_id entry already non-empty
         (cannot merge two non-empty registry entries).

    Idempotent no-op: if from_id appears nowhere, returns the report with empty
    lists and registry_moved=False. Does not raise.

    Returns:
        {
          "claims_rewritten":   [{"path", "claim_id", "field", "from", "to"}, ...],
          "attacks_rewritten":  [{"path", "attack_id", "field", "from", "to"}, ...],
          "sections_rewritten": [{"path", "section_id", "from", "to"}, ...],
          "registry_moved":     bool,
        }
    """
    # ----- Rails -----
    if from_id == to_id:
        raise SchemaError(
            f"apply_decision_id_canonicalization: from_id == to_id ({from_id!r}); "
            "nothing to do — caller bug"
        )
    _require_slug(from_id, "from_id")
    _require_slug(to_id, "to_id")
    if to_id not in decisions:
        raise SchemaError(
            f"apply_decision_id_canonicalization: to_id {to_id!r} not registered "
            "in decisions; human must edit goal.toml + bump goal_version first"
        )
    if from_id in decisions:
        raise SchemaError(
            f"apply_decision_id_canonicalization: from_id {from_id!r} still "
            "registered in decisions; human must remove the old [[decision]] "
            "block before this function runs"
        )

    report: dict = {
        "claims_rewritten": [],
        "attacks_rewritten": [],
        "sections_rewritten": [],
        "registry_moved": False,
    }

    # ----- Walk: cl-*.json -----
    if variants_nodes_root.exists():
        for variant_dir in sorted(variants_nodes_root.iterdir()):
            if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
                continue
            claims_dir = variant_dir / "claims"
            if claims_dir.exists():
                for cl_file in sorted(claims_dir.glob("cl-*.json")):
                    with cl_file.open() as f:
                        data = _json.load(f)
                    changed = False
                    rel_path = str(cl_file.relative_to(
                        variants_nodes_root.parent.parent
                    ))
                    if data.get("decision_id") == from_id:
                        data["decision_id"] = to_id
                        report["claims_rewritten"].append({
                            "path": rel_path, "claim_id": data.get("id"),
                            "field": "decision_id", "from": from_id, "to": to_id,
                        })
                        changed = True
                    if data.get("section_id") == from_id:
                        data["section_id"] = to_id
                        report["claims_rewritten"].append({
                            "path": rel_path, "claim_id": data.get("id"),
                            "field": "section_id", "from": from_id, "to": to_id,
                        })
                        changed = True
                    proposed = data.get("proposed_decision")
                    if isinstance(proposed, dict) and proposed.get("id") == from_id:
                        proposed["id"] = to_id
                        report["claims_rewritten"].append({
                            "path": rel_path, "claim_id": data.get("id"),
                            "field": "proposed_decision.id",
                            "from": from_id, "to": to_id,
                        })
                        changed = True
                    if changed:
                        with cl_file.open("w") as f:
                            _json.dump(data, f, indent=2, sort_keys=True)

            # ----- Walk: at-*.json -----
            attacks_dir = variant_dir / "attacks"
            if attacks_dir.exists():
                for at_file in sorted(attacks_dir.glob("at-*.json")):
                    with at_file.open() as f:
                        data = _json.load(f)
                    changed = False
                    rel_path = str(at_file.relative_to(
                        variants_nodes_root.parent.parent
                    ))
                    # propose_decision_cut: target_decision_id
                    if data.get("at_type") == "propose_decision_cut" and \
                       data.get("target_decision_id") == from_id:
                        data["target_decision_id"] = to_id
                        report["attacks_rewritten"].append({
                            "path": rel_path, "attack_id": data.get("id"),
                            "field": "target_decision_id",
                            "from": from_id, "to": to_id,
                        })
                        changed = True
                    # propose_canonicalization kind=position: scope
                    if data.get("at_type") == "propose_canonicalization" and \
                       data.get("kind") == "position" and \
                       data.get("scope") == from_id:
                        data["scope"] = to_id
                        report["attacks_rewritten"].append({
                            "path": rel_path, "attack_id": data.get("id"),
                            "field": "scope", "from": from_id, "to": to_id,
                        })
                        changed = True
                    if changed:
                        with at_file.open("w") as f:
                            _json.dump(data, f, indent=2, sort_keys=True)

            # ----- Walk: doc/*.md frontmatter section_id -----
            doc_dir = variant_dir / "doc"
            if doc_dir.exists():
                for md in sorted(doc_dir.glob("*.md")):
                    text = md.read_text()
                    if not text.startswith("+++"):
                        continue
                    end = text.find("+++", 3)
                    if end == -1:
                        continue
                    frontmatter = text[3:end]
                    body = text[end:]
                    section_id = _section_decision_id(frontmatter)
                    if section_id != from_id:
                        continue
                    new_frontmatter = _set_section_id(frontmatter, to_id)
                    md.write_text("+++" + new_frontmatter + body)
                    report["sections_rewritten"].append({
                        "path": str(md.relative_to(
                            variants_nodes_root.parent.parent
                        )),
                        "section_id": to_id,
                        "from": from_id, "to": to_id,
                    })

    # ----- Registry move (last) -----
    if from_id in registry.data:
        existing_to = registry.data.get(to_id)
        if existing_to is not None:
            if existing_to["canonical"] or existing_to["aliases"]:
                raise RegistryInvariantError(
                    f"Cannot move registry entry {from_id!r} → {to_id!r}: "
                    f"{to_id!r} already has non-empty canonical/aliases "
                    "(cannot merge two non-empty registry entries via "
                    "decision_id canonicalization)"
                )
        registry.data[to_id] = registry.data.pop(from_id)
        report["registry_moved"] = True

    return report
```

Also update the module docstring to advertise the new function. Use Edit on `/Users/liwen/develop/projects/auto_design_doc/harness/claim_graph.py:22-27`:

Old:
```
  Registry mechanics (mutating):
    - add_canonical_position
    - register_alias
    - rewrite_position_to_canonical
    - apply_canonicalization
```

New:
```
  Registry mechanics (mutating):
    - add_canonical_position
    - register_alias
    - rewrite_position_to_canonical
    - apply_canonicalization
    - apply_decision_id_canonicalization
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_canonicalization_apply.ApplyDecisionIdCanonicalizationTest -v`
Expected: All 14 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ -v 2>&1 | tail -5`
Expected: `Ran 126 tests / OK` (101 + 4 + 3 + 4 + 14).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/claim_graph.py tests/test_canonicalization_apply.py
git commit -m "feat(claim_graph): apply_decision_id_canonicalization for Flow D §4.5 cascade"
```

---

## Spec coverage check

| Spec section | Requirement | Implemented in |
|---|---|---|
| §1 gap 1 | `render_canonicalizations_applied` | Task 3 |
| §1 gap 1 | `render_stale_proposals_table` | Task 2 |
| §1 gap 2 | `apply_decision_id_canonicalization` | Task 4 |
| §1 gap 3 | `register_decision` regenerates `decisions.json` atomically | Task 1 |
| §2 | New `apply_decision_id_canonicalization` exported | Task 4 |
| §2 | New `render_canonicalizations_applied` exported | Task 3 |
| §2 | New `render_stale_proposals_table` exported | Task 2 |
| §2 | New optional `decisions_json_path` on `register_decision` | Task 1 |
| §3.1 rails (4) | Pre-flight checks raise SchemaError | Task 4 (Step 3 + rail tests) |
| §3.1 walk order | cl-*.json → at-*.json → doc/*.md | Task 4 (Step 3) |
| §3.1 registry move semantics | Move with empty-overwrite carve-out; raise on non-empty conflict | Task 4 (Step 3 + 3 registry tests) |
| §3.1 idempotent no-op | Empty everywhere → empty report, no raise | Task 4 (idempotency test) |
| §3.2 render shape | Two sub-tables, friendly empty line | Task 3 |
| §3.3 render shape | Sorted by decision_id, friendly empty line | Task 2 |
| §3.4 atomicity | Reload from disk + dump, parent mkdir | Task 1 |
| §4 test counts | ~14 + ~7 + ~4 = ~25 tests | Tasks 1-4 |
| §5 edge cases | `from`/`to` both registered → rail catches | Task 4 (`test_raises_when_from_id_still_registered`) |
| §5 edge cases | Variants dir absent → empty walk, no raise | Task 4 (`test_idempotent_when_from_id_absent_everywhere`) |
| §6 LOC budget | ~130 runtime, ~310 tests | Met (each Step 3 code block sized accordingly) |
| §7 compatibility | `register_decision` change purely additive | Task 1 (existing-behavior test) |
| §8 success criteria 1 | All 126 tests pass | Task 4 Step 5 |
| §8 success criteria 2 | Three new functions exported | Tasks 2, 3, 4 |
| §8 success criteria 3 | Four Flow D rails enforced | Task 4 |
| §8 success criteria 4 | Idempotent no-op | Task 4 |
| §8 success criteria 5 | `register_decision` backward-compatible | Task 1 |
| §8 success criteria 6 | Renderers' friendly empty-state line | Tasks 2 + 3 |
| §10 deferred items | Orchestrator wiring, commit trailers, pre-commit hook | Not in plan (correctly out of scope) |

All in-scope spec requirements have a task.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", or "implement later" entries.
- All function signatures used in tests match the signatures defined in Step 3 code blocks of the same task.
- All field names in test fixtures (`decision_id`, `section_id`, `target_decision_id`, `scope`, `proposed_decision.id`) match the schemas already in `harness/claim_graph.py` (Claim, Attack dataclasses).
- Return-shape keys used in tests (`claims_rewritten`, `attacks_rewritten`, `sections_rewritten`, `registry_moved`, plus the inner `field`/`from`/`to`/`path`/`claim_id`/`attack_id`/`section_id`) match exactly what Step 3's implementation populates.
- Renderer input-shape keys (`decision_id`, `from`, `to`, `claim_id`, `path`, `kind`, `paths`, `question`, `rounds_since_proposal`, `introduced_round`) are consistent between the test inputs and the implementation's field access.
- Helper name consistency: `_write_cl_flow_d` (not `_write_cl`) deliberately differs from `_write_cl` already in `test_canonicalization_apply.py` because the existing `_write_cl` does not support independent `section_id` and `proposed_decision` arguments; the new helper is a superset.
- Path-relativization style (`variants_nodes_root.parent.parent`) matches the convention already used by `apply_canonicalization`.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-24-claim-graph-drift-closure.md`.
