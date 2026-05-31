# Verifiers A + B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement orchestrator sub-project 2 per [2026-05-25-verifiers-a-and-b-design.md](../specs/2026-05-25-verifiers-a-and-b-design.md): a new `harness/verifiers.py` module with three pure verifier functions (`verify_citation_completeness`, `verify_cite_resolution`, `verify_excerpt_match`) plus a `_normalize_text` helper, all returning structured `VerifierResult`/`VerifierFailure` dataclasses. Adds ~25 tests across 4 test classes.

**Architecture:** Single self-contained module, stdlib only. Each verifier walks `variants/nodes/v-*/doc/*.md`, parses TOML frontmatter via `tomllib`, and returns aggregated failure records (no early exit, no exceptions). The orchestrator (sub-project 4) decides what to do with the records. `_normalize_text` is shared by the public test surface and Verifier B; Verifier A doesn't normalize (cite resolution is byte-equality).

**Tech Stack:** Python 3.11+ stdlib only (`dataclasses`, `pathlib`, `re`, `tomllib`, `unicodedata`, `string`, `difflib`, `unittest`).

---

## File Structure

**Created in this plan:**
- `harness/verifiers.py` — dataclasses + 4 public functions + 1 internal helper (~230 LOC)
- `tests/test_verifiers.py` — 4 test classes, ~25 tests (~340 LOC)

**NOT modified:**
- `harness/claim_graph.py`, `harness/cli.py`, `harness/__main__.py` — untouched.
- `workspace_template/*`, including hooks — untouched.
- Existing tests — untouched.

---

## Task 1: Dataclasses + `_normalize_text` helper

This task lands the smallest, most independently-testable piece: the data shapes plus the text normalization function. Verifier B depends on `_normalize_text`; the dataclasses are referenced by all three verifiers. Doing this first lets later tasks consume stable building blocks.

**Files:**
- Create: `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`
- Create: `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py`

- [ ] **Step 1: Write failing dataclass + normalize tests**

Write `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py`:

```python
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
        # "cafe" + combining acute → composed "café"; result must match a
        # naturally-composed "café".
        decomposed = "café"
        composed = "café"
        self.assertEqual(v._normalize_text(decomposed),
                         v._normalize_text(composed))

    def test_smart_quotes_map_to_ascii(self):
        # All four smart-double-quote variants → ASCII "
        self.assertEqual(v._normalize_text("“foo”"), '"foo"')
        # left/right single → '
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers -v`
Expected: `ModuleNotFoundError: No module named 'harness.verifiers'`.

- [ ] **Step 3: Create `harness/verifiers.py` with dataclasses + `_normalize_text`**

Write `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`:

```python
"""Verifier A + Verifier B for the Design Doc Evolution Harness.

Pure-Python verifiers that operate on the on-disk variants directory.
The orchestrator (sub-project 4) calls these between the designer's
write and the commit; structured failures get written as rj-*.md.

Public API:
  Schemas:
    - VerifierFailure
    - VerifierResult

  Verifier A (cite enforcement, parent design SC4):
    - verify_citation_completeness  — every decided-section assertion has a cite
    - verify_cite_resolution        — every cite resolves to non-superseded evidence

  Verifier B (excerpt match, parent design SC5):
    - verify_excerpt_match          — each cite's sentence matches the cited excerpt

  Internal (exposed for unit testing):
    - _normalize_text
"""
from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass, field


# ----- Dataclasses ------------------------------------------------------------


@dataclass
class VerifierFailure:
    kind: str           # "uncited-claim" | "dangling-cite" | "superseded-cite" | "excerpt-mismatch"
    variant: str        # "v-001"
    section_path: str   # "variants/nodes/v-001/doc/01-retry-policy.md"
    detail: str         # human-readable explanation
    excerpt_diff: str | None = None   # populated for excerpt-mismatch only


@dataclass
class VerifierResult:
    verdict: str        # "pass" | "fail"
    failures: list[VerifierFailure] = field(default_factory=list)


# ----- Text normalization (Verifier B helper) ---------------------------------


_SMART_MAP = str.maketrans({
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "„": '"',   # German low-9 quote
    "‟": '"',   # high-reversed-9 quote
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "—": "-",   # em dash
    "–": "-",   # en dash
})

_WS_RE = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    """Six-step normalization for Verifier B's difflib comparison.

    Order matters: NFC first so smart quotes are composed, then map them
    to ASCII, then lowercase, then collapse whitespace, then strip leading
    and trailing punctuation from each word (interior punctuation, like
    apostrophes inside contractions, is preserved).
    """
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_SMART_MAP)
    s = s.lower()
    s = _WS_RE.sub(" ", s).strip()
    s = " ".join(w.strip(string.punctuation) for w in s.split(" "))
    return s
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers -v`
Expected: 10 tests pass (plan prose previously said 8, but the test code block has 10 methods — 2+2+6 across the three test classes).

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 186 tests / OK` (176 existing + 10 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): dataclasses + _normalize_text for Verifier A/B"
```

