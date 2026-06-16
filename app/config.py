from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    # Ollama — for LLM-assisted Kiwix book selection
    # Leave blank to disable LLM routing and fall back to Wikipedia
    ollama_url: str = "http://192.168.3.162:11434"
    ollama_model: str = "qwen3:8b"

    class Config:
        env_file = ".env"


settings = Settings()
