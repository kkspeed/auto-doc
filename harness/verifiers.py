"""Verifier A + Verifier B for the Design Doc Evolution Harness.

Pure-Python verifiers that operate on the on-disk variants directory.
The orchestrator (sub-project 4) calls these between the designer's
write and the commit; structured failures get written as rj-*.md.

Public API:
  Schemas:
    - VerifierFailure
    - VerifierResult

  Verifier A (cite enforcement, parent design SC4):
    - verify_cite_resolution        — every cite resolves to non-superseded evidence

  Verifier B (excerpt match, parent design SC5):
    - verify_excerpt_match          — each cite's sentence matches the cited excerpt

  Internal (exposed for unit testing):
    - _normalize_text

v0 trade-offs (deferred to v0.1+):
  - `excerpt_diff` is computed on normalized single-line text, so
    the unified-diff output is always single-hunk. Word-level diff
    (via ndiff) would improve debuggability.
  - Performance: each evidence file is re-read per cite occurrence.
    At ~100 rounds × ~5 cites/round this is negligible; if real
    runs surface hot paths, add a per-invocation cache.
"""
from __future__ import annotations

import difflib
import re
import string
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


# ----- Dataclasses ------------------------------------------------------------


@dataclass(frozen=True)
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
    # Filter empty tokens after punctuation strip — a word like "." reduces
    # to "" and would otherwise produce a double space in the output.
    s = " ".join(t for t in (w.strip(_PUNCT_STRIP) for w in s.split(" ")) if t)
    return s


# ----- Section walker ---------------------------------------------------------