---

## Task 2: Shared walk helper + test fixtures

Both verifiers walk `variants/nodes/v-*/doc/*.md` and parse TOML frontmatter the same way. Factor that out before adding the verifiers themselves so we don't duplicate the walk three times.

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (append)

- [ ] **Step 1: Append failing walker test + section fixture helper**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (before the `if __name__ == "__main__":` line):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.WalkSectionsTest -v`
Expected: `AttributeError: module 'harness.verifiers' has no attribute '_walk_sections'`.

- [ ] **Step 3: Append `_walk_sections` to `harness/verifiers.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`:

```python


# ----- Section walker ---------------------------------------------------------


import tomllib
from pathlib import Path


def _walk_sections(variants_nodes_root: Path):
    """Yield (variant_name, section_path, tags, body) for every well-formed
    section under variants_nodes_root.

    Skips silently when:
      - variants_nodes_root does not exist
      - a variant has no doc/ subdirectory
      - a file lacks +++ frontmatter fence
      - the frontmatter fails to parse as TOML

    section_path is a string relative to variants_nodes_root.parent.parent
    (i.e., starts with "variants/nodes/...").
    """
    if not variants_nodes_root.exists():
        return
    for variant_dir in sorted(variants_nodes_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        doc_dir = variant_dir / "doc"
        if not doc_dir.exists():
            continue
        for md in sorted(doc_dir.glob("*.md")):
            text = md.read_text()
            if not text.startswith("+++"):
                continue
            end = text.find("+++", 3)
            if end == -1:
                continue
            frontmatter_text = text[3:end]
            body = text[end + 3:]
            # Strip leading newline from body if present (TOML fence semantics)
            if body.startswith("\n"):
                body = body[1:]
            try:
                meta = tomllib.loads(frontmatter_text)
            except tomllib.TOMLDecodeError:
                continue
            tags = meta.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            rel_path = str(md.relative_to(variants_nodes_root.parent.parent))
            yield variant_dir.name, rel_path, tags, body
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.WalkSectionsTest -v`
Expected: All 6 walk tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 192 tests / OK` (186 + 6).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): _walk_sections helper + section/evidence test fixtures"
```

---

## Task 3: `verify_citation_completeness`

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (append)

- [ ] **Step 1: Append failing citation-completeness tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (before the `if __name__ == "__main__":` line):

```python
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

    def test_empty_body_passes(self):
        _write_section(self.variants, "v-001", "01-empty", ["decided"], "")
        result = v.verify_citation_completeness(self.variants)
        self.assertEqual(result.verdict, "pass")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyCitationCompletenessTest -v`
Expected: `AttributeError: module 'harness.verifiers' has no attribute 'verify_citation_completeness'`.

- [ ] **Step 3: Append `verify_citation_completeness` to `harness/verifiers.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`:

```python


# ----- Verifier A.1: citation completeness ------------------------------------


