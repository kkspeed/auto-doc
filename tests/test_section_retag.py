import shutil
import tempfile
import unittest
from pathlib import Path

from harness import claim_graph as cg


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


def _write_section(variant_doc_dir: Path, section_id: str, claim_id: str,
                   tags: list[str]):
    variant_doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    text = SECTION_TEMPLATE.format(section_id=section_id, claim_id=claim_id,
                                   tags=tag_str)
    (variant_doc_dir / f"01-{section_id}.md").write_text(text)


def _write_cl(variant_dir: Path, claim_id: str, decision_id: str,
              position: str):
    claims = variant_dir / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    import json
    (claims / f"{claim_id}.json").write_text(json.dumps({
        "id": claim_id, "section_id": decision_id,
        "decision_id": decision_id, "claim_type": "decision",
        "evidence_ids": [], "assertion": "x", "position": position,
    }, indent=2))


class RetagSectionsForRetiredDecisionsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.v1 = self.variants / "v-001"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decided_section_retired_decision_flips_to_unresolved(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["decided"])
        retired = {"retry-policy"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(len(retagged), 1)
        self.assertEqual(retagged[0]["section_id"], "retry-policy")
        text = (self.v1 / "doc" / "01-retry-policy.md").read_text()
        self.assertIn('tags = ["unresolved"]', text)
        self.assertNotIn('"decided"', text)

    def test_decided_section_non_retired_decision_untouched(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["decided"])
        retired = {"some-other-decision"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(retagged, [])
        text = (self.v1 / "doc" / "01-retry-policy.md").read_text()
        self.assertIn('"decided"', text)

    def test_unresolved_section_untouched(self):
        _write_cl(self.v1, "cl-001", "retry-policy", "expo-backoff")
        _write_section(self.v1 / "doc", "retry-policy", "cl-001", ["unresolved"])
        retired = {"retry-policy"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(retagged, [])

    def test_missing_claim_file_logs_and_skips(self):
        # Section references a claim_id that doesn't exist; walker is defensive.
        # Walker should still retag (it works off section_id frontmatter, not claim_id resolution).
        _write_section(self.v1 / "doc", "retry-policy", "cl-missing", ["decided"])
        retired = {"retry-policy"}
        retagged = cg.retag_sections_for_retired_decisions(self.variants, retired)
        self.assertEqual(len(retagged), 1)


if __name__ == "__main__":
    unittest.main()
