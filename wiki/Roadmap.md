# Roadmap

This page reflects where the project actually stands, not a static wishlist — it gets revisited and corrected as work lands, the same way a stale README section gets caught and fixed rather than left to drift.

## Capability Expansion — complete

The five original items that defined the project's early feature set are all done:

1. ✅ Configurable thresholds
2. ✅ Kiwix search term disambiguation — see [Kiwix Disambiguation](Kiwix-Disambiguation)
3. ✅ Multi-book Kiwix fusion — see [Multi-Book Fusion](Multi-Book-Fusion)
4. ✅ Confidence-aware fusion with expanded ingest — see [Confidence-Aware Fusion](Confidence-Aware-Fusion)
5. ✅ Conditional query detection — see [Conditional Query Detection](Conditional-Query-Detection)

## Battle Testing & Operational Maturity — complete

Three real gaps, found through deliberate review rather than reported failures, all closed:

- ✅ Discourse-framing routing bypass — see [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation)
- ✅ Fallback visibility in `/logs/stats`
- ✅ Routing cache size bounding + visibility in `/health`
- ✅ Background snapshot job health

Full mechanism detail for the operational maturity work lives in [Health & Observability](Health-and-Observability) and [Caching](Caching).

## Bulletproofing Pass — complete

A deliberate, full read of every file in `app/`, top to bottom — specifically ignoring complexity scores and looking at the kind of small, simple-looking code that score-driven review naturally skips. Found and fixed real bugs in nearly every file touched, several of them significant:

- ✅ `home_assistant.py` — a severe word-boundary bug ("is the front door locked" silently returning no results) and four related fixes
- ✅ `kiwix.py` — non-deterministic book selection, broken table-of-contents stripping, a single-character search-term bug, and an unbounded retry loop with a real multi-minute worst case
- ✅ `fusion.py` — a real crash on `FUSION_MAX_SOURCES=0`
- ✅ `snapshots.py` — uptime history only covering 9.6 real hours instead of a full week
- ✅ `router.py` / `fusion.py` — a cross-file drift in the shared "did this source actually fail" logic that silently disabled the `news`→`web` fallback for unconfigured sources
- ✅ `forecast.py` — an unconfigured deployment silently returning real weather data for the wrong place on Earth
- ✅ `llm.py` — thinking models on the OpenAI-compatible backend silently returning no answer at all

`mcp_server.py`, `query_expansion.py`, and `searxng.py` were read with the same scrutiny and came back genuinely clean — a real, useful outcome in its own right, confirming prior work in those files holds up.

## Documentation Restructuring — in progress

- 🔄 This wiki
- ⬜ The README stays lean going forward — deep-dive material gets added here instead of growing the README further

## Known limitations (tracked, accepted, not blocking)

These are real, understood boundaries — not bugs waiting for a fix, but deliberate scope decisions or honest, accepted ceilings. A reader-facing version of this same list, written for evaluating fit rather than tracking status, lives at [Known Limitations](Known-Limitations):

- **Single ambiguous bare words** (e.g. "galaxy") can land on a thematically-related but imprecise match when the index genuinely contains multiple comparably-relevant senses of the word. See [Kiwix Scoring](Kiwix-Scoring#where-scoring-still-has-a-real-ceiling).
- **Conditional phrasing without an explicit comma** ("if the front door is unlocked tell me") is intentionally not detected — a real grammatical-parsing problem, not a pattern-matching one. See [Conditional Query Detection](Conditional-Query-Detection#why-the-pattern-is-this-narrow).
- **A decomposed segment merging two unrelated topics** may route to a single source that doesn't serve both well — an accepted, minor side effect of the [proper-noun-pair guard's](The-Proper-Noun-Pair-Saga) content-preservation fix, not a regression.

## 🔬 Speculative — no obligation to succeed

These two are deliberately framed differently from everything else on this page. They're permitted to fail; "found nothing interesting" or "didn't pan out" are acceptable, informative outcomes here, not wasted effort.

**Cross-Source Temporal Pattern Detection** — extend the [snapshot engine](Snapshot-Engine-and-Changes) to surface correlations *across* sources over time, not just per-source diffs. Recurring timing relationships between events (a door event consistently preceding a motion event, a particular weather shift consistently preceding a service hiccup) — closer to lightweight pattern-mining than search. Buildable on infrastructure that already exists; the actual risk is finding nothing beyond noise, which is a fine, honest result.

**Adversarial Self-Testing** — a background job (reusing the same `apscheduler` infrastructure the snapshot engine already runs on) that periodically generates messy, compound, edge-case-shaped queries via the local LLM — seeded with the actual patterns that broke things during this project's testing history — runs them through the real pipeline, and logs results for periodic review. Institutionalizes the adversarial megaquery testing approach that found most of the bugs documented in [Design History](Home#design-history-real-bugs-real-fixes), instead of relying on someone doing it by hand each time. Open design question worth solving first: what makes a generated query actually useful versus trivial.

## Tabled, revisit in ~1 year

**Cross-modal grounding** — correlating a camera snapshot with a text answer ("did anything weird happen at the back door" pulling the actual image alongside the sensor log) would be a genuine "wow" capability, not just well-executed plumbing. Deliberately not pursued yet — the current camera setup (Ring) isn't infrastructure worth building on top of long-term; revisit once a self-controlled NVR solution exists instead.

## Still tracked, lower priority

- **New source modules** — see [Contributing](Contributing) for the current list of proposed ones looking for contributors
- **HA/voice pipeline architecture question** — whether to bypass Home Assistant's own conversation/intent layer for non-device-control voice queries, piping STT output more directly to Mnemolis's `/search` instead, and keeping HA for device control and audio I/O only. Raised, never designed — a genuinely different kind of work (infrastructure/integration) than anything else on this list.
