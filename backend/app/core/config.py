from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator
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

    # --- single-paper scope -------------------------------------------------
    # Both numbers below apply ONLY when retrieval is scoped to one paper
    # (the targeter named it, or the project holds a single paper). They are
    # MODEL-SPECIFIC in exactly the way similarity_threshold above is:
    # measured on text-embedding-3-small against the 100-paper dev corpus on
    # 2026-08-12, and invalid for any other embedding model.
    #
    # They do different jobs and neither works alone.
    #
    # The CEILING is the noise floor. It replaces similarity_threshold as the
    # SQL cutoff in single-paper scope. Measured on the three off_topic golden
    # cases, scoped to the paper holding the globally nearest chunk (the
    # targeter-misfire simulation): 0.85 admits 0, 1 and 1 chunks; 0.90 admits
    # 1, 2 and 9. A relative rule cannot do this job — an off-topic question's
    # distances are compressed (best 0.7518-0.8581), so the delta alone (0.25,
    # the then-current value; it is 0.20 below now) keeps 24/24, 44/44 and
    # 64/64 chunks of the paper.
    #
    # THOSE THREE CASES WERE TOO EASY, and 0.85 is looser than they made it
    # look. The 2026-08-12 widening added nine NEAR-domain negatives (drone
    # regulation, insurance, hobbyist gear — plausible questions this library
    # cannot answer); they land at 0.5474-0.6521, well inside the ceiling, and
    # a mis-targeted one keeps 9-52 chunks of the wrong paper (the `kept`
    # column of those nine rows in the delta-0.20 per-case table). `run_eval
    # --targeted` prints RAISE THE CEILING'S SCRUTINY at every delta because
    # of it. Re-tuning the ceiling is its own measurement, not a delta problem
    # — see "Measured" in evals/retrieval/README.md.
    intra_paper_ceiling: float = 0.85

    # The DELTA is cost and precision: keep every chunk within this distance
    # of the paper's own nearest chunk. The ceiling alone is far too generous
    # on real questions — measured (evals/retrieval/README.md, "Measured —
    # 2026-08-12"), at delta 0.20 four cases (marl-security-attacks,
    # deadly-triad, lazy-agents-reward, hnpfl-fair-comparison) keep exactly
    # the 60-chunk max_context_chunks budget, i.e. the BUDGET bound them
    # before the delta even got a chance to — the ceiling alone would let
    # far more of a large paper through unguarded.
    #
    # 0.20 is a swept measurement, not a single witness. `run_eval --targeted`
    # on 2026-08-12 over a golden set at the harness's own confidence gate
    # (30 positives — 20 of them inside papers larger than the 60-chunk budget
    # — and 12 negatives, nine of them near-domain), 100 papers / 4527 chunks,
    # text-embedding-3-small, ceiling 0.85, budget 60:
    #
    #   delta  survival@cut  mean kept chunks  mean kept tokens
    #   0.15   0.93 (28/30)  17.7              ~7.8k
    #   0.20   1.00 (30/30)  27.5              ~12.1k
    #   0.25   1.00 (30/30)  36.2              ~15.9k
    #   0.30   1.00 (30/30)  42.4              ~18.6k
    #   0.35   1.00 (30/30)  46.8              ~20.5k
    #
    # 0.20 is the smallest delta that loses no answer chunk, and delta is the
    # cost lever, so the smallest survivor wins. The binding witness is
    # iot-lowpower-protocols (rank 18 of its paper's 97 chunks), whose answer
    # sits 0.1651 from that paper's own nearest chunk; ground-control-station
    # (0.1640, the case that used to justify 0.25 alone) is only second. So
    # 0.20 carries 0.035 of margin over the worst case, NOT the 0.05 a fresh
    # tuning would prefer — this is the first number to re-check if a targeted
    # answer ever comes back truncated, and the reason to re-sweep rather than
    # nudge after any embedding-model change.
    intra_paper_delta: float = 0.20

    # --- Hybrid retrieval -------------------------------------------------
    #
    # Dense-only ranking buries lexically-exact chunks. Measured: the chunk
    # holding a paper's reward table ranked 53rd globally behind 40 chunks
    # from 18 other papers in the same domain, and 771st under a different
    # phrasing of the same question. See the max_context_chunks block above,
    # which raised the budget 40 -> 60 as slack around that failure without
    # fixing it.
    #
    # False restores the pre-2026-08-15 dense-only path EXACTLY. It is both
    # the eval harness's A/B control arm and production's kill switch, which
    # is why intra_paper_delta below is not deleted: it still governs the cut
    # whenever this is False.
    hybrid_retrieval: bool = True

    # Weighted RRF: score = w_dense/(k + rank_dense) + w_sparse/(k + rank_sparse).
    #
    # These weight RANKS, not scores, and that is not a stylistic choice.
    # Cosine distance is bounded [0, 2] and comparable across queries;
    # ts_rank_cd is unbounded and depends on query length and corpus
    # statistics. A weighted sum of the raw values is arithmetic on
    # incompatible units, and per-query normalization makes a chunk's score
    # depend on which other chunks came back.
    hybrid_dense_weight: float = 0.7
    hybrid_sparse_weight: float = 0.3

    # RRF's rank-damping constant. Cormack et al. (2009)'s k=60 is the
    # textbook default, but it is WRONG here, not a matter of taste -- it was
    # measured to interact badly with two OTHER constants on this corpus.
    # The dense arm saturates hybrid_dense_pool (200) on all 30 golden-set
    # positives, so at k=60 a sparse-arm rank-1 chunk scores w_sparse/(k+1) =
    # 0.3/61 = 0.00492, which ties a dense chunk around rank ~82 -- past the
    # max_context_chunks (60) budget. With the shipped 0.7/0.3 weights and
    # that budget, k=60 makes the sparse arm mathematically incapable of ever
    # entering the budget at all: measured as rescued=0 at every k=60 sweep
    # point. k=30 unblocks it (the admission inequality flips around k≈35).
    # Measured effect (0.7/0.3, k=30 vs k=60): recall@60 0.87 -> 0.93, MRR
    # 0.527 -> 0.562, survival@cut unchanged at 1.00, and 2 of the 4
    # rescue-eligible cases are recovered by genuine sparse-only admissions
    # (d_rank=None). See "Measured — 2026-08-15" in evals/retrieval/README.md.
    hybrid_rrf_k: int = 30

    # Per-arm candidate bounds. The dense pool is generous because the dense
    # arm is already gated by an absolute distance cutoff; the sparse pool IS
    # the sparse arm's entire admission rule, since ts_rank_cd has no
    # cross-query-comparable magnitude to threshold on.
    hybrid_dense_pool: int = 200
    hybrid_sparse_pool: int = 100

    # Single-paper cut under hybrid. Replaces intra_paper_delta, which is a
    # distance-space rule (best + delta) and has no meaning against a fused
    # rank. TUNED BY THE SWEEP -- see the measured block in
    # evals/retrieval/README.md before changing it.
    intra_paper_rank_window: int = 30

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

    @model_validator(mode="after")
    def _check_intra_paper_relationship(self) -> "Settings":
        """Guards for the single-paper scope constants (see the "single-paper
        scope" block above), and now also for the hybrid retrieval constants
        (see the "Hybrid retrieval" block above) for the same reason.
        Construction-time, not on the prod-only validate_for_environment()
        gate below, because a bad value here is NOT prod-only: the delta
        sweep documented in evals/retrieval/README.md sets `INTRA_PAPER_DELTA`
        as a DEV env override (`docker compose exec -T -e INTRA_PAPER_DELTA=$d`),
        which never reaches validate_for_environment() (it returns immediately
        unless ENVIRONMENT=prod) — a mistyped sweep point used to surface
        only as `survival@cut 0.00` with no diagnostic. The hybrid sweep runs
        the same way (`docker compose exec -e` overrides), so a mistyped
        hybrid weight or rank window would fail just as silently without
        these checks. The shipped defaults (delta 0.20, ceiling 0.85 against
        threshold 0.75, dense/sparse weights 0.7/0.3, rrf_k 30, pools 200/100,
        rank window 30) pass all checks below, so this never blocks a normal
        dev or test boot.
        """
        if self.intra_paper_delta < 0:
            raise ValueError(
                "INTRA_PAPER_DELTA is negative (keep_within_paper returns 0, "
                "emptying every single-paper retrieval -- and a single-paper "
                "project has no untargeted scope to fall back to, so the "
                "model would answer ungrounded)"
            )
        if self.intra_paper_ceiling < self.similarity_threshold:
            raise ValueError(
                "INTRA_PAPER_CEILING is below SIMILARITY_THRESHOLD (single-paper "
                "scope would silently become STRICTER than global scope, the "
                "opposite of its purpose as a looser noise floor)"
            )
        if self.hybrid_dense_weight < 0 or self.hybrid_sparse_weight < 0:
            raise ValueError(
                "HYBRID_DENSE_WEIGHT/HYBRID_SPARSE_WEIGHT must be >= 0 "
                "(a negative weight makes one arm actively demote the chunks "
                "it ranks highest)"
            )
        if abs((self.hybrid_dense_weight + self.hybrid_sparse_weight) - 1.0) > 1e-9:
            raise ValueError(
                "HYBRID_DENSE_WEIGHT + HYBRID_SPARSE_WEIGHT must equal 1.0 "
                "(RRF is linear, so weights summing to anything else still "
                "'work' -- they silently rescale every fused score and the "
                "configured ratio is not the ratio that runs)"
            )
        if self.hybrid_rrf_k <= 0:
            raise ValueError(
                "HYBRID_RRF_K must be >= 1 (k=0 makes rank 1 worth 1/1 and "
                "rank 2 worth 1/2, so one arm's best chunk dominates the "
                "fusion; k < 0 divides by zero at rank |k|)"
            )
        if self.hybrid_dense_pool <= 0 or self.hybrid_sparse_pool <= 0:
            raise ValueError(
                "HYBRID_DENSE_POOL/HYBRID_SPARSE_POOL must be >= 1 "
                "(a pool of 0 silently disables that arm)"
            )
        if self.intra_paper_rank_window <= 0:
            raise ValueError(
                "INTRA_PAPER_RANK_WINDOW must be >= 1 (a window of 0 empties "
                "every single-paper retrieval, and a single-paper project has "
                "no untargeted scope to fall back to, so the model would "
                "answer ungrounded)"
            )
        return self

    def validate_for_environment(self) -> None:
        """Fail fast when prod boots on dev fallbacks. Called at startup.

        The code defaults exist so dev/tests boot keyless — silently running
        prod on them (no LLM key, sqlite) must be impossible. The single-paper
        scope constants (intra_paper_delta, intra_paper_ceiling) are NOT
        checked here — they are checked unconditionally in
        `_check_intra_paper_relationship` above, because that misconfiguration
        is not prod-only (see its docstring).
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
