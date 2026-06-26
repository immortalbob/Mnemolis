# Changelog

All notable changes to Mnemolis are documented here.

---

## [3.50.4]

### Fixed — `uptime`'s Recurring Benchmark Tail: a Persistent Socket.IO Connection, Not a TTL Change
The v3.50.3 entry deliberately left `uptime`'s warm-cache tail (reproduced across v3.17.0, v3.44.0, and v3.50.2) as an unconfirmed hypothesis rather than guess-fixing it by raising `CACHE_TTL_UPTIME_SECONDS`. `CACHE_TTL_UPTIME_SECONDS` (60s, the only source TTL shorter than the 120-second benchmark window) explains why a cache miss happens during a run — it never explained why that one miss costs 1.5-1.9 real seconds. That second question needed an actual read of `app/sources/uptime_kuma.py`'s connection mechanism, not just the TTL math.

**Confirmed directly by reading the file**: `search()` opened a brand-new Socket.IO connection, logged in, read data, and disconnected — on every single call, with no persistent connection or pooling. This isn't a rare, benchmark-only cost: `snapshot_uptime()` in `app/snapshots.py` calls this exact same `search()` function every 2 minutes via the background scheduler, independent of whether anyone is ever asking Mnemolis a live `uptime` question — the real-world rate of fresh connect/login cycles is the scheduler's 2-minute interval, not just live query cache misses.

Two options were considered and rejected before settling on the real fix. Raising `CACHE_TTL_UPTIME_SECONDS` would trade a deliberate design choice (uptime status staying close to real-time — a stale "all services up" is actively misleading in a way a stale weather forecast isn't) for hiding a cost that's still there underneath; it wouldn't make any individual connect/login cycle faster, just less frequent. Lowering `UPTIME_KUMA_TIMEOUT_SECONDS` bounds the failure case, not the success case — the observed 1.5-1.9s tail is a *successful* connection that's simply slow, not a near-timeout.

**The real fix**: a persistent Uptime Kuma connection, reused across calls and managed by `app/main.py`'s existing `lifespan()` — the same established pattern already used for the snapshot scheduler and the MCP session manager, not a new resource-lifecycle convention. `get_connection()` returns the shared connection, creating and logging in only on first use or after a confirmed-dead connection; `disconnect()` cleanly closes it on app shutdown via `stack.callback()`, mirroring `scheduler.shutdown()` a few lines below it. The connection is warmed once during startup (before the scheduler starts, so the scheduler's own immediate startup `snapshot_uptime()` call finds it already live) rather than paying the first connect cost on whichever request happens to arrive first.

**A real assumption from this fix's own design doc was wrong, and confirmed wrong before any code shipped, not after**: the doc's pseudocode checked `_persistent_api.connected`, flagging this explicitly as unverified and naming reading the actual installed library source as the required first step. Doing exactly that (`python3 -c "import uptime_kuma_api, inspect; print(inspect.getsource(uptime_kuma_api.UptimeKumaApi))"`) confirmed `UptimeKumaApi` has no `.connected` property of its own — the real liveness signal is its underlying `sio` attribute's `.connected`, a genuine public attribute on `python-socketio`'s `BaseClient` (`True` after a successful connect, `False` after any disconnect). Reading the library source also confirmed two more things the design doc treated as open questions: `UptimeKumaApi.__init__()` already calls `self.connect()` itself, so constructing the object performs the handshake — no separate `.connect()` call is needed; and `get_monitors()`/`get_heartbeats()` both read from `self._event_data`, populated by push events the server sends automatically after login and continuously thereafter (every real heartbeat gets pushed live to a persistent connection) — not two additional independent request/response round-trips on top of login, consistent with the design doc's "one handshake + one login exchange" correction to the v3.50.3 changelog's "real, slow-but-successful Socket.IO round-trip" framing.

A genuinely new failure mode this design introduces, absent from the old fresh-connection-every-time approach: a persistent connection can go stale (an Uptime Kuma restart, a dropped network socket) in a way a fresh-every-time connection structurally cannot. `get_connection()` checks `sio.connected` before reuse and transparently reconnects if the existing connection is dead, rather than silently reusing it in a broken state.

`search()`'s public contract is unchanged: same signature, same `not_configured` message, same `"Could not connect to Uptime Kuma: {e}"` failure format every existing test (and `fusion._looks_empty()`'s fallback detection) already depends on. `UPTIME_KUMA_TIMEOUT_SECONDS` keeps its exact existing meaning; `CACHE_TTL_UPTIME_SECONDS` is untouched.

