# Changelog

All notable changes to Mnemolis are documented here.

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
