# Design Doc: Semantic Tiebreaker & Typed Query Expansion

**Status:** Proposed, not yet built
**Origin:** Discussion comparing Mnemolis against adjacent self-hosted/MCP-aggregator projects surfaced two ideas that fit the existing scoring/expansion architecture without requiring a new kind of infrastructure. Written up here rather than built directly, following this project's own established pattern of a design doc preceding implementation for anything touching shared scoring/routing code.

This covers two related but independently shippable features:

1. **Semantic tiebreaker** — an optional, local, non-LLM re-rank step that only activates when keyword scoring leaves two or more results close enough that the keyword signal alone can't confidently separate them.
2. **Typed query expansion** — generalizing `query_expansion.py`'s single alternate phrasing into 2-3 deliberately different *kinds* of rephrasing, mirroring the pattern already proven in Kiwix disambiguation's multi-candidate generation.

Both are explicitly **additive**: neither replaces the existing deterministic scoring path. Both fail back to today's exact behavior if anything about the new mechanism is unavailable or fails.

---

## Part 1: Semantic Tiebreaker

### The actual problem, precisely stated

[Kiwix Scoring](https://github.com/immortalbob/Mnemolis/wiki/Kiwix-Scoring) and [Confidence-Aware Fusion](https://github.com/immortalbob/Mnemolis/wiki/Confidence-Aware-Fusion) both rank candidates with real, deterministic point values — stemmed keyword overlap, exact-match bonuses, generic-result penalties. This works well and is fully debuggable, which is why it's stayed keyword-based this long. But it has one real, already-documented ceiling: **two results can both be genuinely, legitimately relevant by keyword overlap while meaning different things** — `Kiwix-Scoring.md`'s own "galaxy" example (astronomy vs. Hitchhiker's Guide) is the canonical case, and it survives even multi-candidate disambiguation because both senses score comparably on real keyword overlap with the query. There's no new keyword signal that fixes this; the words genuinely are shared. What's different is *meaning*, not vocabulary — which is the one thing a keyword-overlap function structurally cannot see.

### Why this isn't an LLM call

A completion model (the thing `app/llm.py` talks to) generates new text token-by-token — that's why it lives on a separate, more capable machine (The Beast). An **embedding model** does something categorically simpler: one forward pass through a small network (tens of millions of parameters, not billions), producing a fixed-length vector of floats that represents a sentence's meaning. No generation, no decoding loop, no sampling. Comparing two embeddings (cosine similarity) is just arithmetic on two short float arrays.

Concretely: `all-MiniLM-L6-v2` (the most common choice for this exact use case) is ~22M parameters and benchmarks in the thousands-of-sentences-per-second range on ordinary CPUs in published benchmarks. Scaled down generously for an N100's real-world throughput, embedding a double-digit number of short titles/excerpts per query is sub-second, CPU-only work — not remotely the same category of cost as an LLM completion, and not something that needs to leave MiniDock.

**This runs inside the Mnemolis container, on MiniDock, never touching The Beast or `LLM_URL`.** It's a new, small, local dependency — closer in spirit to how `scoring.py` already does math locally than to anything in `llm.py`.

### Proposed mechanism

A new module, `app/semantic.py`, mirroring `scoring.py`'s existing shape (pure functions, no shared state beyond a lazily-loaded model):

```python
# app/semantic.py (proposed)

_model = None  # lazy-loaded, module-level, loaded once per process

def is_available() -> bool:
    """False if the embedding model failed to load or is disabled —
    callers must treat this as a normal, expected case, not an error."""

def embed(text: str) -> list[float] | None:
    """Returns None on any failure — embedding is enhancement, never
    a hard dependency for any existing code path."""

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure arithmetic, no model involvement."""

def rerank_near_ties(query: str, scored_results: list[tuple[int, dict]],
                      tie_window_pct: float) -> list[tuple[int, dict]]:
    """
    Given results already scored and sorted by the EXISTING keyword
    function, find the cluster of results within tie_window_pct of the
    top score, and re-order ONLY that cluster by semantic similarity to
    the query. Results outside the tie window are untouched — this
    function only ever resolves ties, it never overrides a clear
    keyword-score winner.
    """
```

The key design constraint, stated explicitly because it's the thing that keeps this safe: **the semantic step only ever re-orders results that are already within a tie window of each other.** It is never the primary ranking signal, and it never promotes a low-keyword-score result above a high-scoring one. This mirrors the existing `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT` pattern exactly (`score >= top_score * settings.kiwix_multi_book_fusion_threshold_pct` in `kiwix.py`) — a real, already-proven precedent for "only act within a percentage-of-top-score band."

### Where it hooks in

Two call sites, both *after* existing scoring, both optional:

**`app/scoring.py::filter_and_rank()`** — after `scored.sort(...)`, before `survivors[:top_n]` truncates:

```python
scored.sort(key=lambda pair: pair[0], reverse=True)
if settings.semantic_tiebreak_enabled and semantic.is_available():
    scored = semantic.rerank_near_ties(query, scored, settings.semantic_tiebreak_window_pct)
survivors = [r for score, r in scored if score > score_threshold]
```

**`app/sources/kiwix.py::search()`**, line ~775, same pattern around the existing sort:

```python
scored = sorted(all_results, key=lambda r: _score_result(r, query, selected_books[0]), reverse=True)
if settings.semantic_tiebreak_enabled and semantic.is_available():
    scored = semantic.rerank_near_ties(query, scored, settings.semantic_tiebreak_window_pct)
top = scored[0]
```

Neither call site's existing behavior changes when the setting is off (default) or when the model fails to load — `is_available()` short-circuits to the current, already-shipped behavior.

### Configuration

| Setting | Proposed default | What it controls |
|---|---|---|
| `SEMANTIC_TIEBREAK_ENABLED` | `false` | Master switch. Off by default — this is new, unproven-in-production logic touching the most heavily-tested code in the project (`Kiwix Scoring`, `Confidence-Aware Fusion`); it should ship disabled and be turned on deliberately, the same caution `ADVERSARIAL_TEST_ENABLED`/`TEMPORAL_PATTERN_DETECTION_ENABLED` were given at launch |
| `SEMANTIC_TIEBREAK_WINDOW_PCT` | `0.9` | Only results scoring within this fraction of the top score are eligible for semantic re-ranking — narrow on purpose, since this should only ever resolve genuine near-ties, not act as a second, broader ranking pass |
| `SEMANTIC_MODEL_NAME` | `all-MiniLM-L6-v2` (or the `fastembed`-bundled default) | Which embedding model to load; exposed so a future, better small model can be swapped in without a code change |

### What needs to be proven before this ships, not just built

This is the part of the design that actually matters most, given how this project treats unverified assumptions. Before merging:

1. **A real before/after test against the actual "galaxy" case** — and ideally a small, hand-built set of 5-10 other known ambiguous-word cases — confirming the semantic step actually picks the keyword-overlap-correct sense more often than the unmodified scorer does. If it doesn't measurably help on real cases, this isn't worth the added dependency and complexity, regardless of how clean the architecture is.
2. **A real timing benchmark on MiniDock's actual N100**, not an assumption from published CPU benchmarks on different hardware — added to `BENCHMARKS.md` the same way every other conditionally-triggered feature's cost has been measured (disambiguation, query expansion). If the real cold-load time for the embedding model, or per-query embedding cost, turns out to be unacceptable on this specific hardware, the feature should stay off by default regardless of architecture quality.
3. **A check that this doesn't regress the multi-book-fusion / multi-candidate-disambiguation interaction** — both of those already produce result pools from multiple candidates/books; the tie window needs to be verified against pooled results from those paths too, not just a single plain search.
4. **Confirmation `fastembed` (or whichever library is chosen) has no real dependency conflict** with the existing `requirements.txt` — this project's dependency list is deliberately lean (no PyTorch, no CUDA anywhere today), and an ONNX-runtime-only library should be chosen specifically to preserve that, not a library that drags in a heavier ML stack.

### What this deliberately does not do

It does not replace any existing scoring signal. It does not touch `web`/`news`/`kiwix` results that aren't already near-tied. It does not call out to The Beast or affect `LLM_URL`/`LLM_MODEL` in any way — this is fully independent of whether an LLM is configured at all, the same "works with reduced capability, never breaks, when nothing's configured" principle `LLM-Client.md` already documents for the rest of the project.

---

## Part 2: Typed Query Expansion

### The actual problem, precisely stated

`get_alternate_phrasing()` (`app/query_expansion.py`) asks the LLM for exactly one alternate phrasing of a web query, searches both, and merges the pools. This is real and already measurably useful — but it asks for a single, undifferentiated rephrasing, with no instruction about *what kind* of alternate would actually be useful. The model can return a rephrasing that's barely different from the original (close to a wasted second search) or one that drifts in an unhelpful direction, with nothing structurally pushing it toward genuinely complementary phrasings.

Kiwix disambiguation already solved a structurally identical problem — "one LLM guess might miss" — not by asking for one better guess, but by asking for several *deliberately different* candidates (`_get_disambiguation_candidates()`'s real prompt: "a broad field name, specific synonym, bare word with no qualifier") and verifying all of them against real results. Query expansion currently doesn't get that same treatment. It asks for one generic alternate instead of several typed ones.

### Proposed mechanism

Generalize `get_alternate_phrasing()` into `get_alternate_phrasings()` (plural), returning up to 3 candidates, each generated against an explicitly different framing — modeled directly on the three real angles already proven in `Kiwix-Disambiguation.md`'s candidate generation:

1. **A more technical/precise rephrasing** — same intent, more specific or formal vocabulary
2. **A colloquial/forum-style rephrasing** — how someone would actually phrase this searching a forum or asking out loud (this directly mirrors `Query-Decomposition.md`'s own colloquial-phrase handling — the project already has real, working logic for recognizing this register, just not for generating it)
3. **A broader/category rephrasing** — one level more general, in case the original phrasing is too narrow to surface complementary results

```python
# app/query_expansion.py (proposed change)

_VARIANT_PROMPTS = {
    "technical": "Rephrase the following search query using more precise, "
                 "technical vocabulary while preserving the exact same intent.",
    "colloquial": "Rephrase the following search query the way someone "
                   "would casually ask it out loud or type it into a forum, "
                   "while preserving the exact same intent.",
    "broader": "Rephrase the following search query slightly more broadly — "
               "one level more general — while still preserving the core intent.",
}

def get_alternate_phrasings(query: str, max_variants: int = 2) -> list[str]:
    """
    Returns up to max_variants alternate phrasings, each from a different
    typed prompt. Each one independently passes the exact same sanity
    checks get_alternate_phrasing() already applies (non-empty, not
    absurdly longer, not identical to original) — a variant that fails
    is simply dropped, not retried; this mirrors get_alternate_phrasing()'s
    existing "expansion is a pure bonus" philosophy applied per-variant.
    Each variant is cached independently under its own routing-cache key
    (altquery:{type}:{query}), so a cache hit on one type doesn't block
    a fresh attempt on another.
    """
```

`max_variants` defaults to **2**, not 3 — deliberately conservative, since this is multiplying the existing real cost (one extra LLM call, one extra SearXNG fetch per variant) and the actual benefit of a third variant is unproven. Starting at 2 (one extra search beyond what already ships today) is the honest, minimal version of this change; a third variant is a separate, later decision once the second one is measured.

### Where it hooks in

`searxng.py::search()`'s existing `_alternate_phrasing_chain()` already runs one alternate-fetch chain concurrently with the primary fetch, on its own thread (the fix from [The Latency Parallelization Investigation](https://github.com/immortalbob/Mnemolis/wiki/The-Latency-Parallelization-Investigation)). Generalizing to N variants means submitting N such chains to the same executor, not inventing new concurrency:

```python
# searxng.py::search(), proposed change
alternate_queries = get_alternate_phrasings(query, max_variants=settings.query_expansion_max_variants)
alternate_futures = [
    executor.submit(contextvars.copy_context().run, _fetch_one_variant, query, v)
    for v in alternate_queries
]
```

This needs the exact same `contextvars.copy_context()` discipline the existing single-variant chain already uses — and needs it *per task*, individually, not one shared copy — per the real regression documented in `The-Latency-Parallelization-Investigation.md`'s "a first attempt shared one captured context between both tasks, which failed... a single `Context` object cannot be entered by two threads simultaneously." This is exactly the kind of mistake this design doc should call out explicitly, given that the project has already hit it once in a structurally similar concurrent-task-submission change.

All variant results merge into the same deduplicated, normalized-URL pool the current two-result merge already uses — no new merge logic needed, just more inputs to the same `normalize_url()`-based dedup.

**Scoring is unaffected and unchanged**: every result from every variant, exactly like today, is scored against the *original* query only, never any variant's wording — this is the existing, load-bearing design principle stated explicitly in `Query-Expansion.md`'s "Why scoring always uses the original query, never the alternate," and this change doesn't touch it.

### Configuration

| Setting | Proposed default | What it controls |
|---|---|---|
| `QUERY_EXPANSION_MAX_VARIANTS` | `2` | How many typed alternate phrasings to attempt (technical, colloquial — broader is the natural third if this proves worthwhile) |
| `QUERY_EXPANSION_MIN_WORDS` | `3` (existing, unchanged) | Same trigger condition as today — applies identically regardless of variant count |

No new master enable/disable switch is proposed — `QUERY_EXPANSION_MAX_VARIANTS=1` is the literal, exact existing behavior, so there's no need for a separate on/off flag; the variant count itself is the switch, the same way `KIWIX_MAX_BOOKS=1` already turns off multi-book fusion without its own separate flag.

### What needs to be proven before this ships

1. **A real benchmark comparing 1 vs. 2 variants** on real, varied queries (not just the original repro query) — added to `BENCHMARKS.md` next to the existing query-expansion numbers, since this directly multiplies an already-measured cost (the original 1-variant fix took `4.15s` sequential to `3.04s` concurrent; a second variant adds another full LLM-completion-plus-SearXNG-fetch chain, concurrent or not).
2. **Confirmation the three typed prompts actually produce meaningfully different phrasings in practice**, not three near-identical rewordings — this needs real output inspection against several real queries before assuming the typed-prompt idea transfers as cleanly from disambiguation (single words) to query expansion (full sentences) as it looks like it should on paper.
3. **A direct check that the existing `contextvars` propagation tests generalize correctly to N concurrent tasks**, not just 2 — the existing test suite (`TestSearxngConcurrentFetch`) was written for exactly one alternate chain; this needs new, explicit multi-variant test coverage, not an assumption that "it worked for one, it'll work for N."

### What this deliberately does not do

It does not change `news`/FreshRSS at all — `Query-Expansion.md`'s existing "why news doesn't have this" reasoning (no external search ranking to route around) is unaffected by adding more *web* variants. It does not change how results are scored, deduplicated, or merged — only how many candidate search strings get generated before the existing pipeline runs.

---

## Shared groundwork

Both features benefit from one piece of infrastructure that's cheap to build once: a small `tests/test_semantic.py`-style harness for "compare ranked output before/after a change" that can be reused to validate both the tiebreaker (does it correctly resolve known ambiguous cases without disturbing clear wins) and the typed variants (do the three prompts produce real lexical diversity, measurable directly via word-overlap or the same embedding model proposed in Part 1). Building this once, rather than ad hoc for each feature separately, follows the same precedent as the shared `timeutil.py` groundwork built once for three originally-separate pending design docs in v3.48.0.

## Open questions, not yet resolved

- Should the semantic tiebreaker's embedding model also be available to a future "search my own browsing history" source, if that's ever built? The model-loading/embedding infrastructure in Part 1 would be directly reusable there, which is worth keeping in mind if both ever get built, but is explicitly out of scope for this doc.
- Is `SEMANTIC_TIEBREAK_WINDOW_PCT` better expressed as a percentage of top score (consistent with the existing `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT` convention) or as an absolute point-difference threshold? Percentage is proposed here for consistency with existing precedent, but the actual scoring ranges differ between `kiwix.py` (0-50+ range) and `scoring.py` (a different, narrower effective range), so this deserves a direct check against real score distributions from both before committing to one convention.
