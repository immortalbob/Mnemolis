"""
Tests for app/sources/home_assistant.py
Uses unittest.mock to avoid real network calls.
"""
import pytest
from unittest.mock import patch, MagicMock


def _make_entity(entity_id: str, state: str, device_class: str = "", friendly_name: str = "", unit: str = "") -> dict:
    """Helper to build a minimal HA entity dict."""
    attrs = {}
    if device_class:
        attrs["device_class"] = device_class
    if friendly_name:
        attrs["friendly_name"] = friendly_name
    if unit:
        attrs["unit_of_measurement"] = unit
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def _mock_states(entities: list) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = entities
    mock_resp.raise_for_status.return_value = None
    return mock_resp


SAMPLE_STATES = [
    _make_entity("light.living_room", "on", friendly_name="Living Room"),
    _make_entity("light.bedroom", "off", friendly_name="Bedroom"),
    _make_entity("light.tv_backlight_segment_1", "on", friendly_name="TV Segment 1"),
    _make_entity("lock.front_door", "locked", friendly_name="Front Door"),
    _make_entity("lock.back_door", "locked", friendly_name="Back Door"),
    _make_entity("binary_sensor.front_door_door", "off", device_class="door", friendly_name="Front Door"),
    _make_entity("binary_sensor.back_door_motion", "off", device_class="motion", friendly_name="Back Door Motion"),
    _make_entity("sensor.room_temperature", "72.5", device_class="temperature", friendly_name="Room Temperature", unit="°F"),
    _make_entity("sensor.room_humidity", "45.2", device_class="humidity", friendly_name="Room Humidity", unit="%"),
    _make_entity("sensor.room_co2", "650", device_class="carbon_dioxide", friendly_name="Room CO2", unit="ppm"),
    _make_entity("sensor.cotech_temperature", "98.6", device_class="temperature", friendly_name="Outdoor Temp", unit="°F"),
    _make_entity("sensor.phone_battery", "85", device_class="battery", friendly_name="Phone Battery", unit="%"),
    _make_entity("sensor.camera_battery", "100", device_class="battery", friendly_name="Camera Battery", unit="%"),
    _make_entity("event.front_yard_motion", "2026-06-17T18:00:00+00:00", friendly_name="Front Yard Motion"),
    _make_entity("sensor.processor_temperature", "45.0", device_class="temperature", friendly_name="CPU Temp", unit="°C"),
    _make_entity("sensor.unavailable_sensor", "unavailable", device_class="temperature", friendly_name="Broken Sensor"),
]


