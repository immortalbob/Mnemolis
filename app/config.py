from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")

    # Kiwix
    kiwix_url: str = "http://kiwix:8080"

    # FreshRSS
    freshrss_url: str = "http://freshrss"
    freshrss_user: str = ""
    freshrss_api_password: str = ""
    freshrss_max_articles: int = 10

    # SearXNG
    searxng_url: str = "http://searxng:8080"

    # Open-Meteo
    forecast_latitude: float = 0.0
    forecast_longitude: float = 0.0
    forecast_location_name: str = ""
    forecast_timezone: str = "UTC"

    # Uptime Kuma
    uptime_kuma_url: str = ""
    uptime_kuma_username: str = ""
    uptime_kuma_password: str = ""

    # Home Assistant
    ha_url: str = ""
    ha_token: str = ""

    # LLM backend — for intelligent source and Kiwix book selection
    # Leave LLM_URL blank to disable LLM routing and fall back to keyword matching + Wikipedia
    llm_url: str = ""
    llm_model: str = "qwen3:8b"
    llm_api_type: str = "ollama"  # "ollama" (native) or "openai" (OpenAI-compatible)

    # Snapshot Engine — time-window phrase defaults
    # Used to resolve phrases like "this morning" or "while at work" into hour windows
    morning_start_hour: int = 6   # "this morning" looks back to this hour, local time
    work_start_hour: int = 9      # "while at work" / "since work" looks back to this hour, local time

    # API key authentication — protects /search and /changes
    # Leave blank to disable auth entirely (default, backward compatible)
    # Comma-separated list of valid keys, e.g. "key1,key2,key3"
    api_keys: str = ""

    # Configurable thresholds — deployment-specific preferences, not algorithm tuning
    # Forecast — when to mention precipitation/wind in the summary, and when a
    # snapshot-to-snapshot temperature shift counts as a meaningful "change"
    forecast_precip_threshold_pct: int = 20
    forecast_wind_threshold_mph: int = 15
    forecast_temp_change_threshold: float = 5.0

    # Home Assistant — battery level (%) below which a snapshot diff reports "low"
    battery_low_threshold_pct: float = 20.0

    # Fusion — concurrency, payload size, and timeout limits
    fusion_max_sources: int = 4
    fusion_max_chars_per_source: int = 1500
    fusion_timeout_seconds: int = 15

    # Result cache — max entries before oldest-eviction kicks in.
    # Lower this on memory-constrained hardware (e.g. an N100 with limited RAM).
    cache_max_size: int = 500

    # Routing cache — max entries before oldest-eviction kicks in. Separate
    # from cache_max_size since the routing cache key space is genuinely
    # larger: every unique conditional query, discourse-framing phrase, and
    # disambiguation candidate set gets its own entry, on top of plain
    # source-routing decisions. Found via real usage — this cache had NO
    # size limit at all until this setting was added, unlike the result
    # cache, which could grow unboundedly over sustained real-world usage.
    routing_cache_max_size: int = 1000

    # Kiwix — results requested per book per search. Higher values give the
    # scoring function more candidates to find the right answer among when
    # common terms are crowded out by brand-name collisions (e.g. "galaxy"
    # returning dozens of Samsung phone articles). Increases Kiwix request size.
    kiwix_search_limit: int = 15

    # Kiwix — maximum number of books the LLM can select for a single query.
    # Raise this if you want broader multi-book fusion (e.g. querying Python,
    # Raspberry Pi, and Unix Stack Exchange together) at the cost of more
    # concurrent Kiwix requests per search and more candidates to score.
    kiwix_max_books: int = 2

    # Web/News confidence-aware fusion — results scoring at or below this
    # threshold are dropped as irrelevant before formatting. Survivors are
    # capped at top_n to bound response size.
    web_news_score_threshold: int = 0
    web_news_top_n: int = 10


settings = Settings()
