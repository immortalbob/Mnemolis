# Cross-Source Temporal Pattern Detection

A background job, on the same `apscheduler` infrastructure the [snapshot engine](Snapshot-Engine-and-Changes) and [Adversarial Self-Testing](Adversarial-Self-Testing) already run on, that looks for reliable timing relationships between structured event types — does a front-door lock event reliably precede a motion event within some lag window, does an HA event reliably precede a service outage — and reports anything that survives a real statistical bar as a candidate, **never** a causal claim.

This was originally the roadmap's "🔬 Speculative" entry. It's no longer speculative — it's built, tested, and running. The honest framing that made it speculative in the first place hasn't gone anywhere, though: this page exists specifically to keep that framing in front of anyone reading a result, not just in the design notes that led to it.

## The one hard rule

**Correlation, even statistically corrected, non-spurious correlation, is not causation, and this feature never claims otherwise.** Every pattern this feature reports — `candidate`, `confirmed`, or `unconfirmed` — carries the same literal note on every single row: *"This reflects observed timing correlation only and does not establish a causal relationship."* Not a documentation footnote. A real field on every real API response.

This isn't caution for its own sake. Real, peer-reviewed temporal pattern-mining methods show genuinely high false-positive rates at data volumes far above anything a homelab will produce — a 2010 paper measuring two algorithms specifically built to limit spurious results (Raajay, Sastry, Unnikrishnan, [arxiv.org/pdf/1006.1543](https://arxiv.org/pdf/1006.1543)) found 15–48% false-positive rates at 50,000–200,000 events. Mnemolis's own most active source (`ha`, the busiest structured one) produces real events in the tens to low hundreds per month, not tens of thousands. Every design decision below — the fixed lag window, the per-comparison correction, the hard minimum-occurrence floor, the mandatory out-of-sample re-check before anything is called "confirmed" — exists to keep this feature honest about what it can and can't actually claim at that real data volume, not to make it look more rigorous than it is.

## Scope — deliberately narrow

This first version covers exactly two sources: **`ha`-internal** event pairs (a lock, door, or battery-low transition compared against another) and **`ha`-to-coarse-`uptime`** pairs (any `ha` event against a source-level outage/recovery/pending signal from Uptime Kuma).

`forecast` and `news` are explicitly **not** covered yet. Both sources' snapshots are free text today, not structured data — extracting a clean, enumerable event type from "Today will be rainy with a high of about 90" requires real, separate groundwork (a fixed taxonomy of weather-shift events; per-monitor uptime tracking, since today's uptime snapshot is already collapsed to "something, somewhere, is down" before this feature ever sees it) that wasn't worth building before the core statistical machinery had been validated against data that's already clean and available. The roadmap's own second example — weather reliably preceding a service hiccup — is the harder, more speculative half of the original idea; this version deliberately solves the easier, better-grounded half first.

## What counts as an "event"

A fixed, small, enumerable type per entity — not "any string difference between two snapshots," which would be statistically meaningless to test for correlation against:

| Source | Event type shape | Example |
|---|---|---|
| `ha` | `{entity_id}:{state}` for locks and doors, `{entity_id}:battery_low` for battery crossings | `lock.front_door:unlocked`, `binary_sensor.back_door:opened`, `sensor.lock_battery:battery_low` |
| `uptime` | A coarse, source-level signal — no per-monitor detail yet | `uptime:outage`, `uptime:recovery`, `uptime:pending` |

Battery events use a fixed `:battery_low` suffix rather than the literal percentage on purpose — encoding the real number would make every low-battery event for the same sensor register as a *different*, never-repeating event type, since the percentage is different every time, which would make pattern mining against it meaningless.

HA event extraction shares its actual comparison logic directly with [`_diff_ha()`](Snapshot-Engine-and-Changes) — the same function that already produces the free-text "what changed" output for `GET /changes` — rather than re-implementing the same entity comparison a second time or regex-parsing `_diff_ha()`'s own already-formatted sentences. One real source of truth for "what counts as a meaningful HA change," not two that could quietly drift apart from each other the way `router.py` and `fusion.py`'s `_looks_empty()` phrase list once did.

## The mining procedure