_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)
_HEADING_LINE_RE = re.compile(r"^#{1,6}(\s.*)?$", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?:\s+|\Z)")
_CITE_RE = re.compile(r"\[\^ev-\d{6}\]")
_LETTER_RE = re.compile(r"[A-Za-z]")


def verify_citation_completeness(variants_nodes_root: Path) -> VerifierResult:
    """Every assertion sentence in a `decided` section must have at least one
    [^ev-NNNNNN] cite.
    """
    failures: list[VerifierFailure] = []
    for variant, section_path, tags, body in _walk_sections(variants_nodes_root):
        if "decided" not in tags:
            continue
        prose = _FENCED_CODE_RE.sub("", body)
        prose = _HEADING_LINE_RE.sub("", prose)
        for sentence in _SENTENCE_SPLIT_RE.split(prose):
            sentence = sentence.strip()
            if not sentence:
                continue
            # Must end with sentence-final punctuation AND contain at least
            # one letter (to skip pure-punctuation fragments).
            if not sentence.endswith((".", "!", "?")):
                continue
            if not _LETTER_RE.search(sentence):
                continue
            if _CITE_RE.search(sentence):
                continue
            preview = sentence[:80].replace("\n", " ")
            failures.append(VerifierFailure(
                kind="uncited-claim",
                variant=variant,
                section_path=section_path,
                detail=f"Sentence ends '{preview}' but has no [^ev-*] cite",
            ))
    return VerifierResult(
        verdict="fail" if failures else "pass",
        failures=failures,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyCitationCompletenessTest -v`
Expected: All 6 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 199 tests / OK` (193 + 6).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): verify_citation_completeness (SC4 part 1)"
```

---

## Task 4: `verify_cite_resolution`

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (append)

- [ ] **Step 1: Append failing cite-resolution tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (before the `if __name__ == "__main__":` line):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyCiteResolutionTest -v`
Expected: `AttributeError: module 'harness.verifiers' has no attribute 'verify_cite_resolution'`.

- [ ] **Step 3: Append `verify_cite_resolution` to `harness/verifiers.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`:

```python


# ----- Verifier A.2: cite resolution ------------------------------------------


def _load_evidence_frontmatter(ev_path: Path) -> dict | None:
    """Read an ev-*.md file and return its parsed TOML frontmatter dict, or
    None if the file is missing, lacks a +++ fence, or fails to parse."""
    if not ev_path.exists():
        return None
    text = ev_path.read_text()
    if not text.startswith("+++"):
        return None
    end = text.find("+++", 3)
    if end == -1:
        return None
    try:
        return tomllib.loads(text[3:end])
    except tomllib.TOMLDecodeError:
        return None


def verify_cite_resolution(
    variants_nodes_root: Path,
    evidence_root: Path,
) -> VerifierResult:
    """Every [^ev-NNNNNN] cite must resolve to an existing non-superseded
    evidence file. Applies to ALL sections, not just decided.
    """
    failures: list[VerifierFailure] = []
    cite_re = re.compile(r"\[\^ev-(\d{6})\]")
    for variant, section_path, _tags, body in _walk_sections(variants_nodes_root):
        for m in cite_re.finditer(body):
            ev_num = m.group(1)
            ev_path = evidence_root / f"ev-{ev_num}.md"
            meta = _load_evidence_frontmatter(ev_path)
            if meta is None:
                failures.append(VerifierFailure(
                    kind="dangling-cite",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} not found at {ev_path} "
                           "(or has malformed frontmatter)",
                ))
                continue
            superseded_by = meta.get("superseded_by")
            if superseded_by:
                failures.append(VerifierFailure(
                    kind="superseded-cite",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} is superseded_by={superseded_by!r}",
                ))
    return VerifierResult(
        verdict="fail" if failures else "pass",
        failures=failures,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyCiteResolutionTest -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 204 tests / OK` (199 + 5).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): verify_cite_resolution (SC4 part 2)"
```

---

## Task 5: `verify_excerpt_match`

**Files:**
- Modify: `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py` (append)
- Modify: `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (append)

