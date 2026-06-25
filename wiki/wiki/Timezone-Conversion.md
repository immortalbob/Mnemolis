# Timezone Conversion

Every database timestamp Mnemolis writes — `query_log.db`, `snapshots.db`, `adversarial_testing.db`, `temporal_patterns.db` — is stored as UTC, confirmed directly across every one of those write paths. This is the right way to store a timestamp, and it's not changing. But it leaves a real gap: nothing in this project could convert one of those stored UTC timestamps back into the person's actual local time, which matters the moment any feature needs to ask "what local hour of day was this," not just "how long ago was this."

## The gap this closes

Mnemolis already has a real, working notion of local time — `_hours_since()` (`app/router.py`) resolves phrases like "this morning" and "while at work" using `datetime.now()`, which is driven entirely by the container's own `TZ` environment variable (see the README's [Timezone configuration](https://github.com/immortalbob/Mnemolis#timezone-configuration) section). That's correct, and it's been correct for a long time. But it has no connection at all to anything in `app/config.py`, and nothing else in the codebase could read a *stored* timestamp and ask "what was the local hour when this was written" — only "how many hours have elapsed since some local hour today," a different question.

Without this, any future feature mining `query_log` for time-of-day patterns would have had to either invent its own, third, independent way of answering "what time is it for this person," or — much worse — silently bucket by the raw UTC hour already sitting in the stored timestamp, which is only correct if the deployment happens to be physically located in the UTC timezone. For Mnemolis's own real reference deployment (Kingman, AZ — `America/Phoenix`, UTC-7, no DST), that mistake would silently shift every single time-of-day bucket by exactly 7 hours, forever, with no error or warning anywhere to catch it.

## How it works

`LOCAL_TIMEZONE` names the same timezone concept `_hours_since()` already implicitly depends on. By default, it reads the exact same `TZ` environment variable — so a deployment that's already correctly set `TZ` per the README gets this conversion for free, at zero new configuration cost. If you specifically want this conversion to use a *different* zone than your container's own `TZ`, set `LOCAL_TIMEZONE` explicitly; it always takes priority.

The actual conversion lives in `app/timeutil.py`, built on Python's standard-library `zoneinfo` — real, calendar-aware conversion, not a naive fixed offset. This matters concretely: a fixed-offset approach would get half the year right and the other half wrong for any timezone that observes Daylight Saving Time. `zoneinfo` handles the transition automatically, confirmed directly with a dedicated test pair: the same `America/New_York` deployment converts UTC noon to 7:00 AM local in January (EST, UTC-5) and 8:00 AM local in July (EDT, UTC-4) — both correct, with no special-casing needed anywhere in the calling code.

## What it's for

This isn't a user-facing feature on its own — it's the shared groundwork two not-yet-built design docs (Predictive Pre-Fetching with Confidence Calibration, Self-Healing Source Selection Through Reinforcement) both independently identified as a real, common dependency during their own research: any feature that wants to learn "you usually ask this around 7am" or "you tend to ask this on weekday mornings" needs this conversion to be right, or its conclusions are built on a silently wrong foundation. Built once, here, rather than risking two separate features each getting their own version of this slightly different — or slightly wrong.

## If you typo your timezone

An invalid `TZ`/`LOCAL_TIMEZONE` value (e.g. a typo like `Amercia/New_York`) falls back to UTC, with a real, visible warning logged — it does not crash anything. The same defensive judgment already applied to `MORNING_START_HOUR`/`WORK_START_HOUR` (an out-of-range hour like `24` is wrapped, not rejected) applies here: a plausible deployment mistake should degrade gracefully, not take anything down.
