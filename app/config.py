from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    log_level: str = "INFO"
    app_env: str = "development"
    odds_api_key: str | None = None
    odds_api_regions: str = "eu"
    api_sports_key: str | None = None

    # Локальный ИИ через Ollama. OpenAI API для работы бота не требуется.
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"

    database_path: str = "data/bot.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