- [ ] **Step 1: Append failing excerpt-match tests**

Append to `/Users/liwen/develop/projects/auto_design_doc/tests/test_verifiers.py` (before the `if __name__ == "__main__":` line):

```python
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
        self.assertIn("no excerpt", result.failures[0].detail)

    def test_threshold_parameter_respected(self):
        # Same fixture: short excerpt, longer body sentence — moderate match.
        excerpt = "exponential backoff with full jitter"
        _write_evidence(self.evidence, "000001", excerpt=excerpt)
        body = "The retry policy uses exponential backoff with full jitter for transient failures [^ev-000001].\n"
        _write_section(self.variants, "v-001", "01-x", ["decided"], body)
        permissive = v.verify_excerpt_match(self.variants, self.evidence,
                                            threshold=0.5)
        strict = v.verify_excerpt_match(self.variants, self.evidence,
                                        threshold=0.99)
        self.assertEqual(permissive.verdict, "pass")
        self.assertEqual(strict.verdict, "fail")

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyExcerptMatchTest -v`
Expected: `AttributeError: module 'harness.verifiers' has no attribute 'verify_excerpt_match'`.

- [ ] **Step 3: Append `verify_excerpt_match` to `harness/verifiers.py`**

Use Edit to append to the end of `/Users/liwen/develop/projects/auto_design_doc/harness/verifiers.py`:

```python


# ----- Verifier B: excerpt match ----------------------------------------------


import difflib

_SENTENCE_BOUNDARY_BACK_RE = re.compile(r"[.!?]|\n\n")


def _extract_sentence_containing(body: str, pos: int) -> str:
    """Return the sentence in `body` containing the character at offset `pos`.

    Scans backward to the most recent sentence-end punctuation or paragraph
    break (or start of body) and forward to the next sentence-end punctuation
    or paragraph break (or end of body). If no boundary is found in either
    direction, the entire paragraph containing pos is returned as a fallback.
    """
    # Backward scan
    start = 0
    for i in range(pos - 1, -1, -1):
        ch = body[i]
        if ch in ".!?":
            start = i + 1
            break
        if ch == "\n" and i > 0 and body[i - 1] == "\n":
            start = i + 1
            break
    # Forward scan
    end = len(body)
    i = pos
    while i < len(body):
        ch = body[i]
        if ch in ".!?":
            end = i + 1
            break
        if ch == "\n" and i + 1 < len(body) and body[i + 1] == "\n":
            end = i
            break
        i += 1
    needle = body[start:end].strip()
    if needle:
        return needle
    # Fallback: entire paragraph
    para_start = body.rfind("\n\n", 0, pos)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", pos)
    para_end = len(body) if para_end == -1 else para_end
    return body[para_start:para_end].strip()


def verify_excerpt_match(
    variants_nodes_root: Path,
    evidence_root: Path,
    threshold: float = 0.92,
) -> VerifierResult:
    """For each [^ev-NNNNNN] cite, normalize the surrounding sentence and the
    cited evidence file's `excerpt` field, then compute a difflib ratio.
    Fail if ratio < threshold.

    Skips dangling/malformed evidence files silently — those are owned by
    verify_cite_resolution. This avoids double-reporting on the same cite.
    """
    failures: list[VerifierFailure] = []
    cite_re = re.compile(r"\[\^ev-(\d{6})\]")
    for variant, section_path, _tags, body in _walk_sections(variants_nodes_root):
        for m in cite_re.finditer(body):
            ev_num = m.group(1)
            ev_path = evidence_root / f"ev-{ev_num}.md"
            meta = _load_evidence_frontmatter(ev_path)
            if meta is None:
                continue   # owned by verify_cite_resolution
            excerpt = meta.get("excerpt")
            if not excerpt:
                failures.append(VerifierFailure(
                    kind="excerpt-mismatch",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} frontmatter has no excerpt field",
                ))
                continue
            needle = _extract_sentence_containing(body, m.start())
            n_needle = _normalize_text(needle)
            n_excerpt = _normalize_text(excerpt)
            ratio = difflib.SequenceMatcher(None, n_needle, n_excerpt).ratio()
            if ratio < threshold:
                diff = "\n".join(difflib.unified_diff(
                    n_excerpt.splitlines() or [""],
                    n_needle.splitlines() or [""],
                    fromfile="excerpt", tofile="needle",
                    lineterm="", n=1,
                ))
                failures.append(VerifierFailure(
                    kind="excerpt-mismatch",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} excerpt match ratio={ratio:.3f} "
                           f"below threshold {threshold}",
                    excerpt_diff=diff,
                ))
    return VerifierResult(
        verdict="fail" if failures else "pass",
        failures=failures,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest tests.test_verifiers.VerifyExcerptMatchTest -v`
