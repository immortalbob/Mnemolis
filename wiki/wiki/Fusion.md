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
                                              Merge consecutive same-
                                              source results into one
                                              block (see below)
                                                        │
                                                        ▼
                                              Join with "[SOURCE — LABEL]"
                                              headers and --- separators
```

Concurrency matters here for a real, practical reason: querying `kiwix`, `web`, and `news` one after another would mean waiting for the slowest one three separate times. Running them in a thread pool means the total wait is roughly as long as the *single slowest* source, not the sum of all of them — meaningful when `kiwix` disambiguation or `web`'s query expansion can each independently take a couple seconds on a cold cache.

## Why single-source results skip the header

If only one source actually returned something usable, fusion returns that result directly — no `[KIWIX — ...]` wrapper, no `---` separator. The header exists to stop a multi-section response from being misread as one continuous block (so an LLM consuming the merged text doesn't, say, infer a news article's location applies to the forecast section sitting right next to it). When there's only one section, there's nothing to disambiguate between, so the header would just be visual noise.

## Deduplication

Fusion results sometimes overlap heavily — a Kiwix article and a web search result can both be substantially about the same thing. `_deduplicate()` checks sentence-level overlap between sources and drops one if 60%+ of its sentences already appear, in substance, in a longer result from another source. This keeps fusion responses from repeating the same information twice under two different headers.

## The `[FUSION — FUSION]` bug, and why it kept coming back

**If you ever saw the literal text `[FUSION — FUSION]` show up as a header inside a response, that's fixed.** It happened because a result that was already internally self-headered (each contributing source has its own `[SOURCE — LABEL]` baked in) got wrapped in *another* header by whatever called it — `fusion.search()` itself never produces a literal `"fusion"` source name; the bug lived entirely in callers of fusion's output.

This happened because `route_with_source()` can itself report `"fusion"` as the overall source for a result that's already internally self-headered. A caller that doesn't check for this and blindly does `f"{_format_header(resolved_source)}\n{result}"` produces a literal `[FUSION — FUSION]` wrapped around content that's already correctly labeled section by section.

This exact bug was found and fixed **twice** in this project's history — once in [Query Decomposition](Query-Decomposition)'s original merge loop, and a second time at a brand-new call site added for [Conditional Query Detection](Conditional-Query-Detection)'s remainder-merging feature, which hadn't existed yet when the first fix shipped. The fix is the same both times: check whether the source being wrapped is literally the string `"fusion"`, and if so, pass the result through unwrapped rather than double-headering it. Worth remembering as a real, recurring footgun any time new code merges multiple `route_with_source()` outputs together — the check has to be applied at every merge site individually, since there's no single chokepoint that catches it automatically.

## The mixed-speed timeout crash

**If a fusion query ever failed completely when it should have at least partially succeeded — say, fusing a fast source with one that was slow or unreachable — that's fixed now.** A real bug meant pairing one quick source with one slow enough to hit the timeout could crash the *entire* fusion call, discarding the fast source's real, already-successful result along with it. Today, the fast source's content comes back correctly, with the slow source logged and excluded — a clean partial success instead of a total failure.

For anyone curious about the mechanism: `concurrent.futures.as_completed(futures, timeout=fusion_timeout)` has its own overall timeout, raised as a `TimeoutError` for the *entire iteration* the moment the deadline passes — a separate mechanism from the per-future timeout already used inside the loop to mark one slow source as failed. The outer one was never caught, so it took down everything gathered so far with it. Found via a careful, deliberate re-read of `search()`, not a failing test (no existing test happened to mix a fast source with a slow one). The fix wraps the iteration in its own `try/except`, marking any future not yet recorded as failed without discarding what had already succeeded.

## Configuration

| Setting | Default | What it actually controls |
|---------|---------|----------------------------|
| `FUSION_MAX_SOURCES` | 4 | Hard cap on how many sources one fusion query can touch, regardless of how many routing decided on |
| `FUSION_MAX_CHARS_PER_SOURCE` | 1500 | Per-source truncation before merging — keeps one verbose source from drowning out the others in the final response |
| `FUSION_TIMEOUT_SECONDS` | 15 | How long any single source gets before fusion gives up on it and moves on without it |

See [Confidence-Aware Fusion](Confidence-Aware-Fusion) for how `web` and `news` results are scored *before* they ever reach fusion's deduplication step, and [Multi-Book Fusion](Multi-Book-Fusion) for the Kiwix-specific case of fusing across more than one ZIM book.
