# Adversarial Self-Testing

A background job, running on the same `apscheduler` infrastructure the [snapshot engine](Snapshot-Engine-and-Changes) already uses, that generates structurally-novel queries by combining Mnemolis's own real ingredient vocabulary, runs each one through the real `route_with_source()` pipeline, and flags structural anomalies for human review. It exists to institutionalize the adversarial megaquery testing approach that found most of the bugs documented in [Design History](Home#design-history-real-bugs-real-fixes) — the [proper-noun-pair saga](The-Proper-Noun-Pair-Saga)'s bug 5, in particular — instead of relying on someone deliberately constructing a nasty test sentence by hand each time.

## The one hard rule

**Nothing in this feature ever judges whether a response was correct.** That's not a stylistic choice — it's the load-bearing design constraint the whole feature depends on.

An LLM-as-judge approach to this exact shape of problem (generate a test input *and* an expected answer, then trust an LLM's own judgment about whether a system's real output matches) was measured in real research at 6.3% precision — 93.7% of flagged "failures" were the judge's own invented expected-answer being wrong, not the system under test. Building this feature around that approach would have meant trading a few hours of setup for a permanent, self-inflicted false-positive problem.

Instead, every check here verifies one of Mnemolis's own *documented, already-stated* behavioral guarantees against what the real pipeline actually did:

- Does a `discourse_framing_plus_real_keyword` query actually keep kiwix in the result, the way [the discourse-framing bias](The-Discourse-Framing-Investigation) is supposed to guarantee?
- Does a query built from N independent intents produce something close to N `[SOURCE — LABEL]` headers, the same signal that originally caught the proper-noun-pair bug?
- Does the response contain a raw traceback, an empty-result phrase from `fusion._looks_empty()`, or a source that doesn't match anything the query actually said?

None of those require knowing whether the *content* of the answer was right. They require knowing whether Mnemolis did the thing it claims to do — a fundamentally more reliable kind of check, and one that needs no LLM call and no ground truth.

## Generation — pure combinatorics, no LLM calls

Every generated query comes from one of seven recipes, each pure Python combining real vocabulary already defined elsewhere in the codebase:

- `router.INTENT_MAP` — the same dict `detect_intent()` uses for keyword routing
- `router._CONJUNCTIONS` / `router._NOSPLIT_PATTERNS` — the same lists [query decomposition](Query-Decomposition) uses
- `kiwix.DISCOURSE_FRAMING_PATTERNS` — the same list behind the [discourse-framing investigation](The-Discourse-Framing-Investigation)
- A small hardcoded seed corpus: real proper-noun pairs, and the real conditional phrases from `tests/locustfile.py`'s `CONDITIONAL_QUERIES`/`CONDITIONAL_WITH_REMAINDER_QUERIES` — reused directly rather than re-typed, so the two test surfaces can never silently drift apart

| Recipe | What it stresses |
|---|---|
| `proper_noun_plus_pronoun_intent` | The exact shape that found proper-noun-pair bug 5 — a real pair immediately followed by a conjunction and the pronoun "I" |
| `multi_intent_chain` | 3–5 independent intents from different sources, joined by different conjunctions |
| `conditional_with_remainder` | A real conditional seed plus a genuinely unrelated remainder intent after it |
| `nosplit_adjacent_to_real_conjunction` | A nosplit phrase ("compare", "versus", etc.) placed next to a *different*, unrelated real conjunction elsewhere in the query |
| `discourse_framing_plus_real_keyword` | A discourse phrase followed by a clean keyword match for a different source |
| `nested_proper_noun_pairs` | Two distinct proper-noun pairs in the same query, testing whether the per-occurrence guard protects both independently |
| `no_intent_fallthrough` | A query with no `INTENT_MAP` keyword at all — does it fall through to Kiwix/LLM routing sanely? |

Each generated query is fingerprinted by the *ingredients* used (not the literal string), and generation biases toward fingerprints never seen before, falling back to a repeat only once a recipe's seed vocabulary is genuinely exhausted — confirmed directly: against a single-recipe, five-topic test vocabulary, all five topics surface as novel within the first five generations before repeats begin.

The one place an LLM call would actually be worth its cost is periodic (weekly-scale, not per-cycle) expansion of the seed lists themselves — `PROPER_NOUN_PAIRS`, `CONDITIONAL_SEEDS`, `_DISCOURSE_TOPICS` — not the generation loop itself. That's a deliberate, not-yet-built follow-up, not part of the hot path.

