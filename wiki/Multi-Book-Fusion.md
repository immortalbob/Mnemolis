# Multi-Book Fusion

Kiwix usually holds several distinct ZIM books — Wikipedia, a handful of Stack Exchange communities, iFixit, FreeCodeCamp, DevDocs. Most questions clearly belong to just one of them, but some genuinely don't: a question that's part hardware troubleshooting, part general knowledge could legitimately have a real answer split across two books. Multi-book fusion exists to merge those cases together instead of forcing a single winner-take-all pick.

## How a question ends up with more than one book in play

Book selection happens once, up front, before any searching: the LLM is asked to rank the available books for the query and return up to `KIWIX_MAX_BOOKS` (default **2**) of them. Most of the time this naturally collapses to one book, since most questions really do belong cleanly to one source. When it returns two, both get searched — and that's the actual trigger for fusion ever being considered at all. A single selected book never reaches the fusion-decision step, because there's nothing to fuse.

**The same query now reliably picks the same book(s) every time, even across container restarts.** When the LLM's response doesn't exactly match a real book name and falls back to fuzzy substring matching, the candidate books used to be checked in an order that wasn't actually guaranteed to stay the same between runs — meaning a genuinely ambiguous LLM response (e.g. a truncated name matching both a "maxi" and a "nopic" variant of the same Wikipedia dump) could resolve to a different one of the two after a restart, for no visible reason. Fixed by checking candidates in a fixed, sorted order.

## Deciding whether to actually fuse, or just pick the winner

Having two books selected doesn't automatically mean both get used. The LLM picking a second, only tangentially-related book "just in case" shouldn't produce a forced two-book response when one book clearly has the real answer and the other has noise. The actual decision:

```text
   More than one book was selected
                  │
                  ▼
   Find each book's OWN best-scored result
   (not the overall top result — each book's
   individual best candidate)
                  │
                  ▼
   For each book: is its best score at least
   50% of the OVERALL top score?
                  │
        ┌─────────┴─────────┐
        ▼ no                 ▼ yes
   Discard — this book's   Keep — this book's
   best result wasn't      result is genuinely
   competitive              competitive
                  │
                  ▼
   Did MORE THAN ONE book survive
   the 50% threshold?
                  │
        ┌─────────┴─────────┐
        ▼ no                 ▼ yes
   Just use the single   Fuse — merge each
   overall winner,       surviving book's best
   no fusion needed       result into one response
```

That 50%-of-top-score threshold is the actual mechanism deciding "genuinely competitive" vs. "noise." A book whose best result scores far below the overall winner gets dropped silently — it was selected by the LLM, searched, and considered, but its content wasn't actually relevant enough to include.

## What the merged response looks like

Each surviving book's best result gets its full article fetched, truncated the same way [Fusion](Fusion) truncates cross-source results, and wrapped in a `[BOOKNAME]` header — sorted so the highest-scoring book's section appears first. If only one book's article actually fetches successfully (the others failing for some reason, like a transient network issue), the response gracefully degrades to that single section, plain, with no header — the same single-survivor behavior [Fusion](Fusion) uses for cross-source results, applied here at the book level instead.

## How this differs from cross-source fusion

This is a real, deliberate parallel to [Fusion](Fusion)'s own merge logic — truncated sections, attribution headers, sorted by relevance, graceful single-survivor fallback — but it's a genuinely separate code path, living inside `kiwix.py` rather than `fusion.py`. The reason: cross-source fusion merges results from entirely different *backends* (Kiwix, web, news), each already a finished, independent answer. Multi-book fusion merges results from within the *same* backend, before [Kiwix Scoring](Kiwix-Scoring) has even finished picking a final answer — it's a Kiwix-internal decision about which of its own books' results deserve to survive, not a decision about which external sources to combine.
