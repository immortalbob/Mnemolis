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
