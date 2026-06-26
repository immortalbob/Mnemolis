# Confidence-Aware Fusion (Web & News)

`web` and `news` results don't get trusted at face value just because SearXNG or your RSS feed returned them. Every result from either source is scored for relevance against the actual query, ranked, and filtered before it's allowed into a response — the same kind of real, verifiable scoring [Kiwix Scoring](Kiwix-Scoring) applies, but tuned for free-text search results instead of encyclopedia articles.

## The scoring breakdown

| Signal | Points | Notes |
|--------|--------|-------|
| Exact title match | **+15** | Query's meaningful keywords exactly equal the title's meaningful keywords (stop words excluded from both sides) |
| Per-keyword title overlap | **+6 each** | Each stemmed query keyword that also appears, stemmed, in the title |
| Content keyword overlap | **up to +20 total** | Stemmed overlap between query and content, normalized by content length — a short, precisely on-topic snippet competes fairly against a long, loosely-related article |
| Generic/homepage penalty | **−20** | See below |
| Recency bonus | **source-dependent** | Passed in by the caller — `news` computes this from each article's published timestamp; `web` has no equivalent and always passes 0 |

This is the same normalized-content-overlap idea [Kiwix Scoring](Kiwix-Scoring) uses, for the same reason: a raw keyword-hit count would systematically favor longer text, regardless of whether that text is actually more relevant.

Single-letter and single-digit keywords count toward this scoring as long as they're genuinely alphanumeric — a query about "R" the programming language, or "C," keeps that one distinguishing word during keyword extraction, so a result actually about the topic asked for correctly outranks one that isn't, even when the topic itself is a single character. A bare punctuation character on its own still doesn't count.

## The generic-result penalty

A surprising amount of real search noise isn't *wrong*, it's just *not actually an article* — a site's homepage, an "about us" page, a bare category listing. `_is_generic_result()` catches three different shapes of this:

- **Title is a known generic label** — things that read like a site name rather than article content
- **Content reads like a site description** — matches a small set of known boilerplate phrasing patterns
- **The URL is a bare domain root with suspiciously short content** — no path beyond a trailing slash, with any query string stripped before that check runs (so a tracking parameter like `?utm_source=twitter` on an otherwise bare homepage doesn't make it look like it has a real article path), and under 40 characters of actual text. A real article almost always has a path (`/article/some-slug`) and more than a sentence fragment of content; a landing page often has neither.

Any one of these triggers a flat −20 penalty, generally enough to push a generic result below anything genuinely on-topic without needing three separate penalty tiers.

## Recency — why news has it and web doesn't

News articles genuinely get less useful the older they are for anything time-sensitive, in a way a general web search result usually doesn't (a well-written reference article from two years ago can still be exactly right; a news article from two years ago about "today's" anything usually isn't). `news` computes a tiered bonus from each article's actual published timestamp:

| Published within | Bonus |
|-------------------|-------|
| Last hour | +15 |
| Last 6 hours | +10 |
| Last 24 hours | +5 |
| Older than 24 hours | +0 |

`web` has no equivalent signal to compute this from (SearXNG doesn't reliably expose publish dates across every engine it aggregates) and always passes a flat 0.

## Filtering and ranking

After every result is scored, `filter_and_rank()` sorts by score descending, drops anything at or below `WEB_NEWS_SCORE_THRESHOLD` (default 0 — so anything net-neutral-or-worse is dropped, not just anything explicitly negative), and caps the survivors at `WEB_NEWS_TOP_N` (default 10). The function returns the original result dicts unmodified — no score gets attached to what's returned, since scoring is purely an internal ranking and filtering decision, not part of the response itself.

## Deduplication across URL variants

Two URLs that are really the same page — `https://www.example.com/page/` and `http://example.com/page` — shouldn't be counted as two separate results just because of a scheme, a `www.` prefix, a trailing slash, or a tracking query string. `normalize_url()` strips all of that before comparing, specifically for deduplication purposes — it's a deliberately lossy normalization, not a real fetchable URL, so it's never used for anything except "are these the same underlying page."

## Where this fits into the bigger picture

This scoring runs *before* a result is even eligible to participate in [Fusion](Fusion)'s cross-source merge — a `news` or `web` result still has to clear this bar before fusion's own deduplication and truncation logic ever sees it. For `web` specifically, scoring also has to account for results coming from two different searches at once — see [Query Expansion](Query-Expansion) for why, and how the same scoring function handles a doubled result pool without favoring one search's results over the other's just because of which one ran first.

---

## Development Notes

- **Single-letter and single-digit keywords used to be dropped entirely during keyword extraction.** A query about "R" the programming language, or "C," lost the one word that actually distinguished it from an unrelated result, since both sides were then only being compared on generic shared words — for a real query like "tutorial for the c programming language," a correct "C Programming Language" result could score *lower* than an unrelated "JavaScript Programming Language" one. Fixed by keeping a single character when it's genuinely alphanumeric, rather than simply allowing any single character through — a bare punctuation mark (a stray hyphen in "C++ vs C#", say) still doesn't count as a keyword on its own.
- **A tracking query string used to defeat the generic-homepage detection.** `?utm_source=twitter` on an otherwise bare homepage made the URL look like it had a real article path, letting an obvious landing page slip past the −20 generic-result penalty. Fixed by stripping the query string before the path check runs.
