from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM provider via the OpenAI-compatible API. The checked-in .env.example
    # points at Groq's free tier (the configuration we actually run); any
    # OpenAI-compatible endpoint works by re-pointing LLM_BASE_URL /
    # LLM_API_KEY / LLM_MODEL. The code defaults below target a host-run
    # Ollama server (host.docker.internal) purely as the keyless boot
    # fallback — .env is what selects the real provider.
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_api_key: str = "ollama"  # local Ollama ignores it; placeholder satisfies the SDK
    llm_model: str = "gemma4"  # examples: gemma4, deepseek-r1:8b, llama3.1, qwen3

    # SDK client retries. On Groq this is the 429-backoff budget (.env raises
    # it to 5); on local Ollama a couple cover transient connection blips
    # while a model loads into memory.
    llm_max_retries: int = 2

    # Preserved for future provider swaps; unused today.
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    database_url: str = "sqlite+aiosqlite:///./researcherx.db"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    log_level: str = "INFO"

    # Groq free tier allows ~30 req/min and one run is 1 (planner) + N
    # (searchers) + N–3N (validations) + 1 (synth) + 1 (critic) requests —
    # keep concurrency modest so a run stays inside the quota. (On local
    # Ollama the same cap budgets RAM/VRAM contention instead: one instance
    # serializes requests unless OLLAMA_NUM_PARALLEL is raised.)
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


settings = Settings()  # type: ignore[call-arg]
