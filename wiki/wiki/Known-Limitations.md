# Known Limitations

A single, honest list of what Mnemolis doesn't do well, written for someone deciding whether it fits their use case — not for someone already reading a mechanism page who's just found the edge of it. Every item here is a deliberate scope decision or an accepted ceiling, not an open bug waiting to be fixed; each links to the page that explains the real mechanism and reasoning behind it.

## Query Understanding

**Conditional phrasing needs an explicit comma.** *"If the back door is unlocked, let me know"* works. *"If the front door is unlocked tell me"* (no comma) doesn't — Mnemolis won't recognize it as a conditional at all, and will just route it as a plain, literal question instead. This is a real, permanent scope boundary, not a bug: reliably telling this apart from the "whether" sense of "if" (*"check if the lights are on"*) would require actual grammatical parsing, not pattern matching, and that's a different kind of project. See [Conditional Query Detection](Conditional-Query-Detection#why-the-pattern-is-this-narrow).

**A decomposed segment merging two unrelated topics can route imperfectly.** If a proper-noun pair (*"Iran and Israel"*) sits directly adjacent to a completely unrelated real intent within the same decomposed segment, both get correctly preserved as text — but they're searched together, as one combined segment, against a single source. That source may not serve both topics well. This is an accepted, minor side effect of the fix that stops real content from being silently discarded near a protected pair — not a regression, and the alternative (losing real content) was worse. See [The Proper-Noun-Pair Saga](The-Proper-Noun-Pair-Saga).

## Search Relevance

**A single, genuinely ambiguous bare word can land on an imprecise match.** *"Galaxy"* is the documented example — astronomy and pop-culture senses are both real, comparably-represented topics in a typical Wikipedia index, and Mnemolis's [disambiguation](Kiwix-Disambiguation) generates and tests several candidate phrasings specifically to solve this kind of problem, but it isn't magic. If the index genuinely contains multiple plausible answers to the same bare word, scoring can still pick the wrong one. This is an honest ceiling of keyword-and-structure scoring, not something a better prompt or another candidate would reliably fix. See [Kiwix Scoring](Kiwix-Scoring#where-scoring-still-has-a-real-ceiling).

## Transport & Upstream Dependencies

**A real, currently-open upstream race condition exists in the MCP transport layer.** Independent of anything Mnemolis's own code does, there's an open issue in the underlying MCP SDK describing a scenario where the Streamable HTTP session manager can report "shutting down" immediately after a request starts, before a response is fully streamed — potentially causing an empty or truncated response under certain timing conditions. This is not something Mnemolis can currently fix on its own side; it's documented directly in `mcp_server.py` as a known risk to watch for, and worth checking that upstream issue before assuming a truncated MCP response is a Mnemolis-specific bug. See [MCP Server](MCP-Server#a-real-currently-open-ecosystem-bug-found-during-the-migration).

## What's deliberately *not* on this list

Things that look like limitations but are actually intentional design boundaries, already explained where they belong rather than repeated here: Mnemolis has no reminder, trigger, or notification capability at all (see how [Conditional Query Detection](Conditional-Query-Detection) handles this honestly rather than pretending otherwise), exposes one MCP tool rather than several per-source ones (see [MCP Server](MCP-Server#why-one-tool-instead-of-several)), and requires explicit, manual registration for new sources rather than auto-discovery (see [Adding a New Source](Adding-a-New-Source#why-this-is-explicit-not-auto-discovered)). None of these are gaps to fix — they're the actual shape of the project, by design.

## If you find something not listed here

This list reflects what's been found and verified through real, deliberate adversarial testing — not a guarantee that nothing else exists. If you hit a genuine limitation that isn't documented anywhere in this wiki, that's worth a real issue on the repo, the same way the [external MCP audit](https://github.com/immortalbob/Mnemolis/issues/7) was taken seriously, investigated properly, and resolved with an actual fix rather than a dismissal.
