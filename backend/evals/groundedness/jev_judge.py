"""TypeSafe Jev as the eval judge: every verdict is a CLASS, never a score.

Jev is a decision model served on OpenRouter's /systemone endpoint (the same
one `app/services/jev_reranker.py` calls): it answers a typed question about a
`state` with a probability per option. Every judgement below is a `choice`
question, so the report reads "incomplete" or "contradicted" where the LLM
judge reported 0.7 -- and the probabilities come back with it.

It implements the same four calls as `judge.Judge`, so `run_eval` drives it
unchanged, plus an answer-level grade the LLM judge does not have:

  judge_claims             per (claim, excerpt) pair: supports /
                           partially_supports / silent / contradicts, after a
                           per-claim gate that sets aside sentences asserting
                           nothing (the LLM judge's `no_claim`). Pairwise
                           because `supporting_excerpts` -- which every
                           citation metric is derived from -- needs to know
                           WHICH excerpt carries the claim, and a choice answer
                           names no excerpt.
  judge_stance             refused / answered / partial, as the LLM rubric.
  judge_context_relevance  per excerpt: answers / supplies_part /
                           same_topic_only / unrelated; the first two count as
                           relevant, matching the LLM rubric's line.
  judge_answer_grade       fully_correct / padded / incomplete / unsupported /
                           contradictory / off_target / refused, against the
                           excerpts the answer cites AND the golden set's
                           expected facts.

How pairwise verdicts fold into one per claim. A single excerpt that supports
the claim settles it (and every such excerpt is a supporting excerpt).
Otherwise, when excerpts each carry PART of it, they are asked together once
-- a claim assembled from two excerpts is supported, not unsupported. Only
then does a contradicting excerpt make the claim contradicted; supported wins
over contradicted for the reason `judge._PRECEDENCE` gives (two papers
disagreeing is the corpus, not a hallucination).

Never fails open, like the LLM judge: a request that still fails after its
retries, or an answer that is not the expected choice, raises, and the case
becomes an error row.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from app.core import observability
from evals.groundedness.judge import AnswerStance, ClaimVerdict

_RETRIES = 4
_BACKOFF_S = 1.0
_RETRY_STATUSES = frozenset({429, 499, 500, 502, 503, 504, 529})

_PROVENANCE = (
    "You are grading provenance, not truth: a claim can be true about the world "
    "and still not be carried by the text given here. Be strict about numbers, "
    "names and units."
)

_CLAIM_GATE = {
    "type": "choice",
    "instructions": "Does `sentence`, written in answer to `question`, assert anything checkable?",
    "criteria": {
        "claim": (
            "`sentence` asserts something checkable about the papers: a fact, number, "
            "name, method, result or comparison."
        ),
        "no_claim": (
            "`sentence` asserts nothing checkable about the papers: a heading, a "
            "transition, a statement about what the library contains, a request for "
            "clarification, or a refusal."
        ),
    },
}

_PAIR_SUPPORT = {
    "type": "choice",
    "instructions": f"How does `excerpt` bear on `claim`? {_PROVENANCE}",
    "criteria": {
        "supports": "`excerpt` states `claim`, or states something `claim` follows from directly.",
        "partially_supports": "`excerpt` states part of `claim` but not all of it.",
        "silent": (
            "`excerpt` neither states nor contradicts `claim`, including when it is only "
            "about the same topic."
        ),
        "contradicts": "`excerpt` states something incompatible with `claim`.",
    },
}

_JOINT_SUPPORT = {
    "type": "choice",
    "instructions": f"Taken together, do `excerpts` carry `claim`? {_PROVENANCE}",
    "criteria": {
        "supported": "`excerpts` together state `claim`, or something it follows from directly.",
        "unsupported": "`excerpts` together still do not carry all of `claim`.",
        "contradicted": "`excerpts` state something incompatible with `claim`.",
    },
}

_STANCE = {
    "type": "choice",
    "instructions": "What did `answer` DO in response to `question`? Not whether it was right.",
    "criteria": {
        "refused": (
            "It declined, said the library or excerpts do not cover the question, or "
            "asked for clarification instead of answering."
        ),
        "answered": "It asserted substantive content as an answer to the question.",
        "partial": "It hedged that coverage is thin but still asserted substantive content.",
    },
}

_CONTEXT = {
    "type": "choice",
    "instructions": "Would a person answering `question` use `excerpt`?",
    "criteria": {
        "answers": "`excerpt` states the answer to `question`.",
        "supplies_part": "`excerpt` states part of the answer, or a fact the answer depends on.",
        "same_topic_only": (
            "`excerpt` shares the topic or vocabulary of `question` but says nothing an "
            "answer would use, including a passage from the right paper about something else."
        ),
        "unrelated": "`excerpt` is unrelated, or is a reference list, header or boilerplate.",
    },
}
_RELEVANT = frozenset({"answers", "supplies_part"})

_GRADE = {
    "type": "choice",
    "instructions": (
        "Grade `answer` to `question`. `cited_excerpts` are the passages the answer "
        "cites; `expected_facts` are what a correct answer must contain. A fact "
        "counts as backed when either of them states it."
    ),
    "criteria": {
        "fully_correct": (
            "`answer` responds to `question` directly, everything it states is backed, "
            "and it contains every item in `expected_facts`."
        ),
        "padded": (
            "Correct and complete as for fully_correct, but it adds material "
            "`question` did not ask for."
        ),
        "incomplete": (
            "What `answer` states is backed, but it leaves out part of what "
            "`question` asks or an item in `expected_facts`."
        ),
        "unsupported": "`answer` states things that neither `cited_excerpts` nor `expected_facts` back.",
        "contradictory": (
            "`answer` states something that conflicts with `cited_excerpts` or `expected_facts`."
        ),
        "off_target": "`answer` responds to a different or much narrower question than `question`.",
        "refused": (
            "`answer` declines, says the documents do not cover the question, or asks "
            "for clarification."
        ),
    },
}
GRADES = tuple(_GRADE["criteria"])


@dataclass(frozen=True)
class AnswerGrade:
    grade: str
    confidence: float
    probabilities: dict[str, float]


@dataclass
class _Usage:
    input: int = 0
    output: int = 0
    cost: float = 0.0
    calls: int = 0
    resolved: set[str] = field(default_factory=set)


class JevJudge:
    """Drop-in for `judge.Judge`, backed by Jev `choice` questions."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        concurrency: int = 16,
        timeout_s: float = 60.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("the Jev judge needs DECISIONS_API_KEY (OpenRouter)")
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._concurrency = concurrency
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None
        self._sem: asyncio.Semaphore | None = None

    # -- transport --------------------------------------------------------

    def _ensure(self) -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
        # Created on first use, inside the running loop: a semaphore binds to
        # the first loop that contends it.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
            self._sem = asyncio.Semaphore(self._concurrency)
        assert self._sem is not None
        return self._client, self._sem

    async def _ask(self, state: dict, question: dict, usage: _Usage) -> dict:
        """One choice question; returns its answer (`choice`, `probabilities`,
        `confidence`). Raises on anything but a well-formed choice."""
        client, sem = self._ensure()
        payload = {"model": self.model, "state": state, "questions": {"q": question}}
        headers = {"authorization": f"Bearer {self._api_key}"}
        for attempt in range(_RETRIES + 1):
            async with sem:
                try:
                    resp = await client.post(self._base_url, json=payload, headers=headers)
                except httpx.TransportError:
                    if attempt == _RETRIES:
                        raise
                    resp = None
            if resp is not None and resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                body = resp.json()
                break
            if attempt == _RETRIES and resp is not None:
                resp.raise_for_status()
            await asyncio.sleep(_BACKOFF_S * (2**attempt))
        answer = (body.get("answers") or {}).get("q")
        options = question["criteria"]
        if not isinstance(answer, dict) or answer.get("choice") not in options:
            raise ValueError(f"Jev returned no valid choice: {str(answer)[:200]}")
        u = body.get("usage") or {}
        usage.input += int(u.get("input_tokens") or 0)
        usage.output += int(u.get("output_tokens") or 0)
        usage.cost += float(u.get("cost") or 0.0)
        usage.calls += 1
        if body.get("model"):
            usage.resolved.add(body["model"])
        return answer

    def _span(self, name: str):
        return observability.span(
            "judge",
            kind="generation",
            attributes={
                "langfuse.observation.model.name": self.model,
                "langfuse.observation.metadata.judge_call": name,
            },
        )

    @staticmethod
    def _record(span, usage: _Usage) -> None:
        observability.set_json(
            span,
            "langfuse.observation.usage_details",
            {"input": usage.input, "output": usage.output, "total": usage.input + usage.output},
        )
        observability.set_json(span, "langfuse.observation.cost_details", {"total": usage.cost})
        observability.set_metadata(
            span, jev_calls=usage.calls, resolved_model=",".join(sorted(usage.resolved))
        )

    # -- the Judge interface ------------------------------------------------

    async def judge_claims(
        self,
        *,
        question: str,
        claims: list[tuple[int, str]],
        excerpts: list[tuple[int, str, str]],
    ) -> dict[int, ClaimVerdict]:
        if not claims:
            return {}
        usage = _Usage()
        with self._span("claims") as span:
            try:
                results = await asyncio.gather(
                    *(self._judge_claim(question, i, t, excerpts, usage) for i, t in claims)
                )
            finally:
                self._record(span, usage)
        return dict(zip((i for i, _ in claims), results, strict=True))

    async def _judge_claim(
        self,
        question: str,
        index: int,
        text: str,
        excerpts: list[tuple[int, str, str]],
        usage: _Usage,
    ) -> ClaimVerdict:
        gate = await self._ask({"question": question, "sentence": text}, _CLAIM_GATE, usage)
        if gate["choice"] == "no_claim":
            return ClaimVerdict(index=index, verdict="no_claim", reason=_why(gate))
        if not excerpts:
            return ClaimVerdict(index=index, verdict="unsupported", reason="no excerpts shown")
        pairs = await asyncio.gather(
            *(
                self._ask(
                    {"question": question, "claim": text, "excerpt": f"(from: {title})\n{body}"},
                    _PAIR_SUPPORT,
                    usage,
                )
                for _, title, body in excerpts
            )
        )
        by_class: dict[str, list[int]] = {}
        for (n, _, _), answer in zip(excerpts, pairs, strict=True):
            by_class.setdefault(answer["choice"], []).append(n)
        if by_class.get("supports"):
            return ClaimVerdict(
                index=index,
                verdict="supported",
                supporting_excerpts=sorted(by_class["supports"]),
                reason="jev: supported by single excerpts",
            )
        partial = by_class.get("partially_supports", [])
        if partial:
            texts = {n: f"[{n}] (from: {t})\n{b}" for n, t, b in excerpts}
            joint = await self._ask(
                {
                    "question": question,
                    "claim": text,
                    "excerpts": "\n\n".join(texts[n] for n in partial),
                },
                _JOINT_SUPPORT,
                usage,
            )
            if joint["choice"] == "supported":
                return ClaimVerdict(
                    index=index,
                    verdict="supported",
                    supporting_excerpts=sorted(partial),
                    reason=f"jev: supported jointly {_why(joint)}",
                )
            if joint["choice"] == "contradicted":
                return ClaimVerdict(index=index, verdict="contradicted", reason=_why(joint))
        if by_class.get("contradicts"):
            return ClaimVerdict(
                index=index,
                verdict="contradicted",
                reason=f"jev: contradicted by {sorted(by_class['contradicts'])}",
            )
        return ClaimVerdict(
            index=index,
            verdict="unsupported",
            reason="jev: partially_supported" if partial else "jev: no excerpt carries it",
        )

    async def judge_stance(self, *, question: str, answer: str) -> AnswerStance:
        usage = _Usage()
        with self._span("stance") as span:
            try:
                got = await self._ask({"question": question, "answer": answer}, _STANCE, usage)
            finally:
                self._record(span, usage)
        return AnswerStance(stance=got["choice"], reason=_why(got))

    async def judge_context_relevance(
        self, *, question: str, excerpts: list[tuple[int, str, str]]
    ) -> frozenset[int]:
        if not excerpts:
            return frozenset()
        usage = _Usage()
        with self._span("context_relevance") as span:
            try:
                answers = await asyncio.gather(
                    *(
                        self._ask(
                            {"question": question, "excerpt": f"(from: {title})\n{body}"},
                            _CONTEXT,
                            usage,
                        )
                        for _, title, body in excerpts
                    )
                )
            finally:
                self._record(span, usage)
        return frozenset(
            n for (n, _, _), a in zip(excerpts, answers, strict=True) if a["choice"] in _RELEVANT
        )

    async def judge_answer_grade(
        self,
        *,
        question: str,
        answer: str,
        cited_excerpts: list[tuple[int, str, str]],
        expected_facts: list[str],
    ) -> AnswerGrade:
        usage = _Usage()
        state = {
            "question": question,
            "answer": answer,
            "cited_excerpts": "\n\n".join(f"[{n}] (from: {t})\n{b}" for n, t, b in cited_excerpts)
            or "(the answer cites nothing)",
            "expected_facts": expected_facts or ["(none given)"],
        }
        with self._span("answer_grade") as span:
            try:
                got = await self._ask(state, _GRADE, usage)
            finally:
                self._record(span, usage)
        return AnswerGrade(
            grade=got["choice"],
            confidence=float(got.get("confidence") or 0.0),
            probabilities={k: float(v) for k, v in (got.get("probabilities") or {}).items()},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def _why(answer: dict) -> str:
    return f"jev: {answer['choice']} (confidence {float(answer.get('confidence') or 0):.2f})"


def is_jev_model(model: str) -> bool:
    return "jev" in model.lower()
