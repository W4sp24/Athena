---
name: news-sentiment
description: Implements cryptolab/news — headline collection with UTC timestamps, local-LLM sentiment/relevance scoring with model+prompt versioning, and leak-free per-bar aggregation (FR-05..FR-08). Use for any news/sentiment work.
---
You own `cryptolab/news/` and `tests/news/`.

Read first: CLAUDE.md, docs/sdd/math.md §7 (the leakage rules are the spec), CRYPTOLAB_CONTEXT.md (P3).

Rules:
- Store published_at and first_seen_at (UTC). Availability time k_h = max(published_at, first_seen_at) + delta.
- LLM via an OpenAI-compatible base URL from settings; temperature 0; model name + prompt version +
  raw response stored per score. Prompts are versioned files, never inline strings edited in place.
- Missing sentiment is missing (None/NaN) with a count — never 0.
- No imports from engine/strategies/execution/risk.

Definition of done: property tests prove headlines with k_h >= d_t cannot change S_{c,t}; scorer is
deterministic on re-run (P3: re-score 50, report agreement); P3 plausibility report reproducible from CLI.
