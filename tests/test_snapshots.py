"""
Tests for app/snapshots.py — snapshot engine diff logic.
Tests diff functions directly without requiring a running scheduler or DB.
"""
import pytest


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


class TestDiffForecast:
    """Tests for _diff_forecast weather change detection."""

    def setup_method(self):
        from app.snapshots import _diff_forecast
        self.diff = _diff_forecast

    def test_no_change_when_identical(self):
        forecast = "Today will be clear with a high of about 96 and a low of 76."
        assert self.diff(forecast, forecast) == []

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