### Added (Tests)
- `TestPersistentConnection` (6 tests) in `test_uptime_kuma.py` — proves the actual properties this fix introduces, not just unchanged output: connection reuse across two sequential calls (`UptimeKumaApi`'s constructor called exactly once, `get_monitors`/`get_heartbeats` still called fresh each time), dead-connection detection and replacement (a connection whose `sio.connected` goes `False` between calls gets discarded and replaced, not silently reused), genuine thread-safety under real concurrent calls (8 threads calling `search()` at once still construct exactly one connection), `disconnect()` actually closing a live connection and leaving the module ready for a fresh one afterward, and a safe no-op when `disconnect()` is called with no connection ever established. One test (`test_get_connection_uses_sio_connected_not_a_nonexistent_api_connected`) is a deliberate regression guard for the wrong-attribute assumption above — confirmed it actually catches that exact mistake by temporarily reintroducing it and watching the dead-connection test fail before restoring the fix.
- `TestUptimeKumaLifespanIntegration` (2 tests) in `test_main.py` — confirms `get_connection()` genuinely gets exercised during app startup when `UPTIME_KUMA_URL`/`UPTIME_KUMA_USERNAME` are configured (and `disconnect()` runs on shutdown, not before), and confirms neither gets called at all when uptime is left unconfigured — the same graceful-disable behavior every other optional source already has.
- Existing `TestUptimeKumaStatus`/`TestUptimeKumaConfigurableTimeout` tests updated for the new mocking shape (`mock_api.sio.connected = True` instead of the old `__enter__`/`__exit__` context-manager mocks, since `search()` no longer uses `with UptimeKumaApi(...) as api:`) — every original assertion preserved exactly, only the mock construction changed to match the real new call pattern.

### Changed
- `wiki/Sources.md`'s `uptime` section and `wiki/Caching.md` updated to note the connection is now persistent across calls, not opened fresh each time
- Version bumped to 3.50.4 — also corrects `app/main.py`'s FastAPI `version=` string, which had drifted to a stale `3.50.1` (the changelog itself was already correctly at 3.50.3; this was a separate, pre-existing string nobody had re-synced)

**Total test count: 1252**

---

## [3.50.3]

### Fixed — `/health`'s Seven Source Checks Now Run Concurrently, Not Sequentially
The first real Locust benchmark run since v3.44.0 (see `BENCHMARKS.md`) surfaced a fresh, previously-undocumented finding: a warm-cache `/health` sample hit `5244ms`, several times worse than its own `750ms` median. `_check_kiwix()`, `_check_forecast()`, `_check_news()`, `_check_web()`, `_check_uptime()`, `_check_ha()`, and `_check_llm()` were plain sequential calls in `app/main.py`'s `health()` endpoint, each a real network request with its own 3-5 second timeout — one or two genuinely slow real checks (the LLM ping reaching across to a separate machine; a slow SearXNG `/healthz`) could stack additively into a multi-second worst case.

This is the same "sequential where it could be concurrent" shape already fixed elsewhere in this codebase (`fusion.py`'s multi-source dispatch, `searxng.py`'s query-expansion chain, `_resolve_conditional()`'s condition/remainder split) — `/health` just never got the same treatment, since it was never on anyone's hot path the way search queries are.

Fixed with the same `ThreadPoolExecutor` pattern `fusion.py` already established, genuinely simpler here since every `_check_*` function already catches its own exceptions internally and never raises — none of `fusion.py`'s `as_completed()`/exception-propagation handling was needed.

### Added (Tests)
- `TestHealthConcurrentSourceChecks` (3 tests) in `test_main.py` — a real timing-based proof of genuine concurrency (not just that the response shape is unchanged, which a refactor that accidentally stayed sequential could still pass), confirmation a single slow check doesn't block the other six from completing, and confirmation response content is identical regardless of execution order. Both timing tests needed to mock `requests.get` at two separate import sites (`app.main` and `app.sources.kiwix`, since `get_books()` makes its own independent real network call) and needed a genuinely valid, non-empty OPDS feed in the mock response — an empty feed makes `get_books()`'s own real caching check (`if _book_cache: return _book_cache`, falsy for an empty list) re-fetch on every single call rather than ever caching, which would have made the timing assertions measure that separate, pre-existing quirk instead of the concurrency property actually being tested.

### Investigation Note — A Real, Separate, Pre-Existing Quirk Found While Writing the Tests Above, Not Yet Fixed
`get_books()`'s cache check (`if _book_cache: return _book_cache`) is falsy for a genuinely empty list — a Kiwix instance with zero books in its catalog (a fresh install before any ZIM files are added, or a real outage) would never actually cache that empty result, and would re-fetch the full catalog on every single call that touches it, forever, rather than caching the "no books" result the same way a real result gets cached. Found incidentally while building the `/health` concurrency tests above; out of scope for this release, not yet fixed.

### Changed — Real Benchmark Run Against Current Code (First Since v3.44.0)
A full cold/warm Locust run (20 users, 120s, the same methodology every prior entry uses) against the current v3.50.1 codebase — the last real benchmark in `BENCHMARKS.md` was v3.44.0; everything since (the config-completeness audit, Adversarial Self-Testing's full build-out, Cross-Source Temporal Pattern Detection, and the full latency-parallelization investigation) had never been measured under real load. See `BENCHMARKS.md`'s new v3.50.2 entry for the full tables.

**Real, measured improvements, consistent with documented fixes shipped in between:** `web`'s cold p99 dropped from `3900ms` to `1300ms`; `discourse_framing`'s cold p98 dropped from `4200ms` to `2100ms`. Both plausible given the query-expansion concurrency fix and the discourse-framing/fusion-merge fixes, though neither drop's exact magnitude is cleanly attributable to a single documented fix — flagged honestly as directionally consistent, not precisely proven.

**`uptime`'s warm-cache tail reproduced a third time (v3.17.0, v3.44.0, now v3.50.2) — and this run produced the first real, testable hypothesis for it.** Unlike `auto`/`conditional`'s already-explained thundering-herd cache-write collisions (which need a small query pool to collide on), `uptime`'s benchmark task uses one fixed, literal query — there's no pool here, so that explanation can't apply. `CACHE_TTL_UPTIME_SECONDS` (60s) is the only source TTL shorter than the 120-second benchmark run itself, meaning the `uptime` cache entry genuinely expires and gets refetched live mid-run, every run. The observed tail sits comfortably within `UPTIME_KUMA_TIMEOUT_SECONDS`'s 10-second cap, consistent with a real, slow-but-successful Socket.IO round-trip rather than a timeout. **Deliberately left unconfirmed and unfixed** — raising `CACHE_TTL_UPTIME_SECONDS` on the strength of an unconfirmed benchmark-methodology hypothesis would trade a real, deliberate design tradeoff (uptime status staying close to real-time) for a guess; confirming this needs either a direct check of Uptime Kuma's own connection logs during a run, or a diagnostic run with the TTL temporarily raised, neither of which was done this release.

**Real, fresh finding, not previously documented**: `/health`'s own worst-case latency, traced and fixed above.

### Changed — `tests/locustfile.py` Pools Widened, Per a Recommendation Made Twice and Not Previously Acted On
`AUTO_QUERIES` widened from 6 to 12 entries; `CONDITIONAL_QUERIES` widened from 4 to 8; `CONDITIONAL_WITH_REMAINDER_QUERIES` widened from 2 to 4 — diluting the thundering-herd cache-write-collision odds the v3.44.0 benchmark entry already identified and recommended fixing, reproduced again (worse: a full `10000ms` p99) in this release's run. Every new entry verified directly against `detect_intent()`/`detect_conditional()` before being added, not assumed — one initial candidate (`"check if everything is running"`) was caught resolving to the wrong source (`kiwix`, not `uptime`) and replaced before being committed.

**A real cross-file dependency caught by the existing test suite, not missed:** `app/adversarial_testing.py`'s `CONDITIONAL_SEEDS` must have a matching entry for every `CONDITIONAL_QUERIES` entry in `locustfile.py` — `TestSeedVocabularyIntegrity` enforces this directly, specifically to keep the two corpora from silently drifting apart, and correctly failed when the widening above landed without a matching `CONDITIONAL_SEEDS` update. Fixed by adding the four new condition fragments there too.

The widened pools have not yet been re-benchmarked — confirming whether this actually reduces the collision rate, rather than just moving it around, is the natural next run.

### Changed
- Version bumped to 3.50.3

**Total test count: 1244**

---

## [3.50.2]

### Changed — Wiki Restructured: Reference-First Pages, Dev-Blog-Style Design History
The wiki had accumulated a real, genuine problem across many releases: bug-discovery narrative was interleaved sentence-by-sentence with the actual mechanism reference on most pages, making it hard to read a page for "how does this work today" without wading through "here's how we found out it didn't." Every page in the wiki was re-read and restructured on the same principle: reference content first, real bug history either condensed into a same-page "Development Notes" section at the bottom, or — when a story had its own real arc worth reading independently — promoted to a new, dedicated saga page.

**Five new dedicated saga pages**, each pulling fully-told investigation narrative out of the reference pages it used to live inside:
- **The Fusion Merge Bugs** — the three-bug same-source merge chain, the `[FUSION — FUSION]` double-header bug, and the mixed-speed timeout crash, extracted from `Fusion.md`
- **The MCP Transport Migration** — rewritten with the corrected chronology after a changelog cross-check found an ordering mistake: an external community audit triggered the SSE→Streamable HTTP migration; the session-manager caching bug was found and fixed *during* that same migration, before it ever shipped; a real client connecting for the first time *afterward* found two more bugs (a doubled `/mcp/mcp` endpoint path, LAN connections rejected by DNS-rebinding protection) that no in-process test could have caught
- **The Caching Concurrency Investigation** — synthetic Adversarial Self-Testing traffic silently polluting real caches, and the separate file-write race found while investigating it
- **The Latency Parallelization Investigation** — the single most cross-page-scattered story in the project's history, consolidated into one page told in the order it actually happened, including the real regression one fix shipped with and the second fix that avoided repeating it
- **The Adversarial Testing Production Bugs** — the complete record of everything Adversarial Self-Testing has found in Mnemolis itself: the four-bug fusion-merge chain (from the production-discovery side, cross-linked to the merge-logic mechanism page rather than re-told), the false positive and the regex bug underneath it, the Uptime Kuma timeout, and the investigation that ended without a root cause

**Fifteen existing pages** gained a same-page "Development Notes" section, with narrative trimmed out of the reference text above it: `Sources`, `Routing`, `Query-Decomposition`, `Conditional-Query-Detection`, `Fusion`, `LLM-Client`, `Home-Assistant-Integration`, `Multi-Book-Fusion`, `Kiwix-Disambiguation`, `Snapshot-Engine-and-Changes`, `Caching`, `Confidence-Aware-Fusion`, `Configuration-Reference`, `Health-and-Observability`, `First-Time-Setup`.

**Real gaps found and closed that the wiki had never actually documented**, surfaced by a full, deliberate re-read of `CHANGELOG.md` against the wiki's existing content rather than trusting the wiki's own prior coverage:
- `home_assistant.py` — an area-scoped query (e.g. "indoor air quality in the living room") silently skipping real `exclude_entity_keywords` filtering that the same unscoped query already correctly applied; the three-bug chain around `binary_sensor`-style motion entity support never being wired into the relevant keywords; a small grammar fix — none of these had a wiki mention before this pass
- `freshrss.py` — 9 of 9 natural phrasings of a general news request ("tell me the news," "give me the headlines," etc.) being misclassified as specific-topic queries
- `Caching` — fusion's cache key applying at the sub-query level too, not just top-level, for a decomposed query whose own clause resolves to internal fusion
- `Kiwix Disambiguation` — the three single-guess prompting strategies tried and discarded before the current multi-candidate architecture was settled on, previously undocumented

**A real duplication found and closed**: `Cross-Source-Temporal-Pattern-Detection.md` told the same motion-tracking gap twice, in two different sections, with slightly different wording each time.

**Programmatic link/anchor verification, run after every single page edit rather than once at the end** — built a slugifier matching GitHub's real heading-anchor algorithm and checked every `[text](Page#anchor)` link in the wiki against it. Caught and fixed real breaks every time a section was moved or a heading was renamed, including the exact double-hyphen em-dash failure mode this verification was specifically built to catch (`[FUSION — FUSION]`-style headings slugify to a single hyphen, not two, since GitHub collapses the punctuation and surrounding whitespace together).

**Home.md re-examined directly** (not just trusted as already-correct) and found three real issues: Core Concepts was ordered Routing-before-Decomposition-before-Conditional-Detection, contradicting the actual pipeline order stated in the pages it links to (conditional detection runs first, then decomposition, then per-piece routing) — reordered to match; the LLM Client entry credited only two of its three real dependents (`Routing`, `Kiwix Disambiguation`) and omitted `Query Expansion` entirely, and none of the three actually linked back to `LLM-Client` despite Home's own description claiming the dependency — fixed both the description and added the three missing cross-links; Backup & Restore's summary said "five data files," stale since `temporal_patterns.db` brought the real count to six.

**Design History reordered to be genuinely chronological**, with an explicit note that it is — it had drifted into extraction-order-this-session rather than actual-event-order.

### Changed — README Corrected Against Actual Code and the Restructured Wiki
A full line-by-line read of `README.md`, with every numeric default in the Configuration table re-verified directly against `app/config.py` (all 64 settings checked; every one was already accurate) and every claim cross-checked against current code or the now-restructured wiki.

**Real, internal README inconsistencies found and fixed:**
- The `GET /backup` endpoint blurb said "four files," while the Backup & Restore section two pages later — correctly — said six. Both now say six.
- Test count claim was stale: 1204, actual current count is 1241.
- Disambiguation candidate count was overstated as a flat "3" in two places (the LLM-routing list and the Kiwix Internal Flow diagram); the actual function generates 2-3, matching the wiki's own more careful phrasing. Both fixed.

**Real errors found outside the README, in files people actually copy and run:**
- `docker-compose.example.yml`'s header comment still documented `http://your-host:8888/mcp/sse` — the pre-migration SSE endpoint, removed back in the v3.19.0 transport migration. Fixed to the real, current `/mcp` Streamable HTTP path.
- `mnemolis_tool.py`'s `MNEMOLIS_URL` default (`http://mnemolis:8000`) and its own description field (which only mentioned port `8888`) were genuinely confusing read together — the default itself is correct (the container's internal port, for same-Docker-network deployments), but nothing explained why it differed from every `8888` reference in the README, which documents the host-mapped port for external access. Description rewritten to state both cases explicitly rather than changing a default that was already right.

**A real documentation gap, not an error**: `FORECAST_TIMEZONE` — a third, genuinely separate timezone setting from `TZ`/`LOCAL_TIMEZONE`, controlling only what timezone Open-Meteo expresses forecast sunrise/sunset times in — had no mention anywhere in the README's Timezone configuration section. Added.

**Debloated**: two Configuration table cells (`UPTIME_KUMA_TIMEOUT_SECONDS`, `SEARXNG_REQUEST_TIMEOUT_SECONDS`) had bug-history framing ("previously a hardcoded 30...") baked directly into the table description, duplicating — less cleanly — what the wiki's own `Configuration Reference` page already separates into a clean reference cell plus a linked Development Note. Trimmed both to state current behavior only.

Checked and confirmed *not* stale, despite initially looking like candidates: the README's seven architecture diagrams (Voice Assistant Flow, Multi-Client Architecture, Snapshot Engine, Source Fusion, Query Decomposition, Conditional Query Detection, Kiwix Internal Flow) were suspected of duplicating the wiki's own per-mechanism diagrams — checked one directly (Query Decomposition) against the wiki's version and found they're complementary, not redundant: the README shows the general decision structure, the wiki shows a real worked trace. Left as-is.

### Changed — Section-Placement Review, and a New Page for a Real Gap That Review Surfaced
A direct question about why `Kiwix`/`Web & News` mechanism pages sit in their own Deep Dive sections rather than `Core Concepts`, and whether `Cross-Source Temporal Pattern Detection` belongs in `Core Concepts` too, led to checking the actual import graph rather than relying on the existing section framing.

**Confirmed correct, with the real reasoning written down for the first time**: `Core Concepts` is genuinely "the per-query pipeline every (or almost every) query passes through" — Sources, Conditional Detection, Decomposition, Routing, LLM Client, Fusion, Caching, Timezone Conversion. The three Kiwix pages and two Web & News pages are correctly excluded — `_score_result()`, `_get_disambiguation_candidates()`, `_pick_books_with_llm()`, and `_fuse_multi_book_results()` are genuinely Kiwix-internal, confirmed directly: nothing outside `kiwix.py` calls any of them. `Adversarial Self-Testing` and `Cross-Source Temporal Pattern Detection` are correctly excluded too, for a different, real reason: both are background jobs on the same `apscheduler` infrastructure as the snapshot engine, running on a timer independent of whether anyone ever queries Mnemolis at all — not steps in a query's own path.

**One real, genuine miss found in that same review**: `kiwix.py` exports two module-level constants — `DISCOURSE_FRAMING_PATTERNS` and `_STOP_WORDS` — that `router.py` imports directly and depends on for logic with nothing to do with whether a query ever reaches Kiwix at all (the discourse-framing routing bias in `Routing`, and the meaningful-content filter in `Query Decomposition`), and `adversarial_testing.py` depends on the same pattern list for its own seed vocabulary. This is the identical shape of cross-cutting dependency already correctly called out for `LLM-Client` — but it had no cross-links in the direction that matters (from `Routing`/`Query-Decomposition` back to where the shared list actually lives) until this pass. Added: a link from `Routing`'s discourse-framing section to [The Discourse-Framing Investigation](https://github.com/immortalbob/Mnemolis/wiki/The-Discourse-Framing-Investigation#a-real-deliberate-single-source-of-truth), a parallel link from `Query Decomposition`'s meaningful-content filter (confirmed it's the exact same `_STOP_WORDS` set, not a separate router-local copy), and a new note in `Home.md`'s `Core Concepts` section naming the dependency explicitly so a reader isn't surprised by it later.

**A real, substantial gap found while checking that import graph**: `kiwix.py`'s own catalog-discovery and article-fetching mechanism — `_fetch_catalog_page()`, `get_books()`, `refresh_catalog()`, `_search_book()`, `_fetch_article()` — had zero wiki coverage anywhere, despite all three existing Kiwix Deep Dive pages silently assuming "your catalog" and "a search result" already exist. Real, substantial mechanism sitting underneath all three: OPDS feed pagination with a real termination condition, the exact field used to extract a book's full versioned name, a real security hardening (`defusedxml` instead of the standard library's XML parser, specifically to reject entity-expansion attacks), and two further previously-undocumented real bugs — a CSS-selector-vs-tag-name bug that meant table-of-contents boxes were never actually being stripped from any fetched article despite the code's clear intent, and an unbounded article-fetch retry loop with a real, quantified worst case (up to 59 sequential attempts at a 10-second timeout each, nearly 10 minutes for one request) capped at 5. Both bugs already had a one-clause mention in `Roadmap.md`/`Benchmarks.md` ("broken table-of-contents stripping," "a capped retry loop instead of an unbounded one") but no actual explanation anywhere of what either meant.

**New wiki page**: **Kiwix Catalog & Article Fetching**, added to the Kiwix Deep Dive section ahead of the three existing pages, since it covers what they all assume already happened. Cross-linked from `Kiwix-Disambiguation`, `Kiwix-Scoring`, `Multi-Book-Fusion`, `Sources`, `Health-and-Observability`, `Configuration-Reference` (×2), and the README (×2, including the existing `/catalog/refresh` documentation and the Kiwix Internal Flow diagram's closing cross-reference line).

### Changed
- Version bumped to 3.50.2

**Total test count: 1241** (unchanged — this release is documentation and config-comment corrections only, no application code touched)

---

## [3.50.1]

### Added — `last_flagged_result_excerpt`, Because an Investigation Just Hit a Wall the Schema Itself Built
A real `unexpected_empty` flag (`"vs Python and JavaScript, plus while at work"`, routed to `changes`) got traced as far as production logs and live container state would allow — six separate, real hypotheses checked and ruled out one at a time: the LLM being down (it was, but this query never touches it), a different query's failures bleeding across in the same batch, a time-window edge case, cold-start snapshots, a swallowed exception, a timezone misconfiguration, and stale message text. Every one confirmed negative against real evidence, not assumption. The investigation reached the genuine limit of what was recoverable and stopped — `times_generated` was `1`; it had only happened once, and there was nothing left to check.

The real, lasting finding wasn't a root cause — it was that `adversarial_combinations` recorded *that* a known empty/error phrase matched, but never *what* the actual response text was. Once that one occurrence was gone, no amount of code-reading could recover it.

New `last_flagged_result_excerpt` column (up to 500 characters), populated only when a flag genuinely fires — never on a clean run, so the overwhelming majority of combinations that never need this stay exactly as small as before. Exposed through `GET /adversarial/flagged` and `review_flagged.py`'s own `list`/`list-dismissed` output. Migrates cleanly onto MiniDock's real, already-running database the same way every previous schema addition to this table has.

### Added (Tests)
- `TestFlaggedResultExcerpt` (6 tests) in `test_adversarial_testing.py` — excerpt stored only on a real flag, truncation at 500 chars, exposure through the public API, preservation through a later clean run (the same "don't erase the original evidence" convention `first_flagged_*` already follows), and a real migration test against a reconstructed pre-existing database matching MiniDock's actual schema

### Changed
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the full, honest account of the investigation — including that it ended without a root cause, which is itself the real finding
- Version bumped to 3.50.1

**Total test count: 1241**

---

## [3.50.0]

### Fixed — `conditional_with_remainder`'s Condition and Remainder Now Run Concurrently
The other half of the latency-stacking investigation, finished properly this time. `_resolve_conditional()`'s condition and remainder calls used to run as two separate, sequential, blocking `route_with_source()` calls — found via real, live Adversarial Self-Testing latency data, and originally left as a documented, accepted cost on the (re-examined and corrected) reasoning that the surrounding code's unrelated real bug history made any change here too risky.

Re-deriving the actual data dependencies directly: the condition and remainder calls don't depend on each other at all — the same as [Query Expansion](https://github.com/immortalbob/Mnemolis/wiki/Query-Expansion)'s two SearXNG fetches, fixed in 3.49.0/3.49.1. Built on the lesson from that fix rather than relearning it: each task submitted to the executor gets its own `contextvars.copy_context()` call before submission, so `suppress_cache_writes()` correctly propagates into both worker threads (the exact regression 3.49.0 shipped with and 3.49.1 fixed). Only spins up the thread pool when a remainder genuinely exists — a plain `"if X, Y"` query with no trailing conjunction (the more common real-world shape) has an empty remainder and never needed a second call in the first place; that path is byte-for-byte unchanged.

Verified with the same three checks that caught two real bugs in the `web` case before trusting it: a genuine timing proof of concurrency, direct confirmation `suppress_cache_writes()` reaches both worker threads correctly, and confirmation normal caching still works when suppression isn't active. A fourth check — real exception propagation from the remainder thread — was added too, since this case's failure semantics differ from the alternate-phrasing chain's deliberately non-fatal design. All four passed cleanly; no second regression this time.

Verified against realistic timings matching the real flagged query's shape: `2.0s` concurrent versus what would have been `3.5s` sequential.

### Added (Tests)
- `TestConditionalRemainderConcurrency` (5 tests) in `test_router.py` — genuine concurrency timing, the empty-remainder fast path confirmed unaffected, `suppress_cache_writes()` propagation into both threads, normal unsuppressed caching, and real exception propagation

### Changed
- Wiki's [Conditional Query Detection](https://github.com/immortalbob/Mnemolis/wiki/Conditional-Query-Detection) and [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated to reflect this is now fixed, not just found feasible — both real recipe-latency-variance mechanisms this investigation uncovered are now resolved at the root
- Version bumped to 3.50.0

**Total test count: 1235**

---

## [3.49.1]

### Fixed — A Real Regression in 3.49.0's Own Concurrent Fetch Fix
Asked directly afterward whether the *other*, more cautiously-treated parallelization candidate (conditional+remainder's sequential routing) was actually infeasible or just assumed to be. Re-deriving the real data dependencies confirmed it has no more of a dependency problem than the `web` query-expansion case already fixed — and re-examining the original "this code has a real bug history" caution found both real bugs live in unrelated parsing/interpretation logic, not in call ordering.

That re-investigation is what surfaced this: researching whether `ThreadPoolExecutor` actually propagates `contextvars.ContextVar` state into worker threads (confirmed, via official Python docs: it does **not**, by default) led to testing the *already-shipped* 3.49.0 fix directly against this. Found a real, live regression: `suppress_cache_writes()` active in the calling thread was being silently ignored inside the concurrent alternate-phrasing thread in `searxng.py`, meaning a synthetic Adversarial Self-Testing query could leak a real write into the routing cache — precisely the bug `suppress_cache_writes()` exists to prevent, reintroduced by 3.49.0's own fix. Confirmed directly with a failing test before this fix.

Fixed by giving each task submitted to the executor its own `contextvars.copy_context()` call before submission, rather than submitting the functions directly. A first attempt shared one captured context between both tasks, which failed a second, separate way — confirmed via a real `RuntimeError: cannot enter context... already entered` from the test suite itself: a single `Context` object cannot be entered by two threads simultaneously (`Context.run()` is documented as non-reentrant across concurrent execution). Each task needs its own, independently-copied context.

### Added (Tests)
- `test_suppress_cache_writes_genuinely_suppresses_writes_from_the_concurrent_alternate_thread` and `test_normal_unsuppressed_call_still_caches_correctly` in `test_searxng.py` — the real regression, reproduced and confirmed fixed, plus confirmation normal caching still works when suppression isn't active

### Changed
- Wiki's [Caching](https://github.com/immortalbob/Mnemolis/wiki/Caching), [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing), and [Conditional Query Detection](https://github.com/immortalbob/Mnemolis/wiki/Conditional-Query-Detection) updated with the full, honest account — including correcting Conditional Query Detection's own earlier reasoning for not parallelizing that case, which conflated "this code has unrelated bug history" with "this specific change is risky." That case is now recorded as likely feasible and not yet attempted, a meaningfully different status than "deliberately rejected" — distinct from being implemented in this release
- Version bumped to 3.49.1

**Total test count: 1230**

---

## [3.49.0]

### Fixed — A Real, Pre-Existing Concurrent File-Write Race in Both Caches
Found while researching whether `web` query expansion's two sequential SearXNG fetches could safely be parallelized — auditing every writer of the routing cache surfaced a real, separate, pre-existing bug unrelated to that question's eventual answer. Both `_save_routing_cache()` and `_save_cache()` persisted to disk with a bare `open(path, "w")` followed by `json.dump()`, with no protection against two concurrent writers truncating the same file at the same time.

This was never a hypothetical risk the parallelization work below would introduce — FastAPI's `/search` endpoint is a synchronous route, so Starlette already runs genuinely concurrent real requests on its own thread pool today, making two near-simultaneous cache saves a real, already-live scenario. Confirmed directly, not just reasoned about: a deliberate 8-writer/8-reader concurrent stress test against the old pattern produced 79,609 JSON corruption errors in two seconds. The real blast radius was bounded (the existing `except json.JSONDecodeError` fallback in `load_routing_cache()` already catches a corrupted file and starts fresh rather than crashing), but silently losing the entire on-disk cache on next restart was still a real, avoidable cost.

Fixed with a new shared `_atomic_write_json()` helper in `router.py`: write to a temporary file in the same directory, then `os.replace()` onto the real target — atomic on POSIX (what this project runs on in production), so the file is always either the complete old version or the complete new one, never a partial write from either side. The identical stress test against the fix: zero errors. Both `_save_routing_cache()` and `_save_cache()` now share this one helper.

### Fixed — `web` Query Expansion's Primary Fetch and Alternate-Phrasing Chain Now Run Concurrently
The actual goal of this investigation, made possible by the fix above: `searxng.py`'s `search()` used to pay for the primary SearXNG fetch, the LLM call behind [Query Expansion](https://github.com/immortalbob/Mnemolis/wiki/Query-Expansion)'s `get_alternate_phrasing()`, and a second SearXNG fetch entirely sequentially — a real, live Adversarial Self-Testing latency flag traced this to roughly 4x the cost of a single fetch on a query that triggered expansion.

The primary fetch and the alternate-phrasing chain have no real data dependency on each other (`get_alternate_phrasing()` only needs the original query text), so unlike [Conditional Query Detection](https://github.com/immortalbob/Mnemolis/wiki/Conditional-Query-Detection)'s own, similarly-shaped sequential cost (left as a documented, accepted tradeoff — see that page), this one was both genuinely parallelizable and, once the file-write race above was confirmed fixed, safe to actually parallelize. Both run concurrently now via a small `ThreadPoolExecutor`, the same pattern `fusion.py` already uses for its own multi-source dispatch — including correctly preserving the primary fetch's specific, real, user-facing timeout message (see "The SearXNG Timeout Lesson") and the alternate chain's existing non-fatal-failure guarantee. Verified against the exact original repro timings: `4.15s` sequential down to `3.04s` concurrent.

### Added (Tests)
- `TestAtomicWriteJson` (6 tests) in `test_cache_persistence.py` — including a real concurrent stress test (6 writer + 6 reader threads) proving zero corruption against the fix
- `TestSearxngConcurrentFetch` (4 tests) in `test_searxng.py` — a real timing-based proof of genuine concurrency (not just unchanged output), the timeout message preserved correctly while the alternate thread is still in flight, and the non-fatal-failure guarantee re-verified under real concurrency
- `TestUptimeKumaConfigurableTimeout` carried forward unchanged from 3.48.9

### Changed
- Wiki's [Caching](https://github.com/immortalbob/Mnemolis/wiki/Caching), [Query Expansion](https://github.com/immortalbob/Mnemolis/wiki/Query-Expansion), and [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the full investigation and the real, measured fix — the query-expansion latency case is no longer a documented limitation, it's resolved
- Version bumped to 3.49.0

**Total test count: 1228**

---

## [3.48.10]

### Documented — A Second, Real Latency-Stacking Mechanism Found After Clearing Caches and Re-Running
A fresh Adversarial Self-Testing flag after clearing both caches and running a few clean cycles: `nosplit_adjacent_to_real_conjunction` on `"difference between Iran and Israel, and find online"`, `6412ms` vs. a recipe p95 of `2502ms`. Traced to a genuinely different mechanism than the existing, documented conditional+remainder latency cost, despite the same symptom shape — this query never decomposes and resolves to a single source (`web`), no fusion or conditional handling involved at all.

The real cost lives entirely inside `searxng.py`'s `search()`: a primary SearXNG fetch, followed by [Query Expansion](https://github.com/immortalbob/Mnemolis/wiki/Query-Expansion)'s `get_alternate_phrasing()` (a real, blocking LLM completion call), followed by a *second* SearXNG fetch for the alternate phrasing — three sequential round-trips billed as one source's latency whenever expansion actually fires. Reproduced directly with realistic mocked timings at roughly 4x the cost of a single fetch.

Checked whether this is safely parallelizable, since the two fetches have no real data dependency on each other (unlike the conditional+remainder case): `_fetch_searxng()` is a pure function with no shared state, genuinely thread-safe. `get_alternate_phrasing()`'s own routing-cache read/write is the one place giving pause — not because it's known-unsafe, but because this project has real history (`suppress_cache_writes()`'s own design) showing concurrent cache access looks safe until it isn't. Documented as a known, accepted cost for now rather than attempted as a fix, the same call made for the conditional+remainder case — verifying it's actually safe to parallelize is real, separate work this finding didn't set out to do.

Two distinct recipes now have real, legitimate latency-variance mechanisms a single, global `ADVERSARIAL_TEST_LATENCY_OUTLIER_MULTIPLIER` can't tell apart from a genuine anomaly — recorded as a real, now twice-motivated design question (a per-recipe latency baseline) in the known-limitations section, not yet built.

### Changed
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) and [Query Expansion](https://github.com/immortalbob/Mnemolis/wiki/Query-Expansion) updated with the real, traced finding and a corrected account of exactly what the routing cache does and doesn't save on a repeat (skips the LLM call; does NOT skip the second SearXNG fetch, which only the alternate-phrasing *text* is cached, not its results)
- Version bumped to 3.48.10

**Total test count: 1218** (no code changes this release — investigation and documentation only)

---

## [3.48.9]

### Fixed — Uptime Kuma's Connection Timeout Was a Bare, Unconfigurable Literal
The second and last of the two genuinely unresolved Adversarial Self-Testing flags from this whole investigation. `unexpected_empty` on `"if any services are down, let me know right away, as well as lights off"`, latency `30056ms` — the number itself was the clue: `UptimeKumaApi(settings.uptime_kuma_url, timeout=30)` was hardcoded, and `30056ms` is exactly that plus normal overhead.

Confirmed via direct tracing that this is **not a Mnemolis bug** — the Uptime Kuma client connection genuinely timed out, the exception was caught, and Mnemolis correctly returned `"Could not connect to Uptime Kuma: {e}"` rather than hiding the failure. `fusion._looks_empty()` correctly recognized the real `"could not connect"` phrase, and the adversarial check correctly flagged the resulting empty response. Every layer worked exactly as designed.

The real, fixable gap: every other source touched by this project (`SEARXNG_REQUEST_TIMEOUT_SECONDS`, `FUSION_TIMEOUT_SECONDS`) already has a configurable timeout; Uptime Kuma's bare `30` was the one literal left over, despite being a same-LAN service that should respond in well under a second. New `UPTIME_KUMA_TIMEOUT_SECONDS` setting (default `10`), wired directly into the real client call — the documented fallback behavior on a genuine failure is completely unchanged, only how long Mnemolis waits before reaching it.

### Added (Tests)
- `TestUptimeKumaConfigurableTimeout` (3 tests) in `test_uptime_kuma.py` — confirms the new default, confirms the configured value is genuinely passed to the real `UptimeKumaApi` call (not a renamed constant defaulting to the same old value), and confirms the documented fallback message still appears correctly on a genuine timeout regardless of the configured value

### Changed
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the full investigation — both genuinely unresolved flags from this round are now real, closed findings, not open questions
- `README.md` and Configuration Reference updated with the new setting
- Version bumped to 3.48.9

**Total test count: 1218**

---

## [3.48.8]

### Fixed — A Real Header-Counting Bug Found While Actually Reviewing the Two Unresolved Flags
After the 3.48.7 undismiss mishap got sorted out, the two genuinely unresolved flags (`unexpected_empty`, `conditional_remainder_missing_sections`) got a real look instead of staying parked. Tracing `conditional_remainder_missing_sections` on `"if it is raining, I will be careful with communication, as well as feeds"` — a response this check claimed had zero real sections — led directly to `_HEADER_PATTERN`, the regex both this check and `_check_multi_intent_part_count` use to count headers in a result string.

The regex required exactly one literal `" — "` separator, with the character class after it deliberately excluding the em-dash itself. `kiwix`'s real label (`"ENCYCLOPEDIC KNOWLEDGE — UNRELATED TO OTHER SECTIONS BELOW"`) and `news`'s real label (`"RECENT NEWS HEADLINES — GENERAL, NOT LOCATION-SPECIFIC UNLESS STATED"`) both legitimately contain a *second* em-dash — so neither header could ever be matched, full stop, regardless of any threshold setting. Reconstructing the real flagged query's actual merge output confirmed this directly as the root cause.

This also reframes the two real `part_count_mismatch` flags from 3.48.1, which both involved `news` as an intended source: reconstructing a realistic 5-header result including both vulnerable headers showed the regex undercounting by exactly 2 — the precise shape of both flags' literal text (`"intended 5, found 3"`). The 3.48.1 fix (loosening the threshold to "fewer than half survived") was real and still correct, but it made the check tolerant of this exact undercount without ever finding why the undercount existed — this fix is the actual root cause, not a second layer on top of an unrelated one.

Several existing tests for both checks had been using fabricated header text (`"[KIWIX — A]"`) that happened to be *equally* invisible to the same broken regex for an unrelated reason — meaning the tests accidentally agreed with the bug instead of catching it. This is the real, structural reason it survived as long as it did.

**Fixed** by rebuilding `_HEADER_PATTERN` from the real, exact header strings `fusion._format_header()` actually produces (`re.escape()`'d, not a generic bracket-matching character class) — the same safe approach `router.py`'s own `_dedupe_nested_fusion_sections()` already uses for the identical need.

### Added (Tests)
- `TestHeaderPatternMatchesEveryRealHeader` (4 tests) — every real header matched exactly once, the two specifically-vulnerable headers tested explicitly, a real 5-header reconstruction counting correctly, and confirmation fabricated header text is correctly rejected (the fix is a genuine exact-match, not a slightly-widened pattern that could drift again)
- Every existing test for `_check_multi_intent_part_count` and `_check_conditional_remainder_sections` that used fabricated header text rewritten to use real strings from `fusion._format_header()`

### Changed
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the real, corrected root-cause story for both the `conditional_remainder_missing_sections` flag and the earlier `part_count_mismatch` flags
- Version bumped to 3.48.8

**Total test count: 1215**

---

## [3.48.7]

### Added — `POST /adversarial/undismiss`, the Real Reversal Dismiss Never Had
Found necessary via real usage on MiniDock, not written defensively up front: a real batch-dismiss review session matched index numbers against a flagged-combination listing fetched a turn earlier, rather than a freshly re-fetched one — the live queue had reordered in between (a new flag had recorded since), so the indices no longer lined up with the current list, and two genuinely unresolved flags (`unexpected_empty`, `conditional_remainder_missing_sections`) got dismissed alongside seven that were actually understood and fixed. There was no way back short of editing the database by hand.

New `undismiss_flagged_combination()` in `app/adversarial_testing.py`, the real, symmetric counterpart to the existing `dismiss_flagged_combination()` — restores `review_status` to exactly `NULL`, the same value a combination has before its first-ever dismissal, not a new third state. New `POST /adversarial/undismiss?fingerprint=...` endpoint, mirroring `/adversarial/dismiss`'s exact contract (404 on unknown fingerprint; a fingerprint that was never dismissed is a safe no-op).

### Added (Tests)
- 6 new tests across `test_adversarial_testing.py`: the real restore-to-default-view round trip, confirming the restored state is exactly `NULL` and not a distinct sentinel, the unknown-fingerprint 404 case, the never-dismissed no-op case, and the full endpoint-level round trip via `TestClient`

### Changed
- `review_flagged.py` (the standalone CLI helper) updated with `list-dismissed`, `undismiss`, and `undismiss-all` commands, and `list`/`dismiss` now always re-fetch the live queue immediately before acting rather than trusting an index from an earlier `list` call — the exact gap that caused the real mis-dismissal this release fixes
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the new endpoint and the real story behind why it exists
- Version bumped to 3.48.7

**Total test count: 1210**

---

## [3.48.6]

### Changed — Every Wiki Diagram and Numeric Claim Verified Against Real Code
A second documentation pass, narrower and deeper than 3.48.5's structural audit: every `text`-block diagram across all 15 wiki pages that have one, traced step by step against the actual function it claims to describe, plus a full programmatic cross-check of all 63 real config defaults against both the README and Configuration Reference. Not a re-skim — every diagram was walked against real code with real test queries, not assumed correct because it looked plausible.

**Real, confirmed errors fixed:**

- **`Query-Decomposition.md`** — the combined-split worked example claimed output `["check the weather", "are the lights on"]`; the real code produces `["check the weather and", "are the lights on"]` for that exact input, since `" also "` (6 chars) is tried before `" and "` (5 chars) in the real longest-first ordering and wins the tie. Diagram corrected to show the real output, with an explanation of the (harmless, routing-correct) trailing-conjunction artifact.
- **`Fusion.md`** — the flowchart's final step claimed `fusion.search()` performs a same-source merge. It doesn't, by construction (`valid`'s own dedup set guarantees no duplicate source ever reaches that point) — confirmed directly against the function and its own comment documenting exactly this. Diagram fixed; the real merge (a genuinely different function) is now clearly distinguished.
- **`Conditional-Query-Detection.md`** — the `forecast` interpretable-source bullet implied symmetric rain/storm keyword checking on both the condition and result sides. It's actually asymmetric (`rain`/`raining` only on the condition side; `storm`/`shower` are result-only signals) — confirmed directly with `_interpret_yes_no()`. The `uptime` bullet was also missing the real `"not up"` condition keyword. Both corrected.
- **`Multi-Book-Fusion.md`** — the fusion-decision diagram was missing a real guard (`top_score > 0`, with its own negative-score bug history) and presented the 50%-of-top-score threshold as a fixed constant. It's `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT`, a real, configurable setting — made configurable specifically because it's the page's own documented "central decision." Diagram and prose both corrected.
- **`The-Discourse-Framing-Investigation.md`** — the bitcoin-example diagram's intermediate search-terms claim (`"what whole bitcoin everyone obsessed"`) and specific score numbers (2 → 32) no longer reproduce against current code (`_build_search_terms()` now correctly strips `"what"` as a stop word; real current scores are different). Rewritten to state the real, current, reproducible search term and to be explicit that specific historical point values are illustrative of the mechanism, not a number to expect byte-for-byte today.
- **`SEARXNG_REQUEST_TIMEOUT_SECONDS`** — documented as `15` in three places (`Configuration-Reference.md` once, `README.md` twice); the real default in `app/config.py` is `10`. This one mattered beyond a cosmetic number: the surrounding advice ("set this to match or exceed SearXNG's own timeout") was undermined by citing the wrong figure, and the real default is now visibly below the `20`-second target [The SearXNG Timeout Lesson](https://github.com/immortalbob/Mnemolis/wiki/The-SearXNG-Timeout-Lesson) itself recommends — both docs now say so explicitly.
- **`README.md`** — 13 real settings (every `ADVERSARIAL_TEST_*` and `TEMPORAL_PATTERN_*` variable) were entirely absent from the config table, despite every other feature area's settings being present there with no "see wiki" deferral pattern anywhere else in the document. Added, using the same descriptions already established in Configuration Reference.

**Verified clean, no changes needed:** `Routing.md`, `Kiwix-Disambiguation.md`, `LLM-Client.md`, `Snapshot-Engine-and-Changes.md`, `The-Recursion-Design-Bug.md`, `The-SearXNG-Timeout-Lesson.md`, `The-Meaningful-Content-Filter-Bugs.md`, `Open-WebUI-System-Prompt-Guide.md` (its specific 3-part decomposition claim re-verified against current code), `Cross-Source-Temporal-Pattern-Detection.md` (including the specific "reuses the original corrected_threshold, never recomputes it" design claim, confirmed directly).

**Minor addition, not a correction:** `Query-Expansion.md` didn't mention that a generated alternate phrasing is itself cached in the routing cache (`altquery:{query}`) — a real, harmless gap, not an error. Added.

A full programmatic check confirmed zero broken internal links and zero broken anchor fragments across the entire wiki, re-run after every edit in this pass, not just once at the end.

### Changed
- Version bumped to 3.48.6

**Total test count: 1204** (no code changes this release — documentation only)

---

## [3.48.5]

### Changed — Full Wiki & README Audit Against Current Code
A systematic pass, not a skim: every real config setting, every endpoint, and every scheduled job cross-checked against what the wiki actually documents, followed by a full re-read of the last 15+ versions of this changelog (3.36.0 through 3.48.4) to catch anything a quick page-by-page check would miss. Settings, endpoints, and most fix-specific documentation updates from that window held up correctly — the project's habit of updating docs alongside fixes mostly worked. Six pages had real, confirmed drift; two real gaps had no dedicated page at all.

**`Routing.md`** — the decision-flow diagram and "discourse-framing bias" section described the bias as applying only to LLM-assisted source selection. As of 3.48.1, it also applies to the keyword-matching path, and a perfectly ordinary keyword match (`"rss"`, `"news"`, dozens of other common `INTENT_MAP` words) can otherwise silently defeat the entire point of detecting discourse framing. Diagram and prose corrected.

**`Query-Decomposition.md`** — "meaningful-content filtering" was described as purely stop-word-based, missing both the real-keyword check added in 3.48.1 and the ordering fix added in 3.48.2 that makes it actually work. Corrected, with a link to the new dedicated bug-history page below.

**`Fusion.md`** — promised a "(see below)" section on same-source merging that didn't exist, and had zero mention of three real, sequential bugs found this same investigation (`_dedupe_nested_fusion_sections()`, `_dedupe_items_across_blobs()`, and `_merge_same_source()`'s actual structural limitation). Added a full new section.

**`The-Discourse-Framing-Investigation.md`** — stated as its confident closing summary that "Black Hole of Calcutta" was the accepted, final outcome for a real query. Directly contradicted by later, more thoroughly verified evidence: the real fix makes the actual astrophysics article win. Corrected, with honest acknowledgment that the saga had two more real chapters after this page's original ending, not the two it looked like at the time.

**`Conditional-Query-Detection.md`** — no mention anywhere of the sequential (not parallel) condition+remainder routing and its real, additive latency cost, previously documented only inside Adversarial Self-Testing's own narrative where someone reading the actual mechanism page would never find it. Added.

**`README.md`** — one stale number: "1161 tests" corrected to the real, current 1204. Notable that there's a *prior* changelog entry documenting this exact line being corrected once before for the same reason — a real, recurring maintenance gap worth naming rather than just quietly fixing again.

### Added — Two New Wiki Pages for Real Gaps With No Dedicated Page

**[The Meaningful-Content-Filter Bugs](https://github.com/immortalbob/Mnemolis/wiki/The-Meaningful-Content-Filter-Bugs)** — the `"is it up"`/`"are they up"` stop-word-only-keyword bug and the `"rss"` length-gate-ordering bug, previously documented only inside Adversarial Self-Testing's narrative. Structurally distinct from [The Proper-Noun-Pair Saga](https://github.com/immortalbob/Mnemolis/wiki/The-Proper-Noun-Pair-Saga) (a different decomposition sub-mechanism), so it didn't belong folded into that page — it gets its own, the same way that saga did.

**[LLM Client](https://github.com/immortalbob/Mnemolis/wiki/LLM-Client)** — there was no dedicated page for `llm.py` at all, despite a real, serious historical bug (3.36.0: every single completion on the OpenAI-compatible path would silently return `None` for any thinking model) being mentioned in exactly one line of the Roadmap's terse summary list and nowhere else. Covers the dual-backend design, the fail-safe contract every caller depends on, and the thinking-model bug in full. A matching `Troubleshooting.md` entry added too, since this is exactly the shape of problem someone would hit on a real deployment and search for by symptom.

### Changed — Home.md Redesigned
Rewritten introduction: a longer, genuinely user-facing description of what Mnemolis actually does and why someone would want it, not just a one-line summary — real example queries, the actual list of backends, the local-first/no-subscription framing, REST+MCP accessibility. "Core Concepts" reordered to better match the real order a query actually flows through (`Sources` → `Routing` → `LLM Client` → decomposition/conditional/fusion). `MCP Server` and `Troubleshooting` moved from Core Concepts/Getting Started into Operations, where "how do I connect to this" and "something's broken" both more naturally belong. Both new pages added in their respective sections.

Every internal wiki link and anchor fragment — not just the ones touched this pass — verified programmatically against GitHub's real slugification rules: zero broken links, zero broken anchors, zero orphaned pages across the entire wiki.

### Changed
- Version bumped to 3.48.5

**Total test count: 1204** (no code changes this release — documentation only)

---

## [3.48.4]

### Fixed — Duplicate Content Inside a Correctly-Deduplicated Section, Found Verifying 3.48.3's Fix
Re-running the exact query from 3.48.3's fix against MiniDock's real stack confirmed that fix worked exactly as designed — a single, correct `[NEWS — ...]` header, not two — but surfaced a fourth, separate bug: the same several headlines still appeared twice inside that one, structurally-correct section's own body.

Root cause: `_dedupe_nested_fusion_sections()` (3.48.3) fixed the structural duplication, but the actual content join — both in `fusion._merge_same_source()` and in `_dedupe_nested_fusion_sections()`'s own header-merge branch — was a plain string concatenation with zero content-level awareness. Two genuinely independent calls to `news.search()` (one nested inside an internal-fusion sub-query, one a separately-decomposed clause's own bare resolution) both legitimately returned overlapping recent headlines, since FreshRSS's own `_is_general_query()` path returns "everything, no filtering" for a broad query — and nothing anywhere deduplicated across the two calls.

A first attempt deduped items *after* the join, by re-splitting the already-merged text on its own item separator — confirmed broken via a failing test: once two blobs are joined with a bare `"\n\n"`, the real boundary between them is no longer reliably distinguishable from an ordinary paragraph break within either blob's own content, so a later split can silently merge two items into one and miss a real duplicate. Fixed properly by moving the dedup *before* the join instead: a new `fusion._dedupe_items_across_blobs()` helper runs at the one point where the boundary between the two original results is still completely unambiguous, removing any item from the second blob whose leading `**Title**` line exactly matches one already in the first (exact match only, never fuzzy). Both join sites now also use the real `"---"` item separator (not a bare double-newline) when joining genuinely multi-item content, so the merged result's own visual structure stays as clean as the dedup logic needs it to be.

### Added (Tests)
- `TestDedupeItemsAcrossBlobs` (6 tests) in `test_fusion.py` — the real overlapping-headlines scenario, complete overlap, non-multi-item no-op, and confirmation `_merge_same_source()` itself now uses the correct separator for multi-item content while leaving plain content's existing behavior untouched
- `test_real_world_regression_case_with_overlapping_headlines` added to `TestDedupeNestedFusionSections` in `test_router.py` — the full real-world shape end-to-end, confirming every distinct headline appears exactly once with clean item separators

### Changed
- Version bumped to 3.48.4
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) extended with this fourth finding, documented as part of the same investigation as 3.48.1–3.48.3's fixes

**Total test count: 1204**

---

## [3.48.3]

### Fixed — Duplicate Section in Merged Fusion Results, Found Verifying the 3.48.2 Fix
Re-running the exact query from 3.48.2's fix to confirm it against MiniDock's real stack — and it worked, the real Black Hole article correctly led the kiwix section — surfaced a third, separate bug in the actual merged answer: a redundant second `[NEWS — ...]` section appeared near the end, duplicating real headlines already shown earlier (`[KIWIX, NEWS, WEB, NEWS]` instead of the correct `[KIWIX, NEWS, WEB]`).

Root cause: once decomposition correctly splits a query into two independent clauses, one clause's own LLM-judged source selection can land on internal fusion (multiple sources sharing one already-headered, nested blob), while a different, separately-decomposed clause resolves to a bare source that happens to be one of the sources already inside that blob. `_merge_same_source()` — which already correctly merges two bare same-source tuples — only ever compares the OUTER tuple label (`"fusion"` vs `"news"`, genuinely different), so it has no way to see a section nested inside the fusion blob duplicates the second, separate tuple. Confirmed real and pre-existing, not a regression from 3.48.1/3.48.2's fixes — already reachable via any query shape where one clause's ordinary LLM judgment happens to overlap with a different clause's source; this recipe's shape just made it reliably reachable instead of needing a rarer coincidence.

Fixed with a second, separate post-processing pass, `_dedupe_nested_fusion_sections()`, run on the final assembled text after the existing tuple-level merge. Splits on the exact, real header strings `fusion._format_header()` produces (`re.escape()`'d, not a generic bracket pattern — confirmed safe against real content containing bracket-like or dash-like text), merges duplicate sections' content while preserving first-occurrence position, and is a true no-op for the overwhelming majority of results that never contain a duplicate at all.

### Added (Tests)
- `TestDedupeNestedFusionSections` (8 tests) in `test_router.py` — the real production scenario, non-adjacent duplicates, 3+ duplicate headers, and a direct check that real content containing literal `[NEWS]` brackets or `---` dashes is never falsely treated as a section boundary

### Changed
- Version bumped to 3.48.3
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) extended with this third finding, documented as part of the same investigation as 3.48.2's fixes

**Total test count: 1197**

---

## [3.48.2]

### Fixed — A Real Answer-Quality Bug a Manual Verification Check Surfaced, Not Adversarial Self-Testing Itself
Manually confirming the 3.48.1 discourse-framing keyword-path fix against a live `/search` call returned a result that was technically correct by every Adversarial Self-Testing check (`source_used: "fusion"`, real `[NEWS — ...]` and `[KIWIX — ...]` sections both present) but had a real, visible answer-quality problem: kiwix returned an unrelated Space StackExchange thread and an unrelated podcast Wikipedia article for `"everyone keeps talking about black holes, and rss"`, never the real Black Hole article. Exactly the kind of thing this feature's own hard rule (never judge correctness) means it can't catch — finding it took a human reading a result, not a structural check — but worth fixing since it traces through the same recipe shape this project already generates synthetically.

Two distinct, real root causes, both upstream of anything already shipped:

**Decomposition failed to split the query, due to a real ordering bug.** `"rss"` — confirmed the only real `INTENT_MAP` keyword that is itself 3 characters or shorter — was discarded by `_filter_meaningful()`'s `if len(p) <= 3: continue` length gate *before* the `_ALL_INTENT_KEYWORDS` check (added in 3.48.1, for `"is it up"`/`"are they up"`) ever got a chance to protect it. Fixed by reordering the keyword/colloquial checks ahead of the length gate. Once decomposition correctly splits `"black holes"` from `"rss"` into two independent clauses, each routes and resolves on its own — kiwix never receives `"rss"` as part of its own search text at all, closing the cross-source pollution problem at the actual root rather than trying to make kiwix defensively robust against arbitrary noise it shouldn't have received in the first place. (A generic "strip every other source's keywords inside kiwix.py" approach was deliberately checked and rejected: confirmed real, legitimate kiwix queries like "tell me about the weather" or "what is google" would have been broken by stripping words that are simultaneously other sources' triggers AND genuine standalone topics.)

**Scoring never used the cleaned text search-term-building already produces.** [The Discourse-Framing Investigation](https://github.com/immortalbob/Mnemolis/wiki/The-Discourse-Framing-Investigation) correctly fixed discourse-framing words polluting the actual Kiwix search query, but `_score_result()` — which ranks whatever comes back — was never updated to match, and still scores against the raw, unstripped query. Confirmed this gap exists even for the *original* bitcoin case the wiki documents as fully fixed (`"everyone"`/`"obsessed"` are still real, counted words in that case's scoring today); it just never visibly changed the outcome there, since the real Bitcoin article's title-overlap signal was strong enough to win regardless. Fixed by stripping discourse framing from the word set used for keyword-overlap scoring specifically — the exact-match check and `_is_definitional_query()` still use the full, original phrasing, since both genuinely need real leading-phrase structure that the discourse strip doesn't touch.

### Added (Tests)
- `TestDecomposeShortKeywordBeforeLengthGate` (7 tests) in `test_router.py` — the real, live regression case plus boundary coverage confirming the reordering can't let trivial filler fragments through
- `TestScoreResultDiscourseFramingWordsExcluded` (5 tests) in `test_kiwix.py` — the real article now winning against both wrong candidates, the original bitcoin case still correctly passing, and `_is_definitional_query()` confirmed unaffected

### Changed
- Version bumped to 3.48.2
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) updated with the full investigation — both root causes, why the generic cross-source-stripping approach was rejected, and why this was found by manual verification rather than the automated checks themselves

**Total test count: 1189**

---

## [3.48.1]

### Fixed — Two Real Bugs Found by Tracing Real Adversarial Self-Testing Production Data
After roughly a day of real runs against MiniDock's actual stack (136 combinations tried, 9 flagged), every single flag was traced to its real root cause — not just the ones that looked interesting. Two were genuine, previously-unknown Mnemolis bugs.

**Discourse-framing escalation never ran on the keyword-match path.** [The Discourse-Framing Investigation](https://github.com/immortalbob/Mnemolis/wiki/The-Discourse-Framing-Investigation) correctly fixed all four real code paths inside `_llm_detect()` — but `detect_intent()`'s own `if source: return source` short-circuited before `_llm_detect()` (and every one of its four fixed paths) was ever reached, for ANY keyword match, single or multi. A real, live flag caught this directly: `"everyone keeps talking about black holes, and rss"` resolved to bare `"news"` in 35ms, kiwix never considered — reproduced and confirmed general, not a one-keyword edge case, since `INTENT_MAP` contains dozens of short, ordinary words that can easily co-occur with genuine discourse framing in a real sentence. Fixed by applying the same, already-existing escalation helpers directly inside `detect_intent()`'s keyword-match branch.

**Two real `INTENT_MAP` keywords made entirely of stop words were silently dropped during decomposition.** `"is it up"` and `"are they up"` (real, literal `uptime` keywords; confirmed the only two of all 113 real keyword phrases with this property) were discarded by `_filter_meaningful()`'s generic stop-word check, which has no awareness of `INTENT_MAP` at all — a clause consisting only of one of these phrases came back with zero real content words and vanished entirely from the decomposed result, not even folded into a neighboring clause. Fixed the same way `_COLLOQUIAL_PHRASES` already handles a structurally identical problem: a real keyword phrase now always counts as meaningful, closing the general case (any future all-stop-word keyword), not just these two by name.

### Fixed — A Real False Positive in Adversarial Self-Testing's Own `part_count_mismatch` Check
Tracing a third flag (5 intended intents, 3 headers) found a false positive, not a Mnemolis bug: decomposition and per-part routing were both correct; the 2 "missing" sources legitimately and correctly returned empty results, which `route_with_source()` deliberately drops before merging with no trace left behind. An exact-count comparison fundamentally cannot tell "legitimate empty results" apart from "real content loss" — both produce an identical signature.

Loosened the check from an exact-count-with-tolerance comparison to **"fewer than half of the intended sources produced any header at all"** — loose enough to never fire on ordinary empty-result variance across this recipe's real range (3–5 intended sources), while still catching a genuine large-scale collapse with the same shape as the original proper-noun-pair bug 5 this check exists to catch. As a direct, deliberate consequence, `ADVERSARIAL_TEST_PART_COUNT_MISMATCH_TOLERANCE` has been removed entirely — there's nothing left to tune; the new threshold is a fixed, principled rule, not a per-deployment knob. This also fixes a separate, worse blind spot the old check had for free: its `n_headers > 0` guard meant a complete collapse to zero headers could never be flagged at all, regardless of intended count — the new check correctly flags that case.

### Changed
- Version bumped to 3.48.1
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) rewritten with a full account of every flag from this real production run, including the false positive and the structural (non-bug) latency finding below
- New wiki section documenting conditional+remainder queries' sequential (not parallel) routing as a known, accepted, structural latency characteristic — deliberately not changed to a concurrency model, since the same conditional-handling code has already had two separate, carefully-reasoned bug fixes and a parallelization change would introduce real new risk for a payoff that's the less common case in the real data so far
- `ADVERSARIAL_TEST_PART_COUNT_MISMATCH_TOLERANCE` removed from `app/config.py` and both configuration wiki pages

**Total test count: 1177**

---

## [3.48.0]

### Added — Shared Groundwork: Local-Timezone Conversion and Read-Only `query_log.db` Access
Pre-work for two not-yet-built design docs (Predictive Pre-Fetching with Confidence Calibration, Self-Healing Source Selection Through Reinforcement, Ambient Intent Disambiguation Through Context) that each independently identified the same two real, missing pieces of shared infrastructure during their own research. Built once, here, rather than letting two or three separate features each invent their own version.

**`app/timeutil.py` — UTC-to-local-time conversion.** Every database timestamp this project writes (`query_log.db`, `snapshots.db`, `adversarial_testing.db`, `temporal_patterns.db`) is hardcoded UTC. Meanwhile, `_hours_since()` (`app/router.py`, resolving "this morning"/"while at work") already has a real, working, *separate* notion of local time, sourced entirely from the container's own `TZ` environment variable, with no connection to anything in `app/config.py`. Any feature needing to bucket a stored UTC timestamp by local hour-of-day or day-of-week was about to either invent a third, independent mechanism, or — far worse — silently bucket by raw UTC hour-of-day, which is only correct for a deployment physically in the UTC zone. For this project's own real reference deployment (Kingman, AZ — `America/Phoenix`, UTC-7, no DST), that mistake would have silently shifted every time-of-day bucket by exactly 7 hours, forever, with no error anywhere.

New setting: `LOCAL_TIMEZONE`, defaulting to read the same `TZ` environment variable `_hours_since()` already implicitly depends on — a deployment that's already correctly set `TZ` per the README gets this conversion capability for free, at zero new configuration cost. An explicit `LOCAL_TIMEZONE` always overrides `TZ`, for the rare case where they should genuinely differ.

Built on the standard library's `zoneinfo` (Python 3.9+, already available, no new dependency) rather than a naive fixed-offset calculation — confirmed directly via dedicated tests that DST transitions are handled correctly and automatically (`America/New_York` converts UTC noon to 7am in January, 8am in July, both correct). An invalid timezone name (a real, plausible typo) falls back to UTC with a logged warning rather than crashing, the same defensive judgment `morning_start_hour`'s own `% 24` fix already applies to a different setting; a malformed stored timestamp raises loudly rather than being silently swallowed, since every real row this project's own databases ever write is already in the correct format, and a malformed one indicates a real bug in whatever wrote it.

**`router.py` gains its own independent, read-only connection to `query_log.db`.** `query_log.db`, `_LOG_DB`, and `_log_query()` all live in `app/main.py`; `router.py` had zero existing access to any of them. Two of the three pending design docs need `router.py` to read recent query history directly. The new `get_recent_queries()` connects via SQLite's own `?mode=ro` URI — a real, enforced read-only connection, not just a documented convention — confirmed directly with a dedicated test that a write attempt against it fails immediately with `sqlite3.OperationalError`, and that the read-only mode never silently creates a missing database file as a side effect of being asked to read from it. Deliberately a direct connection by file path, never an import from `app.main`, since `main.py` already imports from `router.py` — the reverse direction would be a genuine circular import, the same class of problem `app/sources/fusion.py`'s own docstring already names as the reason a different shared function lives there rather than in `router.py`.

### Fixed (incidental, found during this work)
A real test-isolation bug in this release's own first draft of `tests/test_timeutil.py`: testing `local_timezone`'s default-resolution behavior (evaluated once at class-definition time) initially used `importlib.reload(app.config)`, which creates a brand-new `Settings` class and module-level `settings` object — but every other already-imported module in this codebase (confirmed: essentially all of them) did `from app.config import settings`, binding that object reference at import time. After the reload, those modules' own `settings` name kept pointing at the stale, pre-reload object, silently breaking 8 unrelated tests in `test_uptime_kuma.py` that only failed when run after these new tests in the same process — caught directly by running the full suite, not just the new file in isolation, the same discipline this project's own README and Contributing page already call for. Fixed by loading `app/config.py` as a genuinely separate module instance via `importlib.util.spec_from_file_location()`, under a different name, never touching `sys.modules['app.config']` at all.

A second, smaller correction, found immediately after this release shipped while updating the three pending design docs (Predictive Pre-Fetching, Self-Healing Source Selection, Ambient Intent Disambiguation) that this release's groundwork affects: `get_recent_queries()`'s own docstring claimed both Self-Healing Source Selection and Ambient Intent Disambiguation "want a small, bounded, most-recent-first window" — true for the latter, not actually true for the former, which needs a time-bounded bulk scan with real outcome columns instead. Corrected in both the function's own docstring and its test class's docstring (`app/router.py`, `tests/test_router.py`) to state this precisely rather than repeat an unverified assumption about a consumer that doesn't exist yet. No behavior change — `get_recent_queries()`'s actual implementation was always correct for its real, intended use (Ambient Intent); only the docstring's claim about a second use case was wrong.

### Changed
- Version bumped to 3.48.0
- README's "Timezone configuration" section updated to document `LOCAL_TIMEZONE`'s relationship to `TZ`

**Total test count: 1161**

---

## [3.47.3]

### Fixed — Real GitHub Actions CI Failure: Cross-Test Cache Pollution in `test_cache_persistence.py`
A genuine CI failure, reported directly from a real GitHub Actions run of `tests.yml` (`pip install -r requirements.txt` + `pytest tests/ -v`, no special flags or environment): `TestLoadCache::test_no_file_starts_fresh` failed with a real, leftover entry — `'kiwix:a real concurrent user query'` — sitting in `app.router._cache` when the test asserted it should be empty.

Root cause: `app.router._cache` and `_routing_cache` are plain module-level dicts, shared across the entire pytest process. Several test classes — some predating this release entirely, spread across `test_router.py`, `test_routing_cache.py`, `test_fusion.py`, `test_cache_persistence.py`, and the two new tests added in 3.47.2 (`TestFullCycle::test_cycle_does_not_pollute_real_cache_with_unmocked_routing` and `test_real_user_query_unaffected_by_concurrent_adversarial_suppression`) — write real entries into one or both dicts and never restore prior state afterward. Each individual test passes in isolation; the bug only surfaces when a *different* test, running later in the same process, depends on either cache being empty.

This had been silently masked, for the entire life of this project so far, by a mundane but real environmental fact: any dev machine or CI runner that has ever started the app for real leaves a `cache.json`/`routing_cache.json` file on disk, and `load_cache()`/`load_routing_cache()` reset the in-memory dict to whatever that file contains as a side effect of successfully parsing it — incidentally cleaning up leftover pollution from a prior test, purely by accident. A genuinely fresh GitHub Actions checkout has no such file, so `load_cache()`'s early-return path (taken specifically when the file doesn't exist) leaves whatever a prior test left behind completely untouched — confirmed directly by reproducing the exact two-test sequence (`test_real_user_query_unaffected_by_concurrent_adversarial_suppression` followed by `test_no_file_starts_fresh`) with `/app/data` genuinely absent, which deterministically reproduces the exact reported failure, byte-for-byte matching cache key included.

Fixed with a new `tests/conftest.py`: a single `autouse=True` fixture that snapshots and restores both `_cache` and `_routing_cache` around **every** test in the suite, regardless of file or class — closing the entire bug class at the root rather than hand-patching each affected class's own `setup_method`/`teardown_method` individually (a real fix for the classes found this round, but one that just reintroduces the same risk for the next test someone adds without remembering the same discipline). A test that already manages this state correctly itself is unaffected; the fixture's own save/restore is simply a redundant, harmless second safety net in that case.

Also restored `.github/workflows/`, `.github/dependabot.yml`, `.gitignore`, and `.dockerignore` to the repository — all four confirmed missing from every tarball produced since the 3.47.0 release, an oversight in archive packaging unrelated to the cache-pollution bug itself, but the reason the original report's CI failure couldn't initially be reproduced against the shipped tarball. `.gitignore`/`.dockerignore` were found in a second pass after the `.github` restoration prompted a fuller audit of the original archive's top-level file listing against what had actually been shipping; a stray `.ruff_cache/` directory (a local lint-run artifact, never meant to be packaged) was found and removed in the same pass.

### Added (Tests)
- `tests/conftest.py` — the autouse fixture described above
- Direct hand-verification (not a committed test, since it duplicates existing coverage once the fixture is in place) that the exact two-test sequence from the original failure report passes deterministically with the fix applied

### Changed
- Version bumped to 3.47.3

**Total test count: 1138** (unchanged from 3.47.2 — this release fixes test isolation, not test coverage)

---

## [3.47.2]

### Fixed — Adversarial Self-Testing's Synthetic Queries Were Silently Polluting the Real Result and Routing Caches
Found via a deliberate cross-check while researching an unrelated design doc, not a reported failure. `run_adversarial_test_cycle()`'s own docstring claimed it "never touches cache.json, routing_cache.json... or any real user-facing state" — confirmed directly, with an unmocked call, that this was false: `route_with_source()` writes to both the result cache and routing cache as an unconditional side effect of any successful query, synthetic or real, several calls deep inside `_resolve_single_source()` and `_llm_detect()`/`_llm_pick_fusion_sources()`. A single synthetic adversarial query really did land in the real, in-memory `_cache` dict, and would have persisted to `cache.json` on the next batched disk save. `test_cycle_never_touches_real_cache_files` could never have caught this — it mocks `route_with_source()` out entirely, proving only "if this function doesn't run, nothing else here touches the cache files either," not the actual claim the test is named for.

Fixed with a new `router.suppress_cache_writes()` context manager, which `run_adversarial_test_cycle()` now wraps its real `route_with_source()` call in. `_set_cached()`/`_set_routing()` both check it and no-op if set.

**Deliberately built on `contextvars.ContextVar`, not a plain module-level boolean** — a plain flag would have been a real, not theoretical, new bug: `BackgroundScheduler` runs Adversarial Self-Testing on its own thread pool, genuinely concurrent with FastAPI's request-handling threads, and a real live request's legitimate cache write landing in the same window a plain global flag was set would have been silently dropped too — strictly worse than the bug being fixed. `ContextVar` is thread-local (and task-local under asyncio) by construction; verified directly with a real two-thread test that a concurrent live request is completely unaffected by suppression active on another thread for an overlapping window.

### Added (Tests)
- `test_cycle_does_not_pollute_real_cache_with_unmocked_routing` — the actual regression test for the found gap: runs the real cycle with `route_with_source()` genuinely unmocked (only the underlying source handler and LLM call are stubbed) and confirms the real `_cache`/`_routing_cache` dicts are empty afterward
- `test_real_user_query_unaffected_by_concurrent_adversarial_suppression` — two real threads, proving a live request's cache write survives a concurrent, overlapping adversarial-testing suppression window
- `TestSuppressCacheWrites` (5 tests) in `test_cache_persistence.py` — direct unit coverage for the new context manager itself: basic suppression for both caches, resumption after the context exits, exception-safety (the flag must reset even if the suppressed code raises), and correct behavior under nested suppression calls

### Changed
- Version bumped to 3.47.2
- Wiki's [Caching](https://github.com/immortalbob/Mnemolis/wiki/Caching) updated with a new section explaining the found gap and the fix, including why `ContextVar` was required over a plain flag

**Total test count: 1138**

---

## [3.47.1]

### Fixed — Cross-Source Temporal Pattern Detection: Motion Events Were Never Actually Extracted
Code review (not a reported failure) caught a real, significant gap: `snapshot_ha()` always captured motion/window/opening device-class sensors, but `_iter_ha_entity_changes()` only ever had branches for `lock`, `door`, and `battery` — meaning the feature's own headline, lead-sentence example ("does a front-door lock event reliably precede a motion event") was never actually testable. Confirmed directly: a real motion "off"→"on" transition produced an empty event list.

Fixed by extending the shared comparison core with the missing branches — door logic generalized to cover `door`/`window`/`opening` together (same real binary open/closed semantics), plus a new, separate motion branch that deliberately only reports the "off"→"on" detection edge, not the reverse — the reverse transition is the sensor settling back to rest, not a new, independently meaningful occurrence.

**A second, related bug was found while fixing the first one, before either ever ran against real data:** `extract_ha_events()`'s event-type labeling only ever distinguished "lock or door" from "everything else, assumed to mean battery_low" — so the newly-added motion/window/opening kinds silently fell into the battery-event branch and were mislabeled `:battery_low` instead of their own correct labels. Fixed by making every kind explicit, with a real, future-proofing change: an unhandled kind now raises rather than silently falling through to a wrong label, so a future new kind added to the shared comparison core without a matching update here fails loudly instead of mislabeling itself.

### Fixed — A Real Discrepancy Between the Changelog and the Shipped Test Suite
3.47.0's changelog described the false-positive validation as "stress-tested... 30 independent random seeds... zero candidates in every single trial" — but the actual committed test ran exactly once, with a single fixed seed. The underlying statistical claim was independently re-verified and confirmed true (30 real, independent trials, zero false positives), but nothing in the shipped code would have caught a future regression across that broader range, and the specific provenance described wasn't backed by anything repeatable. Rewritten as a genuine 30-seed loop, so the claim is true of what's actually committed and re-checked on every test run.

### Fixed — Two Lint Errors
A dead import (`collections.Counter`, never used) and a dead local variable in the hand-rolled Poisson tail-probability function (`_poisson_sf`) — cosmetic, the underlying math was independently verified correct against `scipy.stats.poisson.sf` across a real range of inputs before either fix was applied.

### Added (Tests)
- 4 new tests in `test_snapshots.py`: window-opened detection, motion-detected detection, confirmation that the reverse motion transition correctly produces no event, alongside the pre-existing door/lock/battery coverage
- 2 new tests in `test_temporal_patterns.py`: confirming `extract_ha_events()` correctly labels window and motion events, not the `:battery_low` mislabel the first attempted fix introduced

### Changed
- Wiki's [Cross-Source Temporal Pattern Detection](https://github.com/immortalbob/Mnemolis/wiki/Cross-Source-Temporal-Pattern-Detection) updated: the event-type table now accurately includes motion/window/opening, with an honest note about the gap and fix; a third entry added to the "real bug found during development" section, explicitly noting this one was caught by review rather than development-time testing
- `_diff_ha()`'s own docstring corrected to mention window/opening sensors and the motion detection-edge-only behavior
- Version bumped to 3.47.1

**Total test count: 1131**

---

## [3.47.0]

### Added — Cross-Source Temporal Pattern Detection
A background job, on the same `apscheduler` infrastructure the snapshot engine and Adversarial Self-Testing already run on, that mines structured event history for reliable timing relationships between event types — does a front-door lock event reliably precede a motion event within some lag window, does an HA event reliably precede a service outage — and reports anything that survives a real statistical bar as a candidate, never a causal claim. This was the roadmap's "🔬 Speculative" entry; it's no longer speculative.

Scope is deliberately narrow for this first version: `ha`-internal event pairs (lock/door/battery transitions against each other) and `ha`-to-coarse-`uptime` pairs only. `forecast`/`news` event extraction is explicitly out of scope — neither source's current snapshot shape supports clean event typing without new, separate groundwork.

The statistical core, summarized (full reasoning, with citations, in the new wiki page):
- **Non-overlapping occurrence counting** — once a real B has been claimed as the match for some A, that same B can never be claimed twice. A naive "every A within range of every B" count would badly overstate how often a relationship actually fires.
- **A hard minimum-occurrence floor** (`TEMPORAL_PATTERN_MIN_OCCURRENCES`, default 5) — checked *before* any significance test runs. A pair with 2-3 raw occurrences isn't a pattern yet regardless of what the math would say.
- **Bonferroni-corrected significance testing** — the per-comparison threshold gets divided by the total number of (A, B) pairs actually tested in a given pass, against a null-hypothesis expected count built from each event type's own real, observed base rate (not an assumed uniform one).
- **Mandatory out-of-sample re-validation** — a `candidate` pattern is mechanically re-checked against a later, non-overlapping window of new data before it can ever be promoted to `confirmed`. A pattern that fails to replicate is marked `unconfirmed`, not deleted — a real, honest finding in its own right, the same "status changes, history doesn't disappear" philosophy already established by Adversarial Self-Testing's dismiss mechanism.

Every single returned pattern — `candidate`, `confirmed`, or `unconfirmed` — carries the literal note *"This reflects observed timing correlation only and does not establish a causal relationship."* directly on the row, not just in documentation. This feature lives only in its own dedicated endpoint, never blended into `GET /changes` or a normal search response — a caveat that important is too easy to lose once folded into an ordinary conversational answer.

New endpoints: `GET /temporal-patterns` (optional `?status=candidate|confirmed|unconfirmed` filter), `POST /temporal-patterns/trigger`. New `/health` field: `temporal_pattern_detection`, with one genuinely new status beyond the usual `ok`/`stale`/`never_ran`/`disabled` set — **`insufficient_data`**, reported when the job ran correctly but the real event volume in its window was below the floor needed to test even one pair. This is the honest, expected state for the first weeks of this feature's life on any real deployment, distinct from `ok` (a real result against a real amount of data, even if that result is "found nothing").

New settings: `TEMPORAL_PATTERN_DETECTION_ENABLED` (default `true`, checked at both scheduler-registration time and inside the cycle function itself, mirroring `ADVERSARIAL_TEST_ENABLED`'s exact defense-in-depth precedent), `TEMPORAL_PATTERN_MINING_INTERVAL_HOURS` (default 24), `TEMPORAL_PATTERN_LAG_WINDOW_MINUTES` (default 30), `TEMPORAL_PATTERN_MIN_OCCURRENCES` (default 5), `TEMPORAL_PATTERN_SIGNIFICANCE_LEVEL` (default 0.05), `TEMPORAL_PATTERN_VALIDATION_WINDOW_HOURS` (default 24), `TEMPORAL_PATTERN_STALE_GRACE_MULTIPLIER` (default 3).

New backup file: `temporal_patterns.db` (event history and mined pattern candidates) — `_BACKUP_DATA_FILES` is now six entries, not five.

Event extraction for `ha` is built directly on `_iter_ha_entity_changes()` — a new shared comparison core factored out of `_diff_ha()` itself, so the new structured-event extractor and the existing free-text "what changed" diff output can never independently drift apart from each other. `_diff_ha()`'s own external behavior is completely unchanged by this refactor; every pre-existing test for it still passes against the exact same inputs/outputs.

Full design rationale, the mining/validation diagrams, and the exact statistical reasoning behind every threshold: wiki's [Cross-Source Temporal Pattern Detection](https://github.com/immortalbob/Mnemolis/wiki/Cross-Source-Temporal-Pattern-Detection).

### Fixed — Two Real Bugs Found During Development, Before Ever Running Against Production Data
Both caught the same way most of this project's real bugs get caught: deliberately testing a harder, more realistic scenario than the simple case that already passed.

- **Non-overlapping counting silently dropped real occurrences.** The first draft of the occurrence counter correctly prevented a single B from being double-claimed by advancing its scan position to just past whichever B got claimed — but that also silently skipped over any genuine, not-yet-evaluated A occurrences sitting between the claiming A and the B it claimed. A burst of 3 A's followed by 3 B's, all within the lag window, returned a count of 1 instead of the correct 3. Fixed by tracking which B *indices* have already been claimed in a separate set, and scanning every A exactly once regardless of what any earlier A claimed.
- **Uptime event misclassification from a shared substring.** `_diff_uptime()`'s own recovery message ("All services restored — previously reported outage resolved") and its own pending message ("Service check pending — possible outage starting") both genuinely contain the literal substring `"outage"`. An early version of `extract_uptime_events()` checked for `"outage"` before checking the more specific `"pending"`/`"restored"` phrasing, misclassifying both as plain outages. Fixed by matching each message's own distinct, unambiguous leading phrase instead of a substring more than one real message type happens to share.

Both bugs are now permanent regression tests, not just inline comments — `TestCountNonoverlappingOccurrences` includes the exact burst scenario plus eight further adversarial variants (shared-B claims, directionality, mixed in-range/out-of-range pairs), and `TestExtractUptimeEvents` locks in the corrected classification for every real message `_diff_uptime()` can produce.

### Changed
- Version bumped to 3.47.0
- Roadmap's "🔬 Speculative" section retired — its one entry shipped and moved into "Battle Testing & Operational Maturity — complete"
- Wiki: new [Cross-Source Temporal Pattern Detection](https://github.com/immortalbob/Mnemolis/wiki/Cross-Source-Temporal-Pattern-Detection) page; [Configuration Reference](https://github.com/immortalbob/Mnemolis/wiki/Configuration-Reference), [Health & Observability](https://github.com/immortalbob/Mnemolis/wiki/Health-and-Observability), [Backup & Restore](https://github.com/immortalbob/Mnemolis/wiki/Backup-and-Restore), and [Home](https://github.com/immortalbob/Mnemolis/wiki/Home) updated for the new feature
- README's Project Structure listing also corrected to include `adversarial_testing.py`/`test_adversarial_testing.py`, which had drifted out of sync with the actual repo before this release, alongside the new `temporal_patterns.py`/`test_temporal_patterns.py` entries; the long-stale "1012 tests" line corrected to the real, current count

### Statistical validation, not just unit tests
Beyond ordinary unit coverage, the mining procedure was stress-tested against synthetic data specifically built to check the claim this feature's whole design rests on: a purely random, mutually-independent event stream (8 event types, realistic per-type volumes, 30 independent random seeds) produced **zero false-positive candidates** in every single trial — confirming Bonferroni correction is doing real, load-bearing work rather than just being present as inert code. A complementary synthetic test with one genuinely reliable signal planted inside a pool of unrelated noise correctly found the real pattern in **30/30** trials with no spurious extras from the noise.

**Total test count: 1126**

---

## [3.46.2]

### Fixed — Adversarial Self-Testing: Real Flag-Visibility Gap
Code review (not a reported failure) caught a real, meaningful gap the previous design had explicitly documented as deliberate: `last_flagged_reason` got overwritten to `NULL` whenever the same fingerprint happened to be re-rolled and ran clean, and since the dedup logic only checked "have I seen this at all" (not "was it flagged"), a previously-flagged combination could silently vanish from `GET /adversarial/flagged` with no human ever reviewing or dismissing it — a real risk specifically for intermittent anomalies (a flaky latency outlier, a transient bug that doesn't reproduce on every run).

"Currently flagged" and "ever flagged" are now genuinely separate, tracked facts. New columns: `ever_flagged` (sticky, never auto-resets), `first_flagged_reason`/`first_flagged_timestamp` (the original anomaly, preserved across any number of later clean runs), `review_status`. `GET /adversarial/flagged` now returns the union of currently-flagged and ever-flagged-but-not-dismissed rows by default, with `currently_flagged` exposed explicitly so a human can still tell "still broken right now" apart from "flagged once, currently clean, still needs a look." New endpoint: `POST /adversarial/dismiss?fingerprint=...` — the actual human action that closes a review out; history is never deleted, and a genuinely new flag on a previously-dismissed fingerprint correctly resurfaces it.

A real schema migration handles every already-deployed 3.46.x database (including the live one on MiniDock, two real days of history) — `init_adversarial_db()` adds the four new columns via guarded `ALTER TABLE` and backfills `ever_flagged`/`first_flagged_*` for any row already flagged before this fix shipped, so no history is lost on upgrade.

Writing this fix surfaced two more real bugs in its own first draft, both caught by failing tests rather than found by inspection: a migration-ordering bug (an index was created on the new `ever_flagged` column before the column itself existed on a pre-existing table, raising `no such column: ever_flagged` on every real already-deployed database — the migration order was corrected), and a missing `review_status` reset (a dismissed combination that got a genuinely new, different flag later stayed permanently invisible, since nothing cleared the earlier dismissal — `_record_result()` now clears `review_status` back to `NULL` specifically when a new flag fires on an already-flagged row).

### Fixed — Two Smaller Issues From the Same Review
- A dead code comment referencing `expand_seed_vocabulary()` — a function that doesn't exist anywhere in the codebase. Corrected to clearly state this is a not-yet-built follow-up, not something already wired in.
- A factually wrong wiki claim: the "part-count mismatch under fallback" known limitation stated a header-less fallback result "always reads as 1 header." Traced directly against `_HEADER_PATTERN`: a header-less string produces zero matches, not one, and `_check_multi_intent_part_count`'s own `n_headers > 0` guard already excludes that case correctly. There was no real limitation here — the claim has been removed from the wiki rather than corrected to a different wrong one, with two new regression tests locking in the correct behavior (including the real, narrower case — a genuine partial fallback with at least one header present — that DOES still correctly flag).

### Changed
- Version bumped to 3.46.2
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) rewritten to document the actual fixed flag-tracking design and the new endpoint, with the corrected known-limitations section

**Total test count: 1076**

---

## [3.46.1]

### Added — Adversarial Self-Testing: Real On/Off Switch and Tunable Thresholds
The first real run against MiniDock's actual Kiwix/SearXNG/Ollama stack came back clean (8/8, zero flags) — see the wiki page for what it generated. Follow-up based on direct feedback: every threshold that was previously a hardcoded constant in `adversarial_testing.py` is now a real setting, plus a genuine master enable/disable switch.

- **`ADVERSARIAL_TEST_ENABLED`** (default `true`) — checked at both scheduler-registration time in `main.py`'s lifespan AND inside `run_adversarial_test_cycle()` itself (defense in depth), so a direct call — including the new `POST /adversarial/trigger` — can never accidentally run real queries against the LLM/SearXNG/Kiwix backends while the feature is supposed to be off. `/health`'s `adversarial_testing` field reports `{"status": "disabled"}` directly when off, rather than eventually reading as `"stale"` — a deliberate off-switch shouldn't look like a job that silently stopped running.
- **`ADVERSARIAL_TEST_LATENCY_OUTLIER_MULTIPLIER`** (default `1.5`), **`ADVERSARIAL_TEST_LATENCY_OUTLIER_FLOOR_MS`** (default `1000`), **`ADVERSARIAL_TEST_LATENCY_OUTLIER_MIN_SAMPLES`** (default `10`) — previously hardcoded; the real first run showed legitimate cache-driven variance (276ms vs. 2028ms on the same recipe) that different hardware will see differently
- **`ADVERSARIAL_TEST_PART_COUNT_MISMATCH_TOLERANCE`** (default `2`) — previously hardcoded

### Added — `POST /adversarial/trigger`
Manually run one cycle immediately rather than waiting for the next scheduled tick — mirrors `/snapshots/trigger`'s exact pattern, and removes the need for the `docker compose exec ... python3 -c "..."` workaround used to force the very first verification run. Returns a real summary (`queries_run`, `flagged`) rather than a bare 200.

### Changed
- Version bumped to 3.46.1
- Wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing) and [Configuration Reference](https://github.com/immortalbob/Mnemolis/wiki/Configuration-Reference) updated with all five new settings, the new endpoint, and the real first-run data from MiniDock

**Total test count: 1061**

---

## [3.46.0]

### Added — Adversarial Self-Testing
A background job, on the same `apscheduler` infrastructure the snapshot engine already runs on, that generates structurally-novel queries by combining Mnemolis's own real ingredient vocabulary (`router.INTENT_MAP`, `router._CONJUNCTIONS`, `router._NOSPLIT_PATTERNS`, `kiwix.DISCOURSE_FRAMING_PATTERNS`), runs each through the real `route_with_source()` pipeline, and flags structural anomalies for human review. Institutionalizes the adversarial megaquery testing approach that found most of the bugs documented in the wiki's Design History, instead of relying on someone constructing a nasty test sentence by hand.

Generation is pure-Python combinatorics — seven recipes, zero LLM calls in the hot path — fingerprint-deduplicated so repeated cycles bias toward never-seen ingredient combinations before falling back to a repeat. Every anomaly check verifies a documented Mnemolis behavioral guarantee against what the real pipeline actually did (does a multi-intent query produce a matching number of result sections, does a discourse-framing query actually keep kiwix in the result, does the result match a known empty/error phrase) — never a correctness judgment about response content, the same distinction that made an LLM-as-judge approach a non-starter for this exact problem shape in published research.

New endpoint: `GET /adversarial/flagged` — every currently-flagged combination, most recent first. New `/health` field: `adversarial_testing`, same `ok`/`stale`/`never_ran` shape as `snapshot_jobs`. New settings: `ADVERSARIAL_TEST_INTERVAL_MINUTES` (default 60), `ADVERSARIAL_TEST_BATCH_SIZE` (default 8). New backup file: `adversarial_testing.db` (synthetic generated queries only, never real user data) — `_BACKUP_DATA_FILES` is now five entries, not four.

Full design rationale, the seven recipes, and a real false-negative bug the discourse-framing check caught in itself during its own unit testing: wiki's [Adversarial Self-Testing](https://github.com/immortalbob/Mnemolis/wiki/Adversarial-Self-Testing).

### Changed
- Version bumped to 3.46.0
- Wiki's Roadmap, Backup & Restore, Configuration Reference, and Health & Observability pages updated to reflect the new feature
- README's Backup & Restore section updated to "five files," matching the new file count

**Total test count: 1049**

---

## [3.45.0]

### Investigation Note — A Config-Completeness Audit, After Battle Testing and Bulletproofing
With the complexity-investigation campaign and the bulletproofing pass both complete, a different kind of review: systematically searching every file in `app/` for hardcoded values a real homelab deployment might genuinely want to tune, rather than hunting for bugs. Deliberately left out of this audit's additions: LLM `max_tokens` values (internal sizing for a specific prompt, not a real user preference), the 3-disambiguation-candidates count in `kiwix.py` (tightly coupled to the actual prompt wording — changing the count without rewriting the prompt would produce inconsistent behavior), and `home_assistant.py`'s minute/hour/day formatting thresholds (structural facts about time, not deployment preferences).

### Fixed — A Real Gap That Directly Undermined Existing Documentation
`searxng.py`'s client-side request timeout was hardcoded at 10 seconds, while the README's own documented fix for `"Error reaching SearXNG: connection failed"` tells people to raise SearXNG's own server-side `max_request_timeout` to 20 seconds for genuinely slow engines. Following that documented advice exactly as written wouldn't have fully worked — Mnemolis's own client would have cut the connection at 10s regardless of how generously SearXNG itself was configured to wait. Now configurable via `SEARXNG_REQUEST_TIMEOUT_SECONDS` (default 15), with the README's own SearXNG timeout section updated to say so explicitly.

### Added — 16 New Configurable Settings
Found hardcoded with no way to adjust them, despite several being presented in the README/wiki as deliberate, reasoned design choices:

- **`SEARXNG_REQUEST_TIMEOUT_SECONDS`** (default 15) — see above
- **`KIWIX_ARTICLE_MAX_CHARS`** (default 3000) — per-article truncation before scoring/fusion ever sees it, distinct from `FUSION_MAX_CHARS_PER_SOURCE`'s post-merge truncation
- **`KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT`** (default 0.5) — the actual, central "should a second book be fused in" decision, documented in the README/wiki as the real mechanism but previously impossible to tune
- **`WEB_NEWS_RAW_RESULT_BUDGET`** (default 25) — the scoring pipeline's input budget, distinct from `WEB_NEWS_TOP_N`'s output cap
- **`QUERY_EXPANSION_MIN_WORDS`** (default 3) — matches the README's documented "3+ words" trigger, which was previously just a fact, not a setting
- **`SNAPSHOT_STALE_GRACE_MULTIPLIER`** (default 3) — `/health`'s staleness-alerting sensitivity
- **`ROUTING_CACHE_TTL_SECONDS`** (default 3600) — presented as a deliberate default in the wiki's Caching page, previously not actually adjustable
- **8 per-source result cache TTLs** (`CACHE_TTL_KIWIX_SECONDS`, `CACHE_TTL_FORECAST_SECONDS`, `CACHE_TTL_NEWS_SECONDS`, `CACHE_TTL_WEB_SECONDS`, `CACHE_TTL_UPTIME_SECONDS`, `CACHE_TTL_HA_SECONDS`, `CACHE_TTL_CHANGES_SECONDS`, `CACHE_TTL_FUSION_SECONDS`) — each independently configurable now rather than one shared hardcoded dict

`config.py` itself now documents this audit's own scope and exclusions directly in the `Settings` class docstring, and groups every setting by what it actually controls rather than the order it happened to be added in.

### Changed — Documentation
- README's config table updated with all 16 new settings, grouped to match `config.py`
- README's SearXNG timeout section updated to mention the new client-side setting explicitly
- Wiki's [Configuration Reference](https://github.com/immortalbob/Mnemolis/wiki/Configuration-Reference) updated with all 16 settings, each in its matching existing section
- Wiki's [Caching](https://github.com/immortalbob/Mnemolis/wiki/Caching) page's per-source TTL table and routing cache TTL description both updated to reference the real, now-configurable env var names instead of presenting the values as fixed facts

### Changed
- Version bumped to 3.45.0

**Total test count: 1012**

---

## [3.44.1]

### Documentation Release — No Code Changes
A docs-only release wrapping up the bulletproofing pass with three pieces of work: a full wiki review, a README pass, and the first real benchmark since v3.17.0.

**Wiki:** every page checked against the actual current code, not just for staleness but for whether user-useful information was leading or buried under mechanism detail. Several pages had real factual drift fixed (`Routing.md`'s fallback phrase list, `Caching.md`'s failure-caching behavior, `Snapshot-Engine-and-Changes.md`'s retention numbers, `Confidence-Aware-Fusion.md`'s generic-result check) and most updates were reordered to lead with "if you saw X, here's what changed" before the implementation story. The severe `home_assistant.py` word-boundary bug got one canonical writeup (`Home-Assistant-Integration.md`) with brief pointers from `Sources.md` and `Troubleshooting.md` rather than three separate explanations drifting apart from each other.

**README:** restructured for actual reading order — a new "Why Mnemolis" section now sits right after the intro, before the `Sources` table, answering "should I keep reading" before "what does it contain." `Architecture`'s deep-dive diagrams moved later, after installation/configuration, since they're reference material for someone already running it, not onboarding material. `MCP` moved up near `Integrations`, since "can my client talk to this" is a before-you-install question. Stale facts fixed (the old `288`-per-source snapshot retention claim, `mcp_server.py`'s description still saying "SSE" after the Streamable HTTP migration, a `FORECAST_LATITUDE`/`LONGITUDE` default mismatch against the real code), one genuinely broken anchor link found and fixed, and a giant unreadable run-on sentence cataloging every test category replaced with a real pointer to the per-file test list that already existed two sections below it.

**Benchmarks:** the first real Locust run since v3.17.0, covering the entire battle-testing campaign (v3.20.0–v3.34.0) and bulletproofing pass (v3.35.0–v3.44.0) — roughly 25 releases and dozens of real bug fixes that had never been measured under load. Aggregated median held at 24ms cold and warm, unchanged from every prior benchmarked version back to v3.5.0. Two real, honest findings reported rather than smoothed over: `discourse_framing` is now the most expensive cold-path query (p98 4200ms, collapsing ~76x to 55ms warm), and `auto`/`conditional`/`uptime` stayed expensive even on the warm pass — traced to a real, identifiable cause for the first two (small query pools generating more distinct cache keys than entries via fusion-escalation and sub-query routing), with `uptime`'s tail now reproduced a second time across a different release, worth treating as a real recurring pattern rather than dismissing as noise again.

A real, genuine gap surfaced while running the benchmark and is now fixed going forward: `BENCHMARKS.md` had never previously documented *how* a "cold cache" run actually gets its empty cache — every prior version's section stated it as a fact with no mechanism. `tests/locustfile.py` and `BENCHMARKS.md` both now spell out the required `POST /cache/clear` + `POST /cache/routing/clear` step before a genuine cold run. Also fixed: a `--host http://your-host:8888` placeholder that silently produced a wall of opaque DNS errors (`Temporary failure in name resolution`) on every single request when used literally instead of being substituted — replaced with a realistic example IP and an explicit warning in both files.

### Changed
- Version bumped to 3.44.1

**Total test count: 1011 (unchanged)**

---

## [3.44.0]

### Investigation Note
A full, deliberate top-to-bottom re-read of `app/sources/fusion.py`, completing the bulletproofing pass across every source file. Most of the file held up cleanly — `_truncate()`'s last-newline boundary logic, `_deduplicate()`'s `sentences()` helper (including a careful check that abbreviation-induced sentence fragmentation, e.g. "U.S." breaking mid-sentence, doesn't create false-positive dedup matches between genuinely different sources — confirmed it doesn't, since the actual differing content dominates each fragment regardless), and `_HEADER_LABELS`'s coverage against every real `SOURCE_MAP` entry were all checked and confirmed correct.

### Fixed — A Real Crash: `FUSION_MAX_SOURCES=0` Broke Every Fusion Query
`FUSION_MAX_SOURCES` is a plain, unvalidated int — setting it to `0` (a plausible misconfiguration, e.g. someone trying to "disable" fusion entirely) capped the valid-sources list to empty *after* the function's only existing empty-list check, meaning `ThreadPoolExecutor(max_workers=0)` crashed with a raw `ValueError: max_workers must be greater than 0` instead of the sensible "no valid sources specified" message already used for the genuinely equivalent case just above it. Confirmed end to end before fixing. Fixed by re-checking for emptiness after the capping step, reusing the same, already-correct error path.

### Changed — Removed a Confirmed-Unnecessary Function Call
Traced through exactly where duplicate sources can and cannot occur in `search()`'s own `parts`-building logic, prompted by a comment claiming a call to `_merge_same_source()` here "fixes duplicate [HA] from decomposition." Confirmed this scenario cannot actually happen at this specific call site: `valid` (the list `parts` is ultimately built from) is already deduplicated via its own `seen` set earlier in the same function, so `parts` here can never contain two entries for the same source. The comment's real scenario — two independently-decomposed sub-queries both resolving to the same source — genuinely happens in `router.py`'s own `_merge_decomposed_parts()`, the *other* real call site for this shared function; that one still needs it, this one never did. Removed the confirmed-dead call here, with a clear explanation of the distinction for future readers.

### Added (Tests)
- 1 new test confirming `FUSION_MAX_SOURCES=0` no longer crashes and produces the sensible "no valid sources" message instead
- 1 new test confirming `search()` itself never has duplicate sources reaching its merge step (a duplicate source passed in is correctly deduplicated before `parts` is ever built, producing exactly one section in the output regardless)

### Changed
- Version bumped to 3.44.0

**Total test count: 1011**

---

## [3.43.1]

### Fixed — Unbounded Article-Fetch Fallback Loop
Found in the same `kiwix.py` pass as 3.43.0's fixes, applied afterward: `search()`'s article-fetch fallback loop ("try the next best result if the top one's article fetch fails") had no upper bound, trying every remaining scored result. A realistic worst case (multiple books selected, disambiguation active across 3 candidate phrases, 15 results per search call) could produce up to ~59 total results — if Kiwix's search endpoint stayed healthy but the specific article-content path kept failing for every single one (a malformed page, broken links, transient timeouts), this loop could make up to 59 sequential real HTTP requests at a 10s timeout each, nearly 10 minutes for one search request. Capped at 5 fallback attempts — generous enough to recover from a realistic cluster of a few broken links near the top of the results, narrow enough to bound the worst case to under a minute. Verified both that the cap genuinely stops well short of trying every result when every attempt fails, and that genuine recovery within the cap still works correctly.

### Added (Tests)
- 2 new tests: confirming the fallback loop is genuinely capped rather than unbounded, and confirming a result within the cap that genuinely succeeds is still correctly returned

### Changed
- Version bumped to 3.43.1

**Total test count: 1009**

---

## [3.43.0]

### Investigation Note
A full, deliberate top-to-bottom re-read of `app/sources/kiwix.py`, the largest and most central source file. Most of the file held up cleanly — pagination boundary logic, the multi-book fusion string formatting, and `_is_definitional_query()`'s "explain"/"describe" substring matches inside "unexplainable"/"described" were all checked carefully and confirmed genuinely correct or harmless, distinct from the real word-boundary bugs found in `home_assistant.py` earlier this release. Five real, distinct bugs were found and fixed.

### Fixed — Non-Deterministic Book Selection Across Restarts
`_pick_books_with_llm()`'s fuzzy-match fallback iterated over a `set()` when an LLM response was ambiguous enough to match more than one real book (e.g. a truncated "wikipedia_en_all" matching both "...maxi" and "...nopic" variants). Python's set iteration order isn't guaranteed stable across process runs (this project never pins `PYTHONHASHSEED`), meaning the exact same query could resolve to a *different* real book purely due to container restart timing. Fixed with `sorted()`, confirmed deterministic across 5 independent calls.

### Fixed — Table-of-Contents Boxes Were Never Actually Stripped From Any Article
`_fetch_article()` passed `.toc`/`#toc` (CSS-selector syntax) to `soup([...])`, which only matches literal HTML tag names — confirmed directly that TOC clutter survived in every fetched article since this code was written, despite the clear intent already documented in the surrounding code. Fixed with `soup.select(".toc, #toc")` for the CSS-selector entries, keeping `soup([...])` for the genuine bare tag names ("script", "table", etc., which were already working correctly).

### Fixed — Single-Character Search Terms Silently Dropped, Independently Re-Discovering Tonight's Earlier `scoring.py` Bug
`_build_search_terms()` had the identical bug already found and fixed in `scoring.py`'s `_keywords()` this same release cycle: "what is r programming used for" reduced to the literal Kiwix search query `"programm,"` losing the one word that actually distinguishes the query from any other programming language. Fixed with the same `isalnum()` approach already proven there.

### Fixed — A Related Sanity-Filter Weakness, Found While Verifying the Fix Above
Once single-character search terms became genuinely reachable, `_get_disambiguation_candidates()`'s sanity filter (checking whether a candidate "contains the original word") became nearly meaningless for one-letter terms — almost any English phrase coincidentally contains a single letter as a bare substring. Fixed with the same word-boundary regex discipline already applied to `home_assistant.py` earlier this release, making the check genuinely meaningful regardless of how short the original word is.

### Added (Tests)
- 1 new test confirming deterministic book selection across multiple independent calls
- 2 new tests in the existing (previously unrelated, accidentally near-duplicated during this pass — caught and merged correctly) `TestFetchArticle` class: the TOC-stripping regression and confirmation that genuine `<table>` stripping still works
- 3 new tests for `_build_search_terms()`: the single-character fix, confirmation bare punctuation noise is still excluded, and the original "c programming language" case
- 1 new test confirming the disambiguation sanity filter correctly uses word-boundary matching for single-character original words

### Changed
- Version bumped to 3.43.0

**Total test count: 1006**

---

## [3.42.0]

### Fixed — A Severe, Real Bug: "Is the Front Door Locked" Was Completely Broken
A full, deliberate top-to-bottom re-read of `app/sources/home_assistant.py`, hunting for the same kind of small-helper bug already found in `router.py`/`fusion.py`. `_build_filter()`'s keyword matching used naive substring search with zero word-boundary awareness — `"on"` (a real, bare dictionary key for "lights on") matched as a substring of **"front"**. Confirmed end to end: asking *"is the front door locked"* — about as natural and common a question as this entire source exists to answer — incorrectly applied `state_filter="on"`, and since a lock's real state is `"locked"`/`"unlocked"`, never `"on"`, the actual, correctly-named, correctly-stated front door lock entity was silently rejected by the filter. The real, full response was **"No matching entities found in Home Assistant for that query"** for an entity that genuinely existed with current, correct data.

A systematic check (the same discipline already applied to `router.py`'s `INTENT_MAP`) found this risk was severe and widespread, not a one-off: `"rain"` matched inside `"training"`, `"on"` also matched inside `"alone"`/`"long"`/`"among"`, and several other short keys carried the same risk. Fixed by replacing bare substring search with proper `\b` word-boundary regex matching throughout `_build_filter()`, verified against every real collision found plus every genuine intended match (multi-word phrases like `"lights on"` still work correctly).

### Fixed — The Identical Bug in `_detect_area()`
The exact same root cause, found via the same systematic check applied to `_AREA_ALIASES`: `"shed"` (a real area alias) is a genuine substring of `"finished,"` `"crashed,"` `"washed,"` and other common past-tense verbs. *"Is the download finished yet"* — a query with nothing to do with any area — incorrectly resolved to `area_id="shed"` before this fix. The existing longest-match-first checking order only incidentally protected against this when a genuine, longer area phrase also happened to be present in the same query (which masked the bug in an earlier, less careful test) — it did nothing when "shed" was the only thing that happened to match at all. Fixed with the same `\b` word-boundary approach.

### Fixed — Binary-Sensor Motion Entities Were Never Actually Reachable, Despite Real Dedup Logic Existing for Them
Investigated why `motion_event_names` dedup logic existed for `binary_sensor` motion entities at all, given `_QUERY_MAP`'s `"motion"`/`"camera"`/`"activity"` keywords only ever listed `"event"` as a possible domain — meaning the more common `binary_sensor` + `device_class: motion` convention (used by many real Zigbee2MQTT/Z-Wave/PIR integrations) was never actually reachable through those keywords at all. The dedup logic only makes sense if `binary_sensor` motion entities were always meant to be reachable, confirmed directly: `"security"`/`"security status"` already correctly included `device_classes: ["motion"]` elsewhere in the same dict, just never applied consistently to these three entries. Fixed by adding `device_classes: ["motion"]` to all three.

**A second, related bug surfaced while fixing the first:** the dedup check itself was global, not per-entity — suppressing *every* `binary_sensor` motion entity in the house if *any* motion sensor anywhere had event-based data, even completely unrelated sensors with no event entity of their own. Confirmed directly: a home with one motion sensor reporting via both an event entity and a binary_sensor (the genuine dedup case) and a second, unrelated motion sensor reporting only via binary_sensor would silently drop the second sensor entirely from an "any motion" query. Fixed to check whether *this specific* physical sensor has a genuine event counterpart, not just whether the set is non-empty at all.

**A third, smaller bug surfaced while verifying the second fix:** `binary_sensor` entities were unconditionally labeled "Door Sensors" regardless of their actual `device_class` — a reasonable assumption when `binary_sensor` entities were never reachable except via door-specific keywords, but genuinely wrong now that `binary_sensor` motion entities are correctly reachable. Fixed to label motion-class binary_sensors "Motion," not "Door Sensors."

### Fixed — A Small Grammar Inconsistency
`_format_motion_event()` already correctly handled the singular/plural distinction for hours and days ("1 hour ago" vs "2 hours ago"), but minutes was overlooked, producing "1 minutes ago." Fixed to match the established pattern.

### Investigation Note
`INTENT_MAP`-style keyword collisions in this file's own `_QUERY_MAP` (e.g. `"security"` being a substring of `"security status"`) were checked and confirmed to be correctly handled by the existing longest-match-first protection — distinct from the word-boundary bugs above, which involved a short key colliding with an unrelated word elsewhere in the query, not with a longer key sharing the same characters.

### Added (Tests)
- 5 new tests for the `_build_filter()` word-boundary fix: the original "front door" bug case, "training"/"rain," "alone"/"on," confirmation that genuine "lights on" still works, and confirmation the existing "outdoor"/"door" longest-match protection still works
- 3 new tests for the `_detect_area()` word-boundary fix: the "finished"/"shed" bug case, "washed"/"shed," and confirmation genuine "shed" area detection still works
- 4 new tests for the binary-sensor motion fixes: a sensor with no event counterpart is now included, a sensor with a genuine event counterpart is still correctly suppressed, motion entities get the correct "Motion" label, and door entities still get the correct "Door Sensors" label
- 1 new test for the singular-minute grammar fix

### Changed
- Version bumped to 3.42.0

**Total test count: 999**

---

## [3.41.0]

### Investigation Note
A full, deliberate re-pass through `router.py`, specifically hunting for the kind of small-helper and string-handling bugs that complexity scores never flagged (the same category that found `_looks_empty`'s cross-file drift in 3.40.0). Most of the file held up cleanly — `INTENT_MAP`'s apparent keyword collisions (every query containing both a source's bare trigger and a `"changes"` trigger, e.g. "any outages today") were checked systematically and confirmed to be a real, intentional, sensible design pattern: escalating to fusion for these genuinely ambiguous compound questions ("what's the current status AND has anything changed") gives a more helpful answer than guessing at just one interpretation, not a bug. `load_cache()`/`load_routing_cache()`'s defensive disk-loading logic was re-confirmed solid.

### Fixed — A Real Crash: a Natural Config Mistake Broke "This Morning" Queries
`MORNING_START_HOUR`/`WORK_START_HOUR` are plain, unvalidated ints — setting either to `24` (a genuinely natural mistake, since `24:00` is a common way to write midnight in 24-hour notation) crashed `_hours_since()` with a raw `ValueError: hour must be in 0..23` the moment any "this morning" or "while at work" query needed it. Confirmed end to end: this is directly reachable from `_resolve_changes_hours()`, which has no exception handling of its own. Fixed with `hour_of_day % 24`, which correctly clamps `24 → 0` while also sensibly handling any other out-of-range value (negative hours wrap correctly too) rather than only patching the one specific mistake found.

### Changed — Removed Genuinely Dead Code in `detect_conditional()`
A redundant `if p != -1` filter ran after a list comprehension whose own condition (`if conj in consequence_lower`) already guarantees a real substring match before `.find()` is ever called on it — confirmed via direct testing across unicode, empty-string, and emoji edge cases that Python's `in` and `.find()` can never disagree. Removed the genuinely unreachable filter.

### Added (Tests)
- 2 new tests for `_hours_since()`: the originally-found `hour=24` case no longer crashes, and a more extreme out-of-range value (`100`) confirms the fix generalizes rather than just patching one specific input

### Changed
- Version bumped to 3.41.0

**Total test count: 986**

---

## [3.40.0]

### Fixed — A Significant, Real Bug: the Fallback Chain Silently Failed to Trigger on Misconfiguration
A second, deliberate "bulletproofing" re-pass through `app/sources/` (specifically checking small helper functions the complexity-driven pass never flagged) found that `router.py` and `fusion.py` each carried their own, independently-maintained copy of `_looks_empty()` — with phrase lists that had drifted apart in **both directions** since the two were originally written separately.

`router.py`'s copy was missing `"not configured"` and `"could not connect"` entirely. Confirmed the real, concrete consequence directly: with `FRESHRSS_URL` unset, `route_with_source("give me the news", "news")` returned the literal string *"FreshRSS is not configured. Set FRESHRSS_URL and FRESHRSS_USER."* as if it were a genuine, successful result — `source_used` stayed `"news"`, and `FALLBACK_CHAIN`'s real, designed `"news" → "web"` fallback never triggered, because `_looks_empty()` never recognized the config-error string as empty in the first place. The exact same gap applies to `"kiwix" → "web"` for any Kiwix-side "not configured"/"could not connect" message, though Kiwix doesn't currently produce one of those specific phrases.

`fusion.py`'s own list was separately missing `"unknown source"` (the real fix from an earlier pass this same release cycle, which never made it into fusion.py's independent copy) and `"error reaching"` — the real SearXNG timeout/connection message (`"Error reaching SearXNG: connection failed."`) doesn't contain a bare `"error:"`, since the colon comes after "SearXNG," not immediately after "Error."

Fixed by unifying both into one canonical `_looks_empty()` living in `fusion.py` (the safe import direction — `router.py` already imports `fusion` directly, the reverse would be circular), with the complete, merged phrase list verified against every real failure message every source file actually produces before being applied. `router.py`'s own copy now delegates to the shared one.

### Added (Tests)
- 4 new tests on the `router.py` side: the previously-missing "not configured" and "could not connect" phrases are now recognized, the real SearXNG "error reaching" message is recognized, and a direct test confirming the delegation to `fusion._looks_empty()` is genuine, not coincidental
- 2 new tests on the `fusion.py` side: the previously-missing "unknown source" and "error reaching" phrases are now recognized
- 1 new, real, end-to-end test confirming the actual fallback chain genuinely triggers when a source returns a real "not configured" message

### Changed
- `router.py`'s `_looks_empty`: now a trivial A(1) delegation
- Version bumped to 3.40.0

**Total test count: 984**

---

## [3.39.0]

### Fixed — A Significant, Real Data-Retention Bug: `uptime` Snapshots Were Pruned Far Too Aggressively
Continuing the bulletproofing pass into `app/snapshots.py`. A single, shared `MAX_SNAPSHOTS_PER_SOURCE = 288` constant was applied identically to every source, with a comment claiming "24 hours at 5-minute intervals" — true only for `ha` specifically, the source whose interval the constant was apparently chosen around. Confirmed directly with a constructed scenario: `uptime` (snapshotted every 2 minutes, the most frequent of any source) only retained **9.6 real hours** of data under that shared constant — while `_resolve_changes_hours()` in `router.py` explicitly, already supports "since yesterday" (48h) and "this week" (168h) as real, documented time-window phrases for every source. A real query for either would have silently returned an incomplete picture for `uptime` specifically, missing most of the requested window, with no indication to the user that the underlying data simply no longer existed. `news` (60-minute interval), by contrast, was retaining 288 real *hours* (12 days) under the same shared constant — far more than ever needed.

Fixed by scaling retention per-source from each source's real snapshot interval (`_RETENTION_PER_SOURCE`, built from the already-existing `JOB_INTERVALS_MINUTES`, moved earlier in the file so the new dict can be built from it directly), so every source genuinely supports a full week. `uptime` now retains 5040 snapshots, `ha` 2016, `forecast` 336, `news` 168 — confirmed via direct calculation that storage impact is genuinely negligible (roughly 1.5MB total across every source at realistic homelab scale).

### Fixed — `format_changes()` Could Display an Ugly, Unrounded Float to Real Users
This function's own type signature (`int | float`) explicitly invites a raw float, and a real caller (`router.py`'s `_search_changes()`, for "this morning"-style natural-language time resolution) genuinely produces one — without rounding, a real user could see "in the last 23.939205609166667 hours" displayed directly. Neither of this function's two current real callers was actually affected (one passes a REST endpoint's plain `int` parameter, the other already rounds before calling) — but formatting a number reasonably for display is this function's own job, not something it should rely on every present and future caller to remember correctly. Fixed with defensive rounding inside the function itself.

### Changed — Removed a Genuinely Dead, Redundant Branch in `_diff_news()`
`extract_headlines()`'s first branch (a bare `"**headline**"` with nothing after the closing `**`) was confirmed unreachable through any real `freshrss.py` output — every real format string always produces `"**title** (source)"`, with a parenthetical suffix. More than just dead, it was also redundant: the second branch's own logic (find the closing `**` via `.index()`, regardless of what follows it) already correctly handles the bare-closing case too, verified directly. Simplified to the one genuinely general check both branches were trying to express.

### Added (Tests)
- 3 new tests confirming the per-source retention fix: the computed retention values for each source, a direct check that every source's retention genuinely covers a full week given its own real interval, and a full end-to-end test with a real database confirming `uptime` genuinely retains enough data to answer a "since yesterday" query
- 1 new test confirming `format_changes()` displays a rounded value even when passed an unrounded float
- 1 new test confirming the simplified `extract_headlines()` still correctly handles the bare-closing case, even though it's unreachable through real output today

### Changed
- [Snapshot Engine and Changes](https://github.com/immortalbob/Mnemolis/wiki/Snapshot-Engine-and-Changes) wiki page updated to accurately describe the new per-source retention scheme and the real bug it fixes
- Version bumped to 3.39.0

**Total test count: 977**

---

## [3.38.0]

### Investigation Note
Continuing the bulletproofing pass into `app/scoring.py` and `app/query_expansion.py`. `query_expansion.py` held up completely clean — genuinely worth noting why: its failure paths all return early, before the single `set_routing()` cache-write at the end ever executes, naturally avoiding the same failure-caching bug found and fixed three times elsewhere this cycle (`_llm_pick_fusion_sources`, `_llm_detect`, `_get_disambiguation_candidates`), all of which called their cache-write unconditionally across every branch instead. Early-return-before-write is a genuinely safer shape for this exact class of bug. `mcp_server.py` was also re-read in full and confirmed clean — including verifying that returning a descriptive error string rather than raising `ToolError` is a deliberate, consistent choice matching every source file's own established "return a string explaining failure" contract, not a deviation from MCP best practice.

### Fixed — A Real, Significant Scoring Failure: Single-Character Keywords Were Silently Dropped
`_keywords()`'s filter (`len(w) > 1`) dropped every single-character token, including genuinely meaningful ones — "c" (the programming language), "r" (the statistics language). Confirmed this was a real, significant scoring failure, not a theoretical gap: for the query "tutorial for the c programming language," a result titled "C Programming Language Tutorial for Beginners" scored **lower** than an unrelated "JavaScript Programming Language Tutorial" result, since "c" — the one word that would have actually distinguished them — was silently dropped from both sides, leaving only the generic shared words to decide the ranking.

Two fix options were assessed directly before choosing: lowering the filter to `len(w) > 0` (simplest) versus keeping single characters only when genuinely alphanumeric. Tested both against a real noise case — a bare hyphen (common in real text like "C++ vs C# - which is better") — and confirmed `len(w) > 0` would let it through as a scored "keyword" (the hyphen isn't in the stripped character set, so it survives `.strip()` untouched), reintroducing real noise. The `isalnum()` check correctly excludes that case while still preserving "c", "c#", and "c++".

### Fixed — A Real, Reachable Bug: Tracking Query Strings Defeated the Bare-Domain-Root Check
`_is_generic_result()`'s URL-path check never stripped query strings or fragments before checking for a real path — a genuine bare-domain-root URL with a tracking parameter attached (`https://example.com/?utm_source=twitter` — a real, common pattern) was incorrectly treated as "has a real path," skipping the generic-result penalty it should have received. Fixed by stripping the query string and fragment first, mirroring `normalize_url()`'s own approach — verified a genuine article path *with* tracking parameters attached still correctly registers as having a real path either way.

### Added (Tests)
- 3 new tests for `_keywords()`: single alphanumeric characters are kept, bare punctuation is still correctly excluded, and multi-character tokens with symbols (`c++`, `c#`) still work
- 2 new tests for `_is_generic_result()`: a bare domain with a tracking query string is now correctly flagged as generic, and a real article with tracking parameters is correctly NOT flagged

### Changed
- Version bumped to 3.38.0

**Total test count: 972**

---

## [3.37.0]

### Investigation Note — Completing the `app/main.py` Bulletproofing Pass
Finished reading `app/main.py` top to bottom. Several areas were checked carefully and confirmed genuinely correct rather than fixed: the `/search` auto-routing cache-key reconstruction (verified with a direct test against `route_with_source()`'s real internal key, not just a source comparison), `/changes`'s `hours` parameter with a negative value (confirmed it produces a future cutoff timestamp that safely returns zero rows rather than anything dangerous — a real, minor UX confusion but not worth a fix given the low stakes), and `/snapshots/trigger`'s unbounded `concurrent.futures.wait()` (confirmed every underlying source call already has a real, finite timeout, bounding the realistic worst case).

### Fixed — `/logs`'s `limit` Parameter Could Return the Entire Query Log
SQLite treats a negative `LIMIT` value as "no limit at all" (confirmed directly, not assumed) — `GET /logs?limit=-1` would return the entire query log, defeating the endpoint's own intent of showing a bounded, recent-entries view. Low real-world severity at realistic homelab scale, but a real correctness gap. Fixed with a sane clamp (`max(1, min(limit, 1000))`).

### Fixed — A Real Maintenance Risk: Duplicated Backup File List
The same hardcoded list of tracked data files was duplicated identically in both `backup()` and `backup_info()` — a real risk that adding or removing a tracked file could update one copy and forget the other, leaving the two endpoints silently disagreeing about what Mnemolis actually tracks. Fixed with a single shared `_BACKUP_DATA_FILES` module-level constant.

### Fixed — Dead Code: a Built-But-Never-Used Variable in `backup()`
`included` (tracking which data files genuinely existed and got backed up) was built but never used for anything — not returned, not logged, just discarded. Since `/backup` returns a raw file download rather than JSON, there's no clean way to surface this in the response itself, but logging it costs nothing and gives real diagnostic value for confirming a backup genuinely included everything expected.

### Added (Tests)
- 2 new tests for the `/logs` limit clamp: a negative limit no longer returns the entire log, and an excessive limit doesn't error or hang
- 1 new test confirming `/backup` and `/backup/info` genuinely share the same file list constant

### Changed
- Version bumped to 3.37.0

**Total test count: 967**

---

## [3.36.1]

### Investigation Note
Continuing the bulletproofing pass into `app/main.py`, read top to bottom. Found and verified the cache-key reconstruction in `/search`'s auto-routing path (`f"fusion[{','.join(sorted(intent))}]:{request.query}"`) genuinely matches `route_with_source()`'s own internal key construction exactly — confirmed with a direct test, not just a side-by-side source comparison, since string-matching across two files has looked right before and turned out subtly wrong elsewhere this project's life. It's correct.

### Fixed — `/search` Reported a Meaningless `source_used` on Failure
When the auto-routing path raised an exception, the failure response's `source_used` field was set to `request.source` — which is just the literal string `"auto"` whenever auto-routing was requested, not a real source name. `intent` (already computed earlier in the same function, specifically to build the cache-check key) is the actual source this query was about to be routed to before the exception occurred. Fixed to report that real, already-known intent instead — a single source name when intent resolved to one, `"fusion"` when intent resolved to a list, and the original `request.source` unchanged when an explicit (non-auto) source was requested in the first place.

### Added (Tests)
- 3 new tests covering all three real cases: auto-routing resolving to a single source, auto-routing resolving to a fusion list, and an explicit source request — all verified to report the genuinely correct `source_used` on failure

### Changed
- Version bumped to 3.36.1

**Total test count: 964**

---

## [3.36.0]

### Fixed — A Significant, Confirmed-Real Bug: Thinking Models Silently Broken on the OpenAI-Compatible LLM Path
Continuing the bulletproofing pass into `app/llm.py`. `_complete_ollama()` already has a real, working fallback for "thinking models (qwen3 etc) that return empty response with thinking field" — but `_complete_openai()`, the code path for this project's actual real LLM backend (llama-server with Qwen3-Coder-30B), had no equivalent at all.

Confirmed this is a genuine, well-known, widely-reported failure mode before fixing it — not a theoretical concern: multiple independent real-world bug reports (across different projects, different OpenAI-compatible servers, and different thinking-model families) all describe the exact same symptom — a thinking model served via an OpenAI-compatible `/v1/chat/completions` endpoint routinely returns an **empty `content` field**, with all of the real output sitting in a separate `reasoning_content` field instead. `llama.cpp`'s own server documentation confirms this is the default `reasoning_format` behavior (`"deepseek"` style: thoughts go to `message.reasoning_content`, `message.content` stays empty). Without a fallback, every single completion on this path would silently return `None` — not a contrived edge case, but the literal default behavior for the specific kind of model this project's own README documents using on this exact backend.

Fixed by mirroring `_complete_ollama()`'s already-proven fallback pattern exactly: if `content` is empty, check `reasoning_content` (and `reasoning`, a variant some servers use instead) and extract the last non-empty line as a best-effort answer.

### Added (Tests)
- 3 new tests: the `reasoning_content` fallback works correctly, the `reasoning` field variant is also checked, and the case where both `content` and `reasoning_content` are genuinely empty still correctly returns `None`

### Changed
- Version bumped to 3.36.0

**Total test count: 961**

---

## [3.35.0]

### Investigation Note — Opening a New Phase: Bulletproofing
The complexity-driven investigation that ran through 3.20.0–3.34.0 has reached its natural end — every function above a meaningful complexity threshold has now had a deliberate, careful look. This release opens a deliberately different phase: reading every file in `app/` top to bottom, specifically looking past complexity scores at genuinely small, simple-looking code, on the theory that simple code earns less scrutiny precisely because nobody expects to find bugs there.

### Fixed — A Real, Significant Gap: Forecast Could Silently Run Unconfigured
The first file read in this new pass, `app/sources/forecast.py`, is genuinely small (under 100 lines) — and it had no check at all for unconfigured `forecast_latitude`/`forecast_longitude`, both of which default to `0.0`. Every other source file (`home_assistant`, `uptime_kuma`, `freshrss`) explicitly checks for missing required configuration and returns a clear "not configured" message; `forecast.py` had no equivalent.

`(0.0, 0.0)` is also a real, valid ocean coordinate off the coast of West Africa — meaning an unconfigured deployment wouldn't error, warn, or fail in any visible way at all. It would silently make a real, successful network call to Open-Meteo and return genuine, real weather data for the wrong place on Earth. Found a real, telling detail while confirming this: `main.py`'s own `/health` endpoint already has this exact check (`if not settings.forecast_latitude or not settings.forecast_longitude: return {"status": "not_configured"}`) — the project's own author had already recognized and solved this problem for the health-check path, but the fix never made it to the actual function real user queries hit through `/search`.

Fixed by adding the same check, matching the existing `/health` logic and every other source file's established pattern.

### Fixed — An Entire Test Class Unknowingly Relying on the Gap
Three existing test classes in `test_forecast.py` never configured real coordinates at all, only "working" because there was no check yet to catch the unconfigured default. Fixed by configuring real, valid coordinates in each affected `setup_method`, so these tests now genuinely exercise the configured-and-working path they were always meant to, rather than accidentally depending on a gap this release closes.

### Added (Tests)
- 1 new test directly confirming the fix: unconfigured `(0.0, 0.0)` coordinates now correctly return a "not configured" message instead of silently fetching weather for the wrong location
- 3 existing test classes' `setup_method`/`teardown_method` updated to configure and restore real coordinates

### Changed
- Both [Configuration Reference](https://github.com/immortalbob/Mnemolis/wiki/Configuration-Reference) and the README already correctly documented `FORECAST_LATITUDE`/`FORECAST_LONGITUDE` as required — this fix makes the actual code finally enforce what the documentation already promised, no documentation changes needed
- Version bumped to 3.35.0

**Total test count: 958**

---

## [3.34.0]

### Investigation Note — Closing This Release Cycle's Complexity-Investigation Arc
A twenty-first and final complexity-investigation pass this release cycle, applied to `_get_disambiguation_candidates()` (C, 14) — the last genuinely untouched function remaining above this cycle's investigation floor. This closes a sustained pass across essentially the entire codebase: every source file, every routing/scoring/dispatch function above a meaningful complexity threshold, and every function sitting adjacent to an already-confirmed real bug, has now had a deliberate, careful, fresh read this cycle.

### Fixed — The Same Real Bug, Found a Third Time
The exact same failure-caching pattern already found and fixed in `_llm_pick_fusion_sources()` and `_llm_detect()` earlier this release cycle was found here too: the bare-fallback result (just the original ambiguous word, no real disambiguation candidates at all) was cached under the same key a genuine LLM success would use, whenever the LLM call failed. A single transient hiccup would permanently lock that specific ambiguous word into the unhelpful fallback for the full routing cache TTL.

**A real, deliberate distinction was made here that didn't apply to the previous two fixes**, since this function's fallback path can be reached for two genuinely different reasons: the LLM call itself failing outright (`complete()` returning `None`/empty — a real, transient failure where a retry is likely to succeed) versus the LLM genuinely responding with three candidate phrases that simply didn't survive the sanity filter (e.g. none containing the original word at all — a substantive answer that just wasn't usable). The second case isn't really a transient hiccup; the same prompt would likely produce a similarly unusable answer again, so caching that specific outcome remains the more sensible default rather than re-querying the LLM on every repeat of a query it has already genuinely struggled with. Fixed by only skipping the cache write when `raw` itself was empty/falsy — confirmed both cases work correctly via direct, separate tests for each.

### Added (Tests)
- 2 new tests distinguishing the two real cases: a genuine LLM call failure is confirmed not cached, while a genuine-but-filtered LLM response is confirmed still cached

### Changed
- `_get_disambiguation_candidates`: C(14) → C(15) — a small, honest increase for the new `if raw:` guard
- Version bumped to 3.34.0

**Total test count: 957**

---

## [3.33.0]

### Investigation Note
A twentieth complexity-investigation pass this release cycle, applied to `_llm_pick_fusion_sources()` (C, 15) — the last genuinely untouched function in `router.py`, and the function deciding which sources get fused together for an explicit fusion query. The investigation found a real, significant bug, then traced the same pattern into a sibling function and fixed both consistently.

### Fixed — A Real, Systemic Bug: Caching a Failure as if It Were a Genuine Success
`_llm_pick_fusion_sources()` cached its own `["kiwix", "web"]` failure fallback under the exact same routing-cache key a genuine LLM success would use, whenever the LLM returned an unrecognized or malformed response. Confirmed directly: a single transient LLM hiccup (a truncated response, a momentary parsing glitch) would permanently lock that specific query into the generic fallback for the **full routing cache TTL** — a retry moments later that would have genuinely succeeded with a better, more specific source selection never even reached the LLM, since the cached failure short-circuited the function before the real call could happen.

**The same pattern was found in `_llm_detect()`**, the sibling function for single/multi-source routing — confirmed reachable with an identical direct test before deciding to fix both rather than just the one this investigation originally targeted. Both functions now correctly return their fallback value on a failed LLM response without caching it, giving every subsequent identical query a fresh, real chance at a correct decision rather than being permanently degraded by one bad LLM response.

### Added (Tests)
- 2 new tests, one per fixed function: each confirms a query that fails once and would genuinely succeed on a second attempt actually re-queries the LLM the second time, rather than being short-circuited by a cached failure

### Changed
- `_llm_pick_fusion_sources` and `_llm_detect`: complexity unchanged (removing one cache-write line each doesn't change branch count)
- Version bumped to 3.33.0

**Total test count: 955**

---

## [3.32.0]

### Investigation Note
A nineteenth complexity-investigation pass this release cycle, applied to `_resolve_changes_hours()` (C, 17) — the highest-scoring genuinely untouched function left anywhere in the codebase, and the natural-language time-phrase resolver behind the `changes` source. Two competing fix options were assessed precisely before choosing between them, rather than picking by instinct.

### Fixed — A Real, Reachable False Positive in Explicit-Hour-Count Parsing
The original regex (`r"(\d+)\s*hour"`) matched any number adjacent to the word "hour," regardless of context. Confirmed reachable and significant: this source's keyword routing (`_keyword_detect()`) is a substring match, not an exact-phrase requirement, so a compound query like `"any updates on my 3 hour delay flight, also what changed today"` correctly routes to the `changes` source (via the `"what changed"` trigger) — but then incorrectly resolves to a 3-hour window from the completely unrelated `"3 hour delay"` phrase, silently ignoring the user's actual, more relevant `"today"` signal and searching a window 8x narrower than intended.

Two fix options were assessed before choosing: reordering the checks (letting "today" win first when both are present) versus requiring an actual window phrase before the number. Reordering was tested and found to only fix the *compound* case — a query with the stray number and *no other* time phrase at all (e.g. `"3 hour delay flight tracker, what changed"`) would still incorrectly match, since the hour-count check would still eventually run with nothing else to win first. Requiring a genuine window phrase (`last`/`past`/`in`) closes both the compound and the basic case, verified against 5 real test scenarios including a second, independently-found false positive (`"24 hour clock display"`, a product feature description, not a time-window request) before being applied.

### Added (Tests)
- 3 new tests: the original compound false-positive case, the second "24 hour clock display" false positive, and confirmation that "in the past N hours" (not just "last") still resolves correctly
- Fixed a real `SyntaxWarning` (invalid escape sequence) introduced into one of the new test docstrings while writing it, caught and corrected before this release rather than shipped

### Changed
- `_resolve_changes_hours`: complexity unchanged at C(17) — the regex became more precise, not more branched
- Version bumped to 3.32.0

**Total test count: 953**

---

## [3.31.1]

### Fixed — An Asymmetric Gap in the Pronoun "I" Proper-Noun-Pair Fix
An eighteenth complexity-investigation pass this release cycle gave `_is_proper_noun_pair_at()` its first ever complete, dedicated, fresh read — despite having been edited twice already (3.24.0's original "I" exclusion, the fix found via the megaquery investigation). The fresh read found a real, if narrow, asymmetric gap left behind by that original fix: only `after_head` was checked for the pronoun "I," leaving `before_tail` unguarded. `"I and Texas are both fine"` (the unusual word order, "I" directly adjacent to the conjunction with no verb between them) still triggered the same false-positive proper-noun-pair protection that `"Texas, plus I need..."` already correctly avoided.

Assessed real-world reachability carefully before deciding whether to fix it: confirmed via direct testing that this specific construction is genuinely low-reachability through natural English — "I" is almost always followed by a verb ("I want," "I think," "I need"), never directly by a conjunction, so this exact asymmetric case essentially never occurs in a real, natural compound request the way the original `after_head` case commonly does (every natural phrasing tested — `"I want the weather and Texas news"`, `"I think Iran and Israel are the topic"` — correctly avoided the gap on its own, since a verb always separates "I" from the conjunction). Fixed anyway with a symmetric check on `before_tail`, since the asymmetry was real and the fix was cheap.

### Added (Tests)
- 2 new tests: the previously-asymmetric "I and Texas" case now correctly returns `False`, and the original, natural-word-order "Texas and I" case re-verified alongside it to confirm both sides of the symmetry hold together

### Changed
- `_is_proper_noun_pair_at`: C(13) → C(14) — a small, honest increase for the new symmetric check
- [The Proper-Noun-Pair Saga](https://github.com/immortalbob/Mnemolis/wiki/The-Proper-Noun-Pair-Saga) wiki page updated with an honest follow-up note under Bug 5, rather than a new heading, since this was a refinement of that same fix found during a later, separate investigation pass
- Version bumped to 3.31.1

**Total test count: 950**

---

## [3.31.0]

### Investigation Note
A seventeenth complexity-investigation pass this release cycle, applied to `app/main.py`'s `query_log_stats()` (D, 21) — the only function in `main.py` never touched all cycle, and a genuinely different category from everything else investigated so far (routing/scoring/dispatch logic): observability and reporting SQL. Two real, distinct findings.

### Fixed — A Stale Comment Describing Behavior the Code Never Actually Had
A comment claimed `latency_by_source` was computed "warm queries only," but the SQL had no `cached` filter at all and never did. Assessed which side was actually wrong before fixing either: constructed a realistic two-source comparison (one genuinely slow, network-bound source; one genuinely fast, local one) and found that a true warm-only average would mask almost all of the real difference between them (15ms vs 12ms in the test, versus a real, honest 3000ms vs 80ms cold-only difference) — cache hits are fast regardless of source, so warm-only averaging would make this metric *worse* at its own apparent diagnostic job, not better. The combined (current) behavior at least reflects real, paid latency. Fixed the comment to honestly describe the current, combined behavior, with the reasoning preserved for whoever reads this next — including an honest note that a cold-only breakdown would be the genuinely most diagnostic version of this metric, a real, deliberate scope decision for a future change, not something this fix took on.

### Fixed — A Real, Confirmed-via-Official-Documentation SQLite Correctness Gap
The `top_queries` SQL selected the bare `source_used` column directly inside a `GROUP BY` query containing four different aggregate functions (`COUNT`, `SUM`, `MIN`, `AVG`). Verified directly against SQLite's own official documentation: the database's "bare column" special-case guarantee — taking the value from the row that produced the aggregate — applies *only* when there is exactly one aggregate function, and that aggregate is specifically `MIN()` or `MAX()`. With four different aggregates present, this guarantee doesn't apply at all, and which row's `source_used` got reported when the same query text was answered by different sources at different times was genuinely undefined, not just unintuitive. This is a real, reachable case — routing logic itself has changed multiple times over this project's life, meaning the same query text could legitimately have been answered by different sources across its history.

Fixed with a correlated subquery reporting the **most recent** source for each query — chosen deliberately over "most frequent" after assessing both: most-recent stays accurate as routing logic evolves, while most-frequent would continue reporting a stale answer from before a real routing fix for as long as old log rows happen to outnumber new ones. Verified this has no meaningful performance cost at realistic homelab log volumes (3ms for 5000 rows / 300 distinct queries in direct testing).

### Added (Tests)
- 1 new test directly confirming the deterministic most-recent-source fix: a query answered by three different sources across its history correctly reports the most recently used one, not an undefined pick

### Changed
- `query_log_stats`: complexity unchanged at D(21) — both fixes were comment/SQL-logic corrections within the existing branch structure, not new decision points
- Version bumped to 3.31.0

**Total test count: 948**

---

## [3.30.1]

### Investigation Note
A sixteenth complexity-investigation pass this release cycle, applied to `app/sources/kiwix.py`'s `_score_result()` (C, 16) — the literal scoring formula deciding which article wins for every single Kiwix answer, including every disambiguation and multi-book fusion decision built on top of it, never read this carefully end to end despite how heavily its documented weights were relied on throughout this whole release cycle. Most of the function held up cleanly under precise verification, including the deliberate, real reason two scoring bonuses (+15 stemmed-title-match, +10 title-starts-with) apply a `len(w) > 3` filter while the broader per-word title/excerpt scoring doesn't — confirmed directly with a real test that a genuinely relevant short-acronym query ("raspberry pi gpio error") still correctly outscores an unrelated result by a wide margin (35 vs 6), the length filter protecting only the two highest-value "this IS the topic" signals from short stop-word noise, not penalizing real short technical terms broadly.

### Fixed — A Real Documentation Error in `_score_result()`'s Own Docstring
The docstring's scoring-breakdown table claimed excerpt word matches score "+1 each" — this never matched the actual formula (`int((excerpt_hits / excerpt_len) * 10)`, normalized by excerpt length, not a flat per-word count), confirmed directly: 3 hits in a 30-word excerpt score +1 total, not +3; 3 hits in a 5-word excerpt score +6, not +3. Not a runtime bug — the actual code and its own inline comment ("normalize by excerpt length to avoid bias") were already correct — but a real, misleading inaccuracy in the docstring summary anyone reading just that table would get wrong. The wiki's own [Kiwix Scoring](https://github.com/immortalbob/Mnemolis/wiki/Kiwix-Scoring) page was independently correct already, apparently written from the real code rather than this stale docstring. Fixed the docstring to match reality.

### Fixed — A Real, Narrow Stemming Inaccuracy
`_stem()`'s plain "ends with s, length > 3" rule has no way to distinguish a genuine plural ("foxes" → fox) from a common, non-plural English word that happens to end in "s" and is long enough to pass the length guard. Confirmed via direct testing: `"this"` → `"thi"`, `"less"` → `"les"`, `"across"` → `"acros"`, `"always"` → `"alway"`, `"towards"` → `"toward"` — all real, genuine inaccuracies. Investigated the actual real-world risk carefully before fixing: `_stem()` is always used to compare two complete strings against each other, never an isolated stop word for its own sake, and a consistent mis-stem applied identically wouldn't typically flip a real match into a false one in practice — confirmed this directly rather than assuming. Fixed anyway with a small, explicit exception list, since the inaccuracy was real and the fix is cheap; verified the exception list is narrow enough not to interfere with genuine plurals sharing a similar shape (`"classes"` → `class`, `"buses"` → `bus` both still stem correctly).

### Added (Tests)
- 2 new tests confirming the 5 exception words pass through unchanged
- 1 new test confirming the exception list doesn't break genuine plural stemming for similarly-shaped real words

### Changed
- `_stem`: C(11) → C(12) — a small, honest increase for the new exception check
- [Kiwix Scoring](https://github.com/immortalbob/Mnemolis/wiki/Kiwix-Scoring) wiki page updated with an honest note about this real, narrow limitation and the fix
- Version bumped to 3.30.1

**Total test count: 947**

---

## [3.30.0]

### Investigation Note
A fifteenth and final complexity-investigation pass this release cycle, applied to `app/sources/searxng.py` — the last completely untouched source file. Most of the file held up cleanly under direct verification, including a genuinely convoluted-looking dedup expression and `normalize_url`'s own behavior on several real edge cases (empty URLs, missing fields, mixed-case paths) — all traced through precisely and confirmed correct. One real, meaningful diagnostic gap was found.

### Fixed — SearXNG Timeouts No Longer Misreported as a Generic "Connection Failed"
`search()` always returned the same hardcoded `"Error reaching SearXNG: connection failed"` message regardless of the real failure cause — even though the actual exception was already captured and logged, just discarded before reaching the user. This is a real, documented pain point this project has already lived through once: [The SearXNG Timeout Lesson](https://github.com/immortalbob/Mnemolis/wiki/The-SearXNG-Timeout-Lesson) describes exactly how confusing it was to diagnose a real timeout when the only visible signal was a generic failure message — and that same gap was still present at the source, just never fixed there directly.

Fixed by adding a `raise_on_timeout` parameter to `_fetch_searxng()`: the primary fetch (the one whose failure reaches the user) now distinguishes a genuine `requests.exceptions.Timeout` from every other failure kind, returning a specific, more actionable message pointing at the real, documented fix. The alternate-query fetch (query expansion's second search) deliberately keeps the original, simpler contract — that failure is genuinely non-fatal already, and doesn't need its own distinct message, confirmed directly with a dedicated test that a timeout on the alternate fetch never produces the new timeout-specific wording when the primary result still succeeded.

### Added (Tests)
- 3 new tests: a genuine timeout produces the new, distinct message; a genuine connection refusal still correctly uses the generic message (confirming the fix didn't make every failure claim to be a timeout); and an alternate-query-only timeout stays correctly non-fatal and silent
- 4 existing tests updated (`fake_fetch` mock signatures) to accept the new `raise_on_timeout` keyword argument

### Changed
- `searxng.search`: C(12) → C(13) — a small, honest increase for the genuine diagnostic improvement
- [Troubleshooting](https://github.com/immortalbob/Mnemolis/wiki/Troubleshooting) wiki page updated — the SearXNG section header and intro now reflect that a timeout gets its own distinct message rather than the old generic one
- Version bumped to 3.30.0

**Total test count: 945**

---

## [3.29.0]

### Investigation Note
A fourteenth complexity-investigation pass this release cycle, applied to `app/sources/freshrss.py` — a complete file never read at all this cycle. Most of the file held up cleanly under scrutiny, including a genuinely convoluted-looking canonical-URL extraction expression that turned out to handle every real edge case correctly when traced through precisely (missing field, empty list, populated list, missing nested key — all verified directly). One real, significant gap was found in `_is_general_query()`.

### Fixed — A Significant Gap: Nearly Every Natural Phrasing of a General News Request Was Misclassified
`_is_general_query()` decides whether a query should skip relevance filtering and return the full feed, versus being scored against specific keywords. The check required every word in the query (after stop-word removal) to be a recognized general-news term — but `_STOP_WORDS` only handled formal grammatical filler ("the", "is", "about"), never the common request verbs people actually use when asking out loud. A direct test against 9 realistic phrasings — `"tell me the news"`, `"give me the headlines"`, `"show me my feeds"`, `"any news today"`, and others — found **9 of 9 failing**, each one incorrectly treated as a specific-topic query and scored against literal words like "tell" or "give" instead of cleanly returning the general feed.

Fixed by expanding `_STOP_WORDS` to include common request verbs and modifiers (tell, give, show, read, check, catch, any, today, update, etc.).

**A second, distinct gap was found while fixing the first:** `"whats new"` (no apostrophe) still failed even after the verb additions, since the bare word "whats" was never itself a recognized stop word — `_GENERAL_QUERIES` already handled both apostrophe forms of the full "what's happening" / "whats happening" phrase, but not the standalone contracted word.

**A real interaction bug was found and avoided while fixing the second gap, not shipped and caught later:** naively adding "whats" to `_STOP_WORDS` would strip it out of `"catch me up on whats happening"` before any multi-word phrase check could run against it, breaking the match against the existing "whats happening" entry. Fixed by checking multi-word `_GENERAL_QUERIES` phrases against the *original* query text directly, deliberately independent of stop-word stripping — verified this doesn't introduce a new false-positive risk either: `"what's happening with bitcoin"` and `"what's the latest news about bitcoin"` both correctly remain classified as specific-topic queries, since the unmatched remainder ("bitcoin") is checked and rejected, not just a blind substring match against the whole query.

All fixes verified together against a comprehensive 23-case sweep before being applied to the real file — every previously-fixed false-positive regression test, every newly-found phrasing gap, and the deliberate interaction-bug check, all passing together.

### Added (Tests)
- 11 new tests: 7 covering the originally-missing natural phrasings (tell/give/show/check/catch + "any news today" + "whats new"), 1 covering the specific interaction-bug scenario, and 2 confirming the fix doesn't introduce new false positives for genuinely specific-topic queries that happen to contain general-query words or phrases as substrings

### Changed
- `_is_general_query`: now its own clearly-measured B(8) function (previously inlined complexity within the broader stop-word-driven check)
- Version bumped to 3.29.0

**Total test count: 942**

---

## [3.28.0]

### Investigation Note
A thirteenth complexity-investigation pass this release cycle, applied to `app/sources/uptime_kuma.py`'s `search()` (C, 16) — a complete file never read at all this cycle, despite being one of three sources with a genuinely structured, binary signal trusted for [Conditional Query Detection](Conditional-Query-Detection)'s yes/no verdicts. The heartbeat-ordering assumption (`_get_status_from_heartbeats` walks a list and keeps the last dict seen, assuming chronological ascending order) was verified directly against the actual installed `uptime-kuma-api` library's own documented example and the official Uptime Kuma wiki — both confirm heartbeats are genuinely returned oldest-first, so this part was correct and is now verified rather than just assumed.

### Fixed — A Real Misclassification: No Heartbeat Data Was Silently Reported as "In Maintenance"
`_get_status_from_heartbeats` defaulted to status `3` (MAINTENANCE) whenever a monitor had no heartbeat data at all — a brand-new monitor that hasn't run its first check yet, or one whose check interval hasn't fired since Uptime Kuma's own restart. This is a real, reachable, everyday scenario, not a contrived edge case, and it produced a specific, false claim: a monitor genuinely never set to maintenance mode would be reported in `/search` and `/changes` summaries as "In maintenance," a deliberately-configured state it was never actually in.

Fixed by using `None` as an explicit "no data" sentinel — distinct from `0`/`1`/`2`/`3`, all genuine `MonitorStatus` values — and adding a new, honest `"No heartbeat data yet"` category to the response, reported separately from genuine maintenance. Two pre-existing tests that directly asserted the old, buggy `== 3` default were updated to assert the new, correct `None` sentinel instead, and a new test confirms genuine maintenance status (from a real heartbeat record) still reports correctly, distinct from the missing-data case.

### Added (Tests)
- Updated 4 existing tests (`test_missing_monitor_returns_none`, `test_empty_list_returns_none`, `test_missing_status_key_returns_none`, `test_non_list_heartbeat_returns_none`) to assert the corrected behavior
- 1 new test confirming a genuine MAINTENANCE status (from a real heartbeat) is still reported correctly, distinct from the no-data sentinel
- 1 new test confirming the actual user-facing fix end to end: a monitor with no heartbeat data is now reported under its own honest category, never as "in maintenance"

### Changed
- `uptime_kuma.search`: C(16) → C(19) — an honest, accepted increase as the cost of the new category and branch
- `_get_status_from_heartbeats`: A(5), now returns `int | None` instead of always `int`
- [Sources](https://github.com/immortalbob/Mnemolis/wiki/Sources) wiki page updated — the `uptime` section's claim that "the data itself is already structured and unambiguous" was true of the protocol, but not of how this code interpreted missing data; corrected with an honest note about the real distinction found
- Version bumped to 3.28.0

**Total test count: 931**

---

## [3.27.0]

### Investigation Note
A twelfth complexity-investigation pass this release cycle, applied to `app/sources/home_assistant.py`'s `_matches_filter()` (D, 24) — the actual entity-matching engine `search()` relies on, never read carefully on its own despite a real bug already found and fixed nearby in 3.22.0 (the area-filtered branch not calling this function correctly at all). Reading it fresh this time surfaced something different: not a missing call, but a genuinely dead feature flag.

### Removed — Dead `strict` Mode, Never Actually Implemented
A comment above `_matches_filter()`'s strict-mode branch claimed: *"only match domain OR device_class, not entity keywords bleeding in."* In practice, the strict and non-strict code branches checked the exact same four conditions in the exact same order, both falling through to the same final `return False` — genuinely, byte-for-byte behaviorally identical. Verified comprehensively before concluding this, not just from a quick read: a sweep across all 13 real `_QUERY_MAP` entries that set `strict: True`, tested against 9 varied synthetic entities (117 total combinations), found **zero** behavioral differences anywhere. The flag had been carried through `_build_filter()`'s merge logic and 13 separate `_QUERY_MAP` entries this whole time without ever actually changing what got matched.

Removed entirely: from `_matches_filter()`'s filter-spec handling, from `_build_filter()`'s merge logic, and from every `_QUERY_MAP` entry that set it.

**A real, pre-existing test quality issue was found and fixed along the way.** Two existing tests (`test_strict_mode_blocks_entity_keyword_bleed`, `test_strict_mode_allows_domain_match`) claimed to verify strict mode's behavior — but both constructed filters with no `entity_keywords` set at all, meaning there was nothing for entity_keywords to "bleed" from regardless of the strict flag's value. Both tests had been passing for the wrong reason since they were written: the scenario they constructed never actually exercised the behavior their names claimed to test. Replaced with a test that documents the real finding directly, including why those two prior tests never could have caught this.

### Changed
- `_matches_filter`: D(24) → C(15) — a substantial, genuine reduction from removing real dead branching, not just code reorganization
- `_build_filter`: B(8) → B(7)
- Version bumped to 3.27.0

**Total test count: 929** (net -1: two misleading pre-existing tests removed, one new, accurate regression test added in their place)

---

## [3.26.0]

### Investigation Note
An eleventh complexity-investigation pass this release cycle, applied to `app/sources/kiwix.py`'s `search()` (D, 24) — the actual entry point for every Kiwix query, never read line by line this cycle despite real production traffic flowing through it constantly. Most of the function held up cleanly under scrutiny (the `_build_search_terms` fallback path, the `primary_book` scoring parameter, the discourse-framing-stripped fallback string — all verified correct via direct tests, not just read and assumed). Two real, distinct issues were found and fixed.

### Fixed — A Real Logic Flaw: Negative Scores Broke the Multi-Book Fusion Threshold Check
A search result can legitimately score negative — a list/index article nets `-2` or `-7` after its own partial offset, with zero other query overlap. If the *overall best result* across every selected book happened to be negative, `score >= top_score * 0.5` silently broke down (e.g. `-10 >= -5` is `False`), meaning even the top result itself wouldn't pass its own bar, leaving the multi-book relevance comparison empty by accident. Traced carefully to assess real impact before fixing: this never produced a *wrong* final answer — when a genuinely good result exists anywhere, it becomes `top` by construction (since `top` is the single best score across everything), so the flaw could only manifest when every available candidate was already poor, in which case falling through to the single best (still poor) result was always the correct outcome anyway. Fixed by adding an explicit `top_score > 0` guard before attempting the multi-book comparison at all — making the existing, accidentally-correct fallthrough behavior intentional and correct by construction, rather than relying on the threshold math breaking down to reach the right answer.

### Changed — Disambiguation Candidates No Longer Applied to Non-Wikipedia Books
Disambiguation candidates are specifically Wikipedia-oriented phrasings (built to resolve encyclopedic ambiguity — see [Kiwix Disambiguation](Kiwix-Disambiguation)), but the search loop previously applied them to **every** selected book whenever multiple books were chosen and disambiguation triggered — including a non-Wikipedia secondary book the mechanism was never designed for. Never produced a wrong answer (scoring still picks the genuine best result across everything, so an irrelevant secondary-book result from a mismatched disambiguation term would simply score low and lose), but meant real, unnecessary extra Kiwix requests against a book with no actual business being searched using Wikipedia-disambiguation phrasings. Fixed: each selected book now searches with the term list actually appropriate for it — disambiguation candidates for a Wikipedia book, the plain `search_terms` for anything else. Verified directly: a 2-book selection (Wikipedia + Stack Exchange) with disambiguation active now correctly searches Wikipedia with all 3 candidate phrasings and the Stack Exchange book with exactly 1 plain term, down from 3.

### Added (Tests)
- 2 new tests for the negative-score guard: confirming a genuinely all-poor-results scenario doesn't crash and falls through correctly, and confirming genuinely competitive positive scores still correctly trigger multi-book fusion exactly as before
- 1 new test directly confirming a non-Wikipedia book in a multi-book disambiguation scenario is searched with plain terms, not Wikipedia-oriented disambiguation candidates

### Changed
- `kiwix.search`: D(24) → D(28) — an honest, accepted increase as the combined cost of both fixes, consistent with every correctness/efficiency-over-complexity-score tradeoff made this release cycle
- Version bumped to 3.26.0

**Total test count: 930**

---

## [3.25.0]

### Fixed — A Significant, Real Crash Bug: Fusion Failed Entirely When Mixing Fast and Slow Sources
A tenth complexity-investigation pass this release cycle, applied to `app/sources/fusion.py`'s `search()` (D, 22) — the actual cross-source merge engine behind every multi-source answer Mnemolis gives, including real production queries verified earlier this cycle. Reading the concurrent-fetch logic carefully surfaced a genuine, significant bug: `concurrent.futures.as_completed(futures, timeout=fusion_timeout)` raises its own `TimeoutError` for the **entire iteration** once the overall deadline passes — a separate, distinct mechanism from the per-future `future.result(timeout=...)` timeout already correctly caught inside the loop. This outer exception was previously uncaught, meaning **a single slow source mixed with a fast one crashed the entire fusion call**, discarding the fast source's genuinely successful result along with it, even though that data already existed in memory. This directly undermined fusion's own documented graceful-degradation design ("if only one source returns results, it is returned directly") by turning a partial success into a total, opaque failure (`error: "1 (of 2) futures unfinished"`) instead.

Confirmed via direct testing: before the fix, a fast source (0.1s) paired with a source exceeding the configured timeout crashed `fusion.search()` entirely. After the fix, the fast source's real result is returned cleanly, with the slow source correctly logged as timed out — exactly matching the intended design. The `/search` REST endpoint's own outer exception handler meant this never produced a raw 500 error, but it did mean every fusion query touching a slow source returned `success: false` with an opaque error message, discarding a real, already-available partial result every time.

### Changed — Unified a Genuine Cross-File Duplicate: `router.py`'s `_merge_decomposed_parts` Now A(5)
The same investigation found `fusion.py`'s `_merge_same_source()` was byte-for-byte identical to logic living inside `router.py`'s `_merge_decomposed_parts()` (extracted during the 3.21.0 pass on `route_with_source()`, without realizing fusion.py already had the same function). Verified both callers genuinely share the same input/output contract (`list[tuple[str, str]]` in, same shape out) before unifying — `router.py` already imports `fusion` directly (it calls `fusion.search()` for internal multi-source dispatch), making `fusion.py` the safe home for the shared function; the reverse import direction would create a circular import. `_merge_decomposed_parts()` dropped from carrying its own copy of this logic to a single call into `fusion._merge_same_source()`, landing at A(5).

### Added (Tests)
- A real, slow-running (~10s) regression test confirming a slow source mixed with a fast one no longer crashes `fusion.search()` and correctly returns the fast source's real result
- 2 new tests confirming the router/fusion merge-logic unification is genuine: one patches `fusion._merge_same_source` directly to confirm `router.py` actually calls into it (not just happens to produce matching output), one confirms consecutive same-source parts still correctly merge into one headered section end to end

### Changed
- `fusion.search`: D(22) → D(25) — an honest, accepted small increase as the cost of the crash-prevention fix, consistent with every other correctness-over-complexity-score tradeoff made this release cycle
- `router.py`'s `_merge_decomposed_parts`: dropped to A(5) as a direct result of the unification
- Version bumped to 3.25.0

**Total test count: 927**

---

## [3.24.0]

### Fixed — A Significant, Real Bug: the Pronoun "I" Mistaken for a Proper Noun
A seventh complexity-investigation pass this release cycle, applied to `_decompose()` (D, 28) — the single highest score left in the codebase, and the function with the densest documented bug history in the entire project (the Proper-Noun-Pair Saga's four prior bugs). Given that history, this pass was deliberately thorough: every line was read fresh, with no assumptions carried over from memory, rather than scanning for an obvious extraction opportunity.

The investigation found something more significant than a refactor — a genuine, common-phrasing bug in `_is_proper_noun_pair_at()`, the helper `_decompose()` relies on to avoid splitting bare proper-noun pairs like "Iran and Israel." **The pronoun "I" is always capitalized in English regardless of sentence position**, making it look exactly like a proper noun to the function's naive capitalization check. This meant a phrase like `"what's happening in Texas, plus I need help with my router"` was being incorrectly protected as a bare proper-noun pair (treating "Texas" + "I" as if they were a real pair like "Texas and Arizona"), causing the **entire query to not split at all** — even though "X, plus I need..." / "X, and I also..." is an extremely common, completely natural way to phrase a second, unrelated request, not a contrived edge case.

Fixed by explicitly excluding the pronoun "I" from counting as the proper-noun half of a pair. No broader pronoun list was needed — no other common English pronoun (he/she/they/we) is unconditionally capitalized regardless of context the way "I" uniquely is, so no other word produces this exact false-positive shape.

**A genuine, additional improvement surfaced while updating the existing regression test for this fix.** The original Proper-Noun-Pair Saga's own megaquery test (`test_proper_noun_pair_skip_does_not_discard_preceding_content`) asserted the query should decompose into exactly 3 parts — but that expected count had unknowingly baked in the limitation of this very bug: the numpy/GPIO clause in that test query was permanently merged into part 1 alongside "Iran and Israel," because the "plus I keep getting a weird numpy import error" text could never be recognized as its own separate intent while "Israel, plus I" kept getting misidentified as a protected pair. With the fix in place, this exact query now correctly produces 4 distinct parts — every original content-integrity assertion (Iran and Israel staying together, no real content lost) still holds true exactly as before; only the count was wrong, for a reason nobody had found yet when that test was originally written.

### Added (Tests)
- 2 new direct regression tests: confirming the pronoun "I" is no longer mistaken for a proper noun in this exact phrasing pattern, and confirming a genuine proper-noun pair ("Texas and Arizona") is still correctly protected after the fix — guarding against the fix being too broad as well as too narrow
- Updated the existing megaquery regression test to assert the correct, improved 4-part split, with clear documentation of why the original expected count of 3 was itself a symptom of the bug this release fixes

### Changed
- `_is_proper_noun_pair_at`: C(12) → C(13) — a small, honest, accepted cost of the new check, the same tradeoff pattern as every correctness fix this release cycle
- Version bumped to 3.24.0

**Total test count: 924**

---

## [3.23.0]

### Investigation Note
A sixth complexity-investigation pass this release cycle, applied to three related functions in `app/snapshots.py` (`_diff_uptime`, `_diff_forecast`, `_diff_ha`, all C-grade, all sharing a "diff two snapshots, return human-readable changes" purpose). Unlike previous passes, these three turned out to share no genuinely comparable logic worth extracting — each parses a fundamentally different data shape (loose English text, regex-extracted numbers, structured JSON) — so no refactor was forced. Instead, each was read carefully for genuine correctness issues on its own merits, the actual original goal of this investigation. All three turned up something real.

### Fixed — `_diff_uptime`: Pending State Mislabeled as Confirmed Outage
The previous version collapsed any transition away from "all up" into the same alarming `"Service outage detected"` wording, including a PENDING-only transition. Uptime Kuma's own status model treats "pending" (a retry/grace-period state) as genuinely distinct from a confirmed outage ("down") — using outage wording for a pending-only state is a real, misleading overclaim. Fixed by checking for the literal `"down"` label explicitly, separate from a generic "not all up" catch-all, giving pending-only transitions their own, honestly-worded message (`"Service check pending (possible outage starting)"`) while a mixed down+pending state correctly keeps the more severe outage wording.

### Fixed — `_diff_forecast`: Negative Temperatures Silently Unparseable
The temperature-extraction regexes (`r"high of (?:about )?(\d+)"`, `r"low of (\d+)"`) had no support for a negative sign, silently returning `None` for any sub-zero forecast text — meaning temperature-change detection would quietly stop working entirely for any Mnemolis deployment in a genuinely cold climate, with no error or warning. Forecast text comes directly from `round()` of Open-Meteo's real temperature data with no floor applied, making this a real, reachable gap for the project's explicitly anywhere-deployable design, not a contrived edge case. Fixed by adding an optional `-?` to both regexes.

### Fixed — `_diff_ha`: Uncaught Crash on a Malformed Entity
Directly accessing `old_e["state"]`/`new_e["state"]` with bracket notation raised an uncaught `KeyError` if either entity was missing that field — crashing the diff for *every other entity in the same snapshot too*, not just the malformed one. `snapshot_ha()` itself always writes a `state` field today, so this specific scenario isn't reachable through the current writer — but snapshots persist in a long-lived SQLite file and get read back potentially much later, so data written by an older version of this code, or before a future schema change, could genuinely still exist. Fixed by skipping (not crashing on) any entity missing the required field, verified with a test confirming one malformed entity no longer prevents a different, well-formed entity in the same snapshot from being correctly diffed.

### Changed
- `_diff_uptime`: C(15) → C(13) — a small, real complexity improvement alongside the wording fix
- `_diff_ha`: C(16) → C(18) — an honest, accepted small increase as the cost of the crash-prevention fix, consistent with the same tradeoff made for the router caching fix (3.21.1) and the HA exclusion-keyword fix (3.22.0) earlier this release cycle
- Version bumped to 3.23.0

### Added (Tests)
- 3 new tests for `_diff_uptime`: pending-only uses accurate wording (not "outage detected"), a confirmed down transition still uses outage wording, and a mixed down+pending state keeps the more severe wording
- 2 new tests for `_diff_forecast`: negative low temperature correctly detected, negative high temperature correctly detected
- 2 new tests for `_diff_ha`: a malformed entity missing the state field doesn't crash, and one malformed entity doesn't prevent a different, well-formed entity in the same snapshot from being diffed correctly

**Total test count: 922**

---

## [3.22.3]

### Changed — Extracted `_interpret_binary_state()`: D(28) → B(8), the Biggest Reduction This Release Cycle
A fifth complexity-investigation pass, applied to `app/router.py`'s `_interpret_yes_no()` (D, 28). The `uptime` and `ha` branches shared a genuinely identical shape — each checks which of two opposite states a condition asserts (down/up, unlocked/locked), then checks which state the result confirms, and returns whether they match.

**A real, self-introduced bug was found and fixed during the extraction itself**, worth being fully honest about rather than glossing over: "locked" is a literal substring of "unlocked", and the original code correctly avoided this trap by always checking for "unlocked" first, regardless of which polarity the condition asserted. A first, naive attempt at generalizing this instead checked whichever result-keyword matched the *condition's own* polarity first — which got the "condition says locked, result says unlocked" case backwards, silently returning `True` instead of the correct `False`. Caught by deliberately constructing and testing this exact scenario before trusting the generalization, not by the existing test suite, which — also found during this investigation — **never actually covered this specific case at all**. The fix checks the negative-state result keyword first in a fixed order, independent of which condition polarity was detected, verified against 14 manually constructed test cases spanning all three real callers before being applied to the actual codebase.

The new `_interpret_binary_state()` helper also correctly generalizes across all three sources' differing needs: `uptime`'s compound result check (`"all" in r and "up" in r`, not a single keyword), `ha`'s simple single-keyword checks, and `forecast`'s deliberately one-directional design (no positive-condition keywords at all, since "is it NOT raining" was never a phrasing this needed to handle) — all verified working correctly through the same shared function via caller-supplied check functions rather than hardcoded keywords.

### Added (Tests)
- **A genuinely missing regression test, added regardless of which version of the code is in place**: `test_ha_locked_condition_false_substring_trap`, confirming `_interpret_yes_no("the back door is locked", "Back Door: unlocked", "ha")` correctly returns `False` — this exact scenario was never tested before this investigation found the gap, meaning the original, correct code's check-order was effectively unprotected against a future "simplification" reintroducing this exact bug.
- 8 new direct, isolated tests for `_interpret_binary_state()`: both condition polarities crossed with both result polarities (including the substring-trap case explicitly), both "no real signal" `None` cases, and explicit coverage of the compound-result-check and empty-positive-keywords-list support needed for `uptime` and `forecast` respectively.

### Changed
- Version bumped to 3.22.3

**Total test count: 915**

---

## [3.22.2]

### Verified — Confirmed Correct Rather Than Finding a Bug This Time
A fourth complexity-investigation pass this release cycle, applied to `app/router.py`'s `_llm_detect()` (D, 29) — the highest score left in the codebase after the previous three passes. Found four near-duplicate "apply discourse-framing bias" blocks (cached fusion, cached single-source, fresh fusion, fresh single-source), the same suspicious shape that surfaced real bugs in the previous three investigations. This time, comparing them precisely surfaced something different worth being honest about: the two cached-decision blocks never re-cache the escalated result after adding the bias, while the two fresh-decision blocks do — initially looked like a real inconsistency, but direct testing confirmed it's correct, deliberate behavior, not an oversight. `_has_discourse_framing()` is cheap (pure string matching, no LLM call) and is re-evaluated fresh on every single call regardless of what's cached — so a routing cache entry that predates this bias, or was written before kiwix happened to be the chosen source, still correctly escalates on every subsequent call, not just the first, without needing the escalated decision itself to be re-cached. Verified directly: a simulated pre-fix cached entry escalates correctly on both the first and second identical call.

### Changed — Extracted Two Genuinely Reused Discourse-Framing Patterns: D(29) → D(23)
With correctness confirmed, the four blocks still shared two real, reusable patterns worth extracting: `_escalate_multi_source_for_discourse_framing()` (add kiwix to a source list if discourse-framing language is present and it isn't already there) and `_escalate_single_source_for_discourse_framing()` (the single-source equivalent, returning `None` when no escalation is needed). Both genuinely reused — each appears at exactly two of the four call sites — verified via direct testing of the helpers themselves, plus re-running the existing `TestDiscourseFramingRoutingBias` class (which already exercised all four real code paths through `_llm_detect()`'s public interface) to confirm zero behavioral change end to end.

**An honest, minor logging-detail tradeoff worth disclosing:** one of the four call sites previously logged a specific "Discourse-framing detected, adding kiwix..." message only when escalation actually happened, in addition to a second, always-fires "LLM escalated to fusion" line immediately after. Since the new shared helper doesn't report back whether escalation occurred (only the possibly-modified list), that specific intermediate log line was dropped in favor of relying on the still-present, always-fires line, which already shows the final source list including kiwix if it was added. A small, deliberate reduction in log specificity for that one path, judged acceptable since the same diagnostic information (that kiwix was added) is still visible in the surviving log line.

### Added (Tests)
- 7 new direct tests for the two extracted helpers: escalation with and without discourse framing present, no-op when kiwix is already included, and an explicit test confirming the multi-source helper returns a new list rather than mutating the caller's list in place

### Changed
- Version bumped to 3.22.2

**Total test count: 906**

---

## [3.22.1]

### Changed — Deduplicated `_pick_books_with_llm()`: E(34) → D(24)
Continuing the same complexity-investigation discipline through a third file this release cycle — `app/sources/kiwix.py`'s `_pick_books_with_llm()`, the next genuine outlier at E(34). Unlike the previous two investigations (`route_with_source()` and `home_assistant.py`'s `search()`), which both surfaced real behavioral bugs once compared carefully, this one turned up something simpler and just as worth fixing: the "pick Wikipedia if available, otherwise the first book" fallback logic was duplicated **byte-for-byte**, used identically for both the "no LLM configured at all" case and the "LLM responded but returned nothing usable" case — a genuine, exact, mechanical duplicate with no hidden divergence to find. Extracted into `_fallback_book_choice()`, confirmed via a dedicated test to verify both call sites genuinely invoke the same shared function (not just coincidentally producing the same output, which could silently mask a future re-divergence).

No other near-duplicate was found worth extracting in this function — the candidate-matching loop (parsing the LLM's raw comma-separated response, fuzzy-matching against real book names) has no comparable logic elsewhere in the file to check it against, and was left as-is, consistent with the same restraint applied to `home_assistant.py`'s grouping/formatting stages in 3.22.0.

### Fixed — A Real Test-Organization Mistake, Caught Before It Shipped
While adding regression tests for the new extraction, a `str_replace` edit accidentally inserted the new test class in the middle of the existing `TestPickBooksWithLLM` class rather than after it, orphaning several pre-existing tests that depended on that class's own `self._books()` helper. Caught immediately by running the affected test file directly rather than just the new tests in isolation — 7 pre-existing tests failed with `AttributeError: 'TestFallbackBookChoice' object has no attribute '_books'`, which would have been a real, confusing regression if it had reached the test suite undetected. Fixed by moving the new class to the correct location, after all legitimate `TestPickBooksWithLLM` tests.

### Added (Tests)
- 5 new tests for `_fallback_book_choice()` directly: Wikipedia-present, no-Wikipedia, empty-books, cache-key-usage, and an explicit test confirming both real call sites in `_pick_books_with_llm()` genuinely invoke the shared function rather than just happening to produce matching output

### Changed
- Version bumped to 3.22.1

**Total test count: 899**

---

## [3.22.0]

### Fixed — Real, Significant Bug: HA Area-Filtered Queries Silently Skipped Exclusion Keywords
Continuing the same complexity-investigation discipline that found the router.py caching gap in 3.21.1, applied this time to `home_assistant.py`'s `search()` — by far the most complex function in the codebase (F, 43-44). Comparing the area-filtered entity-matching branch against the keyword-filtered branch (the same side-by-side comparison that's now found four real bugs across two files this release cycle) surfaced a genuine, significant, user-facing issue: **the area-filtered branch reimplemented only a subset of `_matches_filter()`'s real logic** — `state_filter` and a simplified domain/device_class check — silently missing `exclude_entity_keywords`, strict mode, `entity_keywords`, and `event_keywords` entirely.

This was genuinely reachable and practically significant: queries like `"temperature"`, `"humidity"`, `"air quality"`, `"indoor"`, and even `"house status summary"` all set real `exclude_entity_keywords` (filtering out `cotech`/`processor`/`esp32`/`va_temperature` sensor-node entities that would otherwise pollute results with raw device telemetry). Combining any of these with a real area name — e.g. **"indoor air quality in the living room"** — silently skipped that exclusion entirely, since the area-filtered branch never checked for it at all. The same query without an area name correctly excluded those entities via `_matches_filter()`.

Fixed by deferring to `_matches_filter()` for the area-filtered branch too, rather than maintaining a second, incomplete reimplementation — so there's exactly one place this filtering logic lives, not two that can silently drift apart.

### Fixed — A Confirmed-Unreachable Defensive Branch, Removed
The first version of this fix added a special case preserving what looked like a deliberate leniency: a bare area-only query with no other real filter should still return everything in that area, since `_matches_filter()` alone would return nothing for a genuinely empty filter spec. Rather than assume this case was reachable, it was verified directly, three independent ways, before deciding whether to keep the extra branch: a full static trace of `_build_filter()`'s control flow, 2000 Hypothesis-generated random fuzz inputs run directly against it, and an exhaustive check of every real entry in `_QUERY_MAP`. All three confirmed `_build_filter()` never actually produces an empty filter spec for any real input — every path either matches something real or falls back to `_build_filter("summary")`, itself a real, non-empty filter. The leniency branch was genuinely dead code, not cheap defensive insurance, and was removed — simplifying the fix further and improving the function's complexity score from F(44) to E(37) as a direct result, on top of the correctness fix itself.

### Considered and Declined
Further extraction or restructuring of `home_assistant.py`'s `search()` beyond this fix was considered and declined — unlike `route_with_source()`, this function is a single continuous pipeline (configure check → fetch states → detect area → build filter → match entities → group → format) rather than several alternate strategies bundled together, and the entity-matching/grouping/formatting stages don't have an obvious near-duplicate worth the same side-by-side comparison that found this release's real bugs. The value of this investigation was the bug found, not a forced reduction in the complexity score — consistent with the same judgment call made in 3.21.1.

### Added (Tests)
- 2 new regression tests confirming `exclude_entity_keywords` is now correctly applied for area-filtered queries, and confirming a bare area-only query (falling back to the real, broad "summary" filter) still correctly returns multiple entity types within the area

### Changed
- Version bumped to 3.22.0

**Total test count: 894**

---

## [3.21.1]

### Fixed — A Real Caching Gap Found While Investigating Whether B-Grade Complexity Was Worth Pursuing
After 3.21.0 brought `route_with_source()` to C(18), a deliberate, honest investigation into what reaching B(6-10) would actually require — explicitly scoped as "only proceed if it surfaces a real bug, not purely to chase the grade" — found one. Comparing the decomposition loop's per-sub-query fusion dispatch against the top-level single-query fusion dispatch (the same side-by-side comparison discipline that found two real bugs during the 3.20.0/3.21.0 extractions) surfaced a genuine, real gap: **a decomposed sub-query that itself resolves to fusion (multiple sources at once) had no caching at all**, unlike every other path in the system — individual single-source sub-query results are cached via `_resolve_single_source()`, and the top-level fusion path explicitly builds a cache key from sorted source names and checks/sets it, but the sub-query-level fusion call fell through both. A repeated compound query whose individual clause happened to resolve to multiple sources internally would re-run `_llm_pick_fusion_sources()` and re-query every fusion source on every single request, even identical, immediate repeats.

Fixed using the exact same cache-key convention (`fusion[sorted,sources]:query`) the top-level fusion path already uses, rather than inventing a separate scheme. Verified directly with a real before/after test: two identical requests now correctly result in exactly one real call to `fusion.search()`, not two.

**The investigation that found this is documented as its own honest finding, not retroactively framed as "we were chasing this all along":** quantifying the actual complexity reduction available from unifying the sub-query and top-level dispatch logic showed it would only bring `route_with_source()` from C(18) to roughly C(12) — nowhere near B(6-10) on its own, and not worth the real risk of restructuring the function's recursive, loop-internal logic for that small a gain. The dispatch-unification idea was explicitly **not** pursued; only the concrete caching bug it surfaced was fixed, exactly as scoped going in.

**Honest cost:** this fix added 2 new decision points to `route_with_source()` (the new cache check and the empty-result check before caching), moving its complexity score from C(18) to C(20) — a small, deliberate, accepted regression in the metric in exchange for fixing a real correctness gap. Still solidly within C range, still a major improvement over the original F(45).

### Added (Tests)
- 3 new regression tests: confirming a repeated sub-query-level fusion result is served from cache rather than re-querying, confirming the new cache key matches the top-level convention exactly, and confirming two different sub-queries that both resolve to fusion get independent cache entries rather than colliding

### Changed
- Version bumped to 3.21.1

**Total test count: 892**

---

## [3.21.0]

### Changed — Continued Refactoring `route_with_source()`: D(30) → C(18)
A direct follow-up to 3.20.0's first extraction, picking up exactly where that one left off. The previous release reduced `route_with_source()` from F(45) to D(30) by extracting `_resolve_single_source()`; this release continues with two more targeted extractions:

- **`_resolve_conditional()`** — the leading "if X, Y" conditional-detection block, including the recursive condition/remainder handling. This is the single most bug-prone piece of logic in the function's history (see the wiki's "The Recursion Design Bug" page), so it was read fresh and completely before extracting, with no assumptions carried over from memory.
- **`_merge_decomposed_parts()`** — the consecutive-same-source merging and header-formatting step that runs after a decomposed query's sub-queries have all been resolved.

**A third near-duplicate was found and deliberately left unmerged, on purpose.** The decomposition loop has its own, separate conditional-detection check for each individual sub-query, which looks similar to the newly-extracted `_resolve_conditional()` at first glance — but a careful comparison (the same discipline that found two real bugs during the previous extraction) confirmed these are genuinely, correctly different by design, not an accidental duplication: the top-level handler builds its own complete headers since it returns directly with nothing else downstream, while the sub-query version correctly defers header-wrapping to the decomposition loop's own later merge step, since its result is just one ingredient among potentially several others still to come. Forcing these into one shared function would have broken the sub-query path's correct deferred-header behavior for the sake of looking less duplicated — left as two separate, deliberately specialized implementations.

**Result:** `route_with_source()` now scores C(18), the original target range — down from F(45) across both releases. Three new helper functions exist as a result, scoring B(9), B(8), and B(7) respectively. Verified with the same real production query confirmed working in 3.20.0 ("is it going to rain this week, and is the back door locked?") — identical, correct, fusion-merged output after the refactor as before it.

### Verified
Full re-verification pass across every tool used during this release cycle (`vulture`, `bandit`, `pip-audit`, `mypy`, `ruff`) confirmed zero new findings introduced by either extraction — every result either identical to the post-3.20.0 baseline or improved.

### Added (Tests)
No new tests this release — the three extracted functions are already covered by the existing `route_with_source()` test suite, which exercises every code path through its public interface regardless of internal structure; the extraction changes how the logic is organized, not what it does.

### Changed
- Version bumped to 3.21.0

**Total test count: 889** (unchanged — pure internal restructuring, zero behavior change beyond what already shipped in 3.20.0)

---

## [3.20.0]

### Fixed — Three Real Issues Found via Static Type Checking
A `mypy` pass (another genuine one-time check, following the same pattern as `vulture` and `bandit`) found 31 raw findings; most were the same low-value "needs an explicit type hint on an empty collection literal" pattern, but three were real and independently verified before fixing:

- **A genuine variable-naming collision inside `route_with_source()`** — two completely different, non-overlapping branches of this one large function both used the name `merged` for two different types (a string in the conditional-detection branch, a list of tuples in the decomposition branch). Not an active bug (the two branches can't both execute in the same call), but a real, confusing code smell worth fixing before it became one — renamed the conditional branch's variable to `merged_text`.
- **A stale return-type annotation on `get_changes()`** — declared `dict[str, list[str]]`, but the actual implementation (and `format_changes()`, the real, only caller) both correctly use `dict[str, list[dict[str, str]]]`. Verified the implementation and caller agree with each other before concluding the annotation itself was simply wrong, not the code.
- **`since_hours` typed too narrowly as `int` across three functions** — `_resolve_changes_hours()` deliberately returns `float` (so "this morning" can resolve to a precise fractional-hour count, e.g. 2.5 hours since 6am), and Python's `timedelta(hours=...)` already handles this correctly at runtime. The `int`-only type hints on `_get_snapshots_since()`, `get_changes()`, and `format_changes()` were simply too narrow for what the code already correctly does — widened to `int | float` rather than changing any actual behavior.

Two findings that looked concerning on first read (`fusion.py`'s `Callable[[str], str] | None` argument type, `home_assistant.py`'s untyped `_QUERY_MAP` causing a cascade of `"object" has no attribute "get"` errors) were investigated and confirmed safe — both are real runtime-safe patterns mypy can't fully trace through without an explicit type hint, not actual bugs. Added the missing hints (`_QUERY_MAP: dict[str, dict]`, `consumed_positions: set[int]`) anyway, since they resolved the false alarms and cost nothing.

### Changed — Refactored `route_with_source()`, the Most Complex Function in the Codebase
A `radon` cyclomatic-complexity pass flagged `route_with_source()` at F (45) — by far the highest in the project, consistent with how much real logic (conditional detection, recursive remainder handling, decomposition, fallback resolution) has accumulated there across many releases. Extracted the single-source-resolution-with-fallback logic into a new, standalone `_resolve_single_source()` helper, reducing `route_with_source()` to D (30) and giving the new helper itself a clean B (9).

**The extraction surfaced two real, previously undetected bugs**, found only by comparing two near-duplicate inline implementations side by side rather than reading either in isolation — exactly the kind of thing a careful refactor is supposed to catch:

1. **A real fallback-caching inconsistency.** The decomposition loop's fallback path called the fallback source's handler directly, with no cache check — while the top-level (explicit-source) path correctly checked the fallback source's own cache first. A repeated query that had already fallen back once should be served from cache the second time, regardless of which code path led to it; the decomposition loop wasn't doing this. Fixed by unifying both call sites on the new helper, which always checks cache first.
2. **An "unknown source" message that `_looks_empty()` couldn't recognize.** If `detect_intent()` ever returned a source name with no matching `SOURCE_MAP` entry (shouldn't normally happen since the two are kept in sync, but isn't provably unreachable), the resulting error message matched none of `NO_RESULT_PHRASES`, so it was incorrectly treated as real content — meaning a stale or misconfigured state could silently inject an "Unknown source" string into an otherwise-clean merged response instead of being dropped the way any other failed result already is. Fixed by adding `"unknown source"` to `NO_RESULT_PHRASES`.

### Considered and Declined
Continuing to refactor `route_with_source()` further (extracting the conditional-detection and decomposition blocks too) was considered and explicitly deferred — this function has the most real bug history of anything in the project, and one careful, fully-verified extraction that surfaced two genuine bugs felt like the right scope for one sitting rather than pushing for a complete restructure in the same pass. `mypy` and `bandit`/`pip-audit`/`vulture` were all, once again, treated as one-time checks rather than added to the permanent CI surface, consistent with every prior tooling decision this release cycle.

### Added (Tests)
- 3 new regression tests for `_resolve_single_source()`: confirming an unknown source is correctly treated as empty, confirming the fallback path now checks cache before calling a handler, and an end-to-end test confirming the decomposition loop and the top-level path now genuinely share identical fallback behavior

### Changed
- Version bumped to 3.20.0

**Total test count: 889**

---

## [3.19.3]

### Fixed — XML Parsing Hardened Against Entity Expansion Attacks
A `bandit` static security analysis pass — run as a genuine one-time check, the same way `vulture` was, not added as a permanent CI tool yet — found one real, medium-severity issue: `app/sources/kiwix.py`'s OPDS catalog parser used the standard library's `xml.etree.ElementTree.fromstring`, which is documented as vulnerable to XML entity expansion attacks (the "billion laughs" attack class) on untrusted input. Switched to `defusedxml.ElementTree`, a drop-in-compatible replacement (verified directly — same `fromstring()` API, same `Element` return type) specifically built to reject these attack patterns. The realistic threat model here is contained (the XML comes from `KIWIX_URL`, expected to be your own self-hosted, trusted Kiwix instance, not arbitrary internet content) but the fix was free and unconditionally worth applying regardless of how contained the risk is.

### Verified — Three Additional Findings Confirmed as Deliberate, Safe Patterns
The same pass flagged three low-severity `try/except: pass`/`try/except: continue` patterns. Each was independently checked in context rather than dismissed on the tool's "low severity" rating alone, and confirmed as deliberate, already-considered design rather than a hidden bug: a SQLite `ALTER TABLE ADD COLUMN` migration relying on the exception itself to detect "column already exists" (SQLite has no `ADD COLUMN IF NOT EXISTS` syntax), a routing-cache disk-load loop skipping one malformed entry without aborting the whole load, and a corrupted-cache-file rename step where a secondary failure (can't even rename the corrupted file) shouldn't crash startup over an already-degraded, non-critical recovery path.

### Verified — No Known Vulnerabilities in Pinned Dependencies
`pip-audit` against the real, current `requirements.txt` (including the `mcp==1.27.2` pin locked during the Streamable HTTP migration) returned zero known CVEs.

### Considered and Declined
Adding `bandit` and `pip-audit` as permanent, ongoing CI checks was considered and explicitly deferred, consistent with the same reasoning applied to `vulture` last release — treated as genuine, valuable one-time passes for now rather than an immediate permanent addition to the CI surface. Revisit if a future finding suggests otherwise.

### Changed
- Version bumped to 3.19.3
- Added `defusedxml` to `requirements.txt`

**Total test count: 886** (unchanged — this release hardens XML parsing with zero behavior change for legitimate input)

---

## [3.19.2]

### Fixed — A Fourth Dead Duplicate Function, Found via Vulture
A static dead-code analysis pass (`vulture`) found a genuinely real issue, independently confirmed before fixing: a *fourth* copy of the exact same dead, undecorated `logs_clear` body that's now been found and removed three separate times this project's life — this one sitting at the true end of `app/main.py`, directly beneath `query_log_stats()`'s own `return`/`except` block, with no separating blank line (the same copy-paste-accident signature as the previous three). The real, working, correctly-decorated `/logs/clear` endpoint was confirmed untouched and unaffected.

### Fixed — Dead Parameter and Documentation Gap in `filter_and_rank()`
`recency_bonuses: dict | None = None` was a real, genuinely unused parameter — confirmed independently via a direct grep showing zero callers anywhere in the codebase pass it, leftover from an earlier, abandoned `id(result)`-keyed approach to factoring recency into scoring. The function's own docstring already explained why that approach was dropped, but the parameter itself was never actually removed when the simpler, current `_recency_bonus` dict-key convention replaced it. Removed the dead parameter, and improved the docstring to document the real, current mechanism directly (callers attach a `_recency_bonus` key to each result dict; `filter_and_rank()` reads it via `r.get("_recency_bonus", 0)`) — verified this description is accurate against the actual function body before writing it.

### Verified — Two Reported Findings Confirmed as Genuine False Positives
The same pass also flagged 23 "unused variable" hits across two test files — both independently verified as standard, correct false-positive patterns rather than accepted on the report's word alone:
- `tests/test_snapshot_jobs.py`'s `temp_snapshot_db` (17 hits) is a pytest fixture injected purely for its `patch()` side effect (redirecting where snapshot tests write), never meant to be referenced by name in test bodies.
- `tests/test_kiwix_network.py`'s `limit` parameter (6 hits) exists in mock `side_effect` helper functions solely to match the real `_search_book()` call signature and avoid a `TypeError`, not because the specific assertions in those tests need to inspect its value.

No code changes for either — confirmed safe as-is.

### Considered and Declined
Adding `vulture` as a permanent, ongoing CI check (alongside the existing `ruff` lint workflow) was considered and explicitly declined for now — `ruff` already covers a meaningfully overlapping space (unused imports/variables), and a second static-analysis tool means a second whitelist file to maintain and a second thing that could go stale. Treated as a useful one-time pass rather than a permanent addition to the CI surface.

### Changed
- Version bumped to 3.19.2

**Total test count: 886** (unchanged — this release removed dead code and a dead parameter with zero behavior change, no new tests required)

---

## [3.19.1]

### Fixed — Two Real Bugs Found Via Actual MCP Client Testing
The 3.19.0 migration to Streamable HTTP had only ever been verified via the test suite and direct code tracing — no real MCP client had ever actually connected to the new endpoint. The very first real connection attempt, using MCP Inspector, surfaced two genuine bugs neither the test suite nor direct code reading had caught, both now fixed and confirmed working end to end against a real client over a real network.

**1. Doubled endpoint path.** `FastMCP`'s own internal Streamable HTTP route defaults to `/mcp`, and `main.py` separately mounts the whole MCP app at `/mcp` — combined, the only actually-reachable path was `http://host:8888/mcp/mcp`, not the documented `http://host:8888/mcp`. `TestClient`-based tests never caught this because they call the app object directly by Python reference and never construct or resolve a real URL path. Fixed by setting `streamable_http_path="/"` on the `FastMCP` instance, so `main.py`'s own `/mcp` mount is the only `/mcp` in the final, effective path.

**2. Real LAN connections rejected with "Invalid Host header."** `FastMCP` auto-enables DNS-rebinding protection whenever its `host` constructor parameter is left at the default `127.0.0.1` — which only allows `Host` header values of `127.0.0.1`/`localhost`/`::1`, rejecting every request addressed to Mnemolis's actual LAN IP or hostname. Since Mnemolis is explicitly designed to be reached over a real home network (the entire point of running it as a homelab service), this meant **no real-network MCP connection could ever succeed**, regardless of the path fix above. `TestClient`'s default `testserver` host never exercises real Host-header validation, so this was invisible to every test written so far. Fixed via `transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)`, matching the trust model already documented for the REST API's own optional auth.

A real, deliberate risk was checked and ruled out while designing the path fix: mounting the MCP app at root (`/`) instead of fixing `streamable_http_path` was considered and tested, then rejected after confirming directly that it would shadow every REST route registered after it in `main.py` (`/health`, `/search`, etc. are all defined after the MCP mount) — a root `Mount` matches any path prefix regardless of registration order. The chosen fix avoids this entirely by keeping the mount at `/mcp` and changing FastMCP's internal route instead.

### Verified
**The full MCP Streamable HTTP migration is now genuinely confirmed working end to end against a real MCP client** (MCP Inspector), not just the test suite — connection established over a real network, exactly one `search` tool listed with the expected schema, and a real test call (`"what is nitrogen"`) correctly returned genuine, multi-book-fused Kiwix content (Wikipedia + Wiktionary). This closes the verification gap explicitly flagged as outstanding when 3.19.0 shipped.

### Added (Tests)
- 3 new regression tests specifically covering both bugs: confirming the MCP endpoint is reachable at the single documented path, confirming a real (non-localhost) Host header is accepted rather than rejected, and confirming the MCP mount's position in `main.py` doesn't shadow other REST routes

### Changed
- Version bumped to 3.19.1

**Total test count: 886**

---

## [3.19.0]

### Changed — MCP Transport Migrated from SSE to Streamable HTTP
Prompted by an external MCP audit (a community-run tool, opened as a GitHub issue against the project) flagging the old transport's use of `request._send`, a private Starlette attribute. Initial research confirmed that pattern genuinely matches the official MCP Python SDK's own low-level reference examples — not a Mnemolis-specific shortcut — but deeper research surfaced two more substantive findings: an official, higher-level integration pattern exists (`FastMCP.streamable_http_app()`) that avoids touching private internals in application code at all, and SSE transport itself is explicitly being superseded — official FastMCP docs state it "exists only for backward compatibility and shouldn't be used in new projects."

**Breaking change for existing MCP client connections:** the endpoint moved from `/mcp/sse` to `/mcp`. Update `claude_desktop_config.json` or any other MCP client configuration accordingly.

- Rewrote `app/mcp_server.py` from the low-level `mcp.server.Server` class with a hand-written JSON Schema dict, to `mcp.server.fastmcp.FastMCP` with a decorator-based `@mcp.tool()` registration.
- **Deliberate, documented schema tradeoff** — the `source` parameter is now a plain `str`, not an `Enum`/`Literal` with a JSON Schema `enum` constraint. FastMCP's tool decorator currently has no supported way to register a fully custom inputSchema (open upstream SDK issue, no workaround), and `Enum`/`Literal` types generate a `$ref`/`$defs`-based schema with a separate, real, open compatibility bug affecting at least one real MCP client (a `$ref` resolution failure that gets the tool rejected outright). Valid source values are now documented in the tool's docstring instead, at the honest cost of losing schema-level enforcement.

### Fixed — A Real, Currently-Open Ecosystem Bug Found During Migration
`FastMCP.streamable_http_app()` lazily creates and caches **one** `StreamableHTTPSessionManager` on the FastMCP instance — calling it again returns a new app object wrapping the *same* cached, already-`.run()` session manager, which can only ever be entered once per instance. A module-level `mcp_app` built once at import time meant every independent app lifecycle (every container restart; every test file's own `TestClient`) tried to reuse an already-exhausted session manager, raising `RuntimeError: StreamableHTTPSessionManager .run() can only be called once per instance` on the second attempt.

Confirmed this is a real, currently-open issue across the broader MCP/FastMCP ecosystem, not specific to Mnemolis — multiple independent reports describe the identical error in both test suites and real production deployments under certain conditions.

**The first attempted fix was itself genuinely incomplete** — resetting the FastMCP instance's cached session manager reference and rebuilding the app worked when tested in isolation (three consecutive fresh app objects, each independently entered and exited cleanly), but failed against the real, actual scenario: `main.py` mounts the MCP app **once**, at module-import time, and the already-mounted route held a reference to the *original* app object's lifespan closure and request handler regardless of what the module-level `mcp_app` variable was reassigned to afterward. The complete fix (`get_mcp_app()` plus rewiring the actual `Mount` route's `.app` attribute in `main.py`'s lifespan function) was verified by directly tracing through the precise failure with real debug introspection — checking `_has_started` state directly, confirming a plain `Mount()` never runs a sub-app's lifespan at all (ruling out one hypothesis), then reproducing the exact `main.py`-shaped failure precisely before fixing it.

A separate, more serious concern was found during the same research but is explicitly **not** addressed by this fix, since it's a transport-level issue outside Mnemolis's control: an open upstream issue describes a race condition where the session manager can report "shutting down" immediately after a request starts, before a response is fully streamed. Documented directly in `mcp_server.py` as a known risk to watch for.

### Changed — Dependency Pinning
`mcp[cli]` in `requirements.txt` was previously unpinned, meaning every fresh install received whatever the latest version happened to be — a real, separate risk given how much the SDK's own API surface shifted across the versions encountered during this migration's research (method names, schema-registration support, and more, varying meaningfully release to release). Pinned to `mcp[cli]==1.27.2`, the exact version this migration was built and tested against.

### Added (Tests)
- Completely rewrote `tests/test_mcp_server.py` against the new implementation — the old file directly tested removed internals (`create_sse_app`, standalone `list_tools`/`call_tool` functions) and would have needed deletion regardless of pass/fail status
- New regression tests specifically covering the session-manager bug: confirming repeated `get_mcp_app()` calls produce genuinely independent session manager instances, and that a full lifespan can be entered and exited multiple times without raising
- New regression test confirming the `source` parameter's schema deliberately has no `enum`/`$ref`/`$defs` — protects the documented tradeoff from being silently reversed without it being a deliberate decision again

### Changed
- Version bumped to 3.19.0

**Total test count: 883** (net unchanged — old MCP tests removed and replaced, new regression tests added for the real bug found)

---

## [3.18.2]

### Added — GitHub Wiki (Documentation Restructuring Complete)
29 wiki pages covering every Core Concept, both Kiwix and Web/News deep dives, Operations, four full Design History narratives (the real bug-hunting stories — proper-noun-pair guard's four sequential bugs, the two-part discourse-framing fix, the SearXNG config-vs-running-process lesson, the conditional-detection recursion bug), Reference material (Roadmap, the verified Open WebUI System Prompt Guide, Contributing), and the remaining Getting Started pages (About, First-Time Setup, Home Assistant Integration, Configuration Reference, Troubleshooting).

Every numeric claim (scoring weights, thresholds, TTLs) verified against actual running code rather than assumed from memory or docstrings — caught one real docstring/code discrepancy in Kiwix's list-article scoring bonus along the way. Every internal link cross-checked against the page map; six broken anchor links (an em-dash-to-double-hyphen slugification mistake, repeated several times before the pattern was fully internalized) found and fixed through systematic full-wiki audits rather than one-off spot checks. One page (`Configuration Reference`) originally promised on the map was found missing entirely only by checking the map against the filesystem directly at the end — written and added before considering the wiki complete.

### Changed — README Restructured to Reference the Wiki
Replaced extensive design-rationale prose, exact scoring breakdowns, and bug-history narrative with short summaries and direct wiki links — applied to Query Decomposition, Conditional Query Detection, Multi-Book Fusion, Confidence-Aware Fusion, Query Expansion, Caching, the SearXNG timeout note, and the Backup & Restore volume-naming note. Architecture diagrams, the REST API reference, and all actionable setup/config steps were deliberately kept in full — only the "why," not the "how" or "what," moved to the wiki. 15 wiki links added across the README, each verified against a real, existing page before shipping.

### Added — CI Infrastructure
- **Test workflow** (`.github/workflows/tests.yml`) — runs the full 883-test suite on every push and pull request to `main`. Verified with a real local dry run in a bare environment (no Docker, no homelab network access) before shipping — confirmed every test is genuinely network-independent and properly mocked, not just claimed to be in file headers.
- **Lint workflow** (`.github/workflows/lint.yml`) — runs `ruff` on every push and PR.
- **Docker build verification** (`.github/workflows/docker-build.yml`) — builds the real `Dockerfile` on every push and PR, catching a class of failure (broken `COPY` paths, dependency issues specific to the slim image, Dockerfile syntax errors) the Python-only test suite can't see at all.
- **Dependabot** (`.github/dependabot.yml`) — weekly automated PRs for `pip` dependency updates and GitHub Actions version updates.
- **Stale issue/PR check** (`.github/workflows/stale.yml`) — flags issues and PRs with 60+ days of inactivity, closes after 14 more days of continued silence. `pinned` and `security` labels are exempt.
- **`.gitignore`** added — `.pytest_cache/`, `.hypothesis/`, local `data/`, `.env`, and the personal (non-template) `docker-compose.yml` are no longer tracked going forward.
- Three new status badges added to the README (Tests, Lint, Docker Build).

### Fixed — Two Dead, Duplicate Functions Removed
Setting up the lint workflow surfaced a real, pre-existing issue unrelated to lint *style*: `logs_clear()` was defined three separate times in `app/main.py`, byte-for-byte identical, with only the first carrying the actual `@app.post("/logs/clear")` decorator. The other two were genuinely dead code — never registered as routes, silently shadowed, sitting orphaned directly after unrelated functions with no separating blank line (a clear sign of an old copy-paste accident). Removed both; `/logs/clear` itself is unaffected, since the real, decorated definition was untouched. Verified via full test suite re-run before and after (883 passed, identical, both times) and via `ruff check` reporting zero remaining issues.

A separate ambiguous variable name (`l` in `app/llm.py`, easily misread as `1` or `I`) and one genuinely unused test variable were also fixed — both real, if minor, and both caught only because the linter was being verified against the actual codebase before being shipped as a required CI check, rather than assumed clean.

**Total test count: 883** (unchanged — this release added no new application logic, only removed dead code with zero behavior change)

---

## [3.18.1]

### Added — Background Snapshot Job Health (Third and Final Operational Maturity Item)
Found via real review, not a reported failure: every background snapshot job (`snapshot_uptime`, `snapshot_forecast`, `snapshot_news`, `snapshot_ha`) already catches its own exceptions internally and just logs a warning — meaning a job that started failing on every single run would never crash, never stop the scheduler, and produce zero externally visible signal beyond a log line nobody is necessarily watching. The `BackgroundScheduler` object itself also had no external visibility at all — it's a local variable inside `main.py`'s lifespan context manager, never exposed to any endpoint — so there was previously no way to ask "is the background scheduler actually still running and succeeding" without reading raw application logs.

- **`get_snapshot_job_health()`** — reports each job's health by comparing its most recent successful snapshot timestamp (already persisted and timestamped in the existing `snapshots` table, requiring zero new instrumentation) against its expected interval, using a 3x grace multiplier to absorb normal jitter without false-alarming. Four possible states per job: `ok`, `stale` (genuinely overdue), `never_ran` (no snapshot ever stored), `unknown` (an unparseable timestamp — degrades gracefully rather than raising).
- **`/health`** now includes a `snapshot_jobs` field with this report for all four jobs.
- **Verified against real production data** — all four jobs correctly reporting `ok` with accurate per-job timing matching the real scheduler configuration (uptime every 2 min, forecast every 30, news every 60, ha every 5).

This closes out the Battle Testing & Operational Maturity phase's three identified gaps: fallback visibility (3.18.0), routing cache bounding (3.18.0), and now background job health.

### Added (Tests)
- 9 new regression tests across `get_snapshot_job_health()` (fresh/stale/never-ran/unknown-timestamp states, correct per-job interval mapping, jitter tolerance) and its `/health` integration, reusing the relative-timestamp test helper pattern established earlier this session for the exact same reason — hardcoded absolute timestamps in a staleness check would silently break the moment real time passed whatever window was hardcoded against them.

### Changed
- Version bumped to 3.18.1

**Total test count: 883**

---

## [3.18.0]

### Added — Fallback Visibility (First Operational Maturity Item)
The query log previously had no signal at all about whether a result came from a query's originally-intended source or from a `FALLBACK_CHAIN` fallback (e.g. kiwix → web) — `source_used` only ever showed the final outcome, with no record of whether it took a detour to get there.

- **`fallback_occurred`** — a single new boolean column on `query_log`, set by comparing the pre-route intended source (from `detect_intent()` for `auto` requests, or the explicit `request.source` otherwise) against the actual resolved source from `route_with_source()`. Deliberately does **not** change `route_with_source()`'s own return signature — that function already recurses into itself at 4 internal call sites (conditional detection's condition/remainder handling, and the same for decomposed sub-queries), so widening its return tuple would have touched every one of those, a much larger and riskier change than a post-hoc comparison needed to be.
- **Proper migration** — `ALTER TABLE query_log ADD COLUMN fallback_occurred` runs defensively alongside the `CREATE TABLE IF NOT EXISTS`, since the latter only ever affects fresh installs; an existing deployment's table doesn't gain new columns just because the `CREATE` statement changed.
- **`/logs/stats`** now reports `fallback_count`, `fallback_rate_pct`, and a `fallback_by_target` breakdown.
- **Real design flaw found and fixed before shipping** — the first version of the `fallback_by_target` breakdown attempted to attribute fallbacks to their original source (`kiwix` vs `news`), but since both share the same fallback target (`web`), a boolean column genuinely cannot distinguish which one triggered a given fallback — querying per-original-source would have run the identical SQL query under both labels and double-counted the same underlying rows. Fixed by reporting an honest, combined label instead (e.g. `kiwix_or_news_fallback_to_web`) rather than guessing at an attribution the data doesn't actually support.
- **Verified against real production data** — the known GPIO/numpy query that falls back from kiwix to web (confirmed multiple times earlier this session) now correctly logs `fallback_occurred=1` and surfaces in `/logs/stats` exactly as designed; a control query with no fallback correctly logs `0`.

### Added — Routing Cache Size Bounding (Second Operational Maturity Item)
Found during operational maturity review: the routing cache had **no size limit at all**, unlike the result cache, which already had a proven bounded-eviction pattern (`_CACHE_MAX_SIZE` / `_evict_oldest()`). Given how many genuinely distinct cache keys this session's own features generate — every unique conditional query, discourse-framing phrase, and disambiguation candidate set gets its own entry — unbounded growth over sustained real-world usage is a real operational risk, not just a theoretical one.

- **`routing_cache_max_size`** config setting (default `1000`), mirroring `cache_max_size`'s existing pattern.
- **`_evict_oldest_routing()`** added, identical in shape to the result cache's `_evict_oldest()`.
- **Defensive cap on load from disk too** — a routing cache file saved before this fix existed could theoretically still be over the new limit; `load_routing_cache()` now trims to the most recently-written entries if so, rather than silently allowing an over-limit cache to persist across a restart.
- **`/health`** now reports `cache_max_size`, `routing_cache_entries`, and `routing_cache_max_size` alongside the existing `cache_entries` field, so growth toward either bound is visible without digging through logs or code.

### Verified
The existing `/health` endpoint was reviewed before building anything new — it already performs real, live network checks against all 6 source dependencies plus the LLM backend, not just config-presence checks. This existing coverage was confirmed working correctly against current production state before concluding the only genuine remaining gaps were the routing cache's missing bound and its absence from `/health`'s reported fields.

### Added (Tests)
- 10 new regression tests across fallback detection (`/search` integration, `/logs/stats` surfacing, the combined-label fix), routing cache eviction (oldest-entry correctness, existing-key-doesn't-evict correctness), and the new `/health` fields

### Changed
- Version bumped to 3.18.0

**Total test count: 875**

---

## [3.17.0]

### Fixed — Discourse-Framing Routing Bypass (Longest-Standing Known Limitation, Resolved)
Queries phrased with current-discourse framing ("what's the deal with that whole mercury retrograde thing everyone keeps talking about") reproducibly routed past Kiwix to news/web entirely, even for genuinely encyclopedic topics Kiwix's disambiguation-backed search is well suited to answer. Root cause traced precisely: the LLM router's news/web descriptions ("current events," "recent information") matched this phrasing almost word-for-word, while Kiwix's description ("factual, encyclopedic, or technical questions") gave no signal that an evergreen topic can also be currently trending in public conversation.

Fixed in two parts, both required — the first alone wasn't sufficient, found through continued real-production testing after it shipped:

1. **Routing bias** — `_has_discourse_framing()` detects the pattern explicitly and biases the routing decision directly: when present and Kiwix wasn't already part of the LLM's chosen source(s), Kiwix is added and the result escalated to fusion. Applied consistently across all four real code paths (fresh single-source, fresh multi-source, cached single-source, cached multi-source) — the cached paths matter because a routing cache entry written before this fix existed would otherwise silently bypass it for the remainder of its TTL.
2. **Search term cleanup** — even with Kiwix correctly included, the words "everyone," "obsessed," "talking," "keep" still survived `_STOP_WORDS` untouched and were sent to Kiwix as literal search terms. "What whole bitcoin everyone obsessed" matched scattered, irrelevant content (a sitcom character, score 2) far more readily than the real topic word could compete against. `_strip_discourse_framing()` removes the whole matched phrase as a unit before tokenizing — more surgical than adding individual words to `_STOP_WORDS`, since it only affects queries that actually contain this exact pattern rather than risking "everyone" or "keep" being treated as filler in some unrelated query where they carry real meaning.

The pattern list (`DISCOURSE_FRAMING_PATTERNS`) lives in `kiwix.py` as the single canonical source — `router.py` imports it from there rather than keeping an independent copy, since `kiwix.py` needs it for search-term stripping and `router.py` needs it for the routing bias, and the import direction (`router.py` → `kiwix.py`) avoids a circular dependency that the reverse direction would create.

**Verified against real production data, before and after both halves of the fix:** "bitcoin" went from a nonsensical match (a sitcom character, score 2) to the correct, exact Bitcoin Wikipedia article (score 32). "Black holes" went from a Thai horror film (score 7) to a genuine historical "Black Hole" topic (Black Hole of Calcutta, score 22) — not the astrophysics article one might expect for this specific bare-word query, but a legitimate, defensible match rather than a nonsensical one. "Galaxy" improved (from a movie reference to a thematically-related literary reference) but didn't fully resolve, since "galaxy" alone remains genuinely ambiguous in the corpus between astronomy and pop-culture senses — tracked as a smaller, separate disambiguation-quality question rather than evidence this fix didn't work.

### Added (Tests)
- 17 new regression tests across `_has_discourse_framing()`, the four-code-path routing bias, and `_strip_discourse_framing()`'s search-term cleanup

### Changed
- Version bumped to 3.17.0

**Total test count: 862**

### Roadmap
**Known limitations carried forward:**
- Single ambiguous bare words (e.g. "galaxy") can still land on a thematically-related but imprecise match when the corpus contains multiple genuinely distinct senses of the same word — a search-relevance question distinct from the discourse-framing routing bypass fixed above, since this persists even when Kiwix is correctly included and search terms are clean
- Conditional phrasing without an explicit separating comma is intentionally not detected — a real grammatical-parsing problem, not a pattern-matching one
- A decomposed segment containing two genuinely unrelated topics merged together (e.g. a proper-noun pair adjacent to unrelated technical content) routes to a single source that may not serve both topics well — an expected, minor side effect of the proper-noun-pair content-preservation fix, not a regression

---

## [3.16.0]

### Added — Conditional Query Detection (Final Capability-Expansion Roadmap Item)
The fifth and final item on the original capability-expansion roadmap, re-scoped after research into prior art (multi-agent query decomposition, conditional tool-use patterns, if-then semantic parsing) found that two of the original three planned pieces weren't actually justified by real evidence — see below.

**What shipped — two genuinely useful, narrowly-scoped pieces:**

1. **`detect_conditional()`** — recognizes a deliberately narrow leading `"if X, Y"` / `"should X, Y"` / `"in case X, Y"` structure. Scope is intentionally restricted to this exact form: "if" is genuinely ambiguous in English (conditional sense vs. "whether" sense, as in "check if the lights are on"), and the "whether" sense never appears at the very start of a sentence followed by a comma — so restricting to the leading-comma form sidesteps the ambiguity entirely rather than guessing at verb-based disambiguation, which runs into genuinely unresolvable cases ("let me know if X" could mean either "tell me the status" or "notify me if it changes," even to a human reader).

2. **Honest, scoped yes/no interpretation** — `_interpret_yes_no()` only attempts to determine whether a condition holds for sources with a genuinely structured, binary signal: HA lock/door states (locked/unlocked), uptime status (up/down), and forecast precipitation (rain/clear) specifically. Subjective conditions ("hot enough" — no universal threshold) and open-ended free-text sources (Kiwix, web, news) are deliberately never interpreted — the response presents the real result honestly without fabricating a verdict it can't actually support. Wrong is worse than uncertain.

**Recursive re-detection** — decomposed sub-queries are re-checked for their own embedded conditional structure, since the top-level check only ever runs once against the original full query. "What is the weather and if the back door is unlocked, let me know" doesn't start with "if," so decomposition runs normally — but the resulting sub-query "if the back door is unlocked, let me know" absolutely is conditional, and now gets correctly framed rather than silently routed as a plain free-text HA query.

### Fixed — Three Real Bugs Found During Implementation and Testing
- **Recursion design bug** — the first implementation recursed on the original `"if X, Y"` sub-query string with a manual depth counter meant to prevent runaway recursion. The counter incremented *before* the conditional was actually consumed, blocking the recursive call's own necessary re-detection of the same conditional. Fixed by extracting the condition/consequence directly and recursing on the already-extracted condition text only, which naturally never re-matches the leading pattern — no depth counter needed at all.
- **Greedy consequence capture** — "if any services are down, let me know, and also whats the weather" originally captured "let me know, and also whats the weather" as one undifferentiated consequence, silently losing track of "whats the weather" as a real, separate, searchable intent. `detect_conditional()` now returns a 3-tuple including the split-off remainder, which gets searched independently and merged into the response.
- **`[FUSION — FUSION]` double-header bug, second occurrence** — the exact same root-cause bug found and fixed earlier this session in the decomposition loop reappeared at the new remainder-merging call site: wrapping an already-self-headered fusion result in another header using the literal string `"fusion"`. Same fix applied: pass fusion results through unwrapped, only header genuinely single-source results.

### Fixed — Proper-Noun-Pair Guard, Fourth Bug Found
A deliberately hard megaquery test surfaced a fourth distinct bug in the proper-noun-pair protection mechanism (after the unbounded-scope, trailing-filler, and global-vs-local bugs found and fixed in 3.15.0/3.15.1): the single-conjunction-type split loop's skip logic, when protecting a pair like "Iran and Israel," reset the search position to just past the skipped occurrence — which also reset where the *next kept part* would start from, silently discarding all real text that preceded the pair ("also whats happening with Iran and" vanished entirely, leaving only "Israel, plus..."). Fixed by tracking where the current accumulating segment begins separately from where to resume searching for the next conjunction, so skipping a protected pair advances the search without discarding any preceding content.

### Research — Scope Correction
Two pieces from the original three-piece plan were dropped after research and testing showed they weren't justified: (1) "pass sibling-clause context to disambiguation" — both apparent motivating cases (Mercury, an "obscure" GPIO phrasing) turned out to be test-construction flaws with no real disambiguating signal present anywhere in the query, not genuine context-loss bugs; (2) true action/branching logic — Mnemolis has no reminder/trigger capability to act on a conditional's consequence at all, so framing the response honestly around the condition's real answer is the right scope, not attempting to simulate actions that don't exist.

### Verified
Extensively tested against real production data across more than a dozen hand-constructed adversarial queries, including: simple leading conditionals against all three structured sources (ha/uptime/forecast), honest abstention on subjective and open-ended conditions, two independent conditionals in one sentence, a conditional remainder containing a proper-noun pair, a conditional remainder containing a colloquial phrase, and a five-mechanism megaquery combining conditional detection, recursive re-detection, the discourse-framing bypass pattern, proper-noun-pair protection, and real technical content preservation all in one sentence.

One known, deliberate scope boundary was re-confirmed (not a new bug): conditional phrasing without an explicit comma separating the condition and consequence ("if the front door is unlocked tell me" with no comma) is correctly not detected, since distinguishing this reliably from "whether" usage would require real grammatical parsing rather than pattern matching.

### Added (Tests)
- 30+ new regression tests across `detect_conditional()`, `_interpret_yes_no()`, `_frame_conditional_response()`, full `route_with_source()` integration, recursive sub-query re-detection, remainder extraction and merging, and the fourth proper-noun-pair guard fix

### Changed
- Version bumped to 3.16.0

**Total test count: 845**

### Roadmap
**All five original capability-expansion items are now complete:**
1. ✅ Configurable thresholds
2. ✅ Kiwix search term disambiguation
3. ✅ Multi-book Kiwix fusion
4. ✅ Confidence-aware fusion with expanded ingest
5. ✅ Conditional query detection (re-scoped from "recursive/conditional decomposition")

---

## [3.15.1]

### Fixed — Proper-Noun-Pair Guard Didn't Actually Work
The proper-noun-pair guard introduced in 3.15.0 (to protect "Iran and Israel," "Phoenix and Kingman" style pairs from being incorrectly split) shipped with three compounding bugs, all found through testing against realistic, full-length compound sentences rather than short isolated test strings — the same lesson from earlier tonight repeating at a deeper layer.

1. **Unbounded scope** — the function only ever checked the *first* occurrence of a conjunction in the whole query, and treated everything after that point — potentially the rest of a long, multi-clause sentence — as the "after" side. In a real compound query, "after" became dozens of unrelated words instead of just the next name, so the length check never matched and the guard silently never fired at all in any realistic multi-intent query.
2. **Trailing filler broke the length check** — once scope was bounded to the next comma/conjunction, "Israel right now" (3 words: the name plus two filler words) failed a strict `<=2 words` check that was meant to validate the name itself, not the whole bounded segment. Fixed by checking only the word immediately following the conjunction, allowing 1-2 word names ("Israel" or "New York") with any trailing filler.
3. **Global gate instead of per-occurrence check** — even after the above fixes, the guard was structured as a single whole-query yes/no gate: if *any* conjunction occurrence anywhere looked like a proper-noun pair, decomposition aborted *entirely*, discarding completely unrelated, genuinely separate real intents elsewhere in the same sentence. A query containing both "Iran and Israel" *and* an unrelated "my back door" clause *and* an unrelated "numpy error" clause would lose all three to one false global abort. Redesigned as `_is_proper_noun_pair_at()` — checked independently at every conjunction occurrence, filtering only the specific split points that are proper-noun pairs rather than vetoing the whole operation.

### Verified
Tested against a deliberately constructed query containing two separate proper-noun pairs ("Iran and Israel," "Kingman and Phoenix") interleaved with two genuinely unrelated real intents ("back door," "numpy import error") in the same sentence — confirmed all 4 parts correctly separated, both pairs intact, both real intents preserved. Also verified against a second, independent test query mixing an "or"-joined proper-noun pair ("Tokyo or Osaka") with three other real intents, confirming the existing conjunction list naturally excludes "or" without needing special-casing.

### Added
- 1 new regression test reproducing the full combined scenario (two proper-noun pairs + two real intents in one query) — the exact shape that exposed bug #3

### Changed
- Version bumped to 3.15.1
- `_decompose()`'s docstring and the renamed `_is_proper_noun_pair_at()` helper's docstring both updated to describe per-occurrence checking instead of a whole-query gate

**Total test count: 813**

---

## [3.15.0]

### Fixed — Decomposition Silently Dropped Technical/Programming Content
The real bug behind tonight's research into recursive/conditional decomposition. `_decompose()`'s meaningful-content check used a fixed allowlist (`_INTENT_WORDS`) of recognized topic nouns — door, light, wifi, router, weather, etc. — extended piecemeal each time a new domain came up. It had zero coverage for technical/programming vocabulary at all, so a genuinely real, specific sub-query like "Ive been getting a python pigpio no permission to update GPIO error on my pi" matched nothing in the list and was silently discarded during decomposition, vanishing from the final response entirely with no error or indication anything had gone wrong.

- **Replaced the fixed allowlist with a general, stop-word-based check** — reuses `kiwix.py`'s already-hardened `_STOP_WORDS` set. A clause is now meaningful if at least one real content word survives stop-word stripping, with no domain-specific vocabulary list to maintain at all. Any future domain (cooking, finance, automotive, anything) is automatically covered without needing its own entry.
- **`_looks_like_proper_noun_pair()` added** — the looser content-word check initially introduced a real regression: "weather in Phoenix and Kingman" and "what is happening with Iran and Israel" started incorrectly splitting into two parts, because the old strict allowlist had been blocking these only by accident (neither "Phoenix" nor "Iran" ever matched any list entry). Added explicit structural detection — short, capitalized, bare-name pairs joined by a conjunction are recognized and protected from splitting, without reintroducing a place/country name list.
- Verified against real production content: a genuine GPIO troubleshooting query (with real, specific technical detail — not deliberately vague phrasing) now correctly survives decomposition, falls back from kiwix to web when needed, and finds the exact real Stack Exchange thread as the top result.

### Research — Recursive/Conditional Decomposition Investigation
Started researching the final capability-expansion roadmap item by reviewing prior art (multi-agent query decomposition literature, conditional tool-use patterns, if-then semantic parsing). Found that two of the evening's "disambiguation context loss" theories (Mercury, an "obscure" GPIO phrasing) were artifacts of deliberately vague test queries that never contained real disambiguating signal anywhere — not real product gaps. Correcting that assumption and re-testing with genuinely specific, realistic phrasing is what surfaced the real bug above. True conditional/branching logic (if X then Y as an action) remains out of scope — Mnemolis has no action/trigger layer to branch into, only search. The roadmap item is narrowed accordingly; see below.

### Added
- 7 new regression tests covering the content-word check, the proper-noun-pair guard (4 cases), and a guard-doesn't-overreach sanity test
- 1 minimum-content sanity test confirming a single real content word is sufficient for a clause to count as meaningful

### Changed
- Version bumped to 3.15.0
- `_decompose()`'s docstring updated to describe the proper-noun-pair guard

**Total test count: 812**

### Roadmap — Recursive/Conditional Decomposition, Re-scoped
Based on tonight's research, narrowed to two concrete, lower-risk pieces rather than the original three-piece plan:
1. **Conditional response framing** (not yet built) — detect "if X, [then] Y" phrasing and frame Y's answer as conditional on X's actual result in the synthesized response, without changing search behavior at all
2. **One-level recursive sub-decomposition** (not yet built) — if a decomposed sub-query is itself still compound, decompose it one additional level, hard-capped at depth 1

Dropped from scope entirely: true action/branching logic (no trigger layer exists in Mnemolis to act on), and "pass sibling-clause context to disambiguation" (no real evidence supports this — both apparent cases tonight were test-construction flaws, not product gaps).

---

## [3.14.0]

### Fixed — Application Logging Was Silently Disabled (Foundational)
**The root cause behind why several of tonight's other bugs went undetected for as long as they did.** The root logger defaulted to Python's standard WARNING level with zero attached handlers, meaning every `_LOGGER.info()` call across the entire codebase — decomposition splits, disambiguation candidates, article selection scores, snapshot job activity — was silently swallowed. Only uvicorn's own access logger (a separate logger with its own handler) ever produced visible output, making `docker logs mnemolis` look like the app was processing requests with zero diagnostic detail, when in fact the logging calls were firing the whole time, just never reaching any output destination.

- `logging.basicConfig()` now called explicitly at startup with a real formatter and handler
- **`LOG_LEVEL`** config var added (default `INFO`) for adjusting verbosity without a code change
- This single fix is what made every other discovery below possible to verify directly via logs instead of inferring indirectly through the routing cache

### Fixed — Kiwix Disambiguation Eligibility Checked the Wrong Variable
`_should_disambiguate()`'s "is this query short enough to be genuinely ambiguous" check was being called with `primary_term` (the already-reduced single longest word) instead of `search_terms` (the full extracted phrase). Since `primary_term` is *always* exactly one word by construction, the eligibility check was trivially always true — meaning even long, specific, completely unambiguous queries like "raspberry pi gpio permission errors in python" (5+ real content words) still triggered single-word disambiguation on "permission" alone, discarding "raspberry"/"pi"/"gpio"/"python" entirely and landing on an unrelated macOS disk-permissions article instead of real Raspberry Pi content. Found directly in logs once the logging fix above made the disambiguation candidate list visible for the first time.

### Fixed — `source_used` Reported the Intended Source, Not the Actual One
A query routed to `kiwix` that returned nothing usable could silently fall back to `web` internally and return genuinely good results — but the API response's `source_used` field still said `"kiwix"`, because `main.py` independently re-derived the intended source *before* calling `route()`, with no way to learn that an internal fallback had occurred. `route()` itself only ever returned a plain string with zero source information.

- **`route_with_source()`** added — returns `(result, actual_source)`, threading the true source through every exit path: direct success, fallback success, fusion, decomposed multi-part responses, and unknown-source errors
- **`route()`** remains a fully backward-compatible thin wrapper for existing callers that only need the result string
- The decomposed sub-query path gained fallback capability it was missing entirely — previously, a decomposed sub-query landing on an empty result never attempted a fallback at all, unlike the top-level single-source path
- `main.py`'s `/search` endpoint now reports the genuinely correct `source_used`

### Verified
Tested against the real production query that surfaced both bugs above: "remind me real quick whats the deal with raspberry pi gpio permission errors in python" now correctly skips disambiguation (full phrase preserved), falls back from kiwix to web when kiwix returns nothing useful, finds real GPIO permission troubleshooting threads, and correctly reports `"source_used": "web"`.

### Added
- 13 new regression tests across `test_main.py` and `test_router.py` (2 logging-configuration, 2 disambiguation-eligibility, 8 route_with_source, 1 corrected from a flawed test fixture caught mid-session — `_looks_empty("")` is actually `False`, since the function checks for known failure phrases rather than literal emptiness, so the original mock didn't trigger the fallback path it was meant to test)

### Changed
- Version bumped to 3.14.0

**Total test count: 806**

### Roadmap
The Mercury/galaxy-style "everyone's obsessed with" routing-past-Kiwix limitation documented in 3.13.0 is now confirmed as a **general, reproducible pattern** rather than word-specific — verified with both "mercury retrograde" and "galaxy" producing the same news/web routing behavior. Still tracked as a deliberate-design item, not a quick patch — see 3.13.0's roadmap entry for the full diagnosis.

---

## [3.13.0]

### Fixed — Mixed-Conjunction-Type Decomposition
The known limitation documented at the end of the prior release — a query mixing multiple different conjunction types (e.g. one `" and "` and one `" plus "`) only ever achieved a single-conjunction-type split — is now fixed.

- **`_decompose()` now also tries splitting on every conjunction occurrence at once, regardless of type**, in addition to the existing single-type-in-isolation approach, keeping whichever produces the most meaningful sub-queries. A query mixing "and also," "plus," "and," and "also" (5 genuine intents) previously collapsed to 2 parts under the single-type approach — every type's isolated split left the other conjunction words bundled inside whichever half didn't get split on. Splitting on all occurrences at once correctly separates all 5.
- **Adjacent/overlapping conjunction matches are collapsed into one split point** — "and also" (two conjunctions back to back) produces one boundary, not an empty fragment between them.
- **Possessive contraction bug in `_INTENT_WORDS` matching** — "internet's" (with the apostrophe) never matched the bare "internet" entry via exact word-membership comparison, the same class of bug previously found and fixed in `kiwix.py`'s stop-word stripping. Normalizing the apostrophe before the membership check fixes both the same way.
- Verified against the real production query that surfaced this: a deliberately extreme 5-intent, 3-conjunction-type, colloquially-phrased test query now correctly decomposes into all 5 parts and routes each independently — internet/wifi troubleshooting, Mercury retrogade, front door/windows, AC/weather, and Raspberry Pi GPIO permissions all landed in separate, correctly-attributed sections.

### Added
- 5 new regression tests in `test_router.py`, including an exact reproduction of the real 5-intent failing query
- 1 existing test corrected — the mixed-conjunction fix retroactively improved an earlier session's wifi/router/sunspots test case from a 3-way split to a genuinely better 4-way split (wifi and router are now correctly separated rather than merged under one "and"), so that test's expected count was updated to reflect the improved behavior

### Changed
- Version bumped to 3.13.0

**Total test count: 791**

### Roadmap — New Known Limitation Documented
**Mercury-retrograde-style queries with current-events framing can route past Kiwix disambiguation entirely.** Found via real testing: "what's the deal with that whole mercury retrograde thing everyone keeps talking about" — decomposition correctly isolated this as its own clean sub-query, but the keyword/LLM source-selection layer (`detect_intent()`, separate from decomposition and separate from Kiwix's own internal disambiguation) resolved it to `web,news` fusion, never reaching Kiwix or its disambiguation logic at all. The phrase "everyone keeps talking about" reads as current-discourse framing to the router, which is a defensible interpretation, not a clear-cut bug — "fixing" it by biasing harder toward Kiwix risks misrouting genuinely news-flavored queries instead. Needs deliberate design (likely: detecting when a query contains both an encyclopedic noun phrase AND a discourse-framing phrase, and explicitly trying fusion across kiwix+news+web rather than picking only news+web) rather than a quick patch. Tracked for a future session.

---

## [3.12.0]

### Fixed — Colloquial Query Handling (Found via Real Open WebUI Usage)
A deliberate stress-testing session running genuinely messy, multi-clause, colloquial questions through Open WebUI surfaced seven real bugs across decomposition, disambiguation, and fusion header composition — all verified against real production output before and after each fix, not just unit tests in isolation.

**Kiwix search term extraction (`app/sources/kiwix.py`):**
- **Apostrophe/contraction bug** — "what's" survived stop-word filtering as a stray `"what'"` token (the trailing "s" got stripped by stemming, leaving a dangling apostrophe that never matched the "what" stop word), polluting search terms and preventing disambiguation from ever triggering on colloquial phrasing. Fixed by normalizing contractions before stop-word matching.
- **Colloquial definitional patterns missing** — `_is_definitional_query()` only recognized formal phrasing ("what is," "tell me about"). Added "what's the deal with," "what's up with," "what's this about," "what's the story with."
- **Expanded `_STOP_WORDS`** — added colloquial filler ("deal," "thing," "stuff," "keep," "hearing," "up," "going") that previously survived filtering and polluted single-word disambiguation candidates.
- **`_build_search_terms()` extracted** as its own standalone, directly-testable function — was previously inline inside `search()`, which meant a prior version of the test suite tested its own separate re-implementation of the logic rather than the real code path, and could have passed while the actual implementation had this exact bug.

**Query decomposition (`app/router.py`):**
- **Conjunction-priority bug** — `_decompose()` stopped at the first conjunction type (by length) that produced ≥2 "meaningful" sub-queries, rather than trying every conjunction and keeping whichever split actually produced the most genuine intents. A query with one `" also "` and two `" and "`s would incorrectly split on `" also "` even when `" and "` produced a better 3-way split.
- **Missing singular intent words** — `_INTENT_WORDS` only contained plural forms ("doors," "lights," "locks," "sensors"), so clauses using the singular ("the back door," "the light," "the sensor") failed the meaningful-intent check and were silently dropped from decomposition entirely.
- **Missing network/connectivity vocabulary** — "wifi," "router," "network," "reboot," "restart," "online," "offline," "down" weren't recognized as real intent signals at all.
- **Colloquial phrase detection added** — "what's the deal with X" and similar now count as a real standalone intent regardless of what specific noun follows, generalizing better than an ever-growing noun list.
- **Colloquial phrase position bug** — the detection above only matched via `.startswith()`, missing real phrasing like "and remind me what's up with X" where the marker phrase is mid-clause, not at position zero (the clause still carries leftover conjunction/filler words from wherever the split occurred). Changed to a substring check.

**Fusion header composition (`app/router.py`):**
- **`[FUSION — FUSION]` double-header bug** — when a decomposed sub-query's own intent resolved to internal fusion across multiple sources, `fusion.search()` already returns content with its own per-source `[SOURCE — DESC]` headers. The outer decomposition loop wrapped that already-headered block in another header using the literal string `"fusion"` as the source name — which has no entry in `_HEADER_LABELS` — producing a nonsensical double-wrapped header around content that was already correctly labeled internally. Fixed by passing fusion sub-results through unwrapped at the outer level.

### Documented — Known Limitation (Not Yet Fixed)
A query containing multiple different conjunction types (e.g. one `" and "` and one `" plus "`) only achieves a single-conjunction-type split, since `_decompose()` picks one best conjunction type for the whole query rather than splitting on mixed conjunction types within the same decomposition pass. True mixed-conjunction splitting is a harder problem than anything fixed this release — tracked for future consideration, not chased prematurely.

### Added
- 14 new regression tests across `test_kiwix.py` and `test_router.py`, each verified against the real (not approximated) implementation before being added, covering every fix above plus the position-bug follow-up

### Changed
- `tests/test_kiwix.py` — `TestSearchTermCleaning` previously duplicated the search term extraction logic locally rather than testing the real `search()` code path; now calls the extracted `_build_search_terms()` directly, closing a gap where the test suite could pass while the real implementation was broken
- Version bumped to 3.12.0

**Total test count: 787**

---

## [3.11.1]

### Added — Documentation Accuracy Pass + Fresh Benchmarks
No code changes to core behavior. A full README and benchmark refresh following the capability expansion series, since both had drifted from what the codebase actually does.

- **README diagram audit** — Source Fusion diagram corrected (was showing the old bare `[SOURCE]` header format, missing the web/news scoring step entirely). New Kiwix Internal Flow diagram added — disambiguation, multi-candidate search, scoring, and multi-book fusion had zero visual documentation despite being the most architecturally complex part of the system.
- **README "Project Structure" rewritten** — was missing `app/scoring.py`, `app/query_expansion.py`, and 8 entire test files. Verified file-count-accurate against the real filesystem (23 test files, 15 app files).
- **README LLM-assisted routing list expanded** from 3 to 5 actual uses — added search term disambiguation and web query expansion, which existed in code but were undocumented.
- **README factual corrections** — `/changes` endpoint docs referenced a hardcoded "≥5°" instead of the now-configurable `FORECAST_TEMP_CHANGE_THRESHOLD`; Kiwix book selection referenced hardcoded "1-2" instead of `KIWIX_MAX_BOOKS`; Backup & Restore section still referenced the pre-rename `minisearch_data` volume name; "Part of the MiniNet stack" corrected to "Mnemo-net" (the actual current network/stack name).
- **`tests/locustfile.py` updated** — the load test had zero `web` source queries and no short/ambiguous Kiwix queries, meaning it was structurally incapable of measuring the cost of the two most computationally expensive features added this series (disambiguation, multi-query expansion). Added `WEB_QUERIES` and `KIWIX_DISAMBIGUATION_QUERIES` task groups.
- **Fresh benchmarks (BENCHMARKS.md)** — re-run with the updated locustfile, cold and warm cache, 20 concurrent users. Confirms the routing cache fully absorbs the new features' cold-start cost: `kiwix_disambiguation` p95 dropped ~295x (5900ms → 20ms) cold-to-warm, `web` p99 dropped ~121x (4600ms → 38ms). Aggregated median held at 17ms in both runs, unchanged from every prior benchmarked version back to v3.5.0 — the capability expansion series traded cold-path tail latency for correctness on a minority of complex queries without touching steady-state performance.

### Changed
- Version bumped to 3.11.1

---

## [3.11.0]

### Added — Confidence-Aware Fusion with Expanded Ingest
Fourth of five capability-expansion items. Web (SearXNG) and news (FreshRSS) results were previously trusted wholesale with zero relevance scoring — unlike Kiwix's dedicated scoring, these sources just returned whatever the upstream API gave back. This release builds real scoring infrastructure and adds multi-query expansion on top.

**Part A — `app/scoring.py` (new shared module):**
- `score_text_result()` — stemmed keyword overlap (title + content), exact-title-match bonus, generic/homepage-result penalty, optional recency bonus
- `_is_generic_result()` — detects homepage/about-page/site-description results rather than actual articles (generic title patterns, generic content phrases, bare-domain-root + short-content heuristic)
- `filter_and_rank()` — drops results at or below a configurable score threshold, caps survivors at a configurable top-N
- `normalize_url()` — strips scheme, `www.`, trailing slashes, query strings, and fragments for deduplication purposes
- `WEB_NEWS_SCORE_THRESHOLD` (default 0) and `WEB_NEWS_TOP_N` (default 10) config vars

**Part B — wired into the sources:**
- `searxng.py` — now pulls up to 25 raw results (was hardcoded top 5) and scores/filters/caps them instead of trusting SearXNG's own ranking
- `freshrss.py` — specific-query path now uses the shared scorer instead of its own duplicated logic; added a recency bonus (3 tiers: 1hr/6hr/24hr) so fresher articles rank higher; general-query bypass ("news", "headlines") preserved exactly as before

**Part C — multi-query expansion (web only):**
- `app/query_expansion.py` (new) — `get_alternate_phrasing()` asks the LLM for one genuinely different phrasing of a query (≥3 words, LLM configured), routing-cached, with sanity checks rejecting empty/oversized/identical responses
- `searxng.py` — when an alternate phrasing is available, searches both the original and alternate query, merges and deduplicates by normalized URL, scores the combined pool against the **original** query only — so a result survives because it's genuinely relevant to what was asked, not because of how the alternate phrasing happened to word it
- Deliberately **not** wired into FreshRSS — FreshRSS fetches and locally re-scores your existing feed items rather than issuing a remote query, so an alternate phrasing has nothing to act on there

### Fixed
- **Real bug** — `_fetch_searxng()` returning `[]` on both genuine connection failure and successful-but-empty results meant a SearXNG outage was silently reported as "no results found" instead of a real error. Now returns `None` on failure, `[]` only for a genuinely empty successful response.
- **Real bug** — duplicate results from the same article (e.g. `https://www.example.com/page/` and `https://example.com/page`) weren't deduplicated across primary/alternate query merges because comparison used raw URL strings. Fixed with `normalize_url()`.
- **Test fragility** — `TestGetChangesNetCollapsing` in `test_snapshots.py` used hardcoded absolute dates (`2026-06-19T08:00:00Z`) in tests that compare against a 24-hour rolling window. These silently failed once real time passed the window relative to the hardcoded dates. Replaced with a `_ago(minutes_ago)` helper generating timestamps relative to the actual current time, so these tests can never expire again.

### Verified
Tested against real production data across genuinely different domains — network troubleshooting, personal finance, home security, baking — confirming scoring, generic-result filtering, and deduplication all generalize well rather than being overfit to any one query style.

### Changed
- Version bumped to 3.11.0
- 64 new tests across `test_scoring.py`, `test_query_expansion.py`, `test_searxng.py`, `test_freshrss.py`, and the `test_snapshots.py` fix

**Total test count: 764**

### Roadmap
Fourth of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done), multi-book Kiwix fusion (done), confidence-aware fusion with expanded ingest (done). Remaining: recursive/conditional decomposition.

---

## [3.10.0]

### Added — Multi-Book Kiwix Fusion
Third of five capability-expansion items. When a query genuinely spans multiple Kiwix books — "python raspberry pi gpio setup" touching both a Raspberry Pi Stack Exchange thread and an Electronics Stack Exchange thread — Mnemolis now merges the best result from each relevant book instead of returning only the single highest-scoring article.

- **`_fuse_multi_book_results()`** — takes the best-scoring result per book, fetches each article, truncates using the existing fusion truncation logic (`settings.fusion_max_chars_per_source`), and merges with `[BOOK NAME]` attribution headers sorted by relevance
- **Relevance gate** — fusion only triggers when a second or third book's top result scores within 50% of the leading book's score. Prevents an LLM book-selection misfire from injecting an irrelevant book into an otherwise clean single-topic answer.
- **`KIWIX_MAX_BOOKS`** config var (default 2) — raise this to let the LLM select more books per query, enabling broader multi-book fusion (e.g. Python + Raspberry Pi + Unix Stack Exchange together) on hardware with the GPU headroom to handle more concurrent Kiwix requests per search
- **`KIWIX_SEARCH_LIMIT`** config var (default 15) — results requested per book per search, raised from the prior hardcoded 5 to give scoring more candidates when common terms get crowded out by brand-name collisions
- Verified against real production data: "python raspberry pi gpio setup" correctly fuses Raspberry Pi SE + Electronics SE. "What is nitrogen" correctly fuses Wikipedia (encyclopedic) + Wiktionary (etymology/pronunciation) — a genuinely complementary pairing the relevance gate identified without being explicitly told to expect it.
- **22 new tests** — `TestFuseMultiBookResults` (6), `TestSearchMultiBookFusionIntegration` (3), `TestConfigurableMaxBooks` (4), plus config default tests for both new settings

### Changed
- `_pick_books_with_llm()` — `max_books` parameter now defaults to `settings.kiwix_max_books` instead of a hardcoded `2`
- `_search_book()` — `limit` parameter now defaults to `settings.kiwix_search_limit` instead of a hardcoded `5`
- Version bumped to 3.10.0

**Total test count: 699**

### Roadmap
Third of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done), multi-book Kiwix fusion (done). Remaining: confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

---

## [3.9.0]

### Added — Kiwix Search Term Disambiguation (Multi-Candidate, Score-and-Verify)
Solves the long-tracked "galaxy returns Samsung phones, battery returns military fortifications" known limitation — a problem that survived three single-guess prompting attempts before landing on the right architecture.

- **`_should_disambiguate()`** — eligibility check: definitional query, Wikipedia selected, single-word search term, LLM configured
- **`_get_disambiguation_candidates()`** — asks the LLM for 3 candidate disambiguation terms taking genuinely different angles (broad field name, specific synonym, bare word with no qualifier), rather than trusting one blind guess
- **`search()` rewritten** — searches every candidate term against the selected book(s), merges and deduplicates results by URL, and lets the existing `_score_result()` scoring function pick the actual winner from the combined pool — grounded in real Kiwix results rather than LLM speculation about an index it can't see
- Verified against the exact production failures: "what are galaxies" now correctly returns the **Galaxy** astronomy article (was: Samsung Galaxy J7 phone). "How do batteries work" now correctly returns the **AA battery** article (was: military fortifications, then Electric vehicle battery)
- **3 attempted single-term prompting strategies were tried and discarded before this architecture**, documented here for anyone revisiting this problem: (1) broad category hint ("galaxy astronomy") — the disambiguation word itself dominated the search, surfacing dozens of unrelated astronomy portal pages instead of the target article; (2) rare/specific qualifier ("galaxy celestial") — collided with an entirely unrelated topic (Marvel Comics characters who happen to share thematic vocabulary with the target domain); (3) abandoning word-injection for scoring-only fixes was considered but rejected as insufficiently general. The working fix required searching multiple candidates and verifying against real results, not guessing better.

### Fixed
- **Real bug** — the single-word disambiguation term builder was including incidental content words ("how do batteries **work**" → disambiguating "battery work" as one phrase) due to picking the longest word from the full search_terms string without isolating it correctly in an earlier iteration. Now correctly isolates the single longest stemmed word before passing it to candidate generation.
- **Misaligned Snapshot Engine diagram in README** — column branches didn't line up under their labels. Redrawn with corrected alignment and updated the stale "Temp Δ≥5°" reference to reflect the now-configurable threshold.

### Changed
- Version bumped to 3.9.0
- 18 new tests — `TestShouldDisambiguate` (5), `TestGetDisambiguationCandidates` (8), `TestSearchMultiCandidateScoring` (5) — replacing the single-candidate disambiguation tests from the abandoned approaches

**Total test count: 685**

### Roadmap
Second of five capability-expansion items complete: configurable thresholds (done), Kiwix search term disambiguation (done). Remaining: multi-book Kiwix fusion, confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

---

## [3.8.2]

### Added — Configurable Thresholds
First step in the capability expansion roadmap. Eight previously hardcoded values are now deployment-configurable, with zero behavior change for anyone who doesn't touch them.

- **`FORECAST_PRECIP_THRESHOLD_PCT`** (default 20) — precipitation probability above which the forecast mentions rain chance
- **`FORECAST_WIND_THRESHOLD_MPH`** (default 15) — wind speed above which the forecast mentions wind
- **`FORECAST_TEMP_CHANGE_THRESHOLD`** (default 5.0) — temperature shift between snapshots that counts as a meaningful weather change in `/changes`
- **`BATTERY_LOW_THRESHOLD_PCT`** (default 20.0) — battery level below which a snapshot diff reports "low"
- **`FUSION_MAX_SOURCES`** (default 4) — maximum sources allowed in a single fusion query
- **`FUSION_MAX_CHARS_PER_SOURCE`** (default 1500) — characters per source result before truncation
- **`FUSION_TIMEOUT_SECONDS`** (default 15) — maximum wait time for any single fusion source
- **`CACHE_MAX_SIZE`** (default 500) — result cache entries before oldest-eviction kicks in, useful to lower on memory-constrained hardware

Deliberately scoped to deployment-preference values, not algorithm-internal tuning weights (Kiwix scoring bonuses, fusion deduplication overlap threshold remain fixed — these aren't user preferences, they're tuned constants).

### Changed
- `app/sources/fusion.py` — `FUSION_TIMEOUT`, `FUSION_MAX_SOURCES`, `FUSION_MAX_CHARS_PER_SOURCE` module constants removed in favor of reading `settings` directly at call time, so changes take effect without a restart-triggering code change
- `app/router.py` — `_CACHE_MAX_SIZE` now initializes from `settings.cache_max_size` instead of a hardcoded `500`
- Version bumped to 3.8.2
- README — all 8 new config vars documented in the Configuration table

### Roadmap
First of five capability-expansion items planned, in increasing difficulty: configurable thresholds (done), Kiwix search term disambiguation, multi-book Kiwix fusion, confidence-aware fusion with expanded ingest, recursive/conditional decomposition.

**Total test count: 665**

---

## [3.8.1]

### Fixed
- **Real bug — non-deterministic Kiwix book selection on empty LLM response.** `_pick_books_with_llm()` had a substring-matching flaw: when the LLM returned an empty or whitespace-only string (network hiccup, timeout, blank model output), the empty candidate string would match via Python's `"" in name` against whatever book name happened to come first in unordered set iteration — silently picking a random book instead of correctly falling back to Wikipedia-first. Found through a full repo-wide test coverage audit, not through user-reported behavior. Fixed by skipping empty candidates before the substring match.

### Added — Full Repo Test Coverage Audit
A deliberate, file-by-file audit confirming every module has direct test coverage, not just coverage by proxy through higher-level integration tests.

- **`tests/test_llm.py`** (26 tests) — first direct coverage of `app/llm.py`, the module backing every routing decision in the system. Covers `is_configured()`, Ollama native completion including the "thinking model" fallback behavior, OpenAI-compatible completion, connection/timeout/HTTP error handling, and payload structure verification.
- **`tests/test_mcp_server.py`** (19 tests) — first direct coverage of `app/mcp_server.py`. Covers tool schema definition, call dispatch (unknown tool, missing query, successful routing, fusion_sources passthrough, exception handling), and Starlette app construction.
- **`tests/test_config.py`** (21 tests) — first direct coverage of `app/config.py` defaults and constructibility. Caught and fixed an env-isolation flaw in the tests themselves: `Settings()` reads live environment variables, so naive "default value" tests were silently asserting against this container's real production config rather than class-level fallback values.
- **`tests/test_cache_persistence.py`** (24 tests) — direct coverage of cache eviction at capacity and disk persistence, including the exact `.corrupt` file rename recovery path observed live in production earlier this project.
- **`tests/test_kiwix_network.py`** (39 tests) — direct coverage of OPDS catalog XML parsing and pagination, LLM book-selection dispatch (including 3 new regression tests for the bug above), Kiwix search HTML scraping with Stack Exchange tag-page exclusion, and article content extraction with multi-selector fallback.
- **`tests/test_snapshot_jobs.py`** (19 tests) — direct coverage of the four APScheduler job functions, including a regression test for the kiosk/dark-mode binary sensor pollution bug fixed in a previous release.
- **26 additional tests** in `test_main.py` (catalog endpoints, API key auth) and `test_home_assistant.py` (`_get_states`, `_format_entity`, `_matches_filter` — the core entity matching engine, previously untested despite extensive higher-level coverage).

### Changed
- Version bumped to 3.8.1
- Fixed stale "MiniSearch" references in `mcp_server.py` docstrings (the project rename's last stragglers)
- Distribution tarball folder corrected to `mnemolis/` (was still `minisearch/`)

**Total test count: 646** (up from 521 — 125 new tests this release)

---

## [3.8.0]

### Added
- **`GET /areas`** — lists all detected Home Assistant areas with entity counts and the natural-language phrases that resolve to each one (e.g. "living room", "master bath"). Returns `not_configured` if HA isn't set up, `error` if the area registry can't be reached.
- **`list_areas()`** in `home_assistant.py` — builds on the existing `_get_area_entities()` and `_AREA_ALIASES` from HA area awareness, exposing them via a clean public function
- **API key authentication** — opt-in, backward compatible. `API_KEYS` config var accepts a comma-separated list of valid keys. When unset (default), auth is fully disabled and all existing integrations continue working unchanged.
- **`require_api_key()`** FastAPI dependency — validates the `X-API-Key` header against configured keys
- Auth applied to **`POST /search`** and **`GET /changes`** only — the two endpoints that return query results or house/service state. `/health`, `/areas`, `/backup`, and all other endpoints remain open for monitoring tools and discovery.
- **21 new tests** — `TestListAreas` (9 tests), `TestAreasEndpoint` (2 tests), `TestAPIKeyAuth` (13 tests covering disabled-by-default passthrough, missing/wrong/correct key handling, multi-key support, whitespace trimming, and confirming unprotected endpoints stay open)

### Changed
- Version bumped to 3.8.0

**Total test count: 473**

---

## [3.7.1]

### Fixed
- **`_search_changes` test coverage gap** — the function actually wired into `SOURCE_MAP["changes"]` had no direct test, only its helper `_resolve_changes_hours` did. Added `TestSearchChanges` (4 tests) covering the real entry point.
- **`mnemolis_tool.py`** — `fusion_sources: list[str] = None` corrected to `list[str] | None = None`. Docstring updated with time-window phrase examples ("this morning," "while at work," "in the last N hours") for the `changes` source.
- **Docker volume naming** — `docker-compose.yml` volume renamed `minisearch_data` → `mnemolis_data`, matching the project rename. `TZ: "America/Phoenix"` added (was present on the live deployment but had drifted out of the tracked file).
- **`docker-compose.example.yml`** — was missing a persistent data volume for Mnemolis entirely. Anyone following the public example would have had nothing for `/backup` to back up. Added `mnemolis_data` volume and mount.
- **Distribution tarball** — internal folder name corrected from `minisearch/` to `mnemolis/`.

### Documented
- **Docker Compose volume project-prefixing** — added a README section explaining that Compose prefixes named volumes with the project name (defaulting to the working directory's folder name), so a volume named `mnemolis_data` in YAML may actually be created as `{foldername}_mnemolis_data`. Includes verification commands and a `COMPOSE_PROJECT_NAME` workaround for a stable prefix regardless of folder name. Discovered during a real production volume migration where renamed volumes silently pointed at fresh empty storage instead of the intended data.

### Changed
- Version bumped to 3.7.1

**Total test count: 452**

---

## [3.7.0]

### Added — Real-World Bugfixes from Production Usage
A session of real Open WebUI usage against Mnemolis surfaced three distinct issues, all fixed and validated against live production data.

- **Forecast location attribution** — `forecast.search()` now prefixes output with "In {location}, " when `FORECAST_LOCATION_NAME` is configured. Previously the forecast text never stated whose weather it was, and an LLM reasoning over fused context incorrectly inferred location from an unrelated news article mentioning a different city.
- **Descriptive fusion section headers** — `_format_header()` added to `fusion.py`. Headers now read `[FORECAST — WEATHER FORECAST FOR YOUR CONFIGURED HOME LOCATION]` and `[NEWS — RECENT NEWS HEADLINES — GENERAL, NOT LOCATION-SPECIFIC UNLESS STATED]` instead of bare `[FORECAST]`/`[NEWS]`, explicitly warning the LLM against cross-referencing unrelated sections to infer facts.
- **Time-window phrase resolution for `source="changes"`** — `_resolve_changes_hours()` and `_hours_since()` added to `router.py`. "This morning," "while at work," "since work," "tonight," "since yesterday" now resolve to precise hour windows instead of collapsing into a fixed 24-hour default. Explicit hour counts ("in the last 3 hours") take priority over vaguer phrases.
- **`morning_start_hour`** (default 6) and **`work_start_hour`** (default 9) added to `config.py` — configurable reference times for resolving "this morning" and "while at work" phrases.
- **Net-change collapsing for flapping sources** — `get_changes()` now compares only the first and last snapshot in the window for `uptime` and `forecast` (sources prone to round-tripping back to baseline — a brief outage that resolves, precipitation that appears then disappears). `news` and `ha` continue reporting every individual event since each is independently meaningful. Eliminates noisy alarm/resolved pairs that don't reflect current state.
- **18 new tests** — `TestResolveChangesHours` (12 tests), `TestHoursSince` (3 tests), `TestLocationNamePrefix` (2 tests), `TestFormatHeader` (4 tests), `TestGetChangesNetCollapsing` (5 tests)

### Fixed
- **Test isolation bug** — `test_concurrent_snapshot_writes_no_crash` in `test_security.py` was writing directly to the production `snapshots.db` instead of an isolated temp database, polluting real snapshot history with literal "snapshot content N" test strings. Now properly isolated with `SNAPSHOT_DB` patched to a temp file.
- **Container timezone** — `docker-compose.yml` now sets `TZ` explicitly. Without it, the container defaulted to UTC while the host ran local time, causing time-window calculations to be off by the UTC offset.

### Changed
- Version bumped to 3.7.0
- Existing fusion/decomposition header tests updated to match new descriptive header format (substring match on `[SOURCE` rather than exact `[SOURCE]`)

**Total test count: 448**

---

## [3.6.3]

### Added — Hardening Pass
- **`tests/test_security.py`** — 27 tests covering SQL injection resistance, path traversal attempts against the backup endpoint, token/secret leakage checks in health responses and error messages, fuzz input (very long queries, unicode/emoji, null bytes, pure punctuation, empty/whitespace), and concurrency tests using real threads against cache clear, log clear, snapshot writes, and concurrent backup downloads
- **`tests/test_property.py`** — Hypothesis property-based tests across 9 pure functions: `_decompose`, `_stem`, `_score_result`, `_is_definitional_query`, `_build_filter`, `_detect_area`, `_is_excluded`, all 4 snapshot diff functions, `_looks_empty`, `_truncate`, `_deduplicate`. Each property runs 100-300 randomly generated examples, totaling thousands of input combinations tested automatically.
- **`hypothesis`** added to `requirements.txt`

### Verified
- No SQL injection vulnerabilities — all queries use parameterized placeholders
- No path traversal possible — backup endpoint uses a fixed file list, ignores all query params
- No token/secret leakage — HA token and FreshRSS password confirmed absent from `/health` responses and connection error messages
- No crashes under adversarial input — confirmed across decomposition, stemming, scoring, HA filtering, and all snapshot diff engines
- No race conditions — confirmed under concurrent cache clear + search, log clear + log write, concurrent snapshot writes, and concurrent backup downloads

### Changed
- Version bumped to 3.6.3

**Total test count: 422**

---

## [3.6.2]

### Added
- **`GET /backup`** — downloads a tarball of all Mnemolis state (result cache, routing cache, query log, snapshot history) as `mnemolis-backup-{timestamp}.tar.gz`
- **`GET /backup/info`** — shows file sizes and last-modified times for each data file without creating a backup
- **Backup & Restore section in README** — manual backup command, cron automation example, and full restore procedure using a throwaway alpine container against the named Docker volume
- **6 new tests** — `TestBackupEndpoint` covering file dict structure, known files present, content-type header, filename format, and valid tar structure

### Fixed
- **`POST /logs/clear`** — restored a third time after being accidentally dropped during endpoint insertion. Verified present in route list post-fix.

### Changed
- Version bumped to 3.6.2

**Total test count: 372**

---

## [3.6.1]

### Added
- **HA Snapshot Engine (Phase 2)** — `snapshot_ha()` captures raw entity states from `/api/states` every 5 minutes, filtered to locks, door/motion/window binary sensors, and battery sensors
- **`_diff_ha()`** — detects lock state changes, door open/closed transitions, and battery levels crossing below 20%. Lights and switches intentionally excluded — too noisy for a "what changed" summary.
- **`tests/test_snapshots.py::TestDiffHA`** — 12 new tests covering lock changes, door changes, battery threshold crossing, light exclusion, new entity handling, malformed JSON, and multiple simultaneous changes
- **WAL mode + busy timeout** — all SQLite connections (`query_log.db`, `snapshots.db`) now use `PRAGMA journal_mode=WAL` and a 10-second busy timeout via a shared `_connect()` helper, reducing lock contention between the snapshot scheduler and concurrent search requests
- **Architecture diagrams updated** — Voice Assistant Flow and Multi-Client Architecture now show the Snapshot Engine and decomposition routing path. New **Snapshot Engine** diagram added showing scheduler → storage → diff → `/changes` flow

### Fixed
- **HA snapshot noise filter** — initial implementation captured all `binary_sensor` domain entities regardless of device class, pulling in irrelevant entities (kiosk browser toggles, dark mode switches). Narrowed to `device_class in (door, motion, window, opening)` only.

### Changed
- Version bumped to 3.6.1
- `/snapshots/trigger` now includes HA in manually triggered snapshots
- Scheduler now runs 4 jobs: uptime (2 min), forecast (30 min), news (60 min), HA (5 min)

**Total test count: 366**

See `BENCHMARKS.md` for updated load test results — WAL mode fix verified, 0 connection errors, p95/p99 within v3.5.0 range despite added scheduler load.

---

## [3.6.0]

### Added
- **Snapshot Engine** — `app/snapshots.py` — periodic background snapshots of Uptime Kuma, Open-Meteo, and FreshRSS stored to SQLite at `/app/data/snapshots.db`
- **APScheduler** — background scheduler starts on container startup, takes snapshots every 2 minutes (uptime), 30 minutes (forecast), 60 minutes (news)
- **Diff engine** — detects meaningful changes between consecutive snapshots:
  - `_diff_uptime()` — service outages and recoveries
  - `_diff_forecast()` — high/low temp changes ≥5°, precipitation appearing or disappearing
  - `_diff_news()` — new article headlines, capped at 5 per diff, deduplication across walk
- **`GET /changes?hours=N`** — returns detected changes across all snapshot sources within the last N hours (default 24)
- **`POST /snapshots/trigger`** — manually trigger all snapshot jobs immediately
- **`source="changes"`** — routes "what changed today", "any new outages", "what happened today" etc. to the snapshot diff engine automatically via keyword detection
- **Immediate startup snapshots** — all three sources snapshot on container startup so `/changes` has data immediately
- **`apscheduler`** added to `requirements.txt`
- **`tests/test_snapshots.py`** — 30 new tests across 5 classes covering `_diff_uptime`, `_diff_forecast`, `_diff_news`, and `format_changes`

### Changed
- `INTENT_MAP` — `changes` source added with 14 trigger keywords
- `SOURCE_MAP` — `changes` source registered
- `SOURCE_DESCRIPTIONS` — `changes` described for LLM routing
- `CACHE_TTL` — `changes` cached for 2 minutes
- Version bumped to 3.6.0

### Known limitations (Phase 1)
- HA entity-level snapshots not yet implemented — "what changed in the house" is Phase 2
- Snapshot diffs are text-based — no semantic understanding of magnitude beyond threshold rules

**Total test count: 354**

---

## [3.5.3]

### Added
- **Missing tests across all source modules** — comprehensive coverage audit followed by additions to six test files:
  - `test_forecast.py` — `TestDegreesToCardinal` (6 tests), `TestFmtTime` (4 tests)
  - `test_uptime_kuma.py` — `TestGetStatusFromHeartbeats` (6 tests)
  - `test_fusion.py` — `TestLooksEmpty` (8 tests)
  - `test_home_assistant.py` — `TestHAHelperFunctions` (9 tests), `TestBuildFilter` (5 tests)
  - `test_freshrss.py` — `TestGetToken` (4 tests)
  - `test_router.py` — `TestLlmPickFusionSources` (5 tests)

### Changed
- Version bumped to 3.5.3

**Total test count: 331**

---

## [3.5.2]

### Added
- **`GET /logs/stats`** — query log statistics endpoint surfacing Time To First Knowledge (TTFK), cache hit rate, success rate, average latency by source, top 10 most-asked queries, unique query count, and learned query count
- **`POST /logs/clear`** — restored missing endpoint for clearing query log entries
- **`tests/test_main.py`** — 27 new tests covering all FastAPI endpoints: `/health`, `/sources`, `/cache`, `/cache/routing`, `/logs`, `/logs/stats`

### Changed
- Version bumped to 3.5.2

**Total test count: 284**

---

## [3.5.1]

### Changed
- **Public readiness scrub** — removed personal location data from example files
- `docker-compose.example.yml` — forecast coordinates, location name, and timezone blanked with placeholder comments
- `app/config.py` — default coordinates set to `0.0`, location name blank, timezone defaulting to `UTC`
- `tests/locustfile.py` — personal IP replaced with `your-host`
- `README.md` — forecast defaults shown as blank, example HA IP neutralized
- **`LICENSE`** added — MIT license
- README updated with license section

---

## [3.5.0]

### Added
- **Query decomposition** — `source="auto"` queries are now split on conjunction words (`and`, `also`, `plus`, `as well as`, `in addition`) into independent sub-queries, each routed and executed separately. "What is the weather and are my services up" becomes two independent queries — one to `forecast`, one to `uptime` — merged with source attribution headers.
- **`_decompose()`** in `router.py` — conjunction splitting with nosplit guard for comparison queries ("compare Python and Rust"), location pairs ("weather in Phoenix and Kingman"), and country names ("Iran and Israel"). Requires sub-queries to contain at least one intent word or known source trigger noun to be considered a valid standalone query.
- **Smart fusion — same-source merging** — consecutive results from the same source are merged under a single `[SOURCE]` header. "Indoor air quality and are the doors locked" now returns one `[HA]` block, not two.
- **Smart fusion — result truncation** — each source result is capped at 1500 characters before merging, cutting at a clean newline boundary. Prevents one verbose source from dominating the merged output.
- **Smart fusion — deduplication** — sentence-level overlap detection drops sources whose content is 60%+ duplicated in another source's result. Handles cases where news and web return the same story.
- **`_truncate()`**, **`_deduplicate()`**, **`_merge_same_source()`** added to `fusion.py`
- **Query decomposition diagram** added to README
- **14 new decomposition tests** in `test_router.py` — `TestDecompose` class covering conjunction splitting, nosplit patterns, triple splits, area-based queries, and explicit source bypass
- **15 new fusion tests** — `TestFusionTruncate`, `TestFusionDeduplicate`, `TestFusionMergeSameSource`

### Changed
- Version bumped to 3.5.0
- `test_router.py` — `TestAutoFusionEscalation` updated to reflect decomposition behavior replacing direct fusion for multi-topic auto queries

**Total test count: 257**

See `BENCHMARKS.md` for updated load test results — p95 improved from 41ms → 36ms, p99 from 1000ms → 780ms at 20 concurrent users. Query decomposition adds no measurable overhead.

---

## [3.4.5]

### Added
- **`tests/locustfile.py`** — Locust load testing suite with two user classes: `MnemolisSingleSourceUser` (all 7 sources with realistic task weights) and `MnemolisFusionUser` (explicit 2-source, LLM auto-selection, and triple source fusion)
- **`BENCHMARKS.md`** — documented load test results at 5, 10, and 20 concurrent users. 15ms median at 20 users, 0 failures across 391 requests, fusion 3-source at 14ms warm cache
- **`.dockerignore`** — excludes `__pycache__`, `.pyc`, and `.pyo` files from Docker builds, preventing stale bytecode from being baked into the image

### Changed
- Kiwix search terms now stemmed after stop word removal — "galaxies" → "galaxy", "batteries" → "battery" — improves Kiwix article matching for plural queries
- Version bumped to 3.4.5

---

## [3.4.0]

### Added
- **HA area awareness** — `source="ha"` now detects room/area names in queries and filters results to entities assigned to that area in Home Assistant. "What lights are in the living room" returns only living room entities. "Temperature in the master bedroom" returns only master bedroom sensors.
- **`_get_area_entities()`** — fetches area → entity mapping from HA's template API using `area_entities()`. Builds a complete room registry on each query.
- **`_detect_area()`** — natural language area detection with alias support. Handles "living room" → `living_room`, "master bedroom" → `master_bedroom`, "outside/outdoors" → `outside`, and all 12 defined areas.
- **`_AREA_ALIASES`** — maps natural language phrases to HA area IDs. Longest match wins — "master bedroom" correctly matches over "bedroom".
- **15 new tests** — `TestAreaDetection` (11 tests) and `TestAreaSearch` (4 tests) covering area detection, longest match, unknown area fallback, state filter with area filter, and keyword fallback when no area detected.

### Changed
- Version bumped to 3.4.0
- README updated — all MiniSearch Intents references updated to Mnemolis Intents with correct GitHub URLs

**Total test count: 230**

---

## [3.3.0]

### Added
- **Source health endpoint** — `GET /health` now returns connectivity status for every configured source: kiwix, forecast, news, web, uptime, ha, and llm. Each check is lightweight — just enough to confirm the service is reachable and configured. LLM check shows model name and API type.
- **Query logging** — SQLite-backed query log at `/app/data/query_log.db`. Every search is logged with timestamp, query text, source requested, source used, cached flag, success flag, and latency in milliseconds.
- `GET /logs?limit=50` — view recent query log entries, newest first
- `POST /logs/clear` — clear all query log entries
- **Kiwix `_is_definitional_query()`** — detects definitional/overview queries ("what is", "what are", "tell me about", "explain", "how does", "history of", etc.) to apply appropriate scoring bonuses
- **Wikipedia scoring bonus** — +8 for definitional queries, +3 for all others. Ensures encyclopedic sources are preferred for overview queries over Q&A threads.
- **List/index article penalty** — -10 for articles whose title starts with "List of", "Lists of", "Index of", "Outline of", "Category:". Prevents navigation pages from winning over content articles.
- **Stemmed word-level title matching** — multi-word queries like "what are galaxies" now correctly match single-word titles like "Galaxy" via per-word stem comparison (+15 bonus)
- **Intent-aware book selection prompt** — LLM book selection prompt now includes a hint about query intent, directing the model to prefer encyclopedic or technical sources appropriately
- **`.dockerignore`** — excludes `__pycache__` and `.pyc` files to prevent stale compiled bytecode from being baked into the image

### Changed
- Version bumped to 3.3.0
- `GET /health` response now includes `sources` dict with per-source status

### Known limitations
- Brand name ambiguity — "galaxies" returns Samsung Galaxy articles because Kiwix's search engine indexes hundreds of Samsung Galaxy phone articles. Scoring correctly prefers the astronomical "Galaxy" article when both are returned, but Kiwix often doesn't surface the main article. Tracked for future improvement via search term disambiguation.
- Generic noun ambiguity — "battery" returns military fortification articles (battery = artillery position). Same root cause.

**Total test count: 215**

---

## [3.2.0]

### Added
- **Home Assistant source module** — `source="ha"` queries HA entity states for analytical summaries that go beyond HA's built-in single-entity intent handling
- `app/sources/home_assistant.py` — keyword-based entity filtering by domain and device class, position-aware phrase matching (longer phrases take priority), deduplication, readable grouped output with time-ago motion events and rounded numeric values
- `HA_URL` and `HA_TOKEN` config vars
- `ha` added to `SOURCE_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL` (30 second TTL), `INTENT_MAP`, MCP tool schema
- 37 new tests in `tests/test_home_assistant.py` covering guards, exclusions, light/lock/environmental/battery/motion queries, and value formatting

### What the HA source handles
Queries HA can't answer natively with its built-in intents:
- **House/security summaries** — "house status", "security status", "are the doors locked"
- **Environmental** — "indoor air quality", "room temperature", "CO2 levels"
- **Outdoor conditions** — "outdoor conditions" (weather station sensors)
- **Battery status** — "battery status", "which devices have low battery"
- **Motion history** — "any recent motion", "security status" with time-ago formatting
- **Power consumption** — "how much power am I using"
- **Auto-fusion** — "house status and what's the weather" automatically fuses `ha` + `forecast`

### Changed
- Version bumped to 3.2.0

**Total test count: 202**

---

## [3.1.0]

### Added
- **Smart auto-fusion escalation** — `source="auto"` now escalates to fusion automatically when a query spans multiple topics. Keyword matching checks all sources before returning, and if multiple sources match, fusion is triggered with those sources — no LLM call needed.
- **LLM fusion escalation** — when no keywords match, the LLM now decides in a single call whether to use one source or multiple. Returns comma-separated source names for complex queries, triggering fusion automatically.
- **Kiwix suffix stemming** — `_stem()` function added to `kiwix.py`. Strips common suffixes (`-s`, `-es`, `-ies`, `-ing`, `-ed`) before scoring so "marsupials" correctly matches "Marsupial", "foxes" matches "Fox", etc. Word-level title and excerpt scoring now uses stemmed terms.
- **Expanded uptime intent triggers** — 15 new trigger phrases added including "my services", "services up/down", "anything down", "everything up/down", "network down/up", "anything offline", "server status", "is it running", "is it up/down", "are they up/down"
- **22 new tests** — `TestKeywordDetectMulti`, `TestNewUptimeTriggers`, `TestAutoFusionEscalation`, `TestStem`, and stemmed scoring tests

### Changed
- `_keyword_detect` now scans all sources before returning — single match returns string, multiple matches return list for fusion escalation
- `_llm_detect` updated with smarter prompt — returns single source or comma-separated list in one call
- `detect_intent` return type updated to `str | list[str]`
- `route()` updated to handle list return from `detect_intent`

**Total test count: 179**

---

## [3.0.0]

### Added
- **Source fusion** — `source="fusion"` queries multiple sources concurrently using `ThreadPoolExecutor`, merges results with source attribution headers, handles partial failures gracefully
- **`app/sources/fusion.py`** — new fusion source module. Validates sources, deduplicates, caps at 4, times out at 15 seconds per source, filters empty/failed results, returns single source directly without headers when only one succeeds
- **LLM fusion source selection** — when `fusion` is used without specifying sources, the LLM picks the best 2-3 sources for the query. Decision cached in routing cache for 1 hour.
- **`fusion_sources` parameter** — optional `list[str]` field on `POST /search` and MCP tool schema. Explicitly specifies which sources to fuse.
- **Fusion cache key** — stable cache key from sorted source list ensures same sources in any order share a cache entry
- **28 new fusion tests** — `tests/test_fusion.py` covering merging, headers, single source passthrough, validation, deduplication, max cap, partial failure, all failure, empty result filtering, and cache behavior
- **Fusion diagram** in README illustrating concurrent source querying and merge

### Changed
- Version bumped to 3.0.0
- FastAPI description updated to mention fusion
- `fusion` added to `SOURCE_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL` (30 min TTL)
- `route()` signature updated to accept `fusion_sources: list[str] | None`
- MCP tool schema updated with `fusion` in source enum and `fusion_sources` array parameter
- Dead import (`from app.config import settings`) removed from `fusion.py`

---

## [2.9.0]

### Added
- `app/llm.py` — unified LLM client supporting both Ollama native API and OpenAI-compatible API (llama-server, LM Studio, etc.)
- `LLM_API_TYPE` config var — set to `"ollama"` (default) or `"openai"` to switch backends
- Routing cache tests — 28 new tests covering all routing cache operations including corruption handling
- Source guards — FreshRSS and SearXNG return clean error messages when not configured

### Changed
- `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_API_TYPE` renamed to `LLM_URL`, `LLM_MODEL`, `LLM_API_TYPE` — better reflects support for any compatible backend
- All LLM calls in `router.py` and `kiwix.py` now route through `llm.py` helper
- `clear_routing_cache` and `load_routing_cache` fixed to use `.clear()` and `.update()` instead of reassignment — prevents stale reference issues

### Fixed
- Routing cache `clear()` and `load()` used dict reassignment instead of mutation, causing external references to see stale data

---

## [2.8.0] — Upcoming

### Added
- Routing cache — source and Kiwix book selection decisions are cached for 1 hour, persisted to disk, eliminating redundant Ollama calls for repeated queries
- `GET /cache/routing` — inspect routing cache entries
- `POST /cache/routing/clear` — clear routing cache
- Source guards — FreshRSS and SearXNG return clean error messages when not configured rather than attempting connection
- API endpoint docstrings — all endpoints now have descriptions visible in `/docs`
- `CHANGELOG.md`

---

## [2.7.0]

### Added
- Test suite — 71 tests covering intent routing, cache logic, Kiwix scoring, search term cleaning, and FreshRSS article filtering
- `pytest` and `pytest.ini` added, tests baked into Docker build

### Fixed
- `_is_general_query` now checks full query string before word-level matching — fixes `"what's happening"` detection
- `_score_result` stop word fallback removed — prevents noise words from inflating article scores
- Pydantic V2 deprecation warning resolved — `config.py` updated to use `ConfigDict`

---

## [2.6.0]

### Changed
- Intent routing hardening — removed 10 overly broad trigger words causing incorrect source routing
- `"recent"` and `"latest"` removed from FreshRSS general query bypass
- `"will it be"` and `"tonight"` removed from forecast triggers
- Dead code removed — `STATUS_LABELS` dict in `uptime_kuma.py`
- `forecast.py` — inline note on `%-I` Linux-only time formatting

---

## [2.5.0]

### Added
- `asyncio` moved to top-level import in `main.py`
- FastAPI startup uses modern `lifespan` context manager
- `load_cache` renamed from `_load_cache` — now a proper public function
- `check_cached`, `get_cache_stats`, `get_cache_count`, `clear_cache` — clean public cache API
- Kiwix `_search_book` and `_fetch_article` now log warnings on failure
- `_score_result` moved to module level in `kiwix.py`
- Logging added to `freshrss.py` and `searxng.py`

---

## [2.4.0]

### Added
- Smart source routing — keyword matching runs first, Ollama called only when no keyword matches
- Per-source result caching with disk persistence — cache survives container restarts
- Cache batched disk writes — saves every 5 writes instead of on every set
- Cache max size (500 entries) with LRU eviction
- Cache corruption hardening — malformed cache renamed to `.corrupt`, container starts clean
- `GET /cache` — inspect cache entries with age and TTL
- `POST /cache/clear` — clear all cached results
- `cached` field in search response

---

## [2.3.0]

### Added
- Uptime Kuma source module — reports service monitor status via Socket.IO API
- `uptime` source added to `SOURCE_MAP`, `INTENT_MAP`, `SOURCE_DESCRIPTIONS`, `CACHE_TTL`
- `UPTIME_KUMA_URL`, `UPTIME_KUMA_USERNAME`, `UPTIME_KUMA_PASSWORD` config vars

---

## [2.2.0]

### Added
- FreshRSS query filtering — articles scored by keyword relevance, general queries bypass filtering
- `_is_general_query` — detects broad news requests and returns full feed
- Stop word lists in `freshrss.py` and `kiwix.py`

---

## [2.1.0]

### Added
- Kiwix stop word stripping — query cleaned before sending to Kiwix search engine
- Improved `_score_result` — exact title match bonus (+20), title-starts-with bonus (+10), normalized excerpt scoring, stop word awareness
- Search limit increased from 3 to 5 results per book
- Multi-book search — LLM selects up to 2 books, results deduplicated and scored across both

---

## [2.0.0]

### Added
- MCP server via SSE at `/mcp/sse` — any MCP client can connect
- Dynamic Kiwix catalog discovery — book list built from OPDS catalog at startup, no hardcoded list
- LLM-assisted Kiwix book selection via Ollama
- `POST /catalog/refresh` — force catalog re-scan without restart
- `GET /catalog` — list loaded books
- `LLM_URL`, `LLM_MODEL` config vars
- `FORECAST_TIMEZONE` config var
- Structured search response — `success`, `cached`, `error` fields
- Source fallback chain — kiwix falls back to web on empty results

---

## [1.0.0]

### Added
- Initial release
- FastAPI container with `POST /search` endpoint
- Sources: Kiwix, FreshRSS, Open-Meteo, SearXNG
- `auto` intent routing via keyword matching
- `GET /health`, `GET /sources`
- Open WebUI bridge tool (`mnemolis_tool.py`)
- Docker Compose with `mnemo-net` network
