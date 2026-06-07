from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "dev" or "prod". Prod refuses to boot on dev fallbacks — see
    # validate_for_environment().
    environment: str = "dev"

    # LLM provider: Groq free tier via its OpenAI-compatible API. The client
    # is the plain OpenAI SDK, so any OpenAI-compatible endpoint works by
    # re-pointing LLM_BASE_URL / LLM_API_KEY / LLM_MODEL in .env.
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""  # set in .env — free key: https://console.groq.com/keys
    llm_model: str = "llama-3.3-70b-versatile"

    # SDK client retries — the 429-backoff budget for Groq's free-tier rate
    # limits (~30 req/min; one run fires a dozen-plus calls).
    llm_max_retries: int = 5

    database_url: str = "sqlite+aiosqlite:///./researcherx.db"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    log_level: str = "INFO"

    # Groq free tier allows ~30 req/min and one run is 1 (planner) + N
    # (searchers) + N–3N (validations) + 1 (synth) + 1 (critic) requests —
    # keep concurrency modest so a run stays inside the quota.
    max_parallel_searchers: int = 3

    # Planner validates each searcher finding and retries with a revised query
    # if invalid (off-topic / empty / unhelpful). Cap retries so a hopeless
    # sub-query can't burn latency forever; after the cap, the best attempt is
    # accepted and marked degraded. attempts = 1 initial + max_search_retries.
    max_search_retries: int = 2

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def validate_for_environment(self) -> None:
        """Fail fast when prod boots on dev fallbacks. Called at startup.

        The code defaults exist so dev/tests boot keyless — silently running
        prod on them (no LLM key, sqlite) must be impossible.
        """
        if self.environment != "prod":
            return
        problems = []
        if not self.llm_api_key:
            problems.append("LLM_API_KEY is empty")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL points at sqlite")
        if problems:
            raise RuntimeError(f"refusing to start with ENVIRONMENT=prod: {'; '.join(problems)}")


settings = Settings()  # type: ignore[call-arg]