Runs once daily by default (`TEMPORAL_PATTERN_MINING_INTERVAL_HOURS`), deliberately far less often than the snapshot engine or [Adversarial Self-Testing](Adversarial-Self-Testing) — pattern mining over a short window is statistically meaningless (there's nothing real to find yet) and wasteful to re-run constantly.

```text
              Mining cycle tick (default: once/24h)
                            │
                            ▼
              Extract structured events from every
              consecutive ha/uptime snapshot pair
              since the last run (8-day lookback,
              deduplicated after the fact)
                            │
                            ▼
              Re-validate any CANDIDATE whose
              discovery window has already closed
              (see "Out-of-sample validation" below)
                            │
                            ▼
              Every distinct (A, B) pair that has
              ACTUALLY occurred in this window
              (not the full theoretical combinatorial
               space of every entity that could exist)
                            │
                            ▼
              For each pair: count non-overlapping
              "B within the lag window after A"
              occurrences
                            │
                ┌───────────┴───────────┐
                ▼ below min_occurrences   ▼ at/above floor
        Never even tested —        Compute expected count
        not enough data to         under the null hypothesis
        say anything, full stop    (each type's own real,
                                    observed base rate —
                                    not an assumed uniform one)
                                            │
                                            ▼
                                  Bonferroni-correct the
                                  significance threshold
                                  across EVERY (A, B) pair
                                  tested this pass
                                            │
                                  ┌─────────┴─────────┐
                                  ▼ not significant     ▼ significant
                            Discarded,              New CANDIDATE,
                            not stored               awaiting validation
```

### Non-overlapping counting

Once a real occurrence of B has been claimed as the match for some A, that same B can never be claimed again by a different A. Without this, a burst of 3 A's followed by 3 B's all within the lag window would inflate the apparent count to 9 (every combination) instead of the real, honest answer: 3 distinct pairs. This directly follows the frequent-episode-mining framework's own non-overlapping-occurrence convention, and it's also where a real bug was found and fixed during this feature's own development — see [the design history note](#a-real-bug-found-during-development) below.

### Multiple-comparisons correction

Testing many `(event type A, event type B)` combinations without correction produces spurious "discoveries" as a mathematical near-certainty, independent of data quality — a study on uncorrected hypothesis testing found that just 4 uncorrected comparisons alone produced a 15% false-positive rate, almost exactly matching plain probability (`1 - 0.95^4 ≈ 18.5%`). Mnemolis applies a straightforward Bonferroni correction: the per-comparison significance threshold (default α = 0.05) gets divided by the total number of pairs actually tested in that pass before any candidate gets compared against it. Bonferroni is conservative by design — the right tradeoff for a feature explicitly framed as low-stakes pattern-mining, not a rigorous scientific claim.

### The hard minimum-occurrence floor

`TEMPORAL_PATTERN_MIN_OCCURRENCES` (default 5) is checked *before* any significance test runs at all. A pair with 2–3 raw occurrences isn't a pattern yet no matter what the math around it would say — the honest truth at that count is simply that there isn't enough data, and no amount of statistical correction changes that.

## Out-of-sample validation

A pattern found once isn't validated — it's just been described. Every `candidate` gets mechanically re-checked against a **later, non-overlapping window** of new data (`TEMPORAL_PATTERN_VALIDATION_WINDOW_HOURS`, default 24h, starting exactly where the discovery window closed) before it can ever be called `confirmed`.

```text
   CANDIDATE found in window W1
                │
                ▼
   Has W1's own validation window
   (W2, starting where W1 ended)
   already closed?
                │
        ┌───────┴───────┐
        ▼ not yet         ▼ yes
   Left untouched    Re-run the SAME comparison
   this cycle        against W2's real data,
                      using W1's own corrected
                      threshold (not a freshly
                      recomputed one — the question
                      being asked is "does this EXACT
                      finding replicate," not "would
                      this also be found fresh today")
                                │
                      ┌─────────┴─────────┐
                      ▼ replicates          ▼ doesn't
              status → CONFIRMED    status → UNCONFIRMED
                                     (kept, never deleted —
                                      a real, honest finding
                                      in its own right)
```

A pattern that fails to replicate is recorded as `unconfirmed`, not silently discarded — the same "status changes, history doesn't disappear" philosophy [Adversarial Self-Testing](Adversarial-Self-Testing#a-bug-this-feature-found-in-itself-before-it-ever-ran-in-production)'s `dismiss` mechanism already established. A genuinely informative "this looked real once but didn't hold up" is worth keeping visible, not cleaning away.

## What gets surfaced, and where

`GET /temporal-patterns` returns every pattern, optionally filtered by `?status=candidate|confirmed|unconfirmed`. Each row includes the two event types, the lag window, the real raw occurrence count, the corrected significance threshold it was compared against, which window(s) it was validated against, and — on every single row, unconditionally — the correlation-not-causation note.

This feature deliberately lives **only** in its own dedicated endpoint. It is never blended into `GET /changes` or a normal search response, even if a pattern is confirmed. A correlation-not-causation caveat is too easy to lose once folded into an ordinary conversational answer; keeping this fully separate means anyone looking at a result is looking at it in the one context built specifically to carry that caveat correctly.

`POST /temporal-patterns/trigger` runs one mining cycle immediately rather than waiting for the next scheduled tick — same pattern as `/snapshots/trigger` and `/adversarial/trigger`.

## Health reporting

`/health`'s `temporal_pattern_detection` field follows the same shape `snapshot_jobs` and `adversarial_testing` already use, with one genuinely new status worth knowing about:

- **`disabled`** — `TEMPORAL_PATTERN_DETECTION_ENABLED=false`. Reported directly, not as an eventual `stale`, the same reasoning Adversarial Self-Testing's own off-switch already established.
- **`never_ran`** — the job hasn't fired yet.
- **`insufficient_data`** — the job ran correctly, but the real event volume in its most recent window was itself below `TEMPORAL_PATTERN_MIN_OCCURRENCES` — there genuinely wasn't enough data to consider even one pair. This is the honest, **expected** state for the first weeks of this feature's life on any real deployment, given the real event volumes discussed above. It is not a bug, and it's deliberately distinct from `ok` (which means the job ran against a real, meaningful amount of data and is reporting a genuine result, even if that result is "found nothing significant").
- **`stale`** — more than `TEMPORAL_PATTERN_STALE_GRACE_MULTIPLIER`× the mining interval has passed since the last successful run.
- **`ok`** — ran recently, against a real amount of data.

If `/health` shows `insufficient_data` for weeks, that's this feature working exactly as designed, not a sign anything is wrong — see "How long this actually needs to run" below.

## Configuration

| Setting | Default | What it actually controls |
|---|---|---|
| `TEMPORAL_PATTERN_DETECTION_ENABLED` | `true` | Master on/off switch, checked at both scheduler-registration time and inside the cycle function itself (defense in depth) |
| `TEMPORAL_PATTERN_MINING_INTERVAL_HOURS` | `24` | How often the mining cycle runs |
| `TEMPORAL_PATTERN_LAG_WINDOW_MINUTES` | `30` | The maximum lag within which B must follow A to count as one real occurrence |
| `TEMPORAL_PATTERN_MIN_OCCURRENCES` | `5` | The hard floor below which a pair is never even tested, regardless of significance |
| `TEMPORAL_PATTERN_SIGNIFICANCE_LEVEL` | `0.05` | The per-comparison α, before Bonferroni correction divides it by the number of pairs tested |
| `TEMPORAL_PATTERN_VALIDATION_WINDOW_HOURS` | `24` | How much later, non-overlapping data a candidate needs before it can be promoted to confirmed |
| `TEMPORAL_PATTERN_STALE_GRACE_MULTIPLIER` | `3` | Same role as `SNAPSHOT_STALE_GRACE_MULTIPLIER` — how many missed intervals before `/health` calls this job stale |

## How long this actually needs to run before it can say anything

Given the real event volumes discussed above, this feature may genuinely need **weeks** of real accumulated history before `TEMPORAL_PATTERN_MIN_OCCURRENCES` is satisfied for any pair at all, even on `ha`, the densest available source. That's not a flaw to work around — it's the honest cost of insisting on a real statistical floor instead of reporting something premature just because a cycle ran. `insufficient_data` exists specifically so this is visible and unambiguous rather than discovered with surprise later.

## If, after a real, meaningful run, nothing is ever found

That's success, not failure — exactly the original roadmap entry's own framing, and worth restating here because it's easy to read a "0 candidates" result as the feature not working. [Adversarial Self-Testing](Adversarial-Self-Testing)'s own first real run came back "8/8, zero flags" and was correctly treated as a clean, successful result. A `temporal_pattern_detection` summary that stays at `ok` with zero candidates after weeks of real data is the equally legitimate, equally honest outcome here — the roadmap entry that proposed this feature said outright that "finding nothing beyond noise" was an acceptable, informative result, not a consolation prize.

## A real bug found during development

Worth recording in the same spirit as the project's other [Design History](Home#design-history-real-bugs-real-fixes) pages: the non-overlapping occurrence counter's first draft passed every simple test (a single A→B pair, an out-of-range pair) but failed a harder, more realistic one constructed deliberately to stress it — 3 A's followed by 3 B's, all mutually within the lag window, expected to count as 3 distinct pairs. It returned 1. The cause: once an A claimed a distant B, the scan position was advanced to just past that B — which correctly stopped that B from being claimed twice, but also silently skipped over every genuine, not-yet-evaluated A sitting between the claiming A and the B it claimed. Fixed by tracking *which B's have already been claimed* in a separate set, and scanning every A exactly once regardless of what any earlier A claimed — the two guarantees this function actually needs, kept independent of each other. Caught by deliberately testing a harder scenario before this ever ran against real data, the same discipline this whole project's [testing culture](Contributing#what-a-good-pr-looks-like-here) is built around.

A second, smaller bug surfaced the same way in uptime event classification: `_diff_uptime()`'s own recovery message ("All services restored — previously reported outage **resolved**") and its own pending message ("Service check pending — possible **outage** starting") both genuinely contain the literal substring `"outage"`, so an early version that checked for `"outage"` before checking for the more specific `"pending"`/`"restored"` phrases misclassified both as plain outages. Fixed by matching each message's own distinct, unambiguous leading phrase instead of a substring more than one real message type happens to share.

## Where this connects to everything else

Built on the [snapshot engine](Snapshot-Engine-and-Changes)'s existing retention and storage, but writes to its own, separate database (`temporal_patterns.db`) — never touches `cache.json`, `routing_cache.json`, `query_log.db`, `snapshots.db`, or `adversarial_testing.db`. Included in [`GET /backup`](Backup-and-Restore)'s file list. Runs on the same scheduler infrastructure as [Adversarial Self-Testing](Adversarial-Self-Testing), following the same defense-in-depth enable switch and `/health` reporting conventions that feature established.
