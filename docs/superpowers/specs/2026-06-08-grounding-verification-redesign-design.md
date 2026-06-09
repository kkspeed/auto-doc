# Grounding Verification Redesign

**Date:** 2026-06-08
**Status:** Approved design, pre-implementation

## Problem

Verifier A.1 (`verify_citation_completeness`) enforces grounding **per sentence**:
it splits a `decided` section's prose into sentences and demands a `[^ev-NNNNNN]`
citation on every one. This is linguistically wrong. Grounding is a property of a
**claim / paragraph**, not a sentence — a paragraph can be well-grounded while
individual sentences are connective tissue (transitions, framing, "therefore"
glue) that carry no standalone factual content. The sentence rule fires a flood
of false positives ("Sentence ends 'xxx' but has no [^ev-*] cite") on perfectly
fine prose, and because Verifier A.1 re-walks **every** decided section on
**every** round, one offending section poisons all subsequent rounds.

A secondary failure mode: if grounding is judged over the **whole doc**, weak
inherited/seed prose drags the score down every round, consecutively failing the
scorecard gate unless the designer happens to fix pre-existing content it didn't
intend to touch.

## Goals

- Stop judging grounding sentence-by-sentence.
- Keep a deterministic, mechanical structural backbone (cites are real; quotes
  are real; claims resolve).
- Judge prose grounding at **paragraph** granularity with an LLM, tolerant of
  connective language.
- Judge only **what the round changed** (the diff), so inherited/seed weakness
  never consecutively fails rounds; improving old content is the designer's
  explicit choice and becomes part of its diff.
- No new hard reject for prose grounding — it is a continuous score.

## Non-goals

- No change to how the designer authors (it edits files; the harness derives the
  patch from git — unchanged from the 2026-06-08 patch refactor).
- No change to the claim/evidence ledger structure.
- No change to cite-resolution or excerpt-match semantics.

## Design

### Grounding stack (after)

| Layer | Unit | Kind | Enforcement |
|---|---|---|---|
| Cite resolution (`verify_cite_resolution`) | each `[^ev-*]` | mechanical | hard reject (dangling / superseded) |
| Excerpt match (`verify_excerpt_match`) | paragraph window | mechanical | hard reject (quote not present) |
| Frontmatter well-formed (`verify_frontmatter_wellformed`, new) | section | mechanical | hard reject (`tags` not a list) |
| **Paragraph grounding (Verifier C)** | paragraph, **diff-scoped** | LLM, independent | **score only** → groundedness dimension |
| Claim resolution (`compute_groundedness`) | claim | mechanical | scorecard cap (`min`) |

`verify_citation_completeness` (Verifier A.1) and its `uncited-claim` reject path
are **deleted**. Nothing makes a per-sentence demand anymore.

### Verifier C as the diff-scoped paragraph-grounding judge

- **Scope = the round's diff.** Verifier C reads `rounds/<round>/patch.diff` (the
  git-derived change set) to identify which sections/paragraphs changed, the
  **full current text of each changed `decided` section** (so it judges complete
  paragraphs, not partial hunk lines), and the evidence cited within the changed
  paragraphs. It judges grounding of **only the paragraphs this round added or
  modified**, and **only** in `decided` sections. Untouched seed/inherited
  paragraphs and `unresolved`/untagged sections are not judged.
