# Adding a New Source

The README's [Adding a New Source](https://github.com/immortalbob/Mnemolis/blob/main/README.md#adding-a-new-source) section has the literal steps. This page covers the reasoning behind why those steps look the way they do — specifically, why this isn't a drop-a-file-and-it-just-works plugin system, and why that's a deliberate choice rather than a missing feature.

## The four files you actually touch

1. **`app/sources/your_source.py`** — a `search(query: str) -> str` function. That's the entire contract. It can do whatever it needs internally; the rest of Mnemolis only ever calls it as a plain function and treats whatever string comes back as the answer.
2. **`app/config.py`** and **`docker-compose.yml`** — any config your source needs (a URL, an API key, a threshold), following the same pattern every existing source already uses.
3. **`app/router.py`** — four registration points: `SOURCE_MAP` (name → function), `INTENT_MAP` (keyword triggers, for the fast pre-LLM routing path), `SOURCE_DESCRIPTIONS` (the one-line description the LLM sees when deciding whether your source applies), and `CACHE_TTL` (how long a result should be trusted before re-fetching).

**A real pitfall to watch for when choosing trigger phrases:** keep them long enough, or specific enough, that they can't accidentally match as a substring inside an unrelated word. A short, bare trigger like `"on"` will match inside `"front"`, `"long"`, or `"among"` — see [Home Assistant Integration](Home-Assistant-Integration#development-notes) for a real bug this exact shape caused. Multi-word phrases are naturally much safer; a single short word is the riskiest shape a trigger can take.

4. **Optionally, `FALLBACK_CHAIN`** — if your source should fall back to another when it returns nothing useful (the way `kiwix` and `news` both fall back to `web`). This is tracked and surfaced in [Health & Observability](Health-and-Observability), so a source with a real, well-matched fallback target gets the same visibility the built-in ones do.

## Why this is explicit, not auto-discovered

It would be possible to scan `app/sources/` at startup, have each module declare its own keywords and description via some standard interface, and auto-register without anyone touching `router.py` by hand. This project deliberately doesn't do that — the explicit, four-file registration is the chosen design, not a placeholder for a plugin system that hasn't been built yet.

The tradeoff is real and was discussed directly rather than defaulted into: a true plugin system would mean less code to write per new source, but also more magic — harder to trace what's actually registered and why just by reading `router.py`, and a less obvious place to look when something about routing isn't behaving as expected. Given how much of this project's actual debugging history (see the [Design History](Home#design-history-real-bugs-real-fixes) section) involved tracing exactly *why* a routing decision went the way it did, an explicit, greppable registration list was judged more valuable than the convenience a plugin system would add. New sources cost a few extra lines in known places; in exchange, "what sources exist and how are they configured" stays answerable by reading one file, not by understanding a discovery mechanism on top of it.

## What you get for free once registered

Once a source is in `SOURCE_MAP`, it's automatically available through both the REST API and the [MCP Server](MCP-Server) — there's no separate registration step for either interface, since both ultimately call the same `route()` / `route_with_source()` logic underneath. It's also immediately eligible for [Fusion](Fusion) with any other registered source, since fusion just iterates over whatever source names it's given and calls `SOURCE_MAP[name]` for each — a new source doesn't need to know fusion exists at all to participate in it.

## What you don't get for free

A new source gets none of [Kiwix](Kiwix-Disambiguation)'s disambiguation or [scoring](Kiwix-Scoring) sophistication automatically — that machinery is genuinely specific to Kiwix's particular problem (an offline, fixed-corpus index with no way to verify an LLM's guess about what's actually in it). If your source needs similar relevance ranking, that's new, source-specific work to build, not something the existing architecture hands you. The same is true of [Conditional Query Detection](Conditional-Query-Detection)'s structured yes/no interpretation — a new source isn't automatically eligible for a confident conditional verdict; that's a deliberate, manually-curated allowlist (`ha`, `uptime`, `forecast` today), and extending it to a new source means deciding, explicitly, that the new source has a genuinely structured, binary signal worth trusting a verdict against.

## Proposed modules looking for contributors

See [Contributing](Contributing) for the current list of source modules that have been discussed but not yet built — Plex is the standing example.