Expected: All 8 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: `Ran 213 tests / OK` (205 + 8).

- [ ] **Step 6: Commit**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "feat(verifiers): verify_excerpt_match (SC5)"
```

---

## Task 6: `/code-review` pass over the sub-project

This task is a gate, not a feature. After Tasks 1–5 are done and per-task reviews have all passed, dispatch a `/code-review` subagent over the full sub-project 2 diff to surface anything the per-task reviews missed.

**Files:** none modified directly. Findings (if any) are addressed in a follow-up commit per the reviewer's recommendation.

- [ ] **Step 1: Capture the sub-project base SHA**

Before Task 1 begins, capture the base SHA so we can review the whole sub-project's diff at once:

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git rev-parse HEAD
```

Record the SHA — this is the `BASE_SHA` for the review. The plan executor should write this down before starting Task 1.

- [ ] **Step 2: Dispatch the `/code-review` subagent**

After Task 5's commit lands, dispatch a subagent that invokes `/code-review` at high effort over the range `BASE_SHA..HEAD`. Use the following prompt (substitute `<BASE_SHA>` with the captured value):

```
You are running a /code-review on orchestrator sub-project 2 (Verifiers A + B).

Invoke the `/code-review` skill at effort=high over the commit range
`<BASE_SHA>..HEAD` in /Users/liwen/develop/projects/auto_design_doc.

Sub-project 2 ships a new module harness/verifiers.py implementing the
parent design's SC4 (cite enforcement) and SC5 (excerpt match) as
pure-Python library functions. The spec is at
docs/superpowers/specs/2026-05-25-verifiers-a-and-b-design.md.

Look for correctness bugs, edge cases the test fixtures don't exercise,
security/safety issues, and design concerns (e.g., the sentence-splitting
heuristic's failure modes on real prose, the excerpt-mismatch diff format
under multi-line excerpts, the interaction between cite-resolution and
excerpt-match for malformed evidence files).

Per-task reviews have already covered: dataclass shape, normalize_text
unicode handling, walk-helper file skipping, sentence-split happy paths,
cite-resolution kinds, threshold semantics. Surface issues BEYOND those.

Return a single review report with Critical/Important/Minor sections.
File:line refs and concrete fix suggestions for each.
```

- [ ] **Step 3: Triage findings**

If the review returns:
- **Critical:** address inline before declaring the sub-project complete.
- **Important:** address inline if the fix is small (≤30 LOC); otherwise defer to a follow-up task with explicit justification.
- **Minor:** record in a note for batched cleanup with the deferred sub-project 1 findings; do not block on these.

If no Critical or Important findings: proceed to Step 4.

- [ ] **Step 4: Commit any inline fixes**

```bash
cd /Users/liwen/develop/projects/auto_design_doc
git add harness/verifiers.py tests/test_verifiers.py
git commit -m "fix(verifiers): address /code-review findings from sub-project 2 pass"
```

If no fixes were needed, skip this step.

- [ ] **Step 5: Run the full suite**

Run: `cd /Users/liwen/develop/projects/auto_design_doc && python3 -m unittest discover tests/ 2>&1 | tail -3`
Expected: at minimum 213 tests pass; possibly more if the review added regression tests.

