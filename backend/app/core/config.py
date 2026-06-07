from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LLMFallback(BaseModel):
    """One alternate OpenAI-compatible endpoint for daily-quota failover."""

    base_url: str
    api_key: str
    model: str


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

    # Alternate providers for daily-quota failover, as a JSON list:
    # LLM_FALLBACKS='[{"base_url":"...","api_key":"...","model":"..."}]'
    # When the active provider keeps 429ing after SDK retries (e.g. Groq's
    # 100k tokens/day exhausted), calls rotate to the next entry. Empty
    # list = single-provider behavior.
    llm_fallbacks: list[LLMFallback] = Field(default_factory=list)

    # Abuse limits (decision D3): anonymous per-IP quotas + a global daily
    # cap; the owner API key (X-API-Key header) bypasses both. The cap is
    # the real DoS backstop for the Groq quota.
    owner_api_key: str | None = None
    rate_limit_runs: str = "3/hour;10/day"  # per-IP, POST /v1/research
    rate_limit_reads: str = "120/minute"  # per-IP, GETs + SSE connects
    # Groq free tier allows 100k tokens/day and a measured full run costs
    # ~12.5k — 10 caps the day at ~light-overdraft, with cancelled/degraded
    # runs costing less. Raise only alongside a paid tier.
    global_daily_run_cap: int = 10

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
        if not self.owner_api_key:
            problems.append("OWNER_API_KEY is empty (required for quota bypass)")
        if problems:
            raise RuntimeError(f"refusing to start with ENVIRONMENT=prod: {'; '.join(problems)}")


settings = Settings()  # type: ignore[call-arg]
