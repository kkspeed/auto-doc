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

    def test_failure_is_immutable(self):
        import dataclasses
        f = v.VerifierFailure(
            kind="uncited-claim",
            variant="v-001",
            section_path="x",
            detail="y",
        )
        # frozen=True causes FrozenInstanceError on assignment
        with self.assertRaises(dataclasses.FrozenInstanceError):
            f.kind = "mutated"


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


def _write_evidence(evidence_root, ev_id, excerpt=None, superseded_by=None,
                    source=None):
    """Write evidence/ev-<ev_id>.md with TOML frontmatter."""
    evidence_root.mkdir(parents=True, exist_ok=True)
    lines = ["+++", f'id = "ev-{ev_id}"']
    if source is not None:
        lines.append(f'source = "{source}"')
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
        for variant, section_path, _tags, _body, _raw_tags in found:
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


class VerifyCitationCompletenessTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decided_section_with_cited_sentences_passes(self):
        body = (
            "Retry policy uses exponential backoff [^ev-000001].\n"
            "Maximum five attempts [^ev-000002].\n"
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.failures, [])

    def test_decided_section_with_uncited_assertion_fails(self):
        body = (
            "Retry policy uses exponential backoff [^ev-000001].\n"
            "We assume the worst case.\n"  # uncited assertion
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "uncited-claim")
        self.assertEqual(result.failures[0].variant, "v-001")

    def test_unresolved_section_with_uncited_assertion_passes(self):
        # Only `decided` sections are checked for completeness
        body = "We assume the worst case.\n"
        _write_section(self.variants, "v-001", "01-x", ["unresolved"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass")

    def test_code_blocks_skipped(self):
        # Sentences inside fenced code blocks don't require cites
        body = (
            "Retry policy uses exponential backoff [^ev-000001].\n"
            "\n"
            "```\n"
            "do_something(); // a comment with periods. and more.\n"
            "```\n"
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass",
                         f"failures: {result.failures}")

    def test_heading_lines_skipped(self):
        # ATX headings don't require cites
        body = (
            "## Heading with a period.\n"
            "Retry policy uses exponential backoff [^ev-000001].\n"
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass")

    def test_metadata_lines_skipped(self):
        # Markdown metadata like "**Status**: draft" / "**Date**: ..." is not an
        # assertion and must not require a cite (the reported false positive).
        body = (
            "**Status**: draft, pre-impl **Date**: 2026-06-08.\n"
            "**Owner**: platform-team\n"
            "Retry policy uses exponential backoff [^ev-000001].\n"
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass",
                         f"failures: {result.failures}")

    def test_metadata_line_does_not_mask_a_real_uncited_claim(self):
        # Stripping metadata must not swallow a following real assertion.
        body = (
            "**Status**: draft.\n"
            "We assume the worst case.\n"  # real uncited assertion
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertIn("worst case", result.failures[0].detail)

    def test_blockquoted_and_bulleted_metadata_skipped(self):
        body = (
            "> **Status**: draft.\n"
            "- **Owner**: platform-team.\n"
            "Retry policy uses exponential backoff [^ev-000001].\n"
        )
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass",
                         f"failures: {result.failures}")

    def test_bold_emphasis_without_colon_still_requires_cite(self):
        # Only "**Label**:" metadata is exempt — emphasized prose is not.
        body = "**Important** the system must retry on failure.\n"
        _write_section(self.variants, "v-001", "01-retry", ["decided"], body)
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(result.failures[0].kind, "uncited-claim")

    def test_empty_body_passes(self):
        _write_section(self.variants, "v-001", "01-empty", ["decided"], "")
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass")

    def test_malformed_tags_emits_warning(self):
        # tags is a string (typo) instead of a list — should emit a
        # malformed-frontmatter failure rather than silently skipping
        doc_dir = self.variants / "v-001" / "doc"
        doc_dir.mkdir(parents=True, exist_ok=True)
        # Hand-write the section to use a bad tags value
        (doc_dir / "01-x.md").write_text(
            '+++\nsection_id = "x"\ntags = "decided"\n+++\nbody [^ev-000001].\n'
        )
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "fail")
        kinds = [f.kind for f in result.failures]
        self.assertIn("malformed-frontmatter", kinds)


class VerifyCiteResolutionTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.evidence = self.td / "evidence"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_cite_to_existing_non_superseded_evidence_passes(self):
        _write_evidence(self.evidence, "000001", excerpt="x")
        body = "Some claim [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass")

    def test_cite_to_missing_evidence_fails_with_dangling_cite_kind(self):
        body = "Some claim [^ev-999999].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "dangling-cite")
        self.assertIn("999999", result.failures[0].detail)

    def test_cite_to_malformed_evidence_frontmatter_fails_with_dangling_cite_kind(self):
        # Evidence file exists but has unparseable TOML frontmatter
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.evidence / "ev-000001.md").write_text(
            '+++\nthis is = "not [ valid TOML\n+++\nbody\n'
        )
        body = "Some claim [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "dangling-cite")
        self.assertIn("malformed frontmatter", result.failures[0].detail)

    def test_cite_to_superseded_evidence_fails_with_superseded_cite_kind(self):
        _write_evidence(self.evidence, "000001", excerpt="x",
                        superseded_by="ev-000002")
        _write_evidence(self.evidence, "000002", excerpt="y")
        body = "Some claim [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "superseded-cite")
        self.assertIn("ev-000002", result.failures[0].detail)

    def test_multiple_cites_mix_of_pass_and_fail_collects_all_failures(self):
        _write_evidence(self.evidence, "000001", excerpt="x")
        # 000002 missing; 000003 superseded
        _write_evidence(self.evidence, "000003", excerpt="z",
                        superseded_by="ev-000004")
        body = (
            "Good cite [^ev-000001].\n"
            "Missing [^ev-000002].\n"
            "Superseded [^ev-000003].\n"
        )
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 2)
        kinds = sorted(f.kind for f in result.failures)
        self.assertEqual(kinds, ["dangling-cite", "superseded-cite"])

    def test_unresolved_section_cites_still_checked(self):
        # Cite-resolution applies to ALL sections, not just decided.
        body = "Some claim [^ev-999999].\n"
        _write_section(self.variants, "v-001", "01-x", ["unresolved"], body)
        result = v.verify_cite_resolution(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")


class VerifyExcerptMatchTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"
        self.evidence = self.td / "evidence"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_verbatim_match_passes(self):
        excerpt = "Retry policy uses exponential backoff with full jitter."
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = "Retry policy uses exponential backoff with full jitter [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")

    def test_smart_quote_drift_passes_after_normalization(self):
        # Excerpt has straight quotes; doc has smart quotes; should still match.
        excerpt = 'The system uses "exponential backoff" with jitter.'
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = 'The system uses “exponential backoff” with jitter [^ev-000001].\n'
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")

    def test_truly_different_sentence_fails_with_excerpt_diff(self):
        excerpt = "Use TCP keepalive with a 30-second interval."
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = "Retry policy uses exponential backoff with full jitter [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "excerpt-mismatch")
        self.assertIsNotNone(result.failures[0].excerpt_diff)

    def test_evidence_missing_excerpt_field_fails(self):
        # Evidence file with no excerpt= field
        _write_evidence(self.evidence, "000001")  # no excerpt passed
        body = "Some claim [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(result.failures[0].kind, "excerpt-mismatch")
        self.assertIn("no usable excerpt", result.failures[0].detail)

    def test_threshold_parameter_respected(self):
        # Partial coverage: about two-thirds of the excerpt appears in the doc
        # paragraph; the rest ("and a circuit breaker") does not. Passes at a
        # permissive threshold, fails at a strict one.
        excerpt = "exponential backoff with full jitter and a circuit breaker"
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = ("The retry policy uses exponential backoff with full jitter "
                "for transient failures [^ev-000001].\n")
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        permissive = v.verify_excerpt_match(self.variants, self.evidence,
                                            threshold=0.5)
        strict = v.verify_excerpt_match(self.variants, self.evidence,
                                        threshold=0.99)
        self.assertEqual(permissive.verdict, "pass")
        self.assertEqual(strict.verdict, "fail")

    def test_verbatim_quote_with_surrounding_prose_passes(self):
        # The regression: a faithful verbatim quote wrapped in lots of doc prose.
        # The old symmetric ratio scored this ~0.26 (rejected at 0.92); coverage
        # scores ~1.0 because the doc's extra prose no longer counts against it.
        excerpt = "exponential backoff with full jitter"
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = ("The retry subsystem is designed for resilience under transient "
                "network failures. It uses exponential backoff with full jitter "
                "to avoid thundering-herd retries, and caps the total number of "
                "attempts to bound tail latency [^ev-000001].\n")
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)  # default 0.6
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")

    def test_repo_sourced_evidence_is_exempt(self):
        # A repo-adapter code excerpt the prose can't quote — exempt from
        # text-matching (Verifier C owns it).
        excerpt = "for attempt in range(self.max_retries):\n    time.sleep(base * 2 ** attempt)"
        _write_evidence(self.evidence, "000001", excerpt=excerpt, source="repo")
        body = "The client retries with bounded, backed-off attempts [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")

    def test_inline_evidence_same_mismatch_still_fails(self):
        # Identical mismatch but WITHOUT a source field (designer-inline) is not
        # exempt — confirms the exemption is source-gated, not blanket.
        excerpt = "for attempt in range(self.max_retries):\n    time.sleep(base * 2 ** attempt)"
        _write_evidence(self.evidence, "000001", excerpt=excerpt)  # no source
        body = "The client retries with bounded, backed-off attempts [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(result.failures[0].kind, "excerpt-mismatch")

    def test_punctuation_only_excerpt_is_no_usable_excerpt(self):
        # Normalizes to empty -> "no usable excerpt", not a ZeroDivisionError.
        _write_evidence(self.evidence, "000001", excerpt="... ?! --")
        body = "Some claim [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "fail")
        self.assertIn("no usable excerpt", result.failures[0].detail)

    def test_multiple_cites_in_one_sentence_each_checked_independently(self):
        _write_evidence(self.evidence, "000001",
                        excerpt="Retry policy uses exponential backoff with full jitter.")
        _write_evidence(self.evidence, "000002",
                        excerpt="Use TCP keepalive with a 30-second interval.")
        # One sentence cites both: first cite matches, second doesn't.
        body = (
            "Retry policy uses exponential backoff with full jitter "
            "[^ev-000001] [^ev-000002].\n"
        )
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        # ev-000001 matches; ev-000002 mismatches → exactly 1 failure
        self.assertEqual(len(result.failures), 1)
        self.assertIn("000002", result.failures[0].detail)

    def test_cite_at_start_of_paragraph_extracts_sentence_correctly(self):
        excerpt = "First sentence in paragraph."
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = (
            "Some preamble.\n"
            "\n"
            "First sentence in paragraph [^ev-000001]. Second sentence.\n"
        )
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")

    def test_cite_with_no_surrounding_punctuation_falls_back_to_paragraph_as_needle(self):
        # No sentence-ending punctuation in the paragraph at all
        excerpt = "Single fragment with no terminator"
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = "Single fragment with no terminator [^ev-000001]"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        result = v.verify_excerpt_match(self.variants, self.evidence)
        self.assertEqual(result.verdict, "pass", f"failures: {result.failures}")


class FrontmatterWellformedTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.variants = self.td / "variants" / "nodes"

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_list_tags_passes(self):
        _write_section(self.variants, "v-001", "01-a", ["decided"], "body\n")
        result = v.verify_frontmatter_wellformed(self.variants)
        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.failures, [])

    def test_tags_not_a_list_fails(self):
        doc = self.variants / "v-001" / "doc"
        doc.mkdir(parents=True)
        (doc / "01-a.md").write_text(
            '+++\nsection_id = "x"\ntags = "decided"\n+++\nbody\n')
        result = v.verify_frontmatter_wellformed(self.variants)
        self.assertEqual(result.verdict, "fail")
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].kind, "malformed-frontmatter")

    def test_missing_variants_root_passes(self):
        result = v.verify_frontmatter_wellformed(self.td / "nope")
        self.assertEqual(result.verdict, "pass")


if __name__ == "__main__":
    unittest.main()
