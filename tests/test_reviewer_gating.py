import unittest

from harness import claim_graph as cg


def _verdict(proposed_id: str, verdict: str, rationale: str = "x"):
    return cg.DecisionProposalVerdict.from_dict({
        "proposed_id": proposed_id, "verdict": verdict, "rationale": rationale,
    })


def _proposed(decision_id: str, question: str = "?", rationale: str = "x"):
    return {"id": decision_id, "question": question, "rationale": rationale}


class ApplyReviewerDecisionProposalsTest(unittest.TestCase):
    def test_all_approve_returns_approved_list(self):
        proposals = [_proposed("a-policy"), _proposed("b-policy")]
        verdicts = [_verdict("a-policy", "approve"), _verdict("b-policy", "approve")]
        outcome = cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertEqual(outcome["status"], "all-approved")
        self.assertEqual(len(outcome["approved"]), 2)
        self.assertEqual(outcome["rejected"], [])

    def test_any_reject_fails_round(self):
        proposals = [_proposed("a-policy"), _proposed("b-policy")]
        verdicts = [_verdict("a-policy", "approve"),
                    _verdict("b-policy", "reject", "off-thesis")]
        outcome = cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertEqual(outcome["status"], "any-rejected")
        self.assertEqual(outcome["round_fail_reason"], "proposal-rejected")
        self.assertEqual(len(outcome["rejected"]), 1)
        self.assertEqual(outcome["rejected"][0]["proposed_id"], "b-policy")

    def test_no_proposals_no_verdicts_ok(self):
        outcome = cg.apply_reviewer_decision_proposals([], [])
        self.assertEqual(outcome["status"], "all-approved")
        self.assertEqual(outcome["approved"], [])

    def test_mismatched_verdict_count_raises(self):
        proposals = [_proposed("a-policy")]
        verdicts = []   # missing
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertIn("missing verdict", str(cm.exception).lower())

    def test_verdict_for_unknown_proposal_raises(self):
        proposals = [_proposed("a-policy")]
        verdicts = [_verdict("z-policy", "approve")]
        with self.assertRaises(cg.SchemaError) as cm:
            cg.apply_reviewer_decision_proposals(proposals, verdicts)
        self.assertIn("z-policy", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
