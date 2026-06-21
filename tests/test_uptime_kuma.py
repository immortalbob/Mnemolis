"""
Tests for app/sources/uptime_kuma.py
Uses unittest.mock to avoid real Socket.IO connections.
"""
from unittest.mock import patch, MagicMock


def _make_monitor(id: int, name: str) -> dict:
    return {"id": id, "name": name}


def _make_heartbeats(monitor_id: int, status: int) -> dict:
    return {monitor_id: [{"status": status}]}


class TestUptimeKumaGuard:
    """Tests for URL guard."""

    def test_returns_not_configured_when_url_blank(self):
        from app.sources import uptime_kuma
        from app.config import settings
        original = settings.uptime_kuma_url
        settings.uptime_kuma_url = ""
        try:
            result = uptime_kuma.search("is anything down")
            assert "not configured" in result.lower()
        finally:
            settings.uptime_kuma_url = original


class TestUptimeKumaStatus:
    """Tests for monitor status parsing with mocked API."""

    def setup_method(self):
        from app.config import settings
        settings.uptime_kuma_url = "http://uptime-kuma:3001"
        settings.uptime_kuma_username = "testuser"

    def teardown_method(self):
        from app.config import settings
        settings.uptime_kuma_url = ""
        settings.uptime_kuma_username = ""

    def _mock_api(self, monitors: list, heartbeats: dict) -> MagicMock:
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.get_monitors.return_value = monitors
        mock_api.get_heartbeats.return_value = heartbeats
        return mock_api

    def test_all_up_returns_clean_message(self):
        from app.sources import uptime_kuma
        monitors = [_make_monitor(1, "MiniDock"), _make_monitor(2, "MiniPlex")]
        heartbeats = {**_make_heartbeats(1, 1), **_make_heartbeats(2, 1)}
        mock_api = self._mock_api(monitors, heartbeats)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "all" in result.lower()
        assert "up" in result.lower()
        assert "down" not in result.lower()

    def test_down_service_listed(self):
        from app.sources import uptime_kuma
        monitors = [_make_monitor(1, "MiniDock"), _make_monitor(2, "MiniPlex")]
        heartbeats = {**_make_heartbeats(1, 1), **_make_heartbeats(2, 0)}
        mock_api = self._mock_api(monitors, heartbeats)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "MiniPlex" in result
        assert "DOWN" in result

    def test_multiple_down_services_listed(self):
        from app.sources import uptime_kuma
        monitors = [
            _make_monitor(1, "ServiceA"),
            _make_monitor(2, "ServiceB"),
            _make_monitor(3, "ServiceC"),
        ]
        heartbeats = {
            **_make_heartbeats(1, 0),
            **_make_heartbeats(2, 0),
            **_make_heartbeats(3, 1),
        }
        mock_api = self._mock_api(monitors, heartbeats)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "ServiceA" in result
        assert "ServiceB" in result
        assert "ServiceC" not in result

    def test_maintenance_reported_separately(self):
        from app.sources import uptime_kuma
        monitors = [_make_monitor(1, "ServiceA"), _make_monitor(2, "ServiceB")]
        heartbeats = {**_make_heartbeats(1, 1), **_make_heartbeats(2, 3)}
        mock_api = self._mock_api(monitors, heartbeats)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "maintenance" in result.lower()
        assert "ServiceB" in result

    def test_no_monitors_returns_message(self):
        from app.sources import uptime_kuma
        mock_api = self._mock_api([], {})
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "no monitors" in result.lower()

    def test_connection_error_returns_error_message(self):
        from app.sources import uptime_kuma
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(side_effect=Exception("Connection refused"))
        mock_api.__exit__ = MagicMock(return_value=False)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "could not connect" in result.lower() or "error" in result.lower()

    def test_up_count_reported(self):
        from app.sources import uptime_kuma
        monitors = [
            _make_monitor(1, "A"), _make_monitor(2, "B"),
            _make_monitor(3, "C"), _make_monitor(4, "D"),
        ]
        heartbeats = {
            **_make_heartbeats(1, 1), **_make_heartbeats(2, 1),
            **_make_heartbeats(3, 1), **_make_heartbeats(4, 0),
        }
        mock_api = self._mock_api(monitors, heartbeats)
        with patch("app.sources.uptime_kuma.UptimeKumaApi", return_value=mock_api):
            result = uptime_kuma.search("status")
        assert "3" in result  # 3 of 4 up
        assert "4" in result


class TestGetStatusFromHeartbeats:
    """Tests for _get_status_from_heartbeats heartbeat parsing."""

    def setup_method(self):
        from app.sources.uptime_kuma import _get_status_from_heartbeats
        self.get_status = _get_status_from_heartbeats

    def test_returns_last_status(self):
        heartbeats = {1: [{"status": 0}, {"status": 1}]}
        assert self.get_status(heartbeats, 1) == 1

    def test_missing_monitor_returns_3(self):
        assert self.get_status({}, 99) == 3

    def test_empty_list_returns_3(self):
        assert self.get_status({1: []}, 1) == 3

    def test_missing_status_key_returns_3(self):
        heartbeats = {1: [{"no_status_key": True}]}
        assert self.get_status(heartbeats, 1) == 3

    def test_down_status(self):
        heartbeats = {1: [{"status": 0}]}
        assert self.get_status(heartbeats, 1) == 0

    def test_non_list_heartbeat_returns_3(self):
        heartbeats = {1: "not a list"}
        assert self.get_status(heartbeats, 1) == 3
