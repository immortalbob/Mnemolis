# The Home Assistant Bulletproofing Pass

Five real bugs found in `app/sources/home_assistant.py` during a deliberate, full read of the file — not in response to a reported failure, but specifically looking for the kind of small, simple-looking code that complexity-score-driven review naturally skips. Two of the five are independent findings; three form a single chain, each one found while fixing the previous.

## "Is the front door locked" silently returned nothing

A real, severe word-boundary bug: the short internal keyword used to detect "lights on"-style queries could match as a substring inside a completely unrelated word in the query — *"front"* contains *"on"*. A query asking specifically about the front door's lock state silently filtered out the exact entity being asked about, because the bare substring match misfired on a word that had nothing to do with lights at all.

The same root cause turned up in area detection too: a bare `"shed"` keyword for matching that area name matched as a substring inside `"finished"`, `"crashed"`, `"washed"` — any query mentioning any of those words risked being misrouted to the wrong area. Fixed in both places with proper word-boundary regex matching instead of a bare substring check.

## A three-bug chain around `binary_sensor`-style motion entities

**Bug one: the second motion convention was never actually wired in.** Home Assistant supports two common ways a motion sensor reports its state — a dedicated `event` domain entity, or a `binary_sensor` with `device_class: motion` (the convention most Zigbee2MQTT/Z-Wave/PIR integrations use). The keywords that trigger a motion lookup only ever listed the `event` domain. Real, existing deduplication logic in the code was specifically built to avoid double-counting a sensor that reports through *both* conventions at once — which only makes sense if both conventions were meant to be reachable. They weren't; the second one had simply never been wired into the actual keyword matching. Fixed by adding `binary_sensor` with `device_class: motion` to the relevant entries.

**Bug two, found immediately while fixing bug one: the dedup check itself was global, not per-entity.** Once `binary_sensor` motion entities actually became reachable, the existing dedup logic suppressed *every* `binary_sensor` motion entity in the house if *any* motion sensor anywhere had event-based data — confirmed this would have silently dropped a real, unrelated, second motion sensor from a "house status" query just because some other, completely different sensor happened to report through both conventions. Fixed to check whether the *specific physical sensor in question* has a genuine event-based counterpart, not whether the set of all such counterparts across the whole house is merely non-empty.

**Bug three, found verifying bug two's fix: a stale label.** `binary_sensor` entities were unconditionally labeled "Door Sensors" in output, regardless of their actual device class — a reasonable shortcut from when `binary_sensor` motion entities weren't reachable at all, but wrong the moment they became reachable. Fixed to label motion-class `binary_sensor` entities "Motion" instead, while confirming genuine door `binary_sensor` entities still correctly keep the "Door Sensors" label.

## A small grammar inconsistency

Relative-time motion event descriptions already correctly handled the singular/plural distinction for hours ("1 hour ago" vs. "2 hours ago") and days, but minutes were overlooked — producing "1 minutes ago." Fixed to match the pattern already established for the other two units.

## Area-scoped queries silently skipped real exclusion filtering

Area-scoped queries (*"indoor air quality in the living room"*) used a separate, simplified filtering implementation that never checked `exclude_entity_keywords` — or several other real filter fields — at all. The identical query *without* an area name correctly excluded raw processor-temperature and ESP32-internal sensor-node entities; the area-scoped version let them back in, since it was running a second, incomplete copy of the filtering logic rather than the real one. Fixed by routing area-scoped queries through the same shared filtering logic every other query already uses, rather than maintaining two copies that could (and did) drift apart.

## The lesson

Four of these five bugs share a shape worth naming: code that was *correct when written*, for the scope that existed at the time, and became wrong only once something else changed around it — a second motion convention got added, dedup logic got built assuming both conventions worked, but the actual wiring lagged behind. None of these were caught by a failing test, because no test exercised the gap between "what the code assumes" and "what's actually reachable" until someone read the file specifically looking for that gap.
