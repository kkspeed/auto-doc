import json
import shutil
import tempfile
import unittest
from pathlib import Path

from harness import scorecard


def _write_evidence(evidence_root, ev_id, superseded_by=None):
    evidence_root.mkdir(parents=True, exist_ok=True)
    fm = [f'id = "{ev_id}"']
    if superseded_by is not None:
        fm.append(f'superseded_by = "{superseded_by}"')
    (evidence_root / f"{ev_id}.md").write_text(
        "+++\n" + "\n".join(fm) + "\n+++\n# Claim\nx\n")


def _write_claim(claims_dir, cl_id, evidence_ids):
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / f"{cl_id}.json").write_text(json.dumps({
        "id": cl_id, "evidence_ids": evidence_ids,
    }))


def _write_section(doc_dir, fname, section_id, body):
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / fname).write_text(
        f'+++\nsection_id = "{section_id}"\n+++\n{body}\n')


class GroundednessTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ev = self.td / "evidence"
        self.claims = self.td / "claims"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_claims_is_vacuously_grounded(self):
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 1.0)

    def test_all_claims_resolved(self):
        _write_evidence(self.ev, "ev-000001")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 1.0)

    def test_one_dangling_one_ok(self):
        _write_evidence(self.ev, "ev-000001")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        _write_claim(self.claims, "cl-000002", ["ev-999999"])  # missing
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 0.5)

    def test_superseded_evidence_counts_as_ungrounded(self):
        _write_evidence(self.ev, "ev-000001", superseded_by="ev-000002")
        _write_claim(self.claims, "cl-000001", ["ev-000001"])
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 0.0)


class CompletenessTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.doc = self.td / "doc"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_required_decisions_is_complete(self):
        self.assertEqual(scorecard.compute_completeness([], self.doc), 1.0)

    def test_retired_decisions_excluded(self):
        decisions = [{"id": "a", "status": "retired"}]
        self.assertEqual(
            scorecard.compute_completeness(decisions, self.doc), 1.0)

    def test_half_covered(self):
        _write_section(self.doc, "01-a.md", "a", "## A")
        decisions = [{"id": "a", "status": "open"},
                     {"id": "b", "status": "proposed"}]
        self.assertEqual(
            scorecard.compute_completeness(decisions, self.doc), 0.5)


class CoherenceTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.doc = self.td / "doc"
        self.ev = self.td / "evidence"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_no_citations_is_coherent(self):
        _write_section(self.doc, "01-a.md", "a", "## A\nno cites here")
        self.assertEqual(
            scorecard.compute_coherence(self.doc, self.ev), 1.0)

    def test_dead_ref_lowers_coherence(self):
        _write_evidence(self.ev, "ev-000001")
        _write_section(self.doc, "01-a.md", "a",
                       "## A\ngood [^ev-000001] bad [^ev-999999]")
        self.assertEqual(
            scorecard.compute_coherence(self.doc, self.ev), 0.5)


class ConstitutionComplianceTest(unittest.TestCase):
    def test_no_actions_is_compliant(self):
        self.assertEqual(
            scorecard.compute_constitution_compliance([]), 1.0)

    def test_one_denied_of_four(self):
        actions = [{"denied": False}, {"denied": True},
                   {"denied": False}, {}]
        self.assertEqual(
            scorecard.compute_constitution_compliance(actions), 0.75)


class TechnicalCorrectnessTest(unittest.TestCase):
    def test_vc_absent_uses_reviewer_score(self):
        self.assertEqual(
            scorecard.compute_technical_correctness(0.8, None), 0.8)

    def test_vc_present_penalizes(self):
        # confirm-rate 0.5 over the reviewer's 0.8 -> 0.4
        self.assertAlmostEqual(
            scorecard.compute_technical_correctness(0.8, 0.5), 0.4)

    def test_vc_confirm_rate_empty_is_none(self):
        self.assertIsNone(scorecard.compute_vc_confirm_rate([]))

    def test_vc_confirm_rate_confirm_over_confirm_plus_weak(self):
        per_claim = [{"verdict": "confirm"}, {"verdict": "confirm"},
                     {"verdict": "weak"}]
        self.assertAlmostEqual(
            scorecard.compute_vc_confirm_rate(per_claim), 2 / 3)
