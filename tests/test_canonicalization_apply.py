import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str, position: str):
    p = variant_dir / "claims"
    p.mkdir(parents=True, exist_ok=True)
    fp = p / f"{claim_id}.json"
    fp.write_text(json.dumps({
        "id": claim_id,
        "section_id": decision_id,
        "decision_id": decision_id,
        "claim_type": "decision",
        "evidence_ids": [],
        "assertion": "x",
        "position": position,
    }, indent=2))
    return fp


class ApplyCanonicalizationTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"
        self.v2 = self.variants / "v-002"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_apply_rewrites_slug_in_cl_files(self):
        f1 = _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        f2 = _write_cl(self.v2, "cl-000002", "retry-policy", "expo-backoff")
        # Both slugs are canonical before applying
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        rewrites = cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        # v-001's cl-000001 should now use expo-backoff
        d1 = json.loads(f1.read_text())
        self.assertEqual(d1["position"], "expo-backoff")
        # v-002's cl-000002 unchanged (already canonical)
        d2 = json.loads(f2.read_text())
        self.assertEqual(d2["position"], "expo-backoff")
        # Registry: 'exponential-backoff' moved to aliases
        self.assertNotIn("exponential-backoff", registry.data["retry-policy"]["canonical"])
        self.assertEqual(registry.data["retry-policy"]["aliases"]["exponential-backoff"],
                         "expo-backoff")
        # Rewrites list reports each file changed; path is relative to workspace root
        # (variants_nodes_root.parent.parent), which in this test is self.td
        self.assertEqual(len(rewrites), 1)
        self.assertEqual(rewrites[0]["path"], str(f1.relative_to(self.td)))
        self.assertEqual(rewrites[0]["from"], "exponential-backoff")
        self.assertEqual(rewrites[0]["to"], "expo-backoff")

    def test_apply_does_not_touch_other_decisions(self):
        # A claim under a DIFFERENT decision with same slug should not be rewritten
        _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        other = _write_cl(self.v1, "cl-000002", "auth-strategy", "exponential-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        cg.add_canonical_position(registry, "auth-strategy", "exponential-backoff")
        cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        # auth-strategy claim is unchanged
        d = json.loads(other.read_text())
        self.assertEqual(d["position"], "exponential-backoff")

    def test_apply_invalid_target_raises(self):
        _write_cl(self.v1, "cl-000001", "retry-policy", "exponential-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        # 'expo-backoff' is NOT canonical yet
        with self.assertRaises(cg.RegistryInvariantError):
            cg.apply_canonicalization(
                self.variants, registry, "retry-policy",
                from_slug="exponential-backoff", to_slug="expo-backoff",
            )

    def test_apply_no_matching_claims_succeeds_with_empty_rewrites(self):
        # Registry has both slugs but no cl-*.json files use the 'from' slug
        _write_cl(self.v1, "cl-000001", "retry-policy", "expo-backoff")
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        rewrites = cg.apply_canonicalization(
            self.variants, registry, "retry-policy",
            from_slug="exponential-backoff", to_slug="expo-backoff",
        )
        self.assertEqual(rewrites, [])
        # Registry still updated
        self.assertNotIn("exponential-backoff",
                         registry.data["retry-policy"]["canonical"])


    def test_apply_raises_when_variants_root_missing(self):
        # variants_nodes_root doesn't exist → should refuse, not silently update registry
        registry = cg.CanonicalSlugRegistry()
        cg.add_canonical_position(registry, "retry-policy", "expo-backoff")
        cg.add_canonical_position(registry, "retry-policy", "exponential-backoff")
        missing = self.td / "no-such-variants"
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.apply_canonicalization(
                missing, registry, "retry-policy",
                from_slug="exponential-backoff", to_slug="expo-backoff",
            )
        self.assertIn("does not exist", str(cm.exception))
        # Registry must NOT have been updated
        self.assertIn("exponential-backoff", registry.data["retry-policy"]["canonical"])
        self.assertNotIn("exponential-backoff", registry.data["retry-policy"]["aliases"])


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
        "decision_id": decision_id,
        "claim_type": claim_type,
        "evidence_ids": [],
        "assertion": "x",
    }
    if section_id is not None:
        payload["section_id"] = section_id
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

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

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


if __name__ == "__main__":
    unittest.main()
