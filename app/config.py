from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    log_level: str = "INFO"
    app_env: str = "development"
    odds_api_key: str | None = None
    odds_api_regions: str = "eu"
    api_sports_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-astra"
    openai_reasoning_effort: str = "high"
    database_path: str = "data/bot.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
