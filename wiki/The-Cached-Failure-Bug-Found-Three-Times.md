# The Cached-Failure Bug, Found Three Times

The same real bug, in three different functions, found one at a time by tracing the same suspicious pattern into each new function it touched: a routing-cache write that didn't distinguish between a genuine success and a fallback triggered by failure, caching both under the identical key.

## First occurrence: `_llm_pick_fusion_sources()`

Caches its routing decision so a repeated explicit-fusion query doesn't re-pay the LLM cost. The bug: when the LLM returned an unrecognized or malformed response, the function's own `["kiwix", "web"]` fallback got written to the cache under the exact same key a genuine, successful LLM decision would use.

Confirmed directly, not just reasoned about: a single transient LLM hiccup — a truncated response, a momentary parsing glitch — would permanently lock that specific query into the generic fallback for the *entire* routing cache TTL. A retry moments later that would have genuinely succeeded with a better, more specific source selection never even got the chance, because the cached failure short-circuited the function before the real call could happen again.

## Second occurrence, found the same day: `_llm_detect()`

Once the pattern was named, it was checked against the obvious sibling function — single/multi-source routing decisions, the same general shape of "ask the LLM, cache the answer." Confirmed reachable with an identical direct test before fixing it, rather than assuming it was fine because it hadn't been the original target. Both functions were fixed the same way: return the fallback value on a failed LLM response, but skip writing it to the cache, so every subsequent identical query gets a fresh, real shot at the LLM instead of being permanently degraded by one bad response.

## Third occurrence, with a real wrinkle: `_get_disambiguation_candidates()`

The same failure-caching pattern turned up a third time, in Kiwix's disambiguation-candidate generation — but this one needed a genuinely different fix, not a copy-paste of the first two, because this function's fallback path is reachable for two different reasons that deserve two different answers:

- **The LLM call itself failed outright** (`complete()` returned nothing) — a real, transient failure where a retry is likely to succeed. This case should skip the cache, the same way the first two fixes did.
- **The LLM responded with real content that just didn't pass a sanity filter** — three candidate phrases were generated, but none of them actually contained the original ambiguous word. This isn't a transient hiccup; the same prompt would likely produce a similarly unusable answer on a retry, so caching this specific outcome is the more sensible default, not a bug to fix.

Fixed by only skipping the cache write when the raw LLM response itself was empty or falsy — the call-failure case — while still caching a genuinely-answered-but-filtered-out response, confirmed correct with separate, direct tests for each case.

## The lesson

The same bug shape doesn't always need the same fix. The first two occurrences were structurally identical and took the identical fix. The third one looked identical on the surface — "a fallback got cached as if it were a success" — but had a real, meaningful distinction underneath that the first two fixes' own logic didn't need to make. Applying the first fix's exact shape here without checking would have been a regression in the other direction: a function that re-queries the LLM on every repeat of a question it has already genuinely, substantively failed to answer well, for no benefit.