## What gets flagged

Seven checks run in priority order against every generated query's real result:

1. **Crash** — an exception escaped, or a raw traceback ended up in the response body
2. **Source mismatch** — `source_used` doesn't match any source the query's own keywords actually pointed at (fusion is always allowed, since merging multiple real sources is itself correct behavior)
3. **Part-count mismatch** — a `multi_intent_chain` query's intended intent count is significantly off from its result's `[SOURCE — LABEL]` header count
4. **Discourse framing dropped kiwix** — a `discourse_framing_plus_real_keyword` query's result has neither `source_used == "kiwix"` nor a `[KIWIX — ...]` header
5. **Conditional remainder missing sections** — a `conditional_with_remainder` query's result has zero `[SOURCE — LABEL]` headers at all
6. **Unexpected empty** — the result matches one of `fusion._looks_empty()`'s own canonical empty/error phrases
7. **Latency outlier** — more than 1.5x the same recipe's own historical p95, once at least 10 samples exist

A flagged combination is stored, never silently dropped — `GET /adversarial/flagged` returns every currently-flagged fingerprint, its most recent query text, source used, latency, and flagged reason, for a human to actually look at. A combination's flag is **not** auto-cleared by a later clean run on a *different* fingerprint — but a re-run of the *same* fingerprint does overwrite its own `last_flagged_reason`, including back to `NULL` if that specific re-run came back clean. Review and dismissal of a standing flag is a human job, not something this feature tries to automate away.

## A bug this feature found in itself, before it ever ran in production

Building the discourse-framing check exposed a real logic bug during its own unit testing, worth recording here in the same spirit as the rest of [Design History](Home#design-history-real-bugs-real-fixes): the first version checked `"kiwix" in result.lower()` as one of its two ways to confirm kiwix was actually used. A genuinely realistic mock result reading `"plain web result, no kiwix involved"` — explicitly *stating* kiwix was **not** used — contains the literal substring `"kiwix"`, so the naive check passed it as if kiwix had been present. Fixed by trusting only `source_used` and the real, structural `"[KIWIX —"` header marker `fusion.py` actually emits — never a freeform substring search across response text. A small, contained version of exactly the kind of trap this whole feature exists to catch in Mnemolis itself, caught here by a real failing unit test rather than by accident.

## Two known limitations worth tracking, not yet tuned

Running a real cycle against this dev sandbox (no reachable Kiwix/SearXNG/Ollama backends) surfaced two genuine rough edges in the checks themselves:

- **Source mismatch on the conditional path** — a conditional query's *condition* text gets routed through LLM-based source selection, which can validly land on a source that doesn't literally appear as an `INTENT_MAP` keyword in the query. The check doesn't yet distinguish "the LLM made a different valid call" from "the LLM made a wrong call" — right now it flags both the same way.
- **Part-count mismatch under fallback** — when every real source is unreachable and everything bottoms out at a `web` fallback, the result legitimately has no fusion-style header at all, which always reads as "1 header" regardless of how many intents were actually merged.

Neither is a defect in the generated queries or in Mnemolis's real routing — both are the detectors themselves needing another pass once they're run against live MiniDock traffic with real backends reachable. Recorded here rather than silently tuned away against a sandbox that can't actually exercise the real failure modes.

## Configuration

| Setting | Default | What it controls |
|---|---|---|
| `ADVERSARIAL_TEST_INTERVAL_MINUTES` | 60 | How often the scheduler tick fires |
| `ADVERSARIAL_TEST_BATCH_SIZE` | 8 | Queries generated per tick |

`/health` reports `adversarial_testing` alongside `snapshot_jobs`, using the same staleness-grace-multiplier convention (`SNAPSHOT_STALE_GRACE_MULTIPLIER`, default 3x) the snapshot engine already uses.

## Endpoint

`GET /adversarial/flagged?limit=50` — every currently-flagged combination, most recent first. Deliberately left unauthenticated, the same way `/health` and `/areas` already are: it exposes only synthetic, generated test queries and their structural anomaly flags, never real user queries or cache contents, so it sits outside `API_KEYS`' documented scope (`POST /search` and `GET /changes` only) for the same reason those two already do.
