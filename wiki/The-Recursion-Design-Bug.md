# The Recursion Design Bug

[Conditional Query Detection](Conditional-Query-Detection) needs to re-check each [decomposed](Query-Decomposition) sub-query for its own embedded conditional structure — *"what is the weather and if the back door is unlocked, let me know"* doesn't start with "if," so the top-level check correctly passes it through to decomposition, but the second resulting sub-query genuinely is conditional and needs the same framing applied to it too. The first attempt at building this recursive re-check had a real bug, caught only by testing it against the exact scenario it was built for — and the bug's root cause, once understood, pointed toward a simpler design than the one that shipped first.

## The first design — recurse with a depth counter

The intuitive way to build "re-check a sub-query for its own conditional structure" is to call `route_with_source()` again on that sub-query, letting the same conditional-detection logic that runs at the top level run again. The obvious risk with any function calling itself is infinite recursion, so the first version added a manual `_depth` parameter, incrementing on each recursive call and refusing to re-detect a conditional past depth 1 — a defensive guard against runaway recursion that seemed prudent to add even though it wasn't clear recursion could actually run away in practice.

This shipped, the full test suite passed, and a quick manual check looked right. Then a real end-to-end test against the exact scenario this feature was built for — a top-level query that decomposes into a sub-query which is itself conditional — came back wrong: the conditional framing simply never appeared. The HA section showed plain entity data, no "this was a conditional question" framing at all, as if the sub-query's conditional structure had never been detected in the first place.

## Finding the actual bug with real tracing, not guessing

Adding genuine debug print statements at both the top-level conditional check and the sub-query recursive call traced the problem precisely: the recursive call's *own* top-level conditional check was firing — but at `_depth=1`, and the guard was written as `if _depth < 1`. The depth counter had incremented to `1` specifically *before* the conditional it was meant to protect had actually been consumed, which meant the recursive call's own necessary re-detection of that very conditional got blocked by the exact counter that was supposed to be preventing infinite recursion that was never actually possible to begin with.

```text
   Top-level call, _depth=0
   Query: "...and if the back door is unlocked, let me know"
                          │
                          ▼
   Decomposes into: "if the back door is unlocked, let me know"
                          │
                          ▼
   Recursive call: route_with_source(sub_q, _depth=1)
                          │
                          ▼
   Conditional check gated on `_depth < 1` — FALSE at depth 1
                          │
                          ▼
   Conditional structure in the sub_q text is simply
   never detected. Routed as plain text. No framing.
```

## The fix — and why it's simpler than the bug it replaced

The actual problem wasn't recursion depth at all — it was *what* was being passed into the recursive call. The original design recursed on the **full original sub-query string**, still containing the literal `"if X, Y"` text, which meant the recursive call genuinely needed to re-run conditional detection from scratch to make any sense of it. The fix: extract the condition and consequence directly in the decomposition loop — the same way the top-level handler already does — and recurse on the **already-extracted condition text only**, mirroring the existing top-level pattern exactly rather than inventing a new one.

```text
   First design:
   recurse on "if the back door is unlocked, let me know"
   (needs to re-detect the conditional from scratch)

   Fixed design:
   extract condition = "the back door is unlocked" FIRST,
   recurse on just that
   (never needs to re-detect anything — it's already
    been extracted before the recursive call happens)
```

This sidesteps the depth-counter problem entirely rather than fixing its off-by-one: the condition text essentially never re-matches the leading `"if"`/`"should"`/`"in case"` pattern a second time, since it's just the bare condition, not a sentence that starts with a conditional word. No depth parameter is needed at all — the recursion terminates naturally because of what's actually being passed, not because of an artificial counter watching how many times it's been called.

## A second, smaller bug found immediately after, in the same area

Fixing the remainder-handling logic alongside this (the case where a real second question follows the conditional's consequence — *"...let me know, and also what's the weather"*) surfaced the exact same `[FUSION — FUSION]` double-header bug described in [Fusion](Fusion), at a brand-new call site that hadn't existed when that bug was first found and fixed elsewhere. Worth remembering as a real, recurring footgun: any new code that merges multiple `route_with_source()` outputs together needs the same "is this literally the string `'fusion'`" check applied at its own merge point — there's no single chokepoint that catches this automatically across the whole codebase.

## The actual lesson

The first instinct when something doesn't terminate correctly is usually "add a safety limit." That's not always wrong, but it's worth asking, before reaching for a depth counter or similar guard, whether the *real* design change is to pass different data into the recursive call rather than to bound how many times it's allowed to happen. A defensive limit that's actually unnecessary doesn't just add code for no reason — as this bug showed directly, it can actively break the exact thing it was added to protect.
