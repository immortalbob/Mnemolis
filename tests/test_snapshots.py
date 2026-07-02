"""
Tests for app/snapshots.py — snapshot engine diff logic.
Tests diff functions directly without requiring a running scheduler or DB.
"""


class TestDiffUptime:
    """Tests for _diff_uptime service status change detection."""

    def setup_method(self):
        from app.snapshots import _diff_uptime
        self.diff = _diff_uptime

    def test_no_change_when_identical(self):
        result = self.diff("All 15 services are up.", "All 15 services are up.")
        assert result == []

    def test_no_change_both_all_up(self):
        result = self.diff("All 15 monitored services are up.", "All 14 monitored services are up.")
        assert result == []

    def test_detects_outage(self):
        old = "All 15 monitored services are up."
        new = "1 service is down: Ollama"
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("outage" in c.lower() or "down" in c.lower() for c in changes)

    def test_detects_recovery(self):
        old = "1 service is down: Ollama"
        new = "All 15 monitored services are up."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("restor" in c.lower() or "up" in c.lower() for c in changes)

    def test_detects_different_outage_state(self):
        old = "1 service is down: Ollama"
        new = "2 services are down: Ollama, FreshRSS"
        changes = self.diff(old, new)
        assert len(changes) > 0

    def test_pending_only_transition_uses_accurate_wording_not_outage(self):
        """Regression test for a real bug found via a deliberate
        complexity/correctness investigation: the previous version
        labeled ANY non-"all up" transition with the same alarming
        "Service outage detected" wording, including a PENDING-only
        transition. Uptime Kuma's own status model treats "pending"
        (status code 2, typically a retry/grace period) as distinct
        from a confirmed outage (status code 0, "down") — using "outage
        detected" (a confirmed-outage claim) for a pending-only state is
        a real, misleading overclaim. The fix's actual wording
        ("possible outage starting") deliberately still contains the
        word "outage" as a softer hedge, so this checks for the
        confirmed-sounding "outage detected" phrase specifically, not a
        blanket absence of the word "outage" anywhere in the message."""
        old = "All 15 monitored services are up."
        new = "PENDING (1): SomeService. 14 of 15 services are up."
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "pending" in changes[0].lower()
        assert "outage detected" not in changes[0].lower()

    def test_confirmed_down_transition_still_uses_outage_wording(self):
        """Confirms the fix didn't accidentally soften the wording for a
        GENUINE confirmed outage — only the pending-only case should get
        the gentler phrasing."""
        old = "All 15 monitored services are up."
        new = "DOWN (1): Ollama. 14 of 15 services are up."
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "outage" in changes[0].lower()

    def test_mixed_down_and_pending_keeps_more_severe_outage_wording(self):
        """A real state Uptime Kuma can genuinely report — some services
        confirmed down, others pending — should keep the more severe,
        accurate "outage" wording rather than being downgraded to the
        gentler "pending" phrasing just because a pending service is
        also present."""
        old = "All 15 monitored services are up."
        new = "DOWN (1): Postgres. PENDING (1): Redis. 13 of 15 services are up."
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "outage" in changes[0].lower()


