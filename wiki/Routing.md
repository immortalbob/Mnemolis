# Routing

Routing is the decision layer that sits between "what you typed" and "which [Source](Sources) actually answers it." Every query that comes in as `source="auto"` passes through this logic; explicit source requests (`source="kiwix"`, `source="forecast"`, etc.) skip most of it deliberately, on the assumption that if you already know which source you want, the router shouldn't second-guess you.

## The decision flow

```text
                              Query arrives, source="auto"
                                          │
                                          ▼
                         Leading "if X, Y" structure?
                      (see Conditional Query Detection)
                                          │
                              ┌───────────┴───────────┐
                              ▼ yes                    ▼ no
                    Search only the condition    Contains a conjunction?
                    Frame response around          (see Query Decomposition)
                    the real answer                       │
                                              ┌─────────────┴─────────────┐
                                              ▼ yes                       ▼ no
                                    Split into sub-queries,         Single intent —
                                    route each one independently   route directly
                                    (recursive conditional                │
                                     re-check applies here too)           │
                                              │                           │
                                              └─────────────┬─────────────┘
                                                             ▼
                                                  Keyword match found?
                                                  (INTENT_MAP lookup)
                                                             │
                                                ┌────────────┴────────────┐
                                                ▼ yes                     ▼ no
                                      Use matched source(s)        Ask the LLM to pick
                                      (2+ matches = fusion)        1 source, or 2-3 for
                                                │                  fusion if complex
                                                │                          │
                                                └────────────┬─────────────┘
                                                             ▼
                                                  Discourse-framing
                                                  phrase detected?
                                                             │
                                                ┌────────────┴────────────┐
                                                ▼ yes                     ▼ no
                                      Add kiwix if                 Use whatever was
                                      not already chosen,          already chosen,
                                      escalate to fusion           keyword or LLM
                                                │                          │
                                                └────────────┬─────────────┘
                                                             ▼
                                                  Query the chosen source(s)
                                                             │
                                                             ▼
                                                  Result looks like "no results"?
                                                  (kiwix/news only — see below)
                                                             │
                                                ┌────────────┴────────────┐
                                                ▼ yes                     ▼ no
                                      Fall back to web              Return result,
                                      Report source_used             source_used =
                                      = "web", log                   what was
                                      fallback_occurred              actually used
```

## Two ways a source gets chosen

**Keyword matching** runs first, always, because it's free — no LLM call needed. `INTENT_MAP` holds a list of trigger phrases per source ("battery status" → `ha`, "will it rain" → `forecast`, "is everything up" → `uptime`, and so on). If a query matches triggers from more than one source, that's treated as a genuine multi-topic question and escalated straight to fusion without ever asking the LLM.

**LLM-assisted selection** only runs when no keyword matched at all. The LLM is shown the query plus a one-line description of every source, and asked to return either one source name, or 2–3 comma-separated names if the question seems complex enough to benefit from combining sources. This is also where [Kiwix Disambiguation](Kiwix-Disambiguation)'s alternate phrasings and book selection happen, and where [Query Expansion](Query-Expansion) kicks in for `web`. See [LLM Client](LLM-Client) for the actual client this call goes through, including what happens when no LLM is configured at all.

Every LLM routing decision is cached for an hour (`ROUTING_CACHE_TTL_SECONDS`) — a query's source assignment is stable in practice, so paying the LLM cost twice for the same question is wasted work. See [Caching](Caching) for how that cache is bounded.

## The discourse-framing bias

This exists because of a real, repeatedly-reproduced bug: queries phrased as current public discourse — *"what's the deal with that whole mercury retrograde thing everyone keeps talking about"* — kept routing past `kiwix` straight to `news`/`web`, even when the underlying topic was genuinely encyclopedic. The cause: `news` and `web`'s own descriptions ("current events," "recent information") matched this kind of phrasing almost word-for-word, while `kiwix`'s description gave the LLM no reason to think it covers evergreen topics that happen to be phrased as current chatter.

Rather than trying to nudge the LLM's judgment by rewording `kiwix`'s description — an indirect, hard-to-verify lever — this is detected explicitly with a small set of literal phrase matches (`"everyone keeps talking about"`, `"everyone's obsessed with"`, and a few variants). If one of these phrases is present and `kiwix` wasn't already part of the chosen source(s), `kiwix` gets added and the result escalates to fusion.

**This check applies to both paths above, not just LLM selection.** `"everyone keeps talking about black holes, and rss"` matches the keyword `"rss"` directly (→ `news`), with the LLM never even consulted — and the bias still fires there too, adding `kiwix` and escalating to fusion the same way it would on the LLM path. `INTENT_MAP` contains dozens of short, common words (`"news"`, `"weather"`, `"rss"`, `"feeds"`, `"door locked"`) that can easily co-occur with genuine discourse framing in a real sentence, so this matters for the majority of real discourse-framed queries, not just an edge case.

The full investigation behind this feature, including the original LLM-path fix and a real gap later found in the keyword-matching path specifically, is in [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation).

## Fallback — when a source comes back empty

Two sources have a configured fallback target: `kiwix` and `news` both fall back to `web` if their own result looks empty. "Looks empty" is a literal phrase match against a known list — `"no results found"`, `"not configured"`, `"could not connect"`, and several others — not a judgment call or a confidence score. Both `router.py` and `fusion.py` share one canonical version of this list, living in `fusion.py`. If `kiwix`'s response contains one of those phrases, `web` gets queried instead, transparently, and `source_used` in the response correctly reports `"web"` — never the originally-intended source that actually failed. The fallback itself is fully observable — see [Health & Observability](Health-and-Observability) for how `fallback_occurred` gets tracked and surfaced in `/logs/stats`.

## Where this connects to everything else

Routing decides *which* source(s) answer a query, but it's not the only layer that touches a query before that decision gets made:

- [Query Decomposition](Query-Decomposition) runs first for any compound question, splitting it into independent sub-queries that each get routed separately
- [Conditional Query Detection](Conditional-Query-Detection) runs before decomposition for leading `"if X, Y"` phrasing, and is re-applied to each decomposed sub-query too
- [Fusion](Fusion) is what actually happens when routing decides more than one source is needed

---

## Development Notes

- **The discourse-framing bias originally only fired on the LLM-selection path.** A real, separate gap meant a keyword match (even an ordinary one like `"rss"`) could silently bypass the bias entirely, defeating its purpose for the majority of real discourse-framed queries that happen to also mention an everyday `INTENT_MAP` word. See [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation) for the full multi-bug history.
- **A source you forgot to configure used to return its own raw error message instead of falling back to `web`.** `router.py` and `fusion.py` carried separate copies of the "looks empty" phrase list that had quietly drifted apart — confirmed directly with an unconfigured FreshRSS returning its literal `"FreshRSS is not configured..."` error as the actual result, with `source_used: "news"`. Fixed by sharing one canonical list. Separately, `source_used` itself used to report the originally-*intended* source even when a fallback happened, which made the bug above harder to see in the first place — fixed at the same time.
