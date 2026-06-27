# The Temporal Pattern Detection Development Bugs

Three real bugs found while building [Cross-Source Temporal Pattern Detection](Cross-Source-Temporal-Pattern-Detection), all caught before the feature ever ran against real production data — two during deliberate, harder-than-the-obvious-case testing, one during a later review pass.

## The non-overlapping occurrence counter undercounted real, distinct pairs

The counter that decides "how many times did B genuinely follow A within the lag window" passed every simple test thrown at it first — a single A→B pair, a pair outside the lag window correctly excluded. It failed a harder, more realistic scenario constructed deliberately to stress it: three A's followed by three B's, every one mutually within the lag window, with three genuinely distinct, non-overlapping pairs expected. It returned one.

The cause: once an A claimed the nearest available B, the scan position was advanced to just past that claimed B — correct on its own, since it stopped that same B from being claimed a second time. But advancing the scan position also silently skipped over every other, genuinely unclaimed A sitting between the claiming A and the B it had just claimed, so those A's never got their own chance to find a B at all. Fixed by tracking *which B's have already been claimed* in a separate set, and scanning every A exactly once regardless of what any earlier A in the same pass had already claimed — two genuinely independent guarantees the function needs, that the original implementation had accidentally coupled together through one shared scan position.

Caught by deliberately constructing a harder test case before this code ever ran against real data, not by a failure in production.

## Uptime event classification misclassified both of its own non-outage messages

`_diff_uptime()`'s own recovery message ("All services restored — previously reported outage **resolved**") and its own pending message ("Service check pending — possible **outage** starting") both genuinely contain the literal substring `"outage"`. An early version of the event classifier checked for `"outage"` before checking for the more specific `"pending"`/`"restored"` phrasing, which meant both of these — a recovery and a pending check — were misclassified as plain outage events, the opposite of what each message was actually reporting.

Fixed by matching each message against its own distinct, unambiguous leading phrase first, rather than checking a generic substring that more than one real message type happens to share. The same general shape of bug this project has found more than once elsewhere: a broad, early-running check matching something a later, more specific check was supposed to claim first.

## The feature's own headline example was never actually testable in the version that first shipped

This feature's motivating example — does a door event reliably precede a motion event — depends on motion, window, and door/opening-sensor events all being extractable from real snapshots. `snapshot_ha()` itself always correctly captured all of those device classes. The comparison logic that turns a pair of raw snapshots into typed events, however, never had a branch for any of them beyond locks and doors specifically — meaning a real motion "off"→"on" transition produced an empty event list, confirmed directly. The one example used throughout this feature's own design and documentation to motivate why it exists was, in the first shipped version, structurally incapable of ever firing.

Caught by review, not by a failing test — nothing in the existing test suite happened to exercise a motion-only snapshot pair, so nothing failed; the gap was only visible by reading the comparison logic directly and checking it against every device class the feature's own documentation claimed to support.

Fixed by extending the shared comparison core with the missing branches. **A second, related bug was found immediately while fixing the first**: `extract_ha_events()`'s own event-type labeling logic only ever distinguished "lock or door" from "everything else, assumed to be a battery event" — a two-way catch-all that worked fine when only two real categories existed, but silently mislabeled the newly-added motion and window event kinds as `:battery_low` the moment a third and fourth category actually existed. Fixed by making every kind explicit rather than relying on a catch-all that had quietly stopped being exhaustive.

## The lesson

Two of these three were caught specifically by testing a case harder than the one that motivated the code in the first place — three overlapping pairs instead of one, a message that happens to share a substring with a different category, rather than two cleanly distinct messages. The third was caught only by checking the code against the feature's own stated promise, not against what its existing tests happened to already cover — a reminder that a test suite only verifies what someone thought to test, and a feature's own headline example is worth checking directly, not just trusting because nothing failed.
