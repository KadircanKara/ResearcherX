from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM provider via the OpenAI-compatible API. Defaults target a locally-run
    # Ollama server reached from the backend container through the Docker host
    # (host.docker.internal). Any OpenAI-compatible cloud provider works by
    # pointing LLM_BASE_URL/LLM_API_KEY/LLM_MODEL at it (e.g. Groq:
    # https://api.groq.com/openai/v1 + a real key).
    llm_base_url: str = "http://host.docker.internal:11434/v1"
    llm_api_key: str = "ollama"  # local Ollama ignores it; placeholder satisfies the SDK
    llm_model: str = "gemma4"  # examples: gemma4, deepseek-r1:8b, llama3.1, qwen3

    # SDK client retries. Local Ollama has no rate limits, but a small retry
    # budget covers transient connection blips while a model loads into memory.
    llm_max_retries: int = 2

    # Preserved for future swap-back; unused while on local models.
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    database_url: str = "sqlite+aiosqlite:///./researcherx.db"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    log_level: str = "INFO"

    # Local Ollama has no rate limits, but one instance serializes requests
    # (OLLAMA_NUM_PARALLEL defaults low) and parallel calls contend for
    # RAM/VRAM. Keep concurrency modest so searchers don't thrash the model;
    # raise only if OLLAMA_NUM_PARALLEL is configured higher on the host.
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
