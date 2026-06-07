from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    question: str = Field(min_length=5, max_length=1000)


class StepOut(BaseModel):
    id: str
    kind: str
    agent_name: str
    input: dict
    output: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    question: str
    status: str
    report: str | None
    error: str | None
    created_at: datetime
    steps: list[StepOut] = []

    model_config = {"from_attributes": True}


class ResearchPlan(BaseModel):
    """Structured output from the planner agent."""

    sub_queries: list[str] = Field(min_length=1, max_length=3)
    rationale: str


class SourceSummary(BaseModel):
    """One source and what it specifically contributes to a sub-query.

    Per-source summaries (not just a flat URL list) are what let the
    synthesizer cite the RIGHT url for each claim instead of guessing the
    number↔url mapping from an aggregate blob.
    """

    url: str
    summary: str


class SearchFinding(BaseModel):
    query: str
    summary: str  # overall synthesis of the sub-query (narrative scaffolding)
    sources: list[SourceSummary]
    # Set by the orchestrator after planner validation; defaults keep the
    # searcher's own construction (which doesn't know about attempts) valid.
    attempts: int = 1
    validated: bool = False
    accepted_degraded: bool = False  # retries exhausted, best attempt kept


class NumberedSource(BaseModel):
    """A deduped, stably-numbered source for the synthesizer/critic.

    The number is assigned ONCE in code (first-seen order across all
    findings) and handed to the synthesizer, so it cannot reshuffle the
    number↔url binding.
    """

    n: int
    url: str
    summary: str


class FindingValidation(BaseModel):
    """Planner's verdict on a searcher finding (structured output).

    One call both judges the finding (on-topic? useful? non-empty?) and, when
    invalid, proposes the revised query for the retry — cheaper than two calls.
    """

    verdict: Literal["valid", "invalid"]
    reasons: list[str] = Field(default_factory=list)
    revised_query: str | None = None  # set iff verdict == "invalid"


class CritiqueIssue(BaseModel):
    claim: str
    severity: Literal["low", "medium", "high"]
    note: str


class Critique(BaseModel):
    issues: list[CritiqueIssue] = Field(default_factory=list)
    overall: Literal["pass", "revise"]
