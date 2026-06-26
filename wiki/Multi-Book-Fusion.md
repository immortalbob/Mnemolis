# Multi-Book Fusion

Kiwix usually holds several distinct ZIM books — Wikipedia, a handful of Stack Exchange communities, iFixit, FreeCodeCamp, DevDocs. Most questions clearly belong to just one of them, but some genuinely don't: a question that's part hardware troubleshooting, part general knowledge could legitimately have a real answer split across two books. Multi-book fusion exists to merge those cases together instead of forcing a single winner-take-all pick.

## How a question ends up with more than one book in play

Book selection happens once, up front, before any searching: the LLM is asked to rank the available books for the query and return up to `KIWIX_MAX_BOOKS` (default **2**) of them. Most of the time this naturally collapses to one book, since most questions really do belong cleanly to one source. When it returns two, both get searched — and that's the actual trigger for fusion ever being considered at all. A single selected book never reaches the fusion-decision step, because there's nothing to fuse.

**The same query reliably picks the same book(s) every time, even across container restarts.** When the LLM's response doesn't exactly match a real book name, fuzzy substring matching checks candidate books in a fixed, sorted order — so a genuinely ambiguous LLM response (e.g. a truncated name matching both a "maxi" and a "nopic" variant of the same Wikipedia dump) always resolves to the same one of the two, regardless of restart timing.

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
   Is the OVERALL top score actually positive?
   (a negative top score means every candidate
   is already poor — the threshold math below
   silently breaks down for a negative number,
   so this is checked explicitly rather than
   relying on it accidentally working out)
                  │
        ┌─────────┴─────────┐
        ▼ no                 ▼ yes
   Skip fusion entirely —   For each book: is its best score at least
   use the single overall   KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT (default
   best result as-is        50%) of the OVERALL top score?
                                          │
                               ┌─────────┴─────────┐
                               ▼ no                 ▼ yes
                          Discard — this book's   Keep — this book's
                          best result wasn't      result is genuinely
                          competitive              competitive
                                          │
                                          ▼
                               Did MORE THAN ONE book survive
                               the threshold?
                                          │
                               ┌─────────┴─────────┐
                               ▼ no                 ▼ yes
                          Just use the single   Fuse — merge each
                          overall winner,       surviving book's best
                          no fusion needed       result into one response
```

That threshold — `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT`, default 50% of the top score — is a real, configurable setting, not a fixed constant; see [Configuration Reference](Configuration-Reference) to tune it. It's the actual, central "should a second book be fused in, or dropped as noise" decision this page documents, so it's exposed rather than hardcoded.

The `top_score > 0` guard above exists because a result can legitimately score negative (a list/index article nets a real penalty with zero other matches), and the threshold check (`score >= top_score * 0.5`) would otherwise behave inconsistently for a negative `top_score` — even the top result itself wouldn't pass its own bar (`-10 >= -5` is `False`). Checking explicitly makes the intent correct by construction: when every candidate is already poor, fall through to "just use the single best, still-poor result," rather than relying on the threshold math accidentally landing in the right place.

## What the merged response looks like

Each surviving book's best result gets its full article fetched, truncated the same way [Fusion](Fusion) truncates cross-source results, and wrapped in a `[BOOKNAME]` header — sorted so the highest-scoring book's section appears first. If only one book's article actually fetches successfully (the others failing for some reason, like a transient network issue), the response gracefully degrades to that single section, plain, with no header — the same single-survivor behavior [Fusion](Fusion) uses for cross-source results, applied here at the book level instead.

## How this differs from cross-source fusion

This is a real, deliberate parallel to [Fusion](Fusion)'s own merge logic — truncated sections, attribution headers, sorted by relevance, graceful single-survivor fallback — but it's a genuinely separate code path, living inside `kiwix.py` rather than `fusion.py`. The reason: cross-source fusion merges results from entirely different *backends* (Kiwix, web, news), each already a finished, independent answer. Multi-book fusion merges results from within the *same* backend, before [Kiwix Scoring](Kiwix-Scoring) has even finished picking a final answer — it's a Kiwix-internal decision about which of its own books' results deserve to survive, not a decision about which external sources to combine.

---

## Development Notes

- **Book selection used to be non-deterministic across restarts** when the LLM's response fell back to fuzzy substring matching — the candidate-checking order wasn't guaranteed stable, so a genuinely ambiguous response could resolve differently after a restart for no visible reason. Fixed by checking candidates in a fixed, sorted order.
- **The fusion threshold used to be hardcoded** rather than exposed as `KIWIX_MULTI_BOOK_FUSION_THRESHOLD_PCT` — made configurable since it's the central decision this page documents.
