# Contributing

## Proposed source modules

These are real, specific ideas for new [Sources](Sources) — not a generic "contributions welcome," but actual gaps in what Mnemolis can currently answer, looking for someone to build them:

- **Jellyfin** — search local media library by title, genre, or actor
- **Paperless-ngx** — search scanned documents and OCR'd content
- **Mealie** — search self-hosted recipe library
- **Grocy** — query pantry inventory, shopping list, or expiring items
- **Calibre** — search local ebook library
- **Navidrome** — search self-hosted music library by artist, album, or track
- **Immich** — search local photo library by date, album, or description

Plex has come up repeatedly in project discussion as well, for the same reason as the others above — a self-hosted media/data service that fits the exact shape `search(query: str) -> str` is built for.

Each of these needs only the same single function every existing source already implements. See [Adding a New Source](Adding-a-New-Source) for the actual registration steps and the reasoning behind why they're explicit rather than auto-discovered, and any existing file in `app/sources/` for a concrete reference implementation to copy the shape of.

## What a good PR looks like here

This project has a real, demonstrated testing culture — not as a formality, but because it's repeatedly found genuine bugs that looked fine on first read. A few concrete expectations that follow from that, worth knowing before opening a PR:

**Test against real, messy phrasing, not just the clean case.** Several of the bugs documented in [Design History](Home#design-history-real-bugs-real-fixes) were only found by deliberately constructing harder, more realistic queries than whatever had been tested before — casual phrasing, compound sentences, queries that combine more than one feature at once. A new source or feature that only gets tested against its own simplest, cleanest example is the same gap that let five separate bugs hide in the [proper-noun-pair guard](The-Proper-Noun-Pair-Saga) before anyone tried a query hard enough, or read the code carefully enough, to expose them.

**Verify against real production data, not just mocked unit tests.** A passing test suite is necessary but has repeatedly not been sufficient on its own in this project's actual history — several fixes that looked complete after the test suite passed turned out to have a second, real issue only visible against live data (see [The Discourse-Framing Investigation](The-Discourse-Framing-Investigation) and [The SearXNG Timeout Lesson](The-SearXNG-Timeout-Lesson) for two direct examples of this).

**If something doesn't work the way you expect, trace the actual cause before assuming you know it.** More than one bug in this project's history was diagnosed correctly only after adding real debug tracing and reading the actual output, rather than guessing at the cause from a plausible-sounding theory (see [The Recursion Design Bug](The-Recursion-Design-Bug) for a direct example — the bug's real cause was different from, and simpler to fix than, the first theory about it).

**Be honest about what a fix doesn't solve.** Several pages in this wiki explicitly document a fix's accepted limitations alongside its success — [Kiwix Disambiguation](Kiwix-Disambiguation#a-genuine-accepted-limitation) and the [Benchmarks](Benchmarks#two-honest-unresolved-findings-from-the-most-recent-run) page's two unexplained anomalies are both left in the record on purpose, rather than smoothed over. A PR description that says "this handles X and Y, but I haven't verified Z" is more useful and more trustworthy than one that implies complete coverage.

## A practical starting point

If you're building a new source and want a template for the kind of error handling and structure the rest of this project follows, `app/sources/uptime_kuma.py` is the smallest, simplest complete example in the codebase — under 100 lines, no scoring or disambiguation complexity, just a clean connect-fetch-format pattern. `app/sources/home_assistant.py` is a larger but still good reference for a source with genuinely distinct query *categories* (the analytical queries described in [Home Assistant Integration](Home-Assistant-Integration)) rather than one single search behavior — useful if your new source needs to recognize different kinds of questions and answer each differently, the way `ha` does.
