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

    # Embedding provider — Ollama (nomic-embed-text) in dev; prod overrides all
    # four via SSM to OpenAI text-embedding-3-small. EMBEDDING_API_KEY falls
    # back to LLM_API_KEY only if unset, which would send a Groq key upstream —
    # always set it explicitly.
    embedding_base_url: str = "http://ollama:11434/v1"
    embedding_api_key: str = ""
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768  # must match vector(N) in schema

    # Asymmetric-retrieval prefixes. nomic-embed-text distinguishes documents
    # from queries by prefix, not by an API parameter; OpenAI needs neither, so
    # the defaults are empty and prod requires no entry for these.
    # No trailing space — the service inserts the separator.
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""

    # Cosine-distance cutoff for retrieval (chunks with distance ≥ this are
    # excluded). MODEL-SPECIFIC — the distance distribution depends on the
    # embedding model, so this must be re-measured whenever the model
    # changes. 0.75 was chosen by measuring live against nomic-embed-text
    # (dev): a real question ("Who wrote this?") sat at distance 0.5457 and
    # was being wrongly dropped by the old hardcoded 0.5 cutoff, while a bare
    # greeting ("thanks") passed at 0.4352 — the old threshold favored
    # small-talk over substantive questions. NOT measured for prod's OpenAI
    # text-embedding-3-small; whoever first runs prod should re-measure
    # against that model's distance distribution and adjust via the
    # SIMILARITY_THRESHOLD env var rather than editing this default.
    similarity_threshold: float = 0.75

    # Hard ceiling on chunks sent to the chat model in one turn.
    #
    # The retrieval budget must be a function of the CONTEXT WINDOW, never of
    # library size. Per-paper allocation made it the latter: measured on a
    # 100-paper library, one turn retrieved 191 chunks ≈ 118.5k tokens and
    # every question died on `context_length_exceeded` before the model saw a
    # word. 60 chunks × ~384 words ≈ 30k tokens, which leaves ample room on a
    # 1M-token window for the system prompt, conversation history and the
    # answer itself.
    #
    # Raised 40 → 60 on 2026-08-11 as SLACK, not a fix. Measured on the live
    # corpus: asked "What reward function does the RL planner use?", the chunk
    # holding the intended paper's reward table ranked 53rd globally, because
    # 40 chunks from 18 other UAV-RL papers sat closer in embedding space. The
    # ranking is what is wrong; a larger budget only buys room around it, and
    # the same chunk sits at rank 771 under a different phrasing of the same
    # question. Hybrid lexical + dense ranking is the actual fix — do not read
    # this number as having solved anything.
    max_context_chunks: int = 60

    # Output budget for one chat answer. PROVIDER-SPECIFIC, like the
    # similarity threshold above — re-measure when LLM_MODEL changes.
    #
    # Reasoning models bill their thinking against `max_tokens` on the
    # OpenAI-compatible endpoint, and the charge does not appear in
    # `completion_tokens` — only in `total_tokens`. Measured on
    # gemini-3.6-flash: of a 2000-token budget, 1,921 went to thinking and 75
    # to the answer, so every reply stopped mid-sentence with
    # finish_reason=length. Thinking cannot be capped from this endpoint —
    # `reasoning_effort` is silently ignored and `thinking_config` is
    # rejected with a 400 — so the only lever is a larger budget. 6000 covers
    # the ~2.3k worst-case thinking observed plus a full answer.
    #
    # Lower it for a non-reasoning model on a tight per-request tier (Groq
    # free caps a single request at 12k TPM, and the papers block already
    # takes ~5.5k of that at 100 papers).
    chat_answer_max_tokens: int = 6000

    # Output budget for one query reformulation. PROVIDER-SPECIFIC for the
    # same reason as chat_answer_max_tokens above: the output is a single
    # short string, so 600 would be ample on gpt-4.1-mini — but a reasoning
    # model spends ~1,900 tokens thinking before emitting a character and
    # would truncate the call outright, exactly as it truncated chat answers.
    # max_tokens is a cap, not a charge, so a generous default costs nothing
    # on a model that does not think.
    query_reformulator_max_tokens: int = 3000

    # Output budget for one paper-targeting call. PROVIDER-SPECIFIC for the
    # same reason as the two budgets above: the output is a single paper id,
    # so a small budget would do on gpt-4.1-mini — but a reasoning model
    # spends ~1,900 tokens thinking before emitting a character and would
    # truncate the call outright. max_tokens is a cap, not a charge, so a
    # generous default costs nothing on a model that does not think.
    paper_targeter_max_tokens: int = 3000

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
        if not self.resolved_embedding_api_key:
            problems.append("EMBEDDING_API_KEY (or LLM_API_KEY fallback) is empty")
        embedding_host = self.embedding_base_url.lower()
        if "ollama" in embedding_host or "localhost" in embedding_host:
            problems.append(
                "EMBEDDING_BASE_URL points at a dev host "
                "(set EMBEDDING_BASE_URL to a hosted embedding endpoint)"
            )
        if problems:
            raise RuntimeError(f"refusing to start with ENVIRONMENT=prod: {'; '.join(problems)}")

    @property
    def resolved_embedding_api_key(self) -> str:
        """Use dedicated embedding key or fall back to the LLM key."""
        return self.embedding_api_key or self.llm_api_key


settings = Settings()  # type: ignore[call-arg]
