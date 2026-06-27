# The Fusion Merge Bugs

Several separate stories from `fusion.py`'s history, bundled together because they're the same kind of bug found the same way: real production traffic, or a deliberate, exhaustive audit pass, surfacing a gap between what the code assumed and what actually happened once enough real, varied queries (or careful, adversarial inspection) hit it.

## The same-source merge chain: three bugs, found in sequence

[Merging consecutive same-source results](Fusion#merging-consecutive-same-source-results) has a real, structural limitation that took three separate, sequential fixes to fully close — each one only became visible once the *previous* fix in the chain was already verified working. All three were found by tracing a single real, live query end to end on actual production data, not by inspection or a synthetic test case.

**Bug 1 — the outer-label blind spot.** `_merge_same_source()` only ever compares the *outer* tuple label. If one decomposed clause resolves to internal fusion itself (multiple sources sharing one already-headered, nested blob — e.g. the [discourse-framing bias](Routing#the-discourse-framing-bias) pulling in `kiwix` alongside whatever else a clause's own LLM judgment picked) and a *different*, separately-decomposed clause resolves to a bare source that happens to be one of the sources already inside that nested blob, `_merge_same_source()` has no way to see the overlap — `"fusion"` and `"news"` are genuinely different outer labels to it, even though a `[NEWS — ...]` section is sitting on both sides.

**Fixed** with a second, separate pass, `_dedupe_nested_fusion_sections()`, that runs on the final, fully-assembled result text — after this function's own tuple-level merge, not instead of it — splitting on the exact, real header strings `_format_header()` can produce and merging any header that appears more than once.

**Bug 2 — content could still repeat under a correctly-merged header.** Even after Bug 1's fix, the actual *content* under a correctly-merged single header could still repeat. Two independent calls to the same backend — one nested inside an internal-fusion clause, one a separately-decomposed clause's own bare resolution — can both legitimately return overlapping items (a real FreshRSS "general query, return everything" case is what surfaced this), and neither `_merge_same_source()`'s plain string concatenation nor Bug 1's section-level fix has any awareness of what's actually *inside* either blob.

**Fixed** with `fusion._dedupe_items_across_blobs()`, which removes any item from the second blob whose leading `**Title**` line exactly matches one already in the first.

**Bug 3 — the fix had to run before the join, not after.** The dedup in Bug 2's fix only works at the one point where the boundary between the two original results is still completely unambiguous: *before* they're joined into one string. A first attempt deduping *after* the join failed a real test — once two blobs are glued together with a blank line, that boundary is no longer reliably distinguishable from an ordinary paragraph break inside either blob's own content, and a later split can silently merge two genuinely separate items into one. The working version dedupes the two blobs while they're still separate values, then joins.

**The lesson:** a merge function that only compares its own immediate inputs (the outer label) can be structurally correct and still produce a wrong result, because the actual scope of "have I seen this before" needed to widen twice — first to the assembled text, then to the content inside it — before the bug was actually gone. Each widening was only findable once the narrower fix was already shipped and being tested against real traffic; none of the three would have been obvious from reading the original function in isolation.

The complete narrative, with the actual MiniDock output at each stage, is in [The Adversarial Testing Production Bugs](The-Adversarial-Testing-Production-Bugs#real-bugs-found-in-mnemolis-itself-after-running-for-real) — these three bugs are part of the larger four-bug chain that page documents from the production-monitoring side; this page covers the merge-logic mechanism itself.

## The `[FUSION — FUSION]` bug, and why it kept coming back

If you ever saw the literal text `[FUSION — FUSION]` show up as a header inside a response, that's fixed — twice, in two different places, because the same root cause had two independent call sites.

`fusion.search()` itself never produces a literal `"fusion"` source name as a result label — the bug lived entirely in *callers* of fusion's output. `route_with_source()` can report `"fusion"` as the overall source for a result that's already internally self-headered (each contributing source already has its own `[SOURCE — LABEL]` baked in). A caller that doesn't check for this and blindly does `f"{_format_header(resolved_source)}\n{result}"` produces a literal `[FUSION — FUSION]` wrapped around content that's already correctly labeled section by section.

This exact bug was found and fixed **twice** in this project's history — once in [Query Decomposition](Query-Decomposition)'s original merge loop, and a second time at a brand-new call site added for [Conditional Query Detection](Conditional-Query-Detection)'s remainder-merging feature, which hadn't existed yet when the first fix shipped. The fix is the same both times: check whether the source being wrapped is literally the string `"fusion"`, and if so, pass the result through unwrapped rather than double-headering it.

**The lesson:** there's no single chokepoint that catches this automatically. Any time new code merges multiple `route_with_source()` outputs together, the `"fusion"`-string check has to be applied at that specific merge site — a real, recurring footgun worth remembering before adding a third one.

## The mixed-speed timeout crash

If a fusion query ever failed completely when it should have at least partially succeeded — say, fusing a fast source with one that was slow or unreachable — that's fixed now. A real bug meant pairing one quick source with one slow enough to hit the timeout could crash the *entire* fusion call, discarding the fast source's real, already-successful result along with it.

The mechanism: `concurrent.futures.as_completed(futures, timeout=fusion_timeout)` has its own overall timeout, raised as a `TimeoutError` for the *entire iteration* the moment the deadline passes — a separate mechanism from the per-future timeout already used inside the loop to mark one slow source as failed. The outer one was never caught, so it took down everything gathered so far with it.

Found via a careful, deliberate re-read of `search()`, not a failing test — no existing test happened to mix a fast source with a slow one. Fixed by wrapping the iteration in its own `try/except`, marking any future not yet recorded as failed without discarding what had already succeeded. Today, the fast source's content comes back correctly, with the slow source logged and excluded — a clean partial success instead of a total failure.

**The lesson:** a timeout guard inside a loop doesn't protect against a *different* timeout wrapped around the whole loop. The two timeouts looked redundant until a real mixed-speed query showed they weren't — the per-future one bounds a single source's wait, the iterator-level one bounds the whole gather, and only one of them was ever being caught.

## v3.50.18: seven findings, two files, one investigation

Seven separate findings surfaced across four successive, deliberately exhaustive audit passes of `fusion.py` and its direct dependents (`router.py`'s sibling functions). None were inferred from reading the code alone — every claim below was confirmed by running it. All seven are independent and shipped together purely because they share a file or its direct dependents, not because fixing one required or motivated fixing another.

### The `ContextVar` propagation gap

`fusion.search()`'s concurrent dispatch used a bare `executor.submit(fn, *args)` — the exact shape that drops `contextvars.ContextVar` state, since `ThreadPoolExecutor` does not propagate it into worker threads by default. `router.py`'s `_resolve_conditional()` and `searxng.py`'s own concurrent fetch had already learned this lesson and already fixed it with `executor.submit(contextvars.copy_context().run, fn, *args)`; `fusion.py` was the one remaining unfixed site in the codebase.

Confirmed the actual blast radius is narrower than it first looks: only `kiwix.py` writes to the routing cache from inside a source handler (via `_pick_books_with_llm()`/`_get_disambiguation_candidates()`), and `router.suppress_cache_writes()` has exactly one real caller anywhere in the codebase — `adversarial_testing.py`'s `run_adversarial_test_cycle()`. A real user's `/search` request never sets this flag, so the gap had **zero effect on real traffic**. It mattered for a different, real reason: it broke `run_adversarial_test_cycle()`'s own documented guarantee that synthetic queries never touch real cache state. Quantified directly across the real keyword/discourse-pattern space: 96 of 96 combinations of a discourse-framing phrase sharing a clause with a real keyword (no conjunction between them) reach this exact leak shape — not a rare edge case, the unconjoined version of a pattern that already exists in real adversarial recipes.

Fixed with the identical, already-proven pattern: one `contextvars.copy_context()` call per submitted task in the dict comprehension fusion's `search()` builds.

### Unbounded thread creation, and a recurring `RemoteDisconnected` mystery

`search()` used to create a brand-new `ThreadPoolExecutor` on every single call, sized to `len(valid)` (typically 2-3) — no shared, bounded pool across concurrent fusion requests at all. Confirmed directly: 20 concurrent fusion-shaped calls produced 81 real, live OS threads at peak, scaling linearly with no ceiling as concurrent traffic increased.

This was investigated because of a real, recurring, previously-unexplained `RemoteDisconnected('Remote end closed connection without response')` failure that had appeared sporadically in this project's own benchmark history since v3.50.9 — every occurrence landing on a `fusion_*` endpoint, never a single-source one, and every occurrence producing zero corresponding application-layer log line (consistent with a failure below the Python application layer, at the OS/socket level). **Not claimed as a proven root cause** — there's no direct access to the real deployment's ulimits or `dmesg` output from the moment of either failure — but a well-corroborated, plausible mechanism with no real downside to fixing regardless.

Fixed by replacing the per-request executor with a single, shared, module-level pool (`FUSION_THREAD_POOL_SIZE`, default 12) — the same shape of fix `app/llm.py`'s connection pool already applied to a different unbounded-per-call resource.

### The order-dependent deduplication bug, and its real bias against kiwix

`_deduplicate()`'s own docstring says the shorter, more-redundant source should be the one dropped on 60%+ sentence overlap — but the implementation didn't actually check which compared source was longer. Its first branch (`overlap / len(sents2) >= 0.6` → drop `s2`) only correctly identifies "s2 is redundant" when `s1` is the longer source; if `s1` happened to be shorter, the same branch could still fire and unconditionally dropped `s2` anyway. Confirmed directly: the same two pieces of content, same actual overlap, produced opposite outcomes purely from which key appeared first in the `results` dict — and dict insertion order in the real call site is determined by `as_completed()`'s own completion order, with zero semantic relationship to which source's content is actually more complete.

Quantified against this project's own real, measured cold-path latency distributions: under cold-cache conditions (the condition where two sources are actually likely to produce real, overlapping content worth deduplicating), `web` wins the completion race roughly two-thirds of the time — meaning the bug had a real, confirmed lean toward discarding `kiwix`'s content (the more often encyclopedic, substantive source) in favor of `web`'s, specifically on the queries most likely to trigger real overlap at all.

Fixed by comparing sentence-set sizes directly and always treating the smaller set as the removal candidate, regardless of which variable held which source.

### Five missing `_looks_empty()` phrases

A second, systematic cross-check — every plain-string failure/empty return statement in every source file, checked one at a time against the phrase list — found five more real gaps a prior pass had missed: `"unable to retrieve"` (forecast.py), `"no valid sources"`/`"no results returned"` (fusion.py's own two self-generated messages — this function had never recognized its own module's failure output), and `"no entity states returned"`/`"no matching entities found"`/`"no significant changes"` (home_assistant.py, snapshots.py).

The forecast.py gap was real and user-visible, not just theoretical: `forecast.search()`'s exception handler returns `f"Unable to retrieve forecast: {e}"` on any failure, and `router.py`'s `_resolve_single_source()` caches any result `_looks_empty()` doesn't recognize as empty. A single transient API hiccup got cached as if it were a genuine, successful weather result for up to 30 minutes (the default forecast cache TTL), instead of correctly retrying on the next request. Fixed by adding all five phrases to the existing list, confirmed protected by the same markdown-bold gate already guarding the original phrases.

### Title-only item deduplication risk — documented, not yet fixed

`_dedupe_items_across_blobs()` keys purely on an item's leading `**Title**` line — exact match, no consideration of whether the rest of the item (content or URL) actually agrees. Confirmed this can treat genuinely different articles as duplicates whenever their headlines happen to coincide (wire-service syndication, multiple outlets covering the same event with identical phrasing). The naive fix (key on the entire item) was checked against a second realistic scenario and found to introduce its own regression — it would treat the same article reached via two different tracking-parameter URLs as different items, a case the original title-only key correctly caught. The right fix (title plus normalized URL when present, falling back to title-only when not) is deliberately left as a direction rather than a fully-specified implementation — see the design doc this section is based on for the full reasoning.

### The per-pair separator bug

Both `fusion._merge_same_source()` and `router.py`'s `_dedupe_nested_fusion_sections()` decided the `"\n\n---\n\n"` vs bare `"\n\n"` item separator independently on each individual pairwise merge, based on whether *either side of that one pair* already contained `"---"` internally. When a chain mixed genuinely single-item results with genuinely multi-item ones — realistic, since `freshrss.py` returns a bare single article when exactly one matches and a proper `"---"`-joined list when more than one does — the early pairs in the chain got the wrong, ambiguous `"\n\n"` separator, even though the assembled whole was unambiguously multi-item by the time the chain finished. Confirmed directly with a real, plausible compound query (three news-resolved clauses, the first two single-item, the third multi-item): the boundary between two genuinely separate, unrelated headlines came out as a bare blank line — visually indistinguishable from two paragraphs of one story.

Fixed by restructuring both functions to group every consecutive same-source (or same-header) part together first, then decide the separator once for the whole group. **One deliberate behavior change worth stating plainly:** a merge of exactly two genuinely single-item same-source parts now always gets `"\n\n---\n\n"` instead of the old `"\n\n"` — this is the correct behavior, not a side effect, since combining 2+ genuinely separate results is inherently multi-item the moment there are two of them.

### `FUSION_TIMEOUT_SECONDS` never actually bounded the caller's wait

The most consequential of the seven. `as_completed(futures, timeout=fusion_timeout)`'s own timeout fires exactly when configured — that part always worked correctly. The bug was one level up: `with ThreadPoolExecutor(...) as executor:`'s implicit `shutdown(wait=True)` on exit blocks until every submitted thread genuinely finishes, completely independent of whatever `as_completed()` already gave up on. `FUSION_TIMEOUT_SECONDS` correctly bounded how long `search()` waited for results before giving up on them — it never actually bounded how long the *caller* waited for `search()` to return.

Confirmed directly, measured: a configured 1-second timeout, an actual ~10-second caller-facing wait, against the real `fusion.search()` with a genuinely slow source mixed in. The clearest possible evidence this was real and already shipping: this project's own existing regression test for this exact code path (`test_slow_source_does_not_crash_or_discard_fast_source_result`) had been silently taking 10.4 real seconds to run, every single time, the whole time it existed, because it only ever asserted what the result contained — never how long producing it took.

Fixed by managing the (now-shared) executor's lifecycle explicitly — `executor.shutdown(wait=False)` instead of the implicit context-manager shutdown — so an abandoned straggler keeps running in the background and is discarded once it finishes, with the caller correctly getting control back at the configured timeout. Measured after the fix: ~1.16 seconds against a configured 1-second timeout, dropping the test's own runtime from 10.4s to ~1.0s — a concrete, externally-checkable confirmation the fix took effect, beyond just the assertions passing.

**The lesson, same as the rest of this page:** a mechanism that looks correct in isolation (`as_completed()`'s timeout firing on schedule) can still leave a real, user-facing latency bug sitting one level up, in how its own surrounding context manager behaves on the way out — and the bug had been quietly costing real time inside this project's own CI feedback loop the entire time, without ever showing up as a failing assertion.