class TestDiffForecast:
    """Tests for _diff_forecast weather change detection."""

    def setup_method(self):
        from app.snapshots import _diff_forecast
        self.diff = _diff_forecast

    def test_no_change_when_identical(self):
        forecast = "Today will be clear with a high of about 96 and a low of 76."
        assert self.diff(forecast, forecast) == []

    def test_detects_negative_temperature_change(self):
        """Regression test for a real bug found via a deliberate
        complexity/correctness investigation: the extraction regexes had
        no support for a negative sign at all, silently returning None
        for any sub-zero forecast text — meaning temperature-change
        detection would quietly stop working entirely for any deployment
        in a genuinely cold climate. Forecast text comes directly from
        round(Open-Meteo's temperature data) with no floor applied, so a
        negative value is a real, reachable case for Mnemolis's
        explicitly anywhere-deployable design, not a contrived edge case."""
        old = "Today will be clear with a high of about 20 and a low of -5."
        new = "Today will be clear with a high of about 20 and a low of -15."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("low" in c.lower() for c in changes)

    def test_negative_high_temperature_also_extracted_correctly(self):
        old = "Today will be clear with a high of about -2 and a low of -10."
        new = "Today will be clear with a high of about 8 and a low of -10."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("high" in c.lower() for c in changes)

    def test_detects_high_temp_increase(self):
        old = "Today will be clear with a high of about 80 and a low of 60."
        new = "Today will be clear with a high of about 90 and a low of 60."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("high" in c.lower() and "up" in c.lower() for c in changes)

    def test_detects_high_temp_decrease(self):
        old = "Today will be clear with a high of about 95 and a low of 70."
        new = "Today will be clear with a high of about 85 and a low of 70."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("high" in c.lower() and "down" in c.lower() for c in changes)

    def test_ignores_small_temp_change(self):
        old = "Today will be clear with a high of about 95 and a low of 70."
        new = "Today will be clear with a high of about 97 and a low of 70."
        changes = self.diff(old, new)
        assert changes == []

    def test_detects_precipitation_appearing(self):
        old = "Today will be clear with a high of about 80."
        new = "Today will be rainy with a high of about 80."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("precipitation" in c.lower() or "rain" in c.lower() for c in changes)

    def test_detects_precipitation_disappearing(self):
        old = "Today will be rainy with a high of about 80."
        new = "Today will be clear with a high of about 80."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("precipitation" in c.lower() for c in changes)

    def test_detects_low_temp_change(self):
        old = "Today will be clear with a high of about 90 and a low of 60."
        new = "Today will be clear with a high of about 90 and a low of 70."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("low" in c.lower() for c in changes)

    def test_detects_high_temp_change_when_old_high_is_exactly_zero(self):
        """Regression test for a real bug found one step past the
        existing negative-temperature fix above: `if old_high and
        new_high` is a truthiness check, and 0.0 is exactly as falsy
        in Python as None is — meaning a forecast high of exactly zero
        degrees was silently indistinguishable from "couldn't extract
        a value at all", so a real, large temperature change starting
        from a 0° day never registered. Confirmed directly before this
        fix: a high changing from 0° to 15° (a real 15-degree swing,
        well above any sane threshold) produced zero detected changes.
        0° is an entirely ordinary winter temperature for a real
        deployment somewhere genuinely cold — the same "deployable
        anywhere" reasoning behind the negative-sign fix, one
        truthiness check further downstream from where that fix
        looked."""
        old = "Today will be clear with a high of about 0 and a low of -5."
        new = "Today will be clear with a high of about 15 and a low of -5."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("high" in c.lower() and "up" in c.lower() for c in changes)

    def test_detects_high_temp_change_when_new_high_is_exactly_zero(self):
        """The opposite direction of the same bug — the NEW value being
        exactly zero must also still register a real change."""
        old = "Today will be clear with a high of about 20 and a low of -5."
        new = "Today will be clear with a high of about 0 and a low of -5."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("high" in c.lower() and "down" in c.lower() for c in changes)

    def test_detects_low_temp_change_when_old_low_is_exactly_zero(self):
        """The same zero-truthiness bug, confirmed for the low-temperature
        check too — both checks shared the identical `if x and y` shape."""
        old = "Today will be clear with a high of about 90 and a low of 0."
        new = "Today will be clear with a high of about 90 and a low of -10."
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("low" in c.lower() for c in changes)

    def test_no_false_positive_when_both_high_values_are_genuinely_zero(self):
        """Confirms the fix didn't overcorrect — two genuinely identical
        zero-degree readings must still report no change, the same as
        any other identical pair of readings would."""
        old = "Today will be clear with a high of about 0 and a low of -10."
        new = "Today will be clear with a high of about 0 and a low of -10."
        changes = self.diff(old, new)
        assert changes == []


class TestDiffNews:
    """Tests for _diff_news new article detection."""

    def setup_method(self):
        from app.snapshots import _diff_news
        self.diff = _diff_news

    def _make_news(self, headlines: list[str]) -> str:
        parts = []
        for h in headlines:
            parts.append(f"**{h}** (World)\nSome article content here.")
            parts.append("---")
        return "\n\n".join(parts)

    def test_no_change_when_identical(self):
        news = self._make_news(["Article One", "Article Two"])
        assert self.diff(news, news) == []

    def test_detects_new_article(self):
        old = self._make_news(["Article One", "Article Two"])
        new = self._make_news(["Article One", "Article Two", "Article Three"])
        changes = self.diff(old, new)
        assert len(changes) > 0
        assert any("Article Three" in c for c in changes)

    def test_ignores_removed_articles(self):
        old = self._make_news(["Article One", "Article Two", "Article Three"])
        new = self._make_news(["Article One", "Article Two"])
        changes = self.diff(old, new)
        assert changes == []

    def test_no_duplicate_changes(self):
        old = self._make_news(["Article One"])
        new = self._make_news(["Article One", "New Story"])
        changes = self.diff(old, new)
        assert len([c for c in changes if "New Story" in c]) == 1

    def test_bare_closing_headline_with_no_suffix_still_detected(self):
        """Regression test confirming a deliberate simplification found
        via a "bulletproofing" pass: extract_headlines() used to have
        two branches — one for "**headline**" with nothing after the
        closing **, one for "**headline** (source)" with a trailing
        suffix. Confirmed the first branch was genuinely unreachable
        through any real freshrss.py output (every real format string
        always has a parenthetical suffix) AND redundant — the second
        branch's own .index()-based logic already correctly finds the
        closing ** regardless of what follows it. Simplified to one
        branch; this test confirms the simplified version still
        correctly handles the bare-closing case even though it's not
        reachable through real output today."""
        old = "**Old Headline**\nSome content."
        new = "**Old Headline**\nSome content.\n\n---\n\n**New Bare Headline**\nMore content."
        changes = self.diff(old, new)
        assert any("New Bare Headline" in c for c in changes)

    def test_caps_at_five_new_stories(self):
        old = self._make_news([])
        new = self._make_news([f"Story {i}" for i in range(10)])
        changes = self.diff(old, new)
        assert len(changes) <= 5


