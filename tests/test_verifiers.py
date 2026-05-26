import unittest

from harness import verifiers as v


class VerifierFailureDataclassTest(unittest.TestCase):
    def test_minimal_failure_record(self):
        f = v.VerifierFailure(
            kind="uncited-claim",
            variant="v-001",
            section_path="variants/nodes/v-001/doc/01-x.md",
            detail="Sentence has no cite",
        )
        self.assertEqual(f.kind, "uncited-claim")
        self.assertIsNone(f.excerpt_diff)

    def test_failure_with_excerpt_diff(self):
        f = v.VerifierFailure(
            kind="excerpt-mismatch",
            variant="v-001",
            section_path="x",
            detail="ratio 0.3 < threshold 0.92",
            excerpt_diff="--- excerpt\n+++ needle\n-foo\n+bar",
        )
        self.assertEqual(f.excerpt_diff.count("\n"), 3)


class VerifierResultDataclassTest(unittest.TestCase):
    def test_pass_result_has_empty_failures(self):
        r = v.VerifierResult(verdict="pass")
        self.assertEqual(r.failures, [])

    def test_fail_result_carries_failures(self):
        fs = [v.VerifierFailure(kind="uncited-claim", variant="v-001",
                                section_path="x", detail="y")]
        r = v.VerifierResult(verdict="fail", failures=fs)
        self.assertEqual(len(r.failures), 1)


class NormalizeTextTest(unittest.TestCase):
    def test_nfc_normalizes_decomposed_to_composed(self):
        import unicodedata
        # NFD: 'e' (U+0065) + combining acute accent (U+0301)
        decomposed = "caf" + "e" + "́"
        # NFC: precomposed é (U+00E9)
        composed = "café"
        # Guard: confirm the test inputs actually differ in normalization form
        self.assertFalse(unicodedata.is_normalized("NFC", decomposed))
        self.assertTrue(unicodedata.is_normalized("NFC", composed))
        # The normalizer should produce equal output for both
        self.assertEqual(v._normalize_text(decomposed),
                         v._normalize_text(composed))

    def test_smart_quotes_map_to_ascii(self):
        # Left/right double quotes → ASCII "
        self.assertEqual(v._normalize_text("“foo”"), '"foo"')
        # German low-9 + high-reversed-9 double quotes → ASCII "
        self.assertEqual(v._normalize_text("„foo‟"), '"foo"')
        # Left/right single quotes → ASCII '
        self.assertEqual(v._normalize_text("‘bar’"), "'bar'")

    def test_em_dash_and_en_dash_map_to_hyphen(self):
        self.assertEqual(v._normalize_text("a—b–c"), "a-b-c")

    def test_lowercase(self):
        self.assertEqual(v._normalize_text("FOO Bar"), "foo bar")

    def test_whitespace_collapses(self):
        self.assertEqual(v._normalize_text("foo\n  bar\ttab"),
                         "foo bar tab")

    def test_per_word_punctuation_stripped_but_not_interior(self):
        # Leading/trailing punctuation stripped per word; interior preserved.
        # "hello, world's" → words: ["hello,", "world's"] →
        # strip punctuation: ["hello", "world's"] → "hello world's"
        self.assertEqual(v._normalize_text("hello, world's"),
                         "hello world's")


if __name__ == "__main__":
    unittest.main()
