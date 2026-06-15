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

    class Config:
        env_file = ".env"


settings = Settings()