class TestConfigurableSnapshotThresholds:
    """Tests for configurable temp-change and battery-low thresholds."""

    def setup_method(self):
        from app.config import settings
        self._orig_temp = settings.forecast_temp_change_threshold
        self._orig_battery = settings.battery_low_threshold_pct

    def teardown_method(self):
        from app.config import settings
        settings.forecast_temp_change_threshold = self._orig_temp
        settings.battery_low_threshold_pct = self._orig_battery

    def test_custom_temp_threshold_higher_suppresses_change(self):
        from app.snapshots import _diff_forecast
        from app.config import settings
        settings.forecast_temp_change_threshold = 10.0
        old = "Today will be clear with a high of about 90."
        new = "Today will be clear with a high of about 95."  # only 5° diff
        changes = _diff_forecast(old, new)
        assert changes == []

    def test_custom_temp_threshold_lower_detects_change(self):
        from app.snapshots import _diff_forecast
        from app.config import settings
        settings.forecast_temp_change_threshold = 2.0
        old = "Today will be clear with a high of about 90."
        new = "Today will be clear with a high of about 93."  # 3° diff
        changes = _diff_forecast(old, new)
        assert len(changes) > 0

    def test_custom_battery_threshold_higher_catches_earlier(self):
        from app.snapshots import _diff_ha
        from app.config import settings
        import json
        settings.battery_low_threshold_pct = 50.0
        old = json.dumps([{"entity_id": "sensor.b1", "state": "60", "friendly_name": "B1", "device_class": "battery"}])
        new = json.dumps([{"entity_id": "sensor.b1", "state": "40", "friendly_name": "B1", "device_class": "battery"}])
        changes = _diff_ha(old, new)
        assert len(changes) == 1

    def test_custom_battery_threshold_lower_misses_earlier_drop(self):
        from app.snapshots import _diff_ha
        from app.config import settings
        import json
        settings.battery_low_threshold_pct = 10.0
        old = json.dumps([{"entity_id": "sensor.b1", "state": "60", "friendly_name": "B1", "device_class": "battery"}])
        new = json.dumps([{"entity_id": "sensor.b1", "state": "40", "friendly_name": "B1", "device_class": "battery"}])
        changes = _diff_ha(old, new)
        assert changes == []


