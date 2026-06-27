# Kiwix Catalog & Article Fetching

[Kiwix Disambiguation](Kiwix-Disambiguation), [Kiwix Scoring](Kiwix-Scoring), and [Multi-Book Fusion](Multi-Book-Fusion) all assume two things already happened: a real, current list of which books exist, and a way to turn a chosen search result into actual article text. This page covers both — the part of the pipeline the other three pages take for granted.

## Catalog discovery

Mnemolis never hardcodes a book list. At first request (or right after a `POST /catalog/refresh`), it fetches Kiwix's own OPDS catalog feed and builds the list from what's actually there.

```text
   get_books()
        │
        ▼
   Cache already populated?
        │
   ┌────┴────┐
   ▼ yes      ▼ no
 Return    Fetch /catalog/v2/entries
 cached    (start=0, count=10)
 list           │
                ▼
        Parse Atom/OPDS XML,
        extract name/title/summary
        per <entry>
                │
                ▼
        Got a FULL page (10 entries)?
                │
        ┌───────┴───────┐
        ▼ yes            ▼ no (partial or empty)
   Fetch next page    Stop — this was
   (start += 10)       the last page
        │                   │
        └─────────┬─────────┘
                   ▼
         Cache the full list,
         return it
```

`get_books()` is a real cache, not just a memoized function — once populated, it's never re-fetched until something explicitly clears it. `POST /catalog/refresh` is that explicit clear: it empties `_book_cache` and immediately re-fetches, which is the actual, only way Mnemolis learns about a ZIM file added after startup.

**Why pagination matters here**: Kiwix's OPDS endpoint returns 10 entries per page by default, and a real homelab stack with several ZIMs (Wikipedia, multiple Stack Exchange sites, iFixit, DevDocs) can easily exceed that in one page. `get_books()` keeps requesting the next page (`start += 10`) until it gets back fewer than a full page — the honest signal that there's nothing left — rather than assuming a fixed page count or a `has_next` field the feed doesn't actually provide.

**Each page is parsed independently, and a parse failure on any one page doesn't crash the whole fetch** — `_fetch_catalog_page()` catches its own exceptions and returns an empty list on failure, which `get_books()`'s loop reads as "no more pages" and stops there. A genuinely malformed catalog response, a Kiwix instance that's temporarily unreachable mid-fetch, or a real XML parsing error all degrade to "stop here with whatever was already collected" rather than discarding everything gathered so far.

**The full, versioned book name** (`wikipedia_en_all_maxi_2026-02`, not just `Wikipedia`) comes from the entry's own `text/html` link `href`, not its title — Kiwix's title field is the human-readable display name, but every other part of this codebase (search requests, `KIWIX_MAX_BOOKS` selection, multi-book fusion attribution) needs the exact, full versioned identifier to actually address the book.

### A real, deliberate security hardening

The OPDS feed is parsed with `defusedxml.ElementTree`, not the standard library's own `xml.etree.ElementTree`. This isn't a stylistic choice — a static security analysis pass found that the standard library parser is documented as vulnerable to XML entity expansion attacks (the "billion laughs" attack class) on untrusted input, and switched to a drop-in-compatible replacement built specifically to reject that pattern. The realistic threat model here is genuinely contained — this XML comes from `KIWIX_URL`, expected to be your own self-hosted, trusted Kiwix instance, not arbitrary internet content — but the fix was free and applied regardless of how contained the risk actually was.

## Searching a book

Once a book is selected (by keyword match, LLM selection, or the Wikipedia-first fallback when no LLM is configured), `_search_book()` queries Kiwix's own `/search` endpoint and scrapes the HTML result page — Kiwix doesn't expose a structured JSON search API, so this is real HTML parsing, not a clean REST call.

Each result needs a title and a link; an excerpt is optional (some results genuinely don't have one, and a missing `<cite>` tag is handled rather than treated as a parse failure). **One deliberate filter runs on every result**: anything whose URL contains `/questions/tagged/` is dropped. A Stack Exchange tag-listing page (`questions/tagged/python`) is a list of many loosely-related questions, not a focused answer to anything — exactly the kind of result that would otherwise win on raw keyword overlap while being useless as an actual answer.

`KIWIX_SEARCH_LIMIT` (default 15) controls how many results are requested per book per search — raised from an original hardcoded 5 specifically to give [scoring](Kiwix-Scoring) more real candidates to choose from when a common search term collides with several brand-name or homonym results.

## Fetching the actual article

A search result is a title, URL, and maybe a short excerpt — none of that is the article. `_fetch_article()` is what turns the winning result into the text that actually gets returned.

It fetches the real page and strips it down to readable content: scripts, stylesheets, navigation, headers, footers, and any `<table>` are removed outright, and a table-of-contents box is removed separately. Content is then pulled from whichever of several known selectors matches first — Wikipedia's own content div, a generic content-text div, a generic `<article>` tag, a blog-style post-content div, a Stack Exchange question div, falling all the way back to `<body>` if nothing more specific matches. The result is truncated to `KIWIX_ARTICLE_MAX_CHARS` (default 3000) — a per-article limit, distinct from [Fusion](Fusion)'s own `FUSION_MAX_CHARS_PER_SOURCE`, which truncates the already-combined multi-source response *after* fusion, not a single article *before* it.

If the winning result's article genuinely can't be fetched — a broken link, a malformed page, a transient timeout — the response is honest about it rather than silently returning nothing: *"Found {title} but could not fetch article content."*, with the real URL attached, so the failure is visible and actionable rather than indistinguishable from a clean empty result.

---

## Development Notes

Two real bugs were found in this file's article-fetching logic during a deliberate, full read — table-of-contents boxes that were never actually being stripped from any article despite the code's clear intent, and an unbounded retry loop with a genuine multi-minute worst case. See [The Kiwix Bulletproofing Pass](The-Kiwix-Bulletproofing-Pass) for both, and for two related bugs found in the same pass elsewhere in `kiwix.py`.