- **Output:** Verifier C's JSON gains `groundedness` — a float in `[0,1]` —
  judged at paragraph level: does each changed decided paragraph's *factual*
  content trace to its cited evidence? Connective sentences need no support.
  Added to `validate_verifier_c_json` as **optional-with-fallback**: a flaky
  omission degrades to the mechanical value rather than hard-failing the round
  (same pattern the reviewer's optional dims use today).
- **Why this solves the consecutive-failure trap:** a round is scored on what it
  contributes. A well-grounded round scores high even if old seed prose is weak.
  A round that introduces ungrounded prose scores low and (only it) may fail the
  scorecard regression gate; once rejected, the prior bar is unchanged, so the
  next well-grounded round still passes. No death spiral.

### Scorecard wiring

- `compute_dimensions` groundedness changes from
  `_cap(reviewer_groundedness, mechanical)` to
  `_cap(verifier_c_groundedness, mechanical)`.
- Mechanical cap stays `compute_groundedness` (fraction of `cl-*.json` claims
  whose `evidence_ids` resolve to existing, non-superseded evidence). This is
  already effectively diff-aligned: the seed contributes no claims, and
  cite-resolution is a hard gate, so it stays ~1.0 and won't poison; it only
  bites when a round's own claims fail to resolve.
- The reviewer stops scoring groundedness: removed from `REVIEWER_PROMPT`,
  `validate_reviewer_json`, and the `compute_dimensions` call. Reviewer still
  scores `goal_alignment`, `technical_correctness`, `completeness`, `coherence`.

### Preserved structural check

A.1 was the only emitter of `malformed-frontmatter` (a section whose `tags` field
isn't a list silently reads as non-decided → SC4 grounding bypass). Extract this
into a tiny mechanical hard gate `verify_frontmatter_wellformed` (frontmatter
parses + `tags` is a list), run in Phase 3 alongside cite-resolution.

### Dead code

Remove `verify_citation_completeness` and the sentence-split machinery only it
used: `_SENTENCE_SPLIT_RE`, `_HEADING_LINE_RE`, `_METADATA_LINE_RE`, `_LETTER_RE`,
`_FENCED_CODE_RE`. Verifier B's `_extract_sentence_containing` /
`_paragraph_containing` are independent and stay.

## Components changed

- `harness/verifiers.py`: delete A.1 + its regexes; add
  `verify_frontmatter_wellformed`.
- `harness/orchestrator.py`:
  - `run_round` Phase 3: drop the completeness call + `uncited-claim` branch;
    keep cite-resolution; add the frontmatter check.
  - `VERIFIER_C_PROMPT`: add the diff-scoped, paragraph-level, connective-tolerant,
    decided-only `groundedness` scoring instruction.
  - `validate_verifier_c_json`: accept optional `groundedness` float in `[0,1]`.
  - `REVIEWER_PROMPT` + `validate_reviewer_json`: remove `groundedness`.
- `harness/context.py`: `build_verifier_c_context` must surface (a) the round's
  `patch.diff` so VC knows *which* sections/paragraphs changed, (b) the **full
  current text of each changed `decided` section** so VC judges *complete*
  paragraphs rather than partial hunk lines, and (c) the evidence cited within
  the changed paragraphs. VC judges grounding only over the changed decided
  paragraphs; the patch is the change selector, the section text is the unit of
  judgment.
- `harness/scorecard.py`: `compute_dimensions` groundedness sourced from a new
  `vc_groundedness` param; drop `reviewer_groundedness`.
- `harness/round_ledger.py` commit-msg hook vocab: `uncited-claim` stays in
  `ALLOWED_REASONS` for historical commits but is no longer emitted.

## Data flow

1. Designer edits decided section files (existing behavior).
2. Mechanical gates: cite-resolution, excerpt-match, frontmatter-wellformed — any
   failure → hard reject (unchanged shape, minus A.1).
3. Verifier C reads the patch.diff + cited evidence → emits per-claim verdicts
   (existing) **and** a paragraph-grounding `groundedness` score over the diff.
4. Scorecard: `groundedness = min(vc_groundedness, claim_resolution_fraction)`;
   compared against the prior merged round under the regression tolerance.

## Edge cases

- **Decided section with zero cites:** previously an A.1 hard reject; now merges.
  Grounding is reflected only in the VC score (low if it asserts unsupported
  facts, fine if it's connective/structural).
- **VC omits `groundedness`:** degrades to the mechanical value (no hard fail).
- **Round touches nothing in a weak prior section:** that section isn't in the
  diff → not judged → not penalized.
- **Malformed `tags`:** caught by `verify_frontmatter_wellformed` (hard reject).

## Testing

- `test_verifiers.py`: delete the `verify_citation_completeness` class (incl. the
  `**Status**:` metadata tests); keep cite-resolution + excerpt-match; add
  `verify_frontmatter_wellformed` tests (well-formed passes; `tags`-not-a-list
  fails).
- Verifier C schema: `groundedness` accepted in `[0,1]`, rejected out-of-range,
  tolerated when omitted.
- `test_scorecard.py`: groundedness from `vc_groundedness`; `min(vc, mechanical)`
  cap holds; a low VC score pulls the dimension down even when mechanical is 1.0.
- `run_round` tests: repoint `RunRoundVerifierAFailureTest` from
  "uncited assertion → phase-a-fail" to dangling-cite and malformed-frontmatter
  rejections; add "decided section with zero cites now MERGES"; add "merge
  groundedness comes from the mocked VC, not the reviewer".
- `test_orchestrator_score_gate.py`: drive groundedness via VC instead of the
  reviewer in fixtures.
- `build_verifier_c_context` test: asserts it includes the round's patch + cited
  evidence (diff-scoping), not a re-render of untouched sections.

## Migration

Zero. Removing A.1 is evaluated at verify-time, so every existing decided section
currently failing the sentence rule stops failing the moment this ships.
