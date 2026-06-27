# The Snapshot Engine Bulletproofing Pass

Five real bugs found in the snapshot/diff engine during a deliberate review — the same kind of pass that found similar chains in [Home Assistant](The-Home-Assistant-Bulletproofing-Pass) and [Kiwix](The-Kiwix-Bulletproofing-Pass). All five are independent findings, not a chain.

## A single shared retention count meant `uptime` only kept 9.6 hours of real history

Snapshot retention used to be one shared count across every source, sized around `ha`'s 5-minute snapshot interval. `uptime` snapshots far more often — every 2 minutes — so the same fixed count that gave `ha` a full week of history only covered 9.6 real hours for `uptime` specifically. A "since yesterday" or "this week" query about service uptime could silently come back incomplete, missing real history that should have still been there, with no error or warning of any kind. Found via a deliberate code-reading pass, not a reported failure. Fixed by scaling retention per source's own actual snapshot interval, so every source genuinely keeps at least a full week of history regardless of how often it gets snapshotted.

## An unrounded fractional hour could reach a user-facing message

`format_changes()` could receive a real, unrounded fractional-hour value and pass it straight through into a message a person would actually read. A real caller — the natural-language time-window resolution path (*"since this morning"*, *"while I was at work"*) — genuinely produces a value like `23.939205609166667`, not a clean number; time-window math doesn't naturally land on round numbers. Fixed by always rounding for display inside `format_changes()` itself, regardless of what precision the caller happens to pass in.

## A pending or retrying service got the same alarming wording as a confirmed outage

Uptime Kuma's own status model treats a confirmed "down" state and a "pending"/retrying state as genuinely distinct — a service that's failed one check and is retrying isn't the same thing as a service that's confirmed down. The diff engine's wording didn't make this distinction, reporting both with the same "service outage detected" message. Fixed by checking for the literal "down" state explicitly, giving a pending-only transition its own, honestly-worded message instead of borrowing the more alarming one. A mixed reporting window containing both a confirmed outage and a separate pending service correctly keeps the more severe wording, since that's the more accurate description of what's actually happening.

## Sub-zero temperatures silently broke change detection entirely

`forecast`'s temperature-change detection relies on regexes to extract numeric values from forecast text. Those regexes had no support for a negative sign at all — meaning any genuinely cold deployment, anywhere a forecast could plausibly go below zero, would never detect a temperature change at all. Not a degraded or partial result — change detection for temperature silently stopped working completely, with no error or warning anywhere to suggest why. Fixed by allowing an optional negative sign in both extraction regexes.

## One malformed entity could crash the diff for every other entity in the same snapshot

Home Assistant entity diffing used direct bracket-notation access to a `state` field that isn't actually guaranteed to be present — a single entity missing that field raised an uncaught `KeyError` that took down the diff for the *entire* snapshot, not just the one malformed entity. Not reachable through the current snapshot writer, which always populates the field — but snapshots persist in a long-lived database table, and a snapshot written under an older schema version could plausibly be read back without it. Fixed by skipping any entity missing the required field rather than crashing on it, so one bad row degrades gracefully instead of taking the rest of a real diff down with it.

## The lesson

None of these five needed a failing test to be visible once looked for directly — each one is a real, traceable gap between what the code assumes (every source snapshots at the same rate; every fractional value is already rounded; every temperature is non-negative; every stored entity has every expected field) and what's actually true once enough real time, real weather, or real schema history passes. A deliberate read specifically looking for these assumptions, rather than waiting for one of them to fail in a way someone would notice and report, is what found all five.