---

## Spec coverage check

| Spec section | Requirement | Implemented in |
|---|---|---|
| §2 — VerifierFailure dataclass | Task 1 |
| §2 — VerifierResult dataclass | Task 1 |
| §2 — verify_citation_completeness | Task 3 |
| §2 — verify_cite_resolution | Task 4 |
| §2 — verify_excerpt_match | Task 5 |
| §2 — _normalize_text | Task 1 |
| §3.1 — walk semantics (TOML frontmatter, skip on parse error, sorted iteration) | Task 2 (`_walk_sections`) |
| §3.2 — citation completeness (decided-only, code-block strip, heading skip, assertion detection) | Task 3 |
| §3.3 — cite resolution (all sections, dangling vs superseded kinds) | Task 4 |
| §3.4 — excerpt match (sentence extraction, threshold, normalize, difflib, diff field) | Task 5 |
| §3.4 — cite-resolution silently skips dangling for excerpt-match (no double-report) | Task 5 (the `if meta is None: continue` branch) |
| §3.5 — `_normalize_text` 5-step pipeline | Task 1 |
| §3.6 — aggregated failures, no early exit | Tasks 3/4/5 |
| §4 — all 4 test classes, ~25 tests | Tasks 1/3/4/5 |
| §5 edge cases — missing variants dir, no doc/ subdir, malformed TOML | Task 2 walk tests |
| §5 edge cases — multiple cites same id, threshold extremes | Tasks 4/5 |
| §8 success criteria — module importable, < 250 LOC, all tests pass | Verified by Step 5 of each task and Task 6 |
| /code-review gate | Task 6 |

All in-scope spec items have a task. Two spec items are partially deferred to Task 6's review pass: edge case verification for "cite at start of paragraph" and "cite with no surrounding punctuation" are covered by Task 5 tests but the reviewer may flag additional edge cases not anticipated here.

---

## Placeholder + type consistency self-check

- No "TODO", "TBD", "implement later" entries.
- Function signatures used across tasks match definitions exactly:
  - `_walk_sections(variants_nodes_root)` defined in Task 2, called in Tasks 3/4/5.
  - `_normalize_text(s)` defined in Task 1, called in Task 5.
  - `_load_evidence_frontmatter(ev_path)` defined in Task 4, called in Task 5.
  - `_extract_sentence_containing(body, pos)` defined in Task 5.
  - `verify_citation_completeness(variants_nodes_root)` — single arg, no evidence dir (it only checks for cite presence, not resolution).
  - `verify_cite_resolution(variants_nodes_root, evidence_root)` — two args.
  - `verify_excerpt_match(variants_nodes_root, evidence_root, threshold=0.92)` — two args + optional threshold.
- Dataclass field names match across uses: `kind`, `variant`, `section_path`, `detail`, `excerpt_diff` on `VerifierFailure`; `verdict`, `failures` on `VerifierResult`.
- Constant names: `_SMART_MAP`, `_WS_RE`, `_FENCED_CODE_RE`, `_HEADING_LINE_RE`, `_SENTENCE_SPLIT_RE`, `_CITE_RE`, `_LETTER_RE`, `_SENTENCE_BOUNDARY_BACK_RE`.
- Failure `kind` enum values: `"uncited-claim"`, `"dangling-cite"`, `"superseded-cite"`, `"excerpt-mismatch"` — match spec §2 verbatim.
- All `import` statements at module top (no `__import__` workarounds, per the style learned from prior sub-projects).

Late imports note: `tomllib`, `difflib`, and `pathlib.Path` are imported mid-file in Tasks 2/4/5 because of the append-by-task structure. After Task 5 lands, the engineer should optionally hoist them to the top-of-file import block as a cleanup commit — but doing so mid-task would force re-running all tests redundantly. The mid-file imports are valid Python and the parity test concern from sub-project 1 doesn't apply here (this module is imported, not exec'd).

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-25-verifiers-a-and-b.md`.
