"""
Tests for app/sources/forecast.py
Uses unittest.mock to avoid real network calls.
"""
import pytest
from unittest.mock import patch, MagicMock


def _mock_forecast_response(
    max_temps=(101, 99, 95),
    min_temps=(77, 75, 73),
    codes=(0, 2, 0),
    precip=(0, 0, 0),
    wind_speeds=(10, 10, 10),
    wind_dirs=(180, 180, 180),
    sunrises=("2026-06-16T05:21:00", "2026-06-17T05:21:00", "2026-06-18T05:21:00"),
    sunsets=("2026-06-16T19:52:00", "2026-06-17T19:52:00", "2026-06-18T19:52:00"),
    times=("2026-06-16", "2026-06-17", "2026-06-18"),
) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "daily": {
            "temperature_2m_max": list(max_temps),
            "temperature_2m_min": list(min_temps),
            "weathercode": list(codes),
            "precipitation_probability_max": list(precip),
            "windspeed_10m_max": list(wind_speeds),
            "winddirection_10m_dominant": list(wind_dirs),
            "sunrise": list(sunrises),
            "sunset": list(sunsets),
            "time": list(times),
        }
    }
    return mock


class TestForecastSearch:
    """Tests for forecast.search() with mocked Open-Meteo responses."""

    def test_returns_today_tomorrow_and_day3(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response()):
            result = forecast.search("weather")
        assert "Today" in result
        assert "Tomorrow" in result
        # Day 3 should be a day name
        assert any(day in result for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])

    def test_includes_high_and_low_temps(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response()):
            result = forecast.search("weather")
        assert "101" in result
        assert "77" in result

    def test_precipitation_included_when_20_or_above(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(precip=(25, 0, 0))):
            result = forecast.search("weather")
        assert "25%" in result

    def test_precipitation_omitted_when_below_20(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(precip=(10, 10, 10))):
            result = forecast.search("weather")
        assert "10%" not in result

    def test_wind_included_when_15mph_or_above(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(wind_speeds=(20, 10, 10))):
            result = forecast.search("weather")
        assert "20" in result
        assert "miles per hour" in result

    def test_wind_omitted_when_below_15mph(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(wind_speeds=(10, 10, 10))):
            result = forecast.search("weather")
        assert "miles per hour" not in result

    def test_sunrise_and_sunset_in_today(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response()):
            result = forecast.search("weather")
        assert "Sunrise" in result or "sunrise" in result
        assert "Sunset" in result or "sunset" in result

    def test_api_error_returns_error_message(self):
        from app.sources import forecast
        import requests
        with patch("app.sources.forecast.requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            result = forecast.search("weather")
        assert "unable to retrieve" in result.lower() or "error" in result.lower()

    def test_clear_weather_described(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(codes=(0, 0, 0))):
            result = forecast.search("weather")
        assert "clear" in result.lower()

    def test_rain_weather_described(self):
        from app.sources import forecast
        with patch("app.sources.forecast.requests.get", return_value=_mock_forecast_response(codes=(63, 63, 63))):
            result = forecast.search("weather")
        assert "rain" in result.lower()
