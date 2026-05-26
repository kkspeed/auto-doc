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


_SMART_MAP = str.maketrans(
    "“”„‟‘’—–",
    '""""\'\'--',
)

_WS_RE = re.compile(r"\s+")

# string.punctuation minus the ASCII quote characters that step 2 produces
# from smart-quote inputs — stripping those here would undo step 2.
_PUNCT_STRIP = string.punctuation.translate(str.maketrans("", "", '"\''))


def _normalize_text(s: str) -> str:
    """Five-step normalization for Verifier B's difflib comparison.

    Order matters: NFC first so smart quotes are composed, then map them
    to ASCII, then lowercase, then collapse whitespace, then strip leading
    and trailing punctuation from each word (interior punctuation, like
    apostrophes inside contractions, is preserved).
    """
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_SMART_MAP)
    s = s.lower()
    s = _WS_RE.sub(" ", s).strip()
    s = " ".join(w.strip(_PUNCT_STRIP) for w in s.split(" "))
    return s


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


# ----- Verifier A.1: citation completeness ------------------------------------


_FENCED_CODE_RE = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)
_HEADING_LINE_RE = re.compile(r"^#{1,6}(\s.*)?$", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?:\s+|\Z)")
_CITE_RE = re.compile(r"\[\^ev-(\d{6})\]")
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
    for variant, section_path, _tags, body in _walk_sections(variants_nodes_root):
        for m in _CITE_RE.finditer(body):
            ev_num = m.group(1)
            ev_path = evidence_root / f"ev-{ev_num}.md"
            if not ev_path.exists():
                failures.append(VerifierFailure(
                    kind="dangling-cite",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} not found at {ev_path}",
                ))
                continue
            meta = _load_evidence_frontmatter(ev_path)
            if meta is None:
                failures.append(VerifierFailure(
                    kind="dangling-cite",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} at {ev_path} has malformed frontmatter",
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