def _walk_sections(variants_nodes_root: Path):
    """Yield (variant_name, section_path, tags, body, raw_tags) for every
    well-formed section under variants_nodes_root.

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
            # errors="replace" prevents a single non-UTF-8 byte from killing
            # the entire walk. The lossy replacement may make a few characters
            # mis-match in Verifier B, which is the right trade-off vs aborting.
            text = md.read_text(encoding="utf-8", errors="replace")
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
            raw_tags = meta.get("tags", [])
            tags = raw_tags if isinstance(raw_tags, list) else []
            rel_path = str(md.relative_to(variants_nodes_root.parent.parent))
            yield variant_dir.name, rel_path, tags, body, raw_tags


# ----- Mechanical gate: frontmatter well-formedness --------------------------


def verify_frontmatter_wellformed(variants_nodes_root: Path) -> VerifierResult:
    """Every section file's +++ frontmatter must parse AND its `tags` must be a
    list. A non-list `tags` silently reads as non-decided (grounding bypass), so
    this is a hard gate. Walks files directly — _walk_sections silently skips the
    malformed sections this is meant to catch."""
    failures: list[VerifierFailure] = []
    if not variants_nodes_root.exists():
        return VerifierResult(verdict="pass", failures=[])
    for variant_dir in sorted(variants_nodes_root.iterdir()):
        if not variant_dir.is_dir() or not variant_dir.name.startswith("v-"):
            continue
        doc_dir = variant_dir / "doc"
        if not doc_dir.exists():
            continue
        for md in sorted(doc_dir.glob("*.md")):
            rel = str(md.relative_to(variants_nodes_root.parent.parent))
            text = md.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("+++"):
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail="missing +++ frontmatter fence"))
                continue
            end = text.find("+++", 3)
            if end == -1:
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail="unterminated +++ fence"))
                continue
            try:
                meta = tomllib.loads(text[3:end])
            except tomllib.TOMLDecodeError as e:
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel, detail=f"TOML parse error: {e}"))
                continue
            raw = meta.get("tags", [])
            if not isinstance(raw, list):
                failures.append(VerifierFailure(
                    kind="malformed-frontmatter", variant=variant_dir.name,
                    section_path=rel,
                    detail=f"tags is {type(raw).__name__!r}, "
                           "expected a list"))
    return VerifierResult(
        verdict="fail" if failures else "pass", failures=failures)


_CITE_RE = re.compile(r"\[\^ev-(\d{6})\]")


# ----- Verifier A.2: cite resolution ------------------------------------------


def _load_evidence_frontmatter(ev_path: Path) -> dict | None:
    """Read an ev-*.md file and return its parsed TOML frontmatter dict, or
    None if the file is missing, lacks a +++ fence, or fails to parse."""
    if not ev_path.exists():
        return None
    text = ev_path.read_text(encoding="utf-8", errors="replace")
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
    for variant, section_path, _tags, body, _raw_tags in _walk_sections(variants_nodes_root):
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


# ----- Verifier B: excerpt match ----------------------------------------------


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
    return _paragraph_containing(body, pos)


def _paragraph_containing(body: str, pos: int) -> str:
    """Return the paragraph (text between blank lines) containing offset `pos`.

    Verifier B compares a cited excerpt against this window — a paragraph rather
    than a single sentence — so a verbatim quote that the doc wraps across
    sentence boundaries still counts as covered."""
    para_start = body.rfind("\n\n", 0, pos)
    para_start = 0 if para_start == -1 else para_start + 2
    para_end = body.find("\n\n", pos)
    para_end = len(body) if para_end == -1 else para_end
    return body[para_start:para_end].strip()


def verify_excerpt_match(
    variants_nodes_root: Path,
    evidence_root: Path,
    threshold: float = 0.6,
) -> VerifierResult:
    """For each [^ev-NNNNNN] cite, check how much of the cited evidence's
    `excerpt` is present in the doc paragraph around the cite — an asymmetric
    COVERAGE score (matched chars / excerpt length). Fail if coverage <
    threshold.

    Coverage (not difflib's symmetric ratio) is deliberate: a design doc wraps a
    quoted fact in surrounding prose, and a symmetric ratio penalizes that extra
    prose — a verbatim quote with context scored ~0.6 and got rejected. Coverage
    only asks "does the excerpt appear here", so the doc's own prose doesn't drag
    the score down.

    Adapter-sourced evidence (a `source` frontmatter field, set by the repo
    adapter and future source adapters) is EXEMPT: its excerpt is a verbatim
    source span (e.g. code) that the doc describes in prose, which text-matching
    can never satisfy. Verifier C judges those for faithfulness.

    Skips dangling/malformed evidence files silently — those are owned by
    verify_cite_resolution. This avoids double-reporting on the same cite.
    """
    failures: list[VerifierFailure] = []
    for variant, section_path, _tags, body, _raw_tags in _walk_sections(variants_nodes_root):
        for m in _CITE_RE.finditer(body):
            ev_num = m.group(1)
            ev_path = evidence_root / f"ev-{ev_num}.md"
            meta = _load_evidence_frontmatter(ev_path)
            if meta is None:
                continue   # owned by verify_cite_resolution
            if meta.get("source"):
                continue   # adapter-sourced evidence — Verifier C owns it
            excerpt = meta.get("excerpt")
            n_excerpt = (_normalize_text(excerpt)
                         if isinstance(excerpt, str) else "")
            if not n_excerpt:
                # Covers: field absent, non-string value (array/int/bool),
                # empty/whitespace-only string, and strings that normalize to
                # empty (e.g. punctuation-only). All are "no usable excerpt".
                failures.append(VerifierFailure(
                    kind="excerpt-mismatch",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} frontmatter has no usable excerpt field",
                ))
                continue
            # Window = the paragraph holding the cite, with cite tokens stripped
            # so they don't perturb the match.
            window = _CITE_RE.sub("", _paragraph_containing(body, m.start()))
            n_window = _normalize_text(window)
            matched = sum(b.size for b in difflib.SequenceMatcher(
                None, n_excerpt, n_window).get_matching_blocks())
            coverage = matched / len(n_excerpt)
            if coverage < threshold:
                diff = "\n".join(difflib.unified_diff(
                    n_excerpt.splitlines() or [""],
                    n_window.splitlines() or [""],
                    fromfile="excerpt", tofile="doc-paragraph",
                    lineterm="", n=1,
                ))
                failures.append(VerifierFailure(
                    kind="excerpt-mismatch",
                    variant=variant,
                    section_path=section_path,
                    detail=f"ev-{ev_num} excerpt coverage={coverage:.3f} "
                           f"below threshold {threshold}",
                    excerpt_diff=diff,
                ))
    return VerifierResult(
        verdict="fail" if failures else "pass",
        failures=failures,
    )