class TestHAGuard:
    """Tests for URL/token guard."""

    def test_returns_not_configured_when_url_blank(self):
        from app.sources import home_assistant
        from app.config import settings
        original_url = settings.ha_url
        original_token = settings.ha_token
        settings.ha_url = ""
        settings.ha_token = ""
        try:
            result = home_assistant.search("house status")
            assert "not configured" in result.lower()
        finally:
            settings.ha_url = original_url
            settings.ha_token = original_token

    def test_returns_error_on_connection_failure(self):
        from app.sources import home_assistant
        from app.config import settings
        import requests
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"
        try:
            with patch("app.sources.home_assistant.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
                result = home_assistant.search("house status")
            assert "could not connect" in result.lower()
        finally:
            settings.ha_url = ""
            settings.ha_token = ""


class TestExclusions:
    """Tests for entity exclusion logic."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_excludes_tv_segments(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("which lights are on")
        assert "TV Segment" not in result

    def test_excludes_unavailable_entities(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "Broken Sensor" not in result

    def test_excludes_processor_temperature(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "CPU Temp" not in result

    def test_excludes_outdoor_from_indoor_query(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "Outdoor Temp" not in result

    def test_no_duplicate_entities(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("house status summary")
        # Count occurrences of a known entity name
        assert result.count("Front Door") <= 3  # lock + binary sensor + motion at most


class TestLightQueries:
    """Tests for light-related queries."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_lights_on_returns_only_on_lights(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("which lights are on")
        assert "Living Room" in result
        assert "Bedroom" not in result

    def test_all_lights_returned_without_state_filter(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("lights status")
        assert "Living Room" in result
        assert "Bedroom" in result


class TestLockQueries:
    """Tests for lock and door queries."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_locked_doors_returned(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("are the doors locked")
        assert "Front Door" in result
        assert "locked" in result

    def test_no_sensor_bleed_in_lock_query(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("are the doors locked")
        assert "CO2" not in result
        assert "Battery" not in result


class TestEnvironmentalQueries:
    """Tests for indoor environmental queries."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_indoor_air_returns_co2_temp_humidity(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "Room CO2" in result
        assert "Room Temperature" in result
        assert "Room Humidity" in result

    def test_temperature_rounded(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "72.5" in result
        assert "72.500000" not in result

    def test_no_locks_in_air_quality(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("indoor air quality")
        assert "locked" not in result


class TestBatteryQueries:
    """Tests for battery status queries."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_battery_returns_battery_sensors(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("battery status")
        assert "Phone Battery" in result
        assert "Camera Battery" in result

    def test_no_lights_in_battery_query(self):
        from app.sources import home_assistant
        with patch("app.sources.home_assistant.requests.get", return_value=_mock_states(SAMPLE_STATES)):
            result = home_assistant.search("battery status")
        assert "Living Room" not in result
        assert "Bedroom" not in result


class TestMotionFormatting:
    """Tests for motion event time-ago formatting."""

    def test_format_motion_recent(self):
        from app.sources.home_assistant import _format_motion_event
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=5)).isoformat()
        entity = {"entity_id": "event.front_yard_motion", "state": recent, "attributes": {"friendly_name": "Front Yard"}}
        result = _format_motion_event(entity)
        assert "5 minutes ago" in result

    def test_format_motion_hours(self):
        from app.sources.home_assistant import _format_motion_event
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        two_hours_ago = (now - timedelta(hours=2)).isoformat()
        entity = {"entity_id": "event.front_yard_motion", "state": two_hours_ago, "attributes": {"friendly_name": "Front Yard"}}
        result = _format_motion_event(entity)
        assert "2 hours ago" in result

    def test_format_motion_days(self):
        from app.sources.home_assistant import _format_motion_event
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        three_days_ago = (now - timedelta(days=3)).isoformat()
        entity = {"entity_id": "event.front_yard_motion", "state": three_days_ago, "attributes": {"friendly_name": "Front Yard"}}
        result = _format_motion_event(entity)
        assert "3 days ago" in result


class TestAreaDetection:
    """Tests for _detect_area area/room name detection."""

    def setup_method(self):
        from app.sources.home_assistant import _detect_area
        self.detect = _detect_area

    def test_living_room_detected(self):
        assert self.detect("what lights are in the living room") == "living_room"

    def test_master_bedroom_detected(self):
        assert self.detect("temperature in the master bedroom") == "master_bedroom"

    def test_bedroom_detected(self):
        assert self.detect("lights in the bedroom") == "bedroom"

    def test_kitchen_detected(self):
        assert self.detect("what lights are on in the kitchen") == "kitchen"

    def test_outside_detected(self):
        assert self.detect("what are the outdoor conditions outside") == "outside"

    def test_outdoors_alias(self):
        assert self.detect("what is the temperature outdoors") == "outside"

    def test_master_bathroom_detected(self):
        assert self.detect("is the light on in the master bathroom") == "master_bathroom"

    def test_guest_bedroom_detected(self):
        assert self.detect("lights in the guest bedroom") == "guest_bedroom"

    def test_no_area_returns_none(self):
        assert self.detect("house status summary") is None

    def test_no_area_for_generic_query(self):
        assert self.detect("what is the temperature") is None

    def test_longest_match_wins(self):
        # "master bedroom" should match over "bedroom"
        assert self.detect("temperature in the master bedroom") == "master_bedroom"


class TestAreaSearch:
    """Tests for area-filtered search behavior."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def _mock_states(self, entities):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = entities
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def _mock_area_template(self, area_map: dict) -> MagicMock:
        """Build a mock response for the area template API."""
        lines = []
        for area_id, entity_ids in area_map.items():
            lines.append(f"{area_id}|||{','.join(entity_ids)}")
        mock_resp = MagicMock()
        mock_resp.text = "\n".join(lines)
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def test_area_filter_limits_results(self):
        from app.sources import home_assistant
        states = [
            _make_entity("light.bedroom_light", "on", friendly_name="Bedroom Light"),
            _make_entity("light.living_room_light", "on", friendly_name="Living Room Light"),
        ]
        area_map = {"bedroom": ["light.bedroom_light"]}

        with patch("app.sources.home_assistant.requests.get", return_value=self._mock_states(states)),              patch("app.sources.home_assistant.requests.post", return_value=self._mock_area_template(area_map)):
            result = home_assistant.search("lights in the bedroom")

        assert "Bedroom Light" in result
        assert "Living Room Light" not in result

    def test_area_filter_with_state_filter(self):
        from app.sources import home_assistant
        states = [
            _make_entity("light.bedroom_light", "on", friendly_name="Bedroom Light"),
            _make_entity("light.bedroom_lamp", "off", friendly_name="Bedroom Lamp"),
        ]
        area_map = {"bedroom": ["light.bedroom_light", "light.bedroom_lamp"]}

        with patch("app.sources.home_assistant.requests.get", return_value=self._mock_states(states)),              patch("app.sources.home_assistant.requests.post", return_value=self._mock_area_template(area_map)):
            result = home_assistant.search("which lights are on in the bedroom")

        assert "Bedroom Light" in result
        assert "Bedroom Lamp" not in result

    def test_unknown_area_falls_back_to_keyword(self):
        from app.sources import home_assistant
        states = [
            _make_entity("sensor.room_temperature", "72.5", device_class="temperature",
                        friendly_name="Room Temp", unit="°F"),
        ]
        # Empty area map — area not found
        area_map = {}

        with patch("app.sources.home_assistant.requests.get", return_value=self._mock_states(states)),              patch("app.sources.home_assistant.requests.post", return_value=self._mock_area_template(area_map)):
            result = home_assistant.search("temperature in the attic")

        # Should fall back to keyword filter and find temperature sensor
        assert "Room Temp" in result

    def test_no_area_uses_keyword_filter(self):
        from app.sources import home_assistant
        states = [
            _make_entity("lock.front_door", "locked", friendly_name="Front Door"),
            _make_entity("lock.back_door", "locked", friendly_name="Back Door"),
        ]

        with patch("app.sources.home_assistant.requests.get", return_value=self._mock_states(states)):
            result = home_assistant.search("are the doors locked")

        assert "Front Door" in result
        assert "Back Door" in result


class TestValueFormatting:
    """Tests for numeric value formatting."""

    def test_temperature_rounded(self):
        from app.sources.home_assistant import _format_value
        assert _format_value("79.9060211181641", "°F") == "79.9 °F"

    def test_humidity_rounded(self):
        from app.sources.home_assistant import _format_value
        assert _format_value("39.117431640625", "%") == "39.1 %"

    def test_co2_rounded(self):
        from app.sources.home_assistant import _format_value
        assert _format_value("694.0", "ppm") == "694.0 ppm"

    def test_non_numeric_passthrough(self):
        from app.sources.home_assistant import _format_value
        assert _format_value("locked", "") == "locked"


class TestHAHelperFunctions:
    """Tests for HA helper functions — friendly name, unit, exclusion."""

    def test_friendly_name_from_attributes(self):
        from app.sources.home_assistant import _friendly_name
        entity = {"entity_id": "light.bedroom", "attributes": {"friendly_name": "Bedroom Light"}}
        assert _friendly_name(entity) == "Bedroom Light"

    def test_friendly_name_falls_back_to_entity_id(self):
        from app.sources.home_assistant import _friendly_name
        entity = {"entity_id": "light.bedroom", "attributes": {}}
        assert _friendly_name(entity) == "light.bedroom"

    def test_unit_from_attributes(self):
        from app.sources.home_assistant import _unit
        entity = {"entity_id": "sensor.temp", "attributes": {"unit_of_measurement": "°F"}}
        assert _unit(entity) == "°F"

    def test_unit_empty_when_missing(self):
        from app.sources.home_assistant import _unit
        entity = {"entity_id": "lock.door", "attributes": {}}
        assert _unit(entity) == ""

    def test_excluded_when_unavailable(self):
        from app.sources.home_assistant import _is_excluded
        entity = {"entity_id": "sensor.temp", "state": "unavailable", "attributes": {}}
        assert _is_excluded(entity) is True

    def test_excluded_when_unknown(self):
        from app.sources.home_assistant import _is_excluded
        entity = {"entity_id": "sensor.temp", "state": "unknown", "attributes": {}}
        assert _is_excluded(entity) is True

    def test_excluded_tv_segment(self):
        from app.sources.home_assistant import _is_excluded
        entity = {"entity_id": "light.tv_backlight_segment_1", "state": "on", "attributes": {}}
        assert _is_excluded(entity) is True

    def test_excluded_doorbell_ding(self):
        from app.sources.home_assistant import _is_excluded
        entity = {"entity_id": "binary_sensor.front_door_ding", "state": "off", "attributes": {}}
        assert _is_excluded(entity) is True

    def test_not_excluded_normal_entity(self):
        from app.sources.home_assistant import _is_excluded
        entity = {"entity_id": "light.bedroom", "state": "on", "attributes": {}}
        assert _is_excluded(entity) is False


class TestListAreas:
    """Tests for list_areas() — GET /areas endpoint backing function."""

    def setup_method(self):
        from app.config import settings
        settings.ha_url = "http://homeassistant:8123"
        settings.ha_token = "fake-token"

    def teardown_method(self):
        from app.config import settings
        settings.ha_url = ""
        settings.ha_token = ""

    def test_not_configured_when_no_url(self):
        from app.sources import home_assistant
        from app.config import settings
        settings.ha_url = ""
        result = home_assistant.list_areas()
        assert result["status"] == "not_configured"
        assert result["areas"] == {}

    def test_not_configured_when_no_token(self):
        from app.sources import home_assistant
        from app.config import settings
        settings.ha_token = ""
        result = home_assistant.list_areas()
        assert result["status"] == "not_configured"

    def test_error_when_area_fetch_fails(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        with patch("app.sources.home_assistant._get_area_entities", return_value=None):
            result = home_assistant.list_areas()
        assert result["status"] == "error"
        assert result["areas"] == {}

    def test_returns_entity_counts(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        area_map = {
            "kitchen": ["light.kitchen_1", "light.kitchen_2", "sensor.kitchen_temp"],
            "bedroom": ["light.bedroom_1"],
        }
        with patch("app.sources.home_assistant._get_area_entities", return_value=area_map):
            result = home_assistant.list_areas()
        assert result["status"] == "ok"
        assert result["areas"]["kitchen"]["entity_count"] == 3
        assert result["areas"]["bedroom"]["entity_count"] == 1

    def test_includes_matching_aliases(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        area_map = {"living_room": ["light.lr_1"]}
        with patch("app.sources.home_assistant._get_area_entities", return_value=area_map):
            result = home_assistant.list_areas()
        assert "living room" in result["areas"]["living_room"]["aliases"]

    def test_area_with_no_aliases_returns_empty_list(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        area_map = {"unmapped_area_xyz": ["light.x"]}
        with patch("app.sources.home_assistant._get_area_entities", return_value=area_map):
            result = home_assistant.list_areas()
        assert result["areas"]["unmapped_area_xyz"]["aliases"] == []

    def test_areas_sorted_alphabetically(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        area_map = {"zebra_room": ["light.z"], "alpha_room": ["light.a"]}
        with patch("app.sources.home_assistant._get_area_entities", return_value=area_map):
            result = home_assistant.list_areas()
        area_names = list(result["areas"].keys())
        assert area_names == sorted(area_names)

    def test_multiple_aliases_for_same_area(self):
        from app.sources import home_assistant
        from unittest.mock import patch
        area_map = {"master_bathroom": ["light.mb_1"]}
        with patch("app.sources.home_assistant._get_area_entities", return_value=area_map):
            result = home_assistant.list_areas()
        aliases = result["areas"]["master_bathroom"]["aliases"]
        assert "master bath" in aliases
        assert "master bathroom" in aliases


class TestBuildFilter:
    """Tests for _build_filter query keyword matching."""

    def test_lights_query_returns_light_domain(self):
        from app.sources.home_assistant import _build_filter
        f = _build_filter("which lights are on")
        assert "light" in f["domains"]

    def test_battery_query_returns_battery_device_class(self):
        from app.sources.home_assistant import _build_filter
        f = _build_filter("battery status")
        assert "battery" in f["device_classes"]

    def test_temperature_query_returns_temperature_class(self):
        from app.sources.home_assistant import _build_filter
        f = _build_filter("indoor air quality")
        assert "temperature" in f["device_classes"] or "carbon_dioxide" in f["device_classes"]

    def test_door_query_returns_lock_domain(self):
        from app.sources.home_assistant import _build_filter
        f = _build_filter("are the doors locked")
        assert "lock" in f["domains"]

    def test_unmatched_query_falls_back_to_summary(self):
        from app.sources.home_assistant import _build_filter
        f = _build_filter("xyzzy nonsense query")
        # Falls back to summary which includes lights and sensors
        assert len(f["domains"]) > 0 or len(f["device_classes"]) > 0