class TestDiffHA:
    """Tests for _diff_ha entity state change detection."""

    def setup_method(self):
        from app.snapshots import _diff_ha
        self.diff = _diff_ha

    def _entity(self, entity_id, state, friendly_name=None, device_class=""):
        return {
            "entity_id": entity_id,
            "state": state,
            "friendly_name": friendly_name or entity_id,
            "device_class": device_class,
        }

    def _snapshot(self, entities):
        import json
        return json.dumps(entities)

    def test_no_change_when_identical(self):
        snap = self._snapshot([self._entity("lock.front_door", "locked", "Front Door")])
        assert self.diff(snap, snap) == []

    def test_entity_missing_state_field_is_skipped_not_crashed(self):
        """Regression test for a real bug found via a deliberate
        complexity/correctness investigation: directly accessing
        old_e["state"]/new_e["state"] with bracket notation raised an
        uncaught KeyError if either entity was missing that field —
        crashing the diff for every OTHER entity in the same snapshot
        too, not just the malformed one. snapshot_ha() itself always
        writes a "state" field today, so this specific scenario isn't
        reachable through the current writer — but snapshots persist in
        a long-lived SQLite file and get read back potentially much
        later, so data written by an older version of this code (or
        before a future schema change) could genuinely still exist."""
        import json
        old = json.dumps([{"entity_id": "lock.front_door"}])  # missing "state" entirely
        new = json.dumps([{"entity_id": "lock.front_door", "state": "locked"}])
        changes = self.diff(old, new)  # must not raise
        assert changes == []

    def test_one_malformed_entity_does_not_prevent_others_from_being_diffed(self):
        """Confirms the fix's actual real-world value — a single
        malformed entity shouldn't take down the diff for every other,
        well-formed entity in the same snapshot."""
        import json
        old = json.dumps([
            {"entity_id": "lock.front_door"},  # malformed — missing "state"
            {"entity_id": "lock.back_door", "state": "locked", "friendly_name": "Back Door"},
        ])
        new = json.dumps([
            {"entity_id": "lock.front_door", "state": "locked"},
            {"entity_id": "lock.back_door", "state": "unlocked", "friendly_name": "Back Door"},
        ])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "Back Door" in changes[0]
        assert "unlocked" in changes[0]

    def test_detects_lock_unlocked(self):
        old = self._snapshot([self._entity("lock.front_door", "locked", "Front Door")])
        new = self._snapshot([self._entity("lock.front_door", "unlocked", "Front Door")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "Front Door" in changes[0]
        assert "unlocked" in changes[0]

    def test_detects_lock_locked(self):
        old = self._snapshot([self._entity("lock.front_door", "unlocked", "Front Door")])
        new = self._snapshot([self._entity("lock.front_door", "locked", "Front Door")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "locked" in changes[0]

    def test_detects_door_opened(self):
        old = self._snapshot([self._entity("binary_sensor.front_door", "off", "Front Door", "door")])
        new = self._snapshot([self._entity("binary_sensor.front_door", "on", "Front Door", "door")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "opened" in changes[0]

    def test_detects_door_closed(self):
        old = self._snapshot([self._entity("binary_sensor.front_door", "on", "Front Door", "door")])
        new = self._snapshot([self._entity("binary_sensor.front_door", "off", "Front Door", "door")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "closed" in changes[0]

    def test_detects_window_opened(self):
        """Regression test for a real gap found via review: snapshot_ha()
        already captures window-class binary sensors, but no branch
        here ever diffed them — a real window transition silently
        produced zero events before this fix, both from _diff_ha()'s
        own free-text output and from app/temporal_patterns.py's
        extract_ha_events(), which is built directly on this same
        comparison core."""
        old = self._snapshot([self._entity("binary_sensor.kitchen_window", "off", "Kitchen Window", "window")])
        new = self._snapshot([self._entity("binary_sensor.kitchen_window", "on", "Kitchen Window", "window")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "opened" in changes[0]

    def test_detects_motion_starting(self):
        """Regression test for the same real gap as the window test
        above, specifically for motion — the design doc's own headline
        example ("does a front-door lock event reliably precede a
        motion event") was never actually testable before this fix,
        confirmed directly: a real motion "off" -> "on" transition
        produced an empty event list."""
        old = self._snapshot([self._entity("binary_sensor.hallway_motion", "off", "Hallway Motion", "motion")])
        new = self._snapshot([self._entity("binary_sensor.hallway_motion", "on", "Hallway Motion", "motion")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "motion detected" in changes[0]

    def test_motion_stopping_produces_no_event(self):
        """Only the "off" -> "on" detection edge is meaningful — the
        reverse transition is the sensor settling back to its resting
        state, not a new, independently meaningful occurrence."""
        old = self._snapshot([self._entity("binary_sensor.hallway_motion", "on", "Hallway Motion", "motion")])
        new = self._snapshot([self._entity("binary_sensor.hallway_motion", "off", "Hallway Motion", "motion")])
        changes = self.diff(old, new)
        assert changes == []

    def test_detects_battery_crossing_below_20(self):
        old = self._snapshot([self._entity("sensor.lock_battery", "25", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "15", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "low" in changes[0].lower()
        assert "15" in changes[0]

    def test_ignores_battery_already_below_20(self):
        old = self._snapshot([self._entity("sensor.lock_battery", "15", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "10", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert changes == []

    def test_ignores_battery_staying_above_20(self):
        old = self._snapshot([self._entity("sensor.lock_battery", "90", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "85", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert changes == []

    def test_battery_at_exactly_threshold_on_both_sides_is_not_low(self):
        """The default threshold (20%) check is `old_val >= threshold and
        new_val < threshold` — a battery sitting exactly AT the threshold
        on both snapshots is, by this convention, "not yet low" rather
        than "already low", since old_val >= 20 is satisfied but
        new_val < 20 is not. Never directly tested before — the existing
        tests all use values comfortably away from the literal boundary
        (25->15, 15->10, 90->85)."""
        old = self._snapshot([self._entity("sensor.lock_battery", "20", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "20", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert changes == []

    def test_battery_landing_exactly_on_threshold_from_above_is_not_yet_low(self):
        """A battery dropping from just above the threshold to exactly
        the threshold (21 -> 20) has NOT yet crossed below it, by the
        same `< threshold` convention — confirms the boundary is
        correctly exclusive on the low side, not inclusive."""
        old = self._snapshot([self._entity("sensor.lock_battery", "21", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "20", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert changes == []

    def test_battery_crossing_from_exactly_threshold_to_just_below_fires(self):
        """The genuine crossing case immediately adjacent to the two
        boundary tests above: starting exactly AT the threshold (still
        "not low" per old_val >= threshold) and dropping to just one
        point below it (now genuinely < threshold) must fire — this is
        the real crossing event the threshold exists to catch, tested
        here at the tightest possible margin rather than a comfortable
        distance away from the boundary."""
        old = self._snapshot([self._entity("sensor.lock_battery", "20", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "19", "Lock Battery", "battery")])
        changes = self.diff(old, new)
        assert len(changes) == 1
        assert "19" in changes[0]

    def test_ignores_lights(self):
        old = self._snapshot([self._entity("light.bedroom", "off", "Bedroom Light")])
        new = self._snapshot([self._entity("light.bedroom", "on", "Bedroom Light")])
        changes = self.diff(old, new)
        assert changes == []

    def test_ignores_new_entity(self):
        old = self._snapshot([])
        new = self._snapshot([self._entity("lock.new_lock", "locked", "New Lock")])
        changes = self.diff(old, new)
        assert changes == []

    def test_handles_malformed_json_gracefully(self):
        changes = self.diff("not json", "also not json")
        assert changes == []

    def test_multiple_changes_detected(self):
        old = self._snapshot([
            self._entity("lock.front_door", "locked", "Front Door"),
            self._entity("binary_sensor.back_door", "off", "Back Door", "door"),
        ])
        new = self._snapshot([
            self._entity("lock.front_door", "unlocked", "Front Door"),
            self._entity("binary_sensor.back_door", "on", "Back Door", "door"),
        ])
        changes = self.diff(old, new)
        assert len(changes) == 2


class TestGetChangesNetCollapsing:
    """Tests for get_changes net-change collapsing behavior on flapping sources."""

    def setup_method(self):
        import tempfile
        from unittest.mock import patch
        self.temp_db_fixture = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db = self.temp_db_fixture.name
        self.temp_db_fixture.close()
        self.patcher = patch("app.snapshots.SNAPSHOT_DB", self.temp_db)
        self.patcher.start()
        from app.snapshots import init_snapshot_db
        init_snapshot_db()

    def teardown_method(self):
        import os
        self.patcher.stop()
        os.unlink(self.temp_db)

    def _insert_snapshot(self, source, content, timestamp):
        from app.snapshots import _connect, SNAPSHOT_DB
        con = _connect(SNAPSHOT_DB)
        con.execute(
            "INSERT INTO snapshots (timestamp, source, content) VALUES (?, ?, ?)",
            (timestamp, source, content)
        )
        con.commit()
        con.close()

    def _ago(self, minutes_ago: int) -> str:
        """Return an ISO timestamp `minutes_ago` minutes before now, so test
        data stays valid regardless of when the suite actually runs — these
        tests previously used hardcoded absolute dates that silently expired
        once real time passed the since_hours=24 window relative to them."""
        from datetime import datetime, timedelta, timezone
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_uptime_flapping_collapses_to_net_change(self):
        from app.snapshots import get_changes
        # Flaps down then back up — net change should be empty (back to baseline)
        self._insert_snapshot("uptime", "All 15 services are up.", self._ago(120))
        self._insert_snapshot("uptime", "1 service is down: Ollama", self._ago(90))
        self._insert_snapshot("uptime", "All 15 services are up.", self._ago(60))
        changes = get_changes(since_hours=24)
        # Net change: first vs last is identical, so no change reported
        assert "uptime" not in changes

    def test_uptime_real_outage_reports_net_change(self):
        from app.snapshots import get_changes
        # Starts up, ends down — real net change should be reported
        self._insert_snapshot("uptime", "All 15 services are up.", self._ago(120))
        self._insert_snapshot("uptime", "1 service is down: Ollama", self._ago(90))
        self._insert_snapshot("uptime", "1 service is down: Ollama", self._ago(60))
        changes = get_changes(since_hours=24)
        assert "uptime" in changes
        assert len(changes["uptime"]) >= 1

    def test_forecast_flapping_collapses_to_net_change(self):
        from app.snapshots import get_changes
        # Precipitation appears then disappears — net change should be empty
        self._insert_snapshot("forecast", "Today will be clear with a high of about 90.", self._ago(120))
        self._insert_snapshot("forecast", "Today will be rainy with a high of about 90.", self._ago(90))
        self._insert_snapshot("forecast", "Today will be clear with a high of about 90.", self._ago(60))
        changes = get_changes(since_hours=24)
        assert "forecast" not in changes

    def test_forecast_real_change_reports_net(self):
        from app.snapshots import get_changes
        self._insert_snapshot("forecast", "Today will be clear with a high of about 80.", self._ago(120))
        self._insert_snapshot("forecast", "Today will be clear with a high of about 95.", self._ago(60))
        changes = get_changes(since_hours=24)
        assert "forecast" in changes

    def test_news_reports_every_event_not_net(self):
        from app.snapshots import get_changes
        self._insert_snapshot("news", "**Story A** (World)\nContent.\n---", self._ago(120))
        self._insert_snapshot("news", "**Story A** (World)\nContent.\n---\n\n**Story B** (World)\nContent.\n---", self._ago(90))
        self._insert_snapshot("news", "**Story A** (World)\nContent.\n---\n\n**Story B** (World)\nContent.\n---\n\n**Story C** (World)\nContent.\n---", self._ago(60))
        changes = get_changes(since_hours=24)
        assert "news" in changes
        # Both Story B and Story C should be reported as individual events
        assert len(changes["news"]) == 2


class TestFormatChanges:
    """Tests for format_changes output formatting."""

    def setup_method(self):
        from app.snapshots import format_changes
        self.fmt = format_changes

    def test_empty_changes_returns_no_changes_message(self):
        result = self.fmt({})
        assert "no significant changes" in result.lower()

    def test_includes_since_hours_in_no_changes(self):
        result = self.fmt({}, since_hours=12)
        assert "12" in result

    def test_unrounded_float_since_hours_is_displayed_rounded(self):
        """Regression test for a real, user-facing presentation bug
        found via a deliberate "bulletproofing" pass: this function's
        own type signature (int | float) explicitly invites a raw
        float, and a real caller (router.py's _search_changes(), for
        "this morning"-style natural-language resolution) genuinely
        produces one. Without defensive rounding, a real user could
        see "in the last 23.939205609166667 hours" displayed directly.
        Both of this function's current real callers happen to already
        avoid the problem, so this wasn't reachable today — fixed
        anyway, since formatting a number reasonably for display is
        this function's own job, not every present and future caller's."""
        result = self.fmt({}, since_hours=23.939205609166667)
        assert "23.939205609166667" not in result
        assert "23.9" in result

    def test_formats_uptime_changes(self):
        changes = {"uptime": [{"timestamp": "2026-06-18T12:00:00Z", "change": "Outage detected"}]}
        result = self.fmt(changes)
        assert "Services" in result
        assert "Outage detected" in result

    def test_formats_forecast_changes(self):
        changes = {"forecast": [{"timestamp": "2026-06-18T12:00:00Z", "change": "High temp up to 99°"}]}
        result = self.fmt(changes)
        assert "Weather" in result
        assert "High temp up to 99°" in result

    def test_formats_news_changes(self):
        changes = {"news": [{"timestamp": "2026-06-18T12:00:00Z", "change": "New article: Big Story"}]}
        result = self.fmt(changes)
        assert "News" in result
        assert "Big Story" in result

    def test_includes_timestamp(self):
        changes = {"uptime": [{"timestamp": "2026-06-18T12:00:00Z", "change": "Outage"}]}
        result = self.fmt(changes)
        assert "UTC" in result


class TestSnapshotJobHealth:
    """Tests for get_snapshot_job_health() — reports each background
    snapshot job's health by comparing its most recent successful
    snapshot timestamp against its expected interval.

    Found via real review, not a reported failure: every snapshot job
    (snapshot_uptime, snapshot_forecast, snapshot_news, snapshot_ha)
    already catches its own exceptions internally and just logs a
    warning — meaning a job that started failing on every single run
    would never crash, never stop the scheduler, and produce zero
    externally visible signal beyond a log line nobody is necessarily
    watching. The scheduler object itself also has no external
    visibility at all (a local variable inside main.py's lifespan
    context manager, never exposed to any endpoint), so there was
    previously no way to ask "is the background scheduler actually
    still running and succeeding" without reading raw application logs.
    """

    def setup_method(self):
        import tempfile
        from unittest.mock import patch
        self.temp_db_fixture = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db = self.temp_db_fixture.name
        self.temp_db_fixture.close()
        self.patcher = patch("app.snapshots.SNAPSHOT_DB", self.temp_db)
        self.patcher.start()
        from app.snapshots import init_snapshot_db
        init_snapshot_db()

    def teardown_method(self):
        import os
        self.patcher.stop()
        os.unlink(self.temp_db)

    def _insert_snapshot(self, source, content, timestamp):
        from app.snapshots import _connect, SNAPSHOT_DB
        con = _connect(SNAPSHOT_DB)
        con.execute(
            "INSERT INTO snapshots (timestamp, source, content) VALUES (?, ?, ?)",
            (timestamp, source, content)
        )
        con.commit()
        con.close()

    def _ago(self, minutes_ago: int) -> str:
        """Return an ISO timestamp `minutes_ago` minutes before now — see
        the identical helper in TestGetChangesNetCollapsing above for why
        relative timestamps matter here: hardcoded absolute dates in a
        staleness check would silently break the moment real time passed
        whatever window was hardcoded against them."""
        from datetime import datetime, timedelta, timezone
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_recent_snapshot_reports_ok(self):
        from app.snapshots import get_snapshot_job_health
        self._insert_snapshot("uptime", "All services up.", self._ago(1))
        health = get_snapshot_job_health()
        assert health["uptime"]["status"] == "ok"

    def test_never_ran_reports_never_ran(self):
        """A source with zero snapshots ever stored — either the job
        hasn't run yet shortly after startup, or it has never once
        succeeded — must be distinguishable from a genuinely stale job
        that DID succeed at some point but has since stopped."""
        from app.snapshots import get_snapshot_job_health
        health = get_snapshot_job_health()
        assert health["forecast"]["status"] == "never_ran"

    def test_stale_snapshot_reports_stale(self):
        """A job whose last successful snapshot is well past its
        expected interval (beyond the grace multiplier) must be flagged
        as stale — this is the actual real gap found during review:
        every snapshot job already catches its own exceptions and just
        logs a warning, so a genuinely stuck job was previously
        completely invisible outside of raw logs."""
        from app.snapshots import get_snapshot_job_health
        # news has a 60-minute interval; 4 hours (240 min) is well past
        # even a generous 3x grace window (180 min)
        self._insert_snapshot("news", "Old headlines.", self._ago(240))
        health = get_snapshot_job_health()
        assert health["news"]["status"] == "stale"
        assert health["news"]["minutes_since_last_snapshot"] >= 239

    def test_slightly_late_snapshot_is_not_flagged_stale(self):
        """Normal jitter (job execution time, a slightly delayed
        scheduler tick) must not trigger a false alarm — the grace
        multiplier exists specifically to absorb this."""
        from app.snapshots import get_snapshot_job_health
        # uptime has a 2-minute interval; 4 minutes late is normal jitter,
        # well within the 3x grace window (6 minutes)
        self._insert_snapshot("uptime", "All up.", self._ago(4))
        health = get_snapshot_job_health()
        assert health["uptime"]["status"] == "ok"

    def test_all_four_jobs_are_reported(self):
        """Every job the real scheduler actually runs must appear in the
        health report, even if some have never produced a snapshot —
        a missing key would be just as bad as a wrong status."""
        from app.snapshots import get_snapshot_job_health
        health = get_snapshot_job_health()
        for source in ["uptime", "forecast", "news", "ha"]:
            assert source in health

    def test_each_job_reports_its_correct_expected_interval(self):
        """Confirm the reported interval actually matches what main.py's
        real scheduler.add_job() calls use — a mismatch here would mean
        this health check is silently checking against the wrong
        expectation for at least one job."""
        from app.snapshots import get_snapshot_job_health
        health = get_snapshot_job_health()
        assert health["uptime"]["expected_interval_minutes"] == 2
        assert health["forecast"]["expected_interval_minutes"] == 30
        assert health["news"]["expected_interval_minutes"] == 60
        assert health["ha"]["expected_interval_minutes"] == 5

    def test_unparseable_timestamp_reports_unknown_not_a_crash(self):
        """A corrupted or unexpected timestamp format must degrade
        gracefully to 'unknown' status, never raise an exception that
        would take down the whole /health endpoint over one bad row."""
        from app.snapshots import get_snapshot_job_health
        self._insert_snapshot("ha", "content", "not-a-valid-timestamp")
        health = get_snapshot_job_health()
        assert health["ha"]["status"] == "unknown"


class TestPerSourceRetention:
    """Regression tests for a real, significant bug found via a
    deliberate "bulletproofing" pass: a single, shared
    MAX_SNAPSHOTS_PER_SOURCE = 288 constant was applied identically to
    every source, with a comment claiming "24 hours at 5-minute
    intervals" — true only for `ha` specifically. `uptime` (snapshotted
    every 2 minutes, the most frequent of any source) only retained 9.6
    real hours of data under that shared constant, while
    _resolve_changes_hours() in router.py explicitly supports "since
    yesterday" (48h) and "this week" (168h) as real, documented
    time-window phrases — a query for either would silently return an
    incomplete picture for uptime specifically, missing most of the
    requested window with no indication the underlying data simply no
    longer existed. Fixed by scaling retention per-source from each
    source's real snapshot interval, so every source genuinely supports
    a full week."""

    def test_retention_scales_inversely_with_snapshot_frequency(self):
        """The more frequently a source is snapshotted, the more rows
        it needs to cover the same real time window — confirms the
        actual real, computed retention values for each source."""
        from app.snapshots import _RETENTION_PER_SOURCE
        assert _RETENTION_PER_SOURCE["uptime"] == 5040   # every 2min, needs the most rows
        assert _RETENTION_PER_SOURCE["forecast"] == 336  # every 30min
        assert _RETENTION_PER_SOURCE["news"] == 168      # every 60min
        assert _RETENTION_PER_SOURCE["ha"] == 2016        # every 5min

    def test_every_source_supports_a_full_week_of_real_data(self):
        """The actual, concrete real-world guarantee this fix provides:
        every source's retention, multiplied by its own real interval,
        genuinely covers at least 168 hours (one week) — the longest
        documented time-window phrase _resolve_changes_hours() supports."""
        from app.snapshots import _RETENTION_PER_SOURCE, JOB_INTERVALS_MINUTES
        for source, retention in _RETENTION_PER_SOURCE.items():
            interval = JOB_INTERVALS_MINUTES[source]
            hours_covered = (retention * interval) / 60
            assert hours_covered >= 168, f"{source} only covers {hours_covered}h, needs >= 168h"

    def test_uptime_genuinely_retains_a_week_of_snapshots_in_practice(self, tmp_path):
        """The actual, real, end-to-end regression test — confirms a
        real database, pruned via the real _store_snapshot() pruning
        logic, genuinely retains enough uptime snapshots to answer a
        "since yesterday" (48h) query, which the old shared constant
        could not do (it retained only 9.6 real hours)."""
        from unittest.mock import patch
        from app.snapshots import _store_snapshot, init_snapshot_db, _get_snapshots_since
        from datetime import datetime, timezone, timedelta

        temp_db = str(tmp_path / "test_snapshots.db")
        with patch("app.snapshots.SNAPSHOT_DB", temp_db):
            init_snapshot_db()
            # Simulate a real, sustained deployment: more than a full
            # day's worth of real uptime snapshots at the real 2-minute
            # interval, written through the actual pruning logic
            import sqlite3
            con = sqlite3.connect(temp_db)
            now = datetime.now(timezone.utc)
            for i in range(800):  # well beyond the old 288-row limit
                ts = (now - timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
                con.execute(
                    "INSERT INTO snapshots (timestamp, source, content) VALUES (?, 'uptime', ?)",
                    (ts, f"snapshot {i}")
                )
            con.commit()
            con.close()
            # Trigger the real pruning logic via one more real write
            _store_snapshot("uptime", "final snapshot")

            # A real "since yesterday" (48h) query should find real data
            # spanning the genuinely requested window, not just whatever
            # fraction of it happened to survive an undersized prune
            results = _get_snapshots_since("uptime", since_hours=48)
            assert len(results) > 700  # the old 288-row cap would have failed this


class TestGetChangesEventDedup:
    """Regression tests for the seen_changes deduplication bug found via a
    deliberate function-by-function read: the original code used a
    seen_changes set on the event-based sources (ha and news), which
    suppressed legitimate repeated state transitions — a door that opened,
    closed, then opened again within the query window would only report
    the first opened event, silently dropping the second."""

    def setup_method(self):
        import tempfile
        from unittest.mock import patch
        self.temp_db_fixture = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db = self.temp_db_fixture.name
        self.temp_db_fixture.close()
        self.patcher = patch("app.snapshots.SNAPSHOT_DB", self.temp_db)
        self.patcher.start()
        from app.snapshots import init_snapshot_db
        init_snapshot_db()

    def teardown_method(self):
        import os
        self.patcher.stop()
        os.unlink(self.temp_db)

    def _insert_snapshot(self, source, content, timestamp):
        from app.snapshots import _connect, SNAPSHOT_DB
        con = _connect(SNAPSHOT_DB)
        con.execute(
            "INSERT INTO snapshots (timestamp, source, content) VALUES (?, ?, ?)",
            (timestamp, source, content)
        )
        con.commit()
        con.close()

    def _ago(self, minutes_ago: int) -> str:
        from datetime import datetime, timedelta, timezone
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_repeated_door_open_event_is_not_suppressed(self):
        """A door that opens, closes, then opens again within the query
        window must report all three transitions — not just the first two.
        Before the fix, the second 'opened' event was silently dropped by
        seen_changes, making the final (currently-open) state invisible."""
        import json
        from app.snapshots import get_changes

        def snap(state):
            return json.dumps([{
                "entity_id": "binary_sensor.front_door",
                "state": state,
                "friendly_name": "Front Door",
                "device_class": "door",
            }])

        self._insert_snapshot("ha", snap("off"), self._ago(40))   # closed
        self._insert_snapshot("ha", snap("on"),  self._ago(30))   # opened
        self._insert_snapshot("ha", snap("off"), self._ago(20))   # closed again
        self._insert_snapshot("ha", snap("on"),  self._ago(10))   # opened again

        changes = get_changes(since_hours=1)
        assert "ha" in changes
        ha_changes = [c["change"] for c in changes["ha"]]
        # All three transitions must appear
        opened_count = sum(1 for c in ha_changes if "opened" in c)
        closed_count = sum(1 for c in ha_changes if "closed" in c)
        assert opened_count == 2, f"Expected 2 opened events, got {opened_count}: {ha_changes}"
        assert closed_count == 1, f"Expected 1 closed event, got {closed_count}: {ha_changes}"

    def test_repeated_motion_event_is_not_suppressed(self):
        """Motion that triggers twice in a window (off->on->off->on) must
        report both detections — they are separate, real events."""
        import json
        from app.snapshots import get_changes

        def snap(state):
            return json.dumps([{
                "entity_id": "binary_sensor.hallway_motion",
                "state": state,
                "friendly_name": "Hallway Motion",
                "device_class": "motion",
            }])

        self._insert_snapshot("ha", snap("off"), self._ago(50))
        self._insert_snapshot("ha", snap("on"),  self._ago(40))   # motion 1
        self._insert_snapshot("ha", snap("off"), self._ago(30))
        self._insert_snapshot("ha", snap("on"),  self._ago(20))   # motion 2
        self._insert_snapshot("ha", snap("off"), self._ago(10))

        changes = get_changes(since_hours=1)
        assert "ha" in changes
        ha_changes = [c["change"] for c in changes["ha"]]
        motion_count = sum(1 for c in ha_changes if "motion detected" in c)
        assert motion_count == 2, f"Expected 2 motion events, got {motion_count}: {ha_changes}"

