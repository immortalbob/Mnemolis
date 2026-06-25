# Snapshot Engine & Changes

The `changes` [Source](Sources) answers a category of question none of the other six can: *"what's different since X"*. That requires actually remembering state over time, which is what the snapshot engine exists to do — a background scheduler periodically captures each source's current state, and a diff engine compares snapshots to surface what genuinely changed.

## The background jobs

Four jobs run on independent schedules, each calling its own snapshot function:

| Job | Interval | What it captures |
|-----|----------|-------------------|
| `snapshot_uptime` | 2 minutes | Current Uptime Kuma monitor states |
| `snapshot_ha` | 5 minutes | Current Home Assistant entity states |
| `snapshot_forecast` | 30 minutes | Current weather forecast |
| `snapshot_news` | 60 minutes | Current RSS feed contents |

Each snapshot is timestamped and stored in a dedicated `snapshots` table. Old entries get pruned per source, and **how much history each source keeps is scaled to how often it's snapshotted** — `uptime` (every 2 minutes) keeps 5040 snapshots, `ha` (every 5 minutes) keeps 2016, `forecast` (every 30 minutes) keeps 336, `news` (every 60 minutes) keeps 168 — so every source genuinely has at least a full week of real history available, regardless of how often it gets snapshotted.

That wasn't always true: a single shared retention count used to apply to every source equally, sized around `ha`'s 5-minute interval. Since `uptime` snapshots far more often, the same count only covered 9.6 real hours for it — meaning a "since yesterday" or "this week" query for service uptime specifically could have silently come back incomplete, even though those same time windows worked correctly for every other source. Found via a deliberate code-reading pass, not a reported failure, and fixed by scaling retention per source instead of sharing one number across all of them.

Every snapshot job already catches its own exceptions internally and just logs a warning on failure — it never crashes, never stops the scheduler, and (until a real gap was found and fixed — see [Health & Observability](Health-and-Observability)) produced zero externally visible signal beyond a log line if it started failing on every single run.

## Net change vs. individual events

Not every source's history should be reported the same way, and the diff engine treats two categories differently on purpose:

**Net-change sources** — `uptime` and `forecast`. These can genuinely "flap": a service can go down and recover within the same reporting window, or a forecast can shift and shift back. Reporting every intermediate blip would be noisy and not reflect current reality, so only the *net* change — first snapshot in the window compared against the last — gets reported. If a service went down and came back up within the window, that's correctly treated as no meaningful change at all.

**Event-based sources** — `news` and `ha`. Every individual event matters independently here — a new article isn't "cancelled out" by another new article, and a door opening at 2pm and closing at 3pm are both real, separately meaningful events, not a round-trip back to a baseline state. These get reported individually, not collapsed to a net comparison.

```text
              get_changes(since_hours=24)
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
       uptime / forecast        news / ha
       (NET_CHANGE_SOURCES)    (event-based)
              │                       │
              ▼                       ▼
     Compare ONLY first vs.    Report EVERY individual
     last snapshot in the      event found across the
     window — intermediate     whole window — nothing
     flapping is discarded     gets collapsed
```

Forecast changes are only reported if the temperature shift exceeds `FORECAST_TEMP_CHANGE_THRESHOLD` (5°F by default) — a meaningless half-degree difference between two snapshots isn't a "change" worth surfacing.

## Time-window phrases

*"What changed this morning"* and *"what happened since I left for work"* need to resolve to an actual hour count before they can be passed to `get_changes(since_hours=N)`. Two configured hours anchor this: `MORNING_START_HOUR` (default 6) and `WORK_START_HOUR` (default 9), both in your local timezone. "This morning" resolves to however many hours have passed since 6am today; "since work" resolves to however many hours have passed since 9am today. Outside of a recognized phrase, `changes` defaults to a flat 24-hour lookback.

This resolution genuinely produces a fractional hour count, not just a round number — "this morning" at, say, 2:36pm with a 6am anchor is 8.6 hours, not a clean 9. `format_changes()` rounds this for display regardless of what's passed in, rather than relying on every caller to round it correctly first — found via a deliberate bulletproofing pass: a real caller (the natural-language path described above) genuinely produces an unrounded float, and without this, a real response could have displayed something like *"no significant changes detected in the last 23.939205609166667 hours"* directly to a user.

## Manually triggering a refresh

`POST /snapshots/trigger` runs all four jobs immediately, useful right after a config change or when testing — there's no need to wait out a job's natural interval to see whether it's working.

## Where this connects to operational maturity

Every job here already had solid error handling for the case "the underlying API call failed" — what it never had was any signal for "the job ran successfully a hundred times in a row, each time fetching nothing useful, because something upstream silently changed shape." That distinction, and the health check built specifically to catch the latter using data the snapshot table already stores, is covered in [Health & Observability](Health-and-Observability).
