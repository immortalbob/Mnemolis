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


settings = Settings()
