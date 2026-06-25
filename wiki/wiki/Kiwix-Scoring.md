# Kiwix Scoring

Every candidate result Kiwix returns — whether from a single search or pooled across several [disambiguation](Kiwix-Disambiguation) candidates and, when relevant, several books — gets run through the same scoring function before one is picked as the actual answer. This page documents the exact point values, because "scored against the query" without the real numbers isn't actually verifiable by anyone reading it.

## The full scoring breakdown

| Signal | Points | Condition |
|--------|--------|-----------|
| Exact title match | **+20** | Query and title are identical (case-insensitive, whitespace-trimmed) |
| Stemmed title match | **+15** | The whole query, or any individual meaningful query word, stems to the same root as the title (`"galaxies"` → `"galaxy"` matching a `"Galaxy"` title) |
| Title starts with query term | **+10** | The title begins with a meaningful (4+ letter) query word |
| Per-word title hit | **+5 each** | Each stemmed query word that also appears, stemmed, in the title |
| Per-word excerpt hit | **up to +10 total** | Stemmed overlap between query and excerpt, normalized by excerpt length so a long excerpt doesn't win purely by having more words to coincidentally match |
| List/index article penalty | **−10** | Title starts with `"list of"`, `"lists of"`, `"index of"`, `"outline of"`, or `"category:"` — these are navigation pages, not real content |
| List-article partial offset | **+8 or +3** | Applied *together with* the −10 penalty above, not on its own — +8 if the query is [definitional](Kiwix-Disambiguation#when-this-actually-triggers), +3 otherwise. A list article is still penalized net −2 or net −7, never fully forgiven |
| Primary book bonus | **+2** | The result came from the book the LLM originally selected, not a secondary book pulled in only for disambiguation pooling |

Worth being precise about that "list-article partial offset" line, since the function's own docstring describes it a little misleadingly as a standalone "Wikipedia bonus." In the actual code, that +8/+3 only ever applies *inside* the same conditional branch as the −10 list penalty — it's not a separate, unconditional bonus that applies to every Wikipedia result. A genuine list article still nets a real penalty either way (−2 for definitional queries, −7 for everything else); the offset just keeps a definitional query's inevitable list-article candidates (Wikipedia has a lot of "List of X" pages) from being penalized as harshly as a non-definitional query's would be.

## Why excerpt scoring is normalized, not raw

A raw per-word excerpt match count would systematically favor longer excerpts — more words means more chances to coincidentally overlap with the query, regardless of actual relevance. Dividing hit count by excerpt length (then scaling to a max of 10 points) keeps a short, precisely on-topic excerpt competitive against a long, loosely-related one.

## Stemming, and why it matters here specifically

`_stem()` is a lightweight, rule-based stemmer (strip trailing "s," "es," "ies" with length guards to avoid mangling short words) — not a full linguistic stemming library, since the actual goal is narrow: catch the specific plural/suffix mismatches that would otherwise cost a correct article real points for no good reason. `"what are galaxies"` needs to score well against a title of `"Galaxy"`; without stemming, the singular/plural mismatch would silently cost both the +15 stemmed-match bonus and the +5 per-word title hit, even though the search obviously found the right thing.

A small, explicit exception list (`this`, `less`, `across`, `always`, `towards`) keeps a handful of common, non-plural English words that happen to end in "s" from being incorrectly suffix-stripped — found via a deliberate, precise re-read of the function, with the real-world scoring impact confirmed genuinely minimal before fixing (this function always compares two complete strings against each other, never an isolated stop word for its own sake), but worth closing as a known inaccuracy rather than leaving it.

## How this feeds into the rest of Kiwix's behavior

- [Kiwix Disambiguation](Kiwix-Disambiguation) pools results from multiple candidate search terms and lets this exact scoring function pick the real winner across all of them — disambiguation only generates candidates, it never decides between them directly
- [Multi-Book Fusion](Multi-Book-Fusion) compares each book's *best-scored* result against the overall top score to decide whether more than one book's content is genuinely worth merging together, rather than just picking whichever book the LLM happened to select first

## Where scoring still has a real ceiling

Scoring rewards genuine textual overlap and structural signals (title match, list-article detection) — it has no actual world knowledge of its own. A single ambiguous word with multiple, comparably well-represented senses in your index (astronomy "galaxy" vs. a film called *Galaxy Quest*) can still land on the wrong one if both genuinely score similarly well by these exact criteria. That's an honest, accepted limit of keyword-and-structure scoring, not a bug waiting to be fixed with a slightly different weight.
