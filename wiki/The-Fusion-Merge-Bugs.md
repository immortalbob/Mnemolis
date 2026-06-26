# The Fusion Merge Bugs

Two separate stories from `fusion.py`'s history, bundled together because they're the same kind of bug found the same way: real production traffic surfacing a gap between what the merge logic assumed and what actually happened once enough real, varied queries hit it.

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
