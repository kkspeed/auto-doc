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


import shutil
import tempfile
from pathlib import Path


def _write_section(variants_root, variant, name, tags, body):
    """Write variants/nodes/<variant>/doc/<name>.md with TOML frontmatter."""
    doc_dir = variants_root / variant / "doc"
    doc_dir.mkdir(parents=True, exist_ok=True)
    tag_str = ", ".join(f'"{t}"' for t in tags)
    fp = doc_dir / f"{name}.md"
    fp.write_text(f'+++\nsection_id = "x"\ntags = [{tag_str}]\n+++\n{body}')
    return fp


def _write_evidence(evidence_root, ev_id, excerpt=None, superseded_by=None):
    """Write evidence/ev-<ev_id>.md with TOML frontmatter."""
    evidence_root.mkdir(parents=True, exist_ok=True)
    lines = ["+++", f'id = "ev-{ev_id}"']
    if excerpt is not None:
        # TOML triple-quoted string for multi-line safety
        escaped = excerpt.replace('"""', '\\"\\"\\"')
        lines.append(f'excerpt = """{escaped}"""')
    if superseded_by is not None:
        lines.append(f'superseded_by = "{superseded_by}"')
    lines.append("+++")
    lines.append("")  # body
    fp = evidence_root / f"ev-{ev_id}.md"
    fp.write_text("\n".join(lines))
    return fp


class WalkSectionsTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_walk_finds_sections_across_variants(self):
        _write_section(self.variants, "v-001", "01-a", ["decided"], "body 1\n")
        _write_section(self.variants, "v-002", "01-b", ["unresolved"], "body 2\n")
        found = list(v._walk_sections(self.variants))
        self.assertEqual(len(found), 2)
        # found entries: (variant_name, section_path, tags, body)
        variants_seen = {entry[0] for entry in found}
        self.assertEqual(variants_seen, {"v-001", "v-002"})
        # Verify body content was correctly extracted (leading newline stripped)
        bodies = {entry[0]: entry[3] for entry in found}
        self.assertEqual(bodies["v-001"], "body 1\n")
        self.assertEqual(bodies["v-002"], "body 2\n")
        # Verify section_path uses the variants/nodes/ prefix
        for variant, section_path, _tags, _body in found:
            self.assertTrue(section_path.startswith("variants/nodes/"),
                            f"section_path {section_path!r} should start "
                            f"with 'variants/nodes/'")

    def test_walk_skips_non_variant_directories(self):
        # A subdir not matching v-* should be ignored
        (self.variants / "scratch").mkdir(parents=True)
        (self.variants / "scratch" / "x.md").write_text("not a section")
        _write_section(self.variants, "v-001", "01-a", ["decided"], "body\n")
        found = list(v._walk_sections(self.variants))
        self.assertEqual(len(found), 1)

    def test_walk_skips_files_without_frontmatter(self):
        _write_section(self.variants, "v-001", "01-a", ["decided"], "body\n")
        # A .md file without +++ frontmatter
        bad = self.variants / "v-001" / "doc" / "02-raw.md"
        bad.write_text("Just plain markdown, no frontmatter.\n")
        found = list(v._walk_sections(self.variants))
        self.assertEqual(len(found), 1)

    def test_walk_skips_files_with_malformed_toml(self):
        # Frontmatter present but TOML is malformed
        doc_dir = self.variants / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "01-bad.md").write_text(
            '+++\nthis is = "not [ valid TOML\n+++\nbody\n'
        )
        _write_section(self.variants, "v-001", "02-good", ["decided"], "body\n")
        found = list(v._walk_sections(self.variants))
        self.assertEqual(len(found), 1)
        self.assertIn("02-good", found[0][1])

    def test_walk_returns_empty_when_variants_root_missing(self):
        missing = self.td / "nonexistent" / "variants" / "nodes"
        found = list(v._walk_sections(missing))
        self.assertEqual(found, [])

    def test_walk_returns_empty_when_variant_has_no_doc_dir(self):
        (self.variants / "v-001").mkdir(parents=True)
        # No doc/ subdir
        found = list(v._walk_sections(self.variants))
        self.assertEqual(found, [])

    def test_walk_yields_variants_in_sorted_order(self):
        # Create variants out of alphabetical order; expect sorted iteration
        _write_section(self.variants, "v-003", "01-a", ["decided"], "c\n")
        _write_section(self.variants, "v-001", "01-a", ["decided"], "a\n")
        _write_section(self.variants, "v-002", "01-a", ["decided"], "b\n")
        found = list(v._walk_sections(self.variants))
        variants_in_order = [entry[0] for entry in found]
        self.assertEqual(variants_in_order, ["v-001", "v-002", "v-003"])


if __name__ == "__main__":
    unittest.main()
