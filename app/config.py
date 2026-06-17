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
    forecast_latitude: float = 35.1894
    forecast_longitude: float = -114.0530
    forecast_location_name: str = "Kingman, Arizona"
    forecast_timezone: str = "America/Phoenix"

    # Uptime Kuma
    uptime_kuma_url: str = ""
    uptime_kuma_username: str = ""
    uptime_kuma_password: str = ""

    # LLM backend — for intelligent source and Kiwix book selection
    # Leave LLM_URL blank to disable LLM routing and fall back to keyword matching + Wikipedia
    llm_url: str = ""
    llm_model: str = "qwen3:8b"
    llm_api_type: str = "ollama"  # "ollama" (native) or "openai" (OpenAI-compatible)


settings = Settings()
