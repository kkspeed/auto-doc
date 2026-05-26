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
