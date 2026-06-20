"""
Tests for app/config.py — pydantic-settings Settings defaults.

These tests guard against accidental default value regressions (a typo
in a default silently changes behavior with no obvious error) and confirm
the settings object is constructible and env-overridable.
"""
import pytest


class TestDefaultValues:
    """Tests confirming each setting has the expected class-level default value.

    Settings() reads from environment variables automatically (that's the
    whole point of pydantic-settings) — in the real container, every one of
    these "defaults" is actually overridden by docker-compose.yml. These
    tests must explicitly clear env vars to test the *class* defaults in
    isolation, or they'll fail against live production config instead of
    verifying the fallback values actually used when something is unset.
    """

    def setup_method(self):
        import os
        # Snapshot and clear every env var Settings() would read, so these
        # tests verify class defaults rather than this container's live config
        self._env_keys = [
            "KIWIX_URL", "FRESHRSS_URL", "FRESHRSS_USER", "FRESHRSS_API_PASSWORD",
            "FRESHRSS_MAX_ARTICLES", "SEARXNG_URL", "FORECAST_LATITUDE",
            "FORECAST_LONGITUDE", "FORECAST_LOCATION_NAME", "FORECAST_TIMEZONE",
            "UPTIME_KUMA_URL", "UPTIME_KUMA_USERNAME", "UPTIME_KUMA_PASSWORD",
            "HA_URL", "HA_TOKEN", "LLM_URL", "LLM_MODEL", "LLM_API_TYPE",
            "MORNING_START_HOUR", "WORK_START_HOUR", "API_KEYS",
            "FORECAST_PRECIP_THRESHOLD_PCT", "FORECAST_WIND_THRESHOLD_MPH",
            "FORECAST_TEMP_CHANGE_THRESHOLD", "BATTERY_LOW_THRESHOLD_PCT",
            "FUSION_MAX_SOURCES", "FUSION_MAX_CHARS_PER_SOURCE",
            "FUSION_TIMEOUT_SECONDS", "CACHE_MAX_SIZE",
        ]
        self._saved_env = {}
        for key in self._env_keys:
            if key in os.environ:
                self._saved_env[key] = os.environ.pop(key)

    def teardown_method(self):
        import os
        for key, value in self._saved_env.items():
            os.environ[key] = value

    def _bare_settings(self):
        """Construct Settings with env_file disabled so only class defaults apply."""
        from app.config import Settings
        return Settings(_env_file=None)

    def test_kiwix_url_default(self):
        s = self._bare_settings()
        assert s.kiwix_url == "http://kiwix:8080"

    def test_freshrss_url_default(self):
        s = self._bare_settings()
        assert s.freshrss_url == "http://freshrss"

    def test_freshrss_user_defaults_blank(self):
        s = self._bare_settings()
        assert s.freshrss_user == ""

    def test_freshrss_max_articles_default(self):
        s = self._bare_settings()
        assert s.freshrss_max_articles == 10

    def test_searxng_url_default(self):
        s = self._bare_settings()
        assert s.searxng_url == "http://searxng:8080"

    def test_forecast_coordinates_default_to_zero(self):
        s = self._bare_settings()
        assert s.forecast_latitude == 0.0
        assert s.forecast_longitude == 0.0

    def test_forecast_location_name_defaults_blank(self):
        s = self._bare_settings()
        assert s.forecast_location_name == ""

    def test_forecast_timezone_defaults_utc(self):
        s = self._bare_settings()
        assert s.forecast_timezone == "UTC"

    def test_uptime_kuma_defaults_blank(self):
        s = self._bare_settings()
        assert s.uptime_kuma_url == ""
        assert s.uptime_kuma_username == ""
        assert s.uptime_kuma_password == ""

    def test_ha_defaults_blank(self):
        s = self._bare_settings()
        assert s.ha_url == ""
        assert s.ha_token == ""

    def test_llm_url_defaults_blank(self):
        s = self._bare_settings()
        assert s.llm_url == ""

    def test_llm_model_default(self):
        s = self._bare_settings()
        assert s.llm_model == "qwen3:8b"

    def test_llm_api_type_defaults_ollama(self):
        s = self._bare_settings()
        assert s.llm_api_type == "ollama"

    def test_morning_start_hour_default(self):
        s = self._bare_settings()
        assert s.morning_start_hour == 6

    def test_work_start_hour_default(self):
        s = self._bare_settings()
        assert s.work_start_hour == 9

    def test_api_keys_defaults_blank(self):
        s = self._bare_settings()
        assert s.api_keys == ""

    def test_forecast_precip_threshold_default(self):
        s = self._bare_settings()
        assert s.forecast_precip_threshold_pct == 20

    def test_forecast_wind_threshold_default(self):
        s = self._bare_settings()
        assert s.forecast_wind_threshold_mph == 15

    def test_forecast_temp_change_threshold_default(self):
        s = self._bare_settings()
        assert s.forecast_temp_change_threshold == 5.0

    def test_battery_low_threshold_default(self):
        s = self._bare_settings()
        assert s.battery_low_threshold_pct == 20.0

    def test_fusion_max_sources_default(self):
        s = self._bare_settings()
        assert s.fusion_max_sources == 4

    def test_fusion_max_chars_per_source_default(self):
        s = self._bare_settings()
        assert s.fusion_max_chars_per_source == 1500

    def test_fusion_timeout_seconds_default(self):
        s = self._bare_settings()
        assert s.fusion_timeout_seconds == 15

    def test_cache_max_size_default(self):
        s = self._bare_settings()
        assert s.cache_max_size == 500


class TestSettingsConstructibility:
    """Tests confirming Settings can be overridden and instantiated normally."""

    def test_settings_instance_exists(self):
        from app.config import settings
        assert settings is not None

    def test_can_override_via_constructor(self):
        from app.config import Settings
        s = Settings(kiwix_url="http://custom-kiwix:9999")
        assert s.kiwix_url == "http://custom-kiwix:9999"

    def test_can_override_numeric_field(self):
        from app.config import Settings
        s = Settings(morning_start_hour=7)
        assert s.morning_start_hour == 7

    def test_can_override_float_field(self):
        from app.config import Settings
        s = Settings(forecast_latitude=35.1894, forecast_longitude=-114.0530)
        assert s.forecast_latitude == 35.1894
        assert s.forecast_longitude == -114.0530

    def test_independent_instances_dont_share_state(self):
        from app.config import Settings
        s1 = Settings(kiwix_url="http://a:8080")
        s2 = Settings(kiwix_url="http://b:8080")
        assert s1.kiwix_url != s2.kiwix_url

    def test_mutating_module_level_settings_does_not_affect_class_defaults(self):
        """Many tests in this suite mutate `settings.X` directly and reset it
        in teardown. Confirm that pattern doesn't leak into fresh instances."""
        from app.config import settings, Settings
        original = settings.ha_url
        settings.ha_url = "http://mutated:8123"
        fresh = Settings()
        assert fresh.ha_url != "http://mutated:8123"
        settings.ha_url = original
