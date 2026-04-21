from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM provider: Groq (OpenAI-compatible). Free tier gives 30 req/min on
    # llama-3.3-70b-versatile with no practical daily cap — enough headroom
    # for the planner + searchers + synth + critic pipeline.
    groq_api_key: str
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.3-70b-versatile"

    # Preserved for future swap-back; unused while on Groq.
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    database_url: str = "sqlite+aiosqlite:///./researcherx.db"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    log_level: str = "INFO"

    # Groq free tier: ~30 req/min on llama-3.3-70b. A full pipeline run is
    # ~1 (planner) + N (searchers) + 1 (synth) + 1 (critic) requests.
    max_parallel_searchers: int = 3

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()  # type: ignore[call-arg]
