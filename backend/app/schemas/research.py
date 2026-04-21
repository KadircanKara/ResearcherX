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


class SearchFinding(BaseModel):
    query: str
    summary: str
    sources: list[str]


class CritiqueIssue(BaseModel):
    claim: str
    severity: Literal["low", "medium", "high"]
    note: str


class Critique(BaseModel):
    issues: list[CritiqueIssue] = Field(default_factory=list)
    overall: Literal["pass", "revise"]
