# Home Assistant Integration

`ha` is a genuinely different kind of [Source](Sources) from the other six — it's the only one answering questions about devices and physical state you directly control, and it specifically targets a category of question Home Assistant's own built-in voice assistant doesn't handle well: analytical, multi-entity summaries rather than single-device commands.

## Setup

1. In Home Assistant, go to your **Profile** (click your username in the sidebar)
2. Scroll to **Long-lived access tokens**
3. Click **Create Token**, give it a name, copy the token
4. Set `HA_URL` to your instance's URL (e.g. `http://192.168.1.100:8123`)
5. Set `HA_TOKEN` to the token you just generated

That's the entire setup — no webhook, no custom component to install in HA itself. Mnemolis reads entity states directly through HA's existing REST API using the token.

## What `ha` actually answers

HA's own built-in conversation/intent system is genuinely good at single-entity commands and lookups — "turn on the kitchen light," "is the front door locked." `ha` exists specifically for the category just above that: questions that need to look across *many* entities at once and summarize, which HA's own intent handling isn't designed to do.

- **"house status summary"** — lights, locks, sensors, motion, batteries, all combined into one overview
- **"indoor air quality"** — CO2, temperature, humidity, specifically from indoor sensors
- **"security status"** — locks, doors, and recent motion with relative timing ("2 hours ago"). Motion is recognized through either of Home Assistant's two common conventions — a dedicated `event` domain entity, or a `binary_sensor` with `device_class: motion` (the convention most Zigbee2MQTT/Z-Wave/PIR integrations use) — so either kind of motion sensor is picked up, not just one.
- **"battery status"** — every device's battery level in one summary, not one lookup per device
- **"outdoor conditions"** — weather *station sensor* readings, distinct from [`forecast`](Sources#forecast-weather)'s predictive data — this is "what does my actual outdoor sensor currently read," not "what will the weather be"
- **"how much power am I using"** — current and historical consumption

## Area awareness

`GET /areas` lists every HA area Mnemolis has detected, along with entity counts and the natural-language aliases it recognizes for each — useful for confirming Mnemolis actually sees your area structure the way you expect, and for understanding what phrasing will correctly scope a query to one part of the house rather than the whole thing.

Scoping a query to an area applies the exact same filtering rules as an unscoped query — the same `exclude_entity_keywords`, device-class, and domain logic, just additionally restricted to that area's entities. *"Indoor air quality in the living room"* excludes the same raw device-telemetry noise (processor temperature sensors, ESP32 internals) that a plain *"indoor air quality"* already excludes, not a separate, looser filter just because an area was specified.

## Fusion and conditional detection

`ha` participates in [Fusion](Fusion) like any other source — "house status and what's the weather" automatically fuses `ha` + `forecast` into one response, no special handling needed for this specific combination.

`ha` is also one of exactly three sources [Conditional Query Detection](Conditional-Query-Detection#honest-abstention-the-actual-point-of-this-feature) trusts for a real, structured yes/no verdict — specifically lock/unlock and door open/closed states. *"If the back door is unlocked, let me know"* gets a genuine, confident answer through `ha`, not just a presented-but-uninterpreted result, because lock and door state is exactly the kind of binary, unambiguous signal that feature was built to recognize.

## Why this exists as a separate source instead of just using HA's own assistant

The honest answer is that HA's own conversation/intent system and Mnemolis's `ha` source are solving genuinely different problems, not competing at the same one. HA's strength is fast, reliable single-device control and lookup — exactly what you want for "turn off the lights," where you don't want an LLM-mediated round trip in between you and the actual device action. `ha`'s strength is the analytical summary case HA's intent system isn't built for at all. Whether to eventually route some voice queries more directly to Mnemolis instead of through HA's own assistant layer — keeping HA for device control and audio I/O specifically — is a real, open architectural question, tracked on the [Roadmap](Roadmap#still-tracked-lower-priority) rather than decided yet.

---

## Development Notes

- **A specific entity question like "is the front door locked" could come back "no matching entities found" even when the entity clearly existed.** A real bug meant short internal keywords (like the one used to detect "lights on" queries) could accidentally match as a substring inside an unrelated word in the query ("front"), silently filtering out the exact entity being asked about. The same root cause could also misidentify which area a query was about (a bare `"shed"` keyword matching inside `"finished"`, `"crashed"`, `"washed"`). Fixed with proper word-boundary regex matching in both places. If something still looks wrong, `GET /areas` and checking the entity's exact name/state in Home Assistant directly are the fastest way to rule out a naming mismatch versus an actual bug.
- **`binary_sensor`-style motion entities were never actually reachable at all, despite real dedup logic existing specifically for them.** The keywords that trigger a motion lookup only ever listed the `event` domain, never `binary_sensor` — so the dedup logic built to avoid double-counting a sensor that reports through both conventions was protecting against a case that, at the time, couldn't happen, because the second convention wasn't wired in yet. Fixed by adding `binary_sensor` with `device_class: motion` to the relevant keyword entries.
- **That same dedup check was global, not per-entity, once it started actually running.** It suppressed *every* `binary_sensor` motion entity in the house if *any* motion sensor anywhere had event-based data — a real bug that would have silently dropped an unrelated, second motion sensor from a "house status" query just because a completely different sensor happened to report through both conventions. Fixed to check whether the *specific* physical sensor in question has a genuine event counterpart, not whether the set of all such counterparts is merely non-empty.
- **`binary_sensor` entities used to be unconditionally labeled "Door Sensors," regardless of their actual device class** — a reasonable shortcut when `binary_sensor` motion entities weren't reachable yet, but wrong once they were. Fixed to label motion-class `binary_sensor`s "Motion."
- **A small grammar inconsistency**: relative-time motion event descriptions correctly handled "1 hour ago" vs. "2 hours ago" and the day equivalent, but minutes were overlooked, producing "1 minutes ago." Fixed to match the established singular/plural pattern.
- **Combining an area name with a query that should exclude device-telemetry noise used to silently skip that exclusion entirely.** Area-scoped queries used a separate, simplified filtering implementation that never checked `exclude_entity_keywords` (or several other real filter fields) at all — so *"indoor air quality in the living room"* let raw processor/ESP32 sensor-node entities back into results, even though the identical query without an area name correctly excluded them. Fixed by routing area-scoped queries through the same shared filtering logic every other query already uses, rather than maintaining a second, incomplete copy of it.
