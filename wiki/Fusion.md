# Fusion

Fusion is what happens when [Routing](Routing) decides a question genuinely needs more than one [Source](Sources). It queries every chosen source concurrently, waits for them with a real timeout, filters out anything that failed or came back empty, deduplicates overlapping content, and merges what's left into one coherent response.

## How a fusion query actually runs

```text
   Routing decides: ["kiwix", "web", "news"]
                      │
                      ▼
        Validate & deduplicate source names
        (cap at FUSION_MAX_SOURCES, default 4)
                      │
                      ▼
        Query ALL sources CONCURRENTLY
        (ThreadPoolExecutor, one worker per source)
        Each call bounded by FUSION_TIMEOUT_SECONDS
                      │
                      ▼
        Filter: keep only successful,
        non-empty results
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
      Zero survived      One survived       Two+ survived
            │                   │                   │
            ▼                   ▼                   ▼
  "No results returned   Return it directly,   Deduplicate overlapping
   from any source in    no header — a single  content (60%+ sentence
   fusion query."        source's own answer   overlap = redundant)
                          doesn't need fusion          │
                          framing                      ▼
                                              Truncate each to
                                              FUSION_MAX_CHARS_PER_SOURCE
                                                        │
                                                        ▼
                                              Join with "[SOURCE — LABEL]"
                                              headers and --- separators
                                              (no same-source merge needed
                                              here — see note below)
```

**There's no same-source merge step inside `fusion.search()` itself, even though it might look like there should be one.** `valid` (the source list this function works from) is deduplicated by its own `seen` set before any results are even gathered, so two entries for the same source can never reach this point — there's nothing to merge. The real same-source merge — for the genuinely different case of two *separately decomposed* sub-queries both happening to resolve to the same source — lives in `router.py`'s `_merge_decomposed_parts()`, a different function entirely, covered in detail in the next section.

Concurrency matters here for a real, practical reason: querying `kiwix`, `web`, and `news` one after another would mean waiting for the slowest one three separate times. Running them in a thread pool means the total wait is roughly as long as the *single slowest* source, not the sum of all of them — meaningful when `kiwix` disambiguation or `web`'s query expansion can each independently take a couple seconds on a cold cache.

## Why single-source results skip the header

If only one source actually returned something usable, fusion returns that result directly — no `[KIWIX — ...]` wrapper, no `---` separator. The header exists to stop a multi-section response from being misread as one continuous block (so an LLM consuming the merged text doesn't, say, infer a news article's location applies to the forecast section sitting right next to it). When there's only one section, there's nothing to disambiguate between, so the header would just be visual noise.

## Deduplication

Fusion results sometimes overlap heavily — a Kiwix article and a web search result can both be substantially about the same thing. `_deduplicate()` checks sentence-level overlap between sources and drops one if 60%+ of its sentences already appear, in substance, in a longer result from another source. This keeps fusion responses from repeating the same information twice under two different headers.

This is a different mechanism from the same-source item merging described next — `_deduplicate()` compares *different* sources' results against each other before merging; the same-source merge below combines results that are *already* attributed to the *same* source, after they've survived this step.

## Merging consecutive same-source results

When [Query Decomposition](Query-Decomposition) splits a compound query into independent clauses and routes each one separately, two different clauses can legitimately resolve to the same source — *"indoor air quality and are the doors locked"* sends both halves to `ha`. `fusion._merge_same_source()` combines any consecutive same-source results into one logical block before headers ever get added, so the final answer shows one `[HA — ...]` section, not two redundant ones back to back.

This merge has a real, structural limitation that took three separate, sequential bug fixes to fully close — `_dedupe_nested_fusion_sections()` for cases where the overlap hides behind a `"fusion"` label, and `fusion._dedupe_items_across_blobs()` for cases where the headers correctly merge but the content underneath still repeats. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-same-source-merge-chain-three-bugs-found-in-sequence) for the full investigation — real bugs were found here, in production, and it took three rounds to close the gap completely.

## Configuration

| Setting | Default | What it actually controls |
|---------|---------|----------------------------|
| `FUSION_MAX_SOURCES` | 4 | Hard cap on how many sources one fusion query can touch, regardless of how many routing decided on |
| `FUSION_MAX_CHARS_PER_SOURCE` | 1500 | Per-source truncation before merging — keeps one verbose source from drowning out the others in the final response |
| `FUSION_TIMEOUT_SECONDS` | 15 | How long any single source gets before fusion gives up on it and moves on without it |

See [Confidence-Aware Fusion](Confidence-Aware-Fusion) for how `web` and `news` results are scored *before* they ever reach fusion's deduplication step, and [Multi-Book Fusion](Multi-Book-Fusion) for the Kiwix-specific case of fusing across more than one ZIM book.

---

## Development Notes

- **The `[FUSION — FUSION]` double-header bug.** `fusion.search()` itself never produces a literal `"fusion"` source name as a result label, but a caller merging multiple `route_with_source()` outputs together can — and if it doesn't check for that case, it ends up wrapping already-self-headered content in a second, redundant header. Found and fixed twice, at two separate call sites. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-fusion-fusion-bug-and-why-it-kept-coming-back) for both.
- **The mixed-speed timeout crash.** Pairing one fast source with one slow enough to hit `FUSION_TIMEOUT_SECONDS` used to crash the entire fusion call, discarding the fast source's already-successful result along with it. Fixed; a clean partial success now comes back instead. See [The Fusion Merge Bugs](The-Fusion-Merge-Bugs#the-mixed-speed-timeout-crash) for the mechanism.
