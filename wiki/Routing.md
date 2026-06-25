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

**LLM-assisted selection** only runs when no keyword matched at all. The LLM is shown the query plus a one-line description of every source, and asked to return either one source name, or 2–3 comma-separated names if the question seems complex enough to benefit from combining sources. This is also where [Kiwix Disambiguation](Kiwix-Disambiguation)'s alternate phrasings and book selection happen, and where [Query Expansion](Query-Expansion) kicks in for `web`.

Every LLM routing decision is cached for an hour (`ROUTING_CACHE_TTL`) — a query's source assignment is stable in practice, so paying the LLM cost twice for the same question is wasted work. See [Caching](Caching) for how that cache is bounded.

## The discourse-framing bias

This exists because of a real, repeatedly-reproduced bug: queries phrased as current public discourse — *"what's the deal with that whole mercury retrograde thing everyone keeps talking about"* — kept routing past `kiwix` straight to `news`/`web`, even when the underlying topic was genuinely encyclopedic. The cause: `news` and `web`'s own descriptions ("current events," "recent information") matched this kind of phrasing almost word-for-word, while `kiwix`'s description gave the LLM no reason to think it covers evergreen topics that happen to be phrased as current chatter.

Rather than trying to nudge the LLM's judgment by rewording `kiwix`'s description — an indirect, hard-to-verify lever — this is detected explicitly with a small set of literal phrase matches (`"everyone keeps talking about"`, `"everyone's obsessed with"`, and a few variants). If one of these phrases is present and `kiwix` wasn't already part of the chosen source(s), `kiwix` gets added and the result escalates to fusion.

**This check applies to BOTH paths above, not just LLM selection** — a real, separate gap from the keyword-matching path specifically, found later than the original fix and worth calling out explicitly here since it's easy to assume (the diagram above used to imply it) that discourse-framing only mattered when the LLM got involved. It doesn't: `"everyone keeps talking about black holes, and rss"` matches the keyword `"rss"` directly (→ `news`), with the LLM never even consulted — and the bias still needs to fire there too, or a perfectly ordinary keyword match silently defeats the entire point of detecting discourse framing in the first place. `INTENT_MAP` contains dozens of short, common words (`"news"`, `"weather"`, `"rss"`, `"feeds"`, `"door locked"`) that can easily co-occur with genuine discourse framing in a real sentence, so this isn't a rare edge case — it's the majority of real discourse-framed queries that happen to also mention any everyday word.

The full investigation, including a second bug found after the routing fix alone turned out not to be enough, and a third bug found later still in the keyword-matching path specifically, is in [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation).

## Fallback — when a source comes back empty

Two sources have a configured fallback target: `kiwix` and `news` both fall back to `web` if their own result looks empty. "Looks empty" is a literal phrase match against a known list — `"no results found"`, `"not configured"`, `"could not connect"`, and several others — not a judgment call or a confidence score. If `kiwix`'s response contains one of those phrases, `web` gets queried instead, transparently, and `source_used` in the response correctly reports `"web"` — never the originally-intended source that actually failed.

**If a source you forgot to configure ever returned its own raw error message instead of automatically falling back to `web`, that's fixed now.** `router.py` and `fusion.py` used to carry separate copies of the "looks empty" phrase list that had quietly drifted apart — confirmed directly: with FreshRSS unconfigured, a "give me the news" query returned the literal string *"FreshRSS is not configured. Set FRESHRSS_URL and FRESHRSS_USER"* as the actual result, with `source_used: "news"`, because the routing module's own copy had never been taught to recognize "not configured" as a failure worth falling back from. Both now share one canonical list, living in `fusion.py`.

This matters because it used to *not* work that way: an earlier version of `source_used` reported the originally *intended* source regardless of whether a fallback happened, meaning a query that silently fell back from `kiwix` to `web` claimed `source_used: "kiwix"` while the real content came from somewhere else entirely. Fixed properly, and now also fully observable — see [Health & Observability](Health-and-Observability) for how `fallback_occurred` gets tracked and surfaced in `/logs/stats`.

## Where this connects to everything else

Routing decides *which* source(s) answer a query, but it's not the only layer that touches a query before that decision gets made:

- [Query Decomposition](Query-Decomposition) runs first for any compound question, splitting it into independent sub-queries that each get routed separately
- [Conditional Query Detection](Conditional-Query-Detection) runs before decomposition for leading `"if X, Y"` phrasing, and is re-applied to each decomposed sub-query too
- [Fusion](Fusion) is what actually happens when routing decides more than one source is needed
