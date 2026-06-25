import logging
import requests
from datetime import datetime
from app.config import settings

_LOGGER = logging.getLogger(__name__)

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with heavy hail",
}


def _degrees_to_cardinal(degrees: float) -> str:
    directions = [
        "north", "north-northeast", "northeast", "east-northeast",
        "east", "east-southeast", "southeast", "south-southeast",
        "south", "south-southwest", "southwest", "west-southwest",
        "west", "west-northwest", "northwest", "north-northwest",
    ]
    return directions[int(((degrees + 11.25) % 360) / 22.5)]


def _describe(code) -> str:
    return WMO.get(int(code), "mixed conditions")


def _fmt_time(iso: str) -> str:
    # %-I removes leading zero on Linux/macOS — works in Docker container, not on Windows
    return datetime.fromisoformat(iso).strftime("%-I:%M %p").lower()


def search(query: str) -> str:
    # Found via a deliberate "bulletproofing" pass reading every file in
    # app/ top to bottom, specifically looking past complexity scores at
    # genuinely small, simple-looking code: forecast_latitude and
    # forecast_longitude both default to 0.0 — a falsy value Python
    # treats the same way every other source file's config checks do
    # ("not settings.ha_url", "not settings.uptime_kuma_url"), EXCEPT
    # this function never actually had the check. (0.0, 0.0) is also a
    # real, valid ocean coordinate off the coast of West Africa, so an
    # unconfigured deployment wouldn't error or warn at all — it would
    # silently return genuine, real weather data for the wrong place on
    # Earth. main.py's own /health endpoint already has this exact
    # check (`if not settings.forecast_latitude or not
    # settings.forecast_longitude: return {"status": "not_configured"}`)
    # — the project's own author had already recognized and solved this
    # for the health-check path, but the fix never made it to the
    # function real user queries actually hit.
    if not settings.forecast_latitude or not settings.forecast_longitude:
        return "Forecast is not configured. Set FORECAST_LATITUDE and FORECAST_LONGITUDE."

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": settings.forecast_latitude,
                "longitude": settings.forecast_longitude,
                "daily": "temperature_2m_max,temperature_2m_min,weathercode,precipitation_probability_max,windspeed_10m_max,winddirection_10m_dominant,sunrise,sunset",
                "temperature_unit": "fahrenheit",
                "windspeed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": settings.forecast_timezone,
                "forecast_days": 3,
            },
            timeout=10,
        )
        response.raise_for_status()
        daily = response.json().get("daily", {})
    except Exception as e:
        _LOGGER.error("Forecast fetch failed: %s", e)
        return f"Unable to retrieve forecast: {e}"

    lines = []

    # Today
    if settings.forecast_location_name:
        s = f"In {settings.forecast_location_name}, today will be {_describe(daily['weathercode'][0])} with a high of about {round(daily['temperature_2m_max'][0])} and a low of {round(daily['temperature_2m_min'][0])}."
    else:
        s = f"Today will be {_describe(daily['weathercode'][0])} with a high of about {round(daily['temperature_2m_max'][0])} and a low of {round(daily['temperature_2m_min'][0])}."
    if daily["precipitation_probability_max"][0] >= settings.forecast_precip_threshold_pct:
        s += f" {daily['precipitation_probability_max'][0]}% chance of precipitation."
    if daily["windspeed_10m_max"][0] >= settings.forecast_wind_threshold_mph:
        s += f" Winds from the {_degrees_to_cardinal(daily['winddirection_10m_dominant'][0])} around {round(daily['windspeed_10m_max'][0])} miles per hour."
    s += f" Sunrise at {_fmt_time(daily['sunrise'][0])}, sunset at {_fmt_time(daily['sunset'][0])}."
    lines.append(s)

    # Tomorrow
    s = f"Tomorrow looks {_describe(daily['weathercode'][1])}, high of {round(daily['temperature_2m_max'][1])}, low of {round(daily['temperature_2m_min'][1])}."
    if daily["precipitation_probability_max"][1] >= settings.forecast_precip_threshold_pct:
        s += f" {daily['precipitation_probability_max'][1]}% chance of rain."
    if daily["windspeed_10m_max"][1] >= settings.forecast_wind_threshold_mph:
        s += f" Winds from the {_degrees_to_cardinal(daily['winddirection_10m_dominant'][1])} around {round(daily['windspeed_10m_max'][1])} miles per hour."
    lines.append(s)

    # Day 3
    day3 = datetime.fromisoformat(daily["time"][2]).strftime("%A")
    s = f"{day3} is looking {_describe(daily['weathercode'][2])}, high of {round(daily['temperature_2m_max'][2])}, low of {round(daily['temperature_2m_min'][2])}."
    if daily["precipitation_probability_max"][2] >= settings.forecast_precip_threshold_pct:
        s += f" {daily['precipitation_probability_max'][2]}% chance of precipitation."
    lines.append(s)

    return " ".join(lines)
