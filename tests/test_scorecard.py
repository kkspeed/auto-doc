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

    def test_single_claim_with_one_dangling_id_is_ungrounded(self):
        _write_evidence(self.ev, "ev-000001")
        _write_claim(self.claims, "cl-000001", ["ev-000001", "ev-999999"])
        self.assertEqual(
            scorecard.compute_groundedness(self.claims, self.ev), 0.0)

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


class CapTest(unittest.TestCase):
    """LLM-judged score capped by its mechanical sanity check."""

    def test_none_llm_falls_back_to_mechanical(self):
        self.assertEqual(scorecard._cap(None, 0.0), 0.0)
        self.assertEqual(scorecard._cap(None, 1.0), 1.0)

    def test_clean_mechanical_lets_continuous_llm_through(self):
        # mechanical==1.0 (no objective defect) -> the LLM's continuous score
        # flows through instead of snapping to 1.0.
        self.assertAlmostEqual(scorecard._cap(0.63, 1.0), 0.63)

    def test_objective_defect_caps_generous_llm(self):
        # half the claims ungrounded -> a generous LLM can't exceed 0.5.
        self.assertAlmostEqual(scorecard._cap(0.9, 0.5), 0.5)

    def test_llm_can_be_stricter_than_clean_mechanical(self):
        self.assertAlmostEqual(scorecard._cap(0.2, 1.0), 0.2)


class ComputeDimensionsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ev = self.td / "evidence"
        self.claims = self.td / "claims"
        self.doc = self.td / "doc"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _dims(self, **kw):
        base = dict(
            variant_claims_dir=self.claims, variant_doc_dir=self.doc,
            evidence_root=self.ev, decisions=[], round_actions=[],
            reviewer_goal_alignment=0.7, reviewer_technical_correctness=0.7,
            vc_per_claim=[],
        )
        base.update(kw)
        return scorecard.compute_dimensions(**base)

    def test_reviewer_scores_used_when_mechanically_clean(self):
        # No claims/decisions/cites -> all mechanical dims are 1.0, so the
        # continuous LLM judgments come through unchanged (no 0/1 snapping).
        dims = self._dims(reviewer_groundedness=0.62,
                          reviewer_completeness=0.55,
                          reviewer_coherence=0.71)
        self.assertAlmostEqual(dims["groundedness"], 0.62)
        self.assertAlmostEqual(dims["completeness"], 0.55)
        self.assertAlmostEqual(dims["coherence"], 0.71)

    def test_falls_back_to_mechanical_when_reviewer_omits(self):
        # No reviewer judgments supplied -> prior mechanical behaviour (1.0 on
        # empty inputs).
        dims = self._dims()
        self.assertEqual(dims["groundedness"], 1.0)
        self.assertEqual(dims["completeness"], 1.0)
        self.assertEqual(dims["coherence"], 1.0)

    def test_mechanical_defect_caps_reviewer_groundedness(self):
        # One claim citing nonexistent evidence -> mechanical groundedness 0.0
        # caps a generous reviewer score to 0.0.
        self.claims.mkdir(parents=True)
        (self.claims / "cl-000001.json").write_text(
            json.dumps({"evidence_ids": ["ev-999999"]}))
        dims = self._dims(reviewer_groundedness=0.9)
        self.assertEqual(dims["groundedness"], 0.0)


class GateTest(unittest.TestCase):
    BASE = {d: 0.5 for d in scorecard.DIMENSIONS}

    def test_bootstrap_none_prior_passes(self):
        passed, detail = scorecard.evaluate_gate(None, self.BASE, 0.05)
        self.assertTrue(passed)
        self.assertEqual(detail, "bootstrap")

    def test_improvement_passes(self):
        new = dict(self.BASE, completeness=0.6)
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)

    def test_no_improvement_fails(self):
        passed, detail = scorecard.evaluate_gate(self.BASE, dict(self.BASE),
                                                 0.05)
        self.assertFalse(passed)
        self.assertIn("no dimension improved", detail)

    def test_regression_beyond_tolerance_fails(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.4)  # -0.1 < -0.05
        passed, detail = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertFalse(passed)
        self.assertIn("coherence", detail)
        self.assertIn("0.50->0.40", detail)

    def test_regression_within_tolerance_with_improvement_passes(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.46)  # -0.04 ok
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)

    def test_drop_of_exactly_tolerance_passes(self):
        new = dict(self.BASE, completeness=0.7, coherence=0.45)  # -0.05 exactly
        passed, _ = scorecard.evaluate_gate(self.BASE, new, 0.05)
        self.assertTrue(passed)


class FormatScoreDeltaTest(unittest.TestCase):
    def test_signed_two_decimals_in_dimension_order(self):
        prior = {d: 0.50 for d in scorecard.DIMENSIONS}
        new = dict(prior, groundedness=0.54, technical_correctness=0.48)
        s = scorecard.format_score_delta(prior, new)
        self.assertTrue(s.startswith("groundedness=+0.04 "))
        self.assertIn("technical_correctness=-0.02", s)
        self.assertIn("goal_alignment=+0.00", s)
        # All six dims present, space-separated.
        self.assertEqual(len(s.split()), len(scorecard.DIMENSIONS))


class LoadBuildWriteTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_load_missing_returns_none(self):
        self.assertIsNone(scorecard.load_scorecard(self.td / "nope.json"))

    def test_load_corrupt_json_returns_none(self):
        path = self.td / "bad.json"
        path.write_text("not json {{{")
        self.assertIsNone(scorecard.load_scorecard(path))

    def test_build_write_load_roundtrip(self):
        dims = {d: 0.5 for d in scorecard.DIMENSIONS}
        card = scorecard.build_scorecard("v-001", "round-000002", dims)
        path = self.td / "scorecard.json"
        scorecard.write_scorecard(path, card)
        loaded = scorecard.load_scorecard(path)
        self.assertEqual(loaded["variant"], "v-001")
        self.assertEqual(loaded["round"], "round-000002")
        self.assertEqual(loaded["dimensions"], dims)
