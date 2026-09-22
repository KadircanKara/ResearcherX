"""The LLM judge: does each claim follow from the excerpts the model was shown?

INDEPENDENT OF THE SYSTEM UNDER TEST, ON PURPOSE. This module does NOT go
through `app.llm.client.create_chat_completion`, and that is the one thing
about it that must not be "cleaned up":

- The pool injects the ACTIVE provider's model and rotates on quota
  exhaustion. A judge that silently changes model mid-run produces a column
  that is two different measurements averaged together, and nothing in the
  output would say so. A comparison across runs would then be meaningless,
  which is the entire purpose of this harness.
- The default answering model in dev is `gpt-4.1-mini`. Letting it grade its
  own output is self-preference bias measured in the literature and cheap to
  avoid here: the judge defaults to `gpt-4.1`, a different (stronger) model on
  the same key.

So the judge owns a plain `AsyncOpenAI` client, pinned to one model named in
the report header. It reads `settings.llm_base_url`/`llm_api_key` because the
key is already there, not because it wants the pool's behaviour.

WHAT THE JUDGE IS ASKED, AND WHAT IT IS NOT. It rules on SUPPORT only: does
this excerpt set entail the claim. It is never asked whether a citation is
correct -- that is derived in `metrics.py` from the excerpt numbers it names,
because a judge asked two questions at once will let the easy one contaminate
the hard one, and because a derived answer is reproducible from the stored
verdict while a judged one is not.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI, BadRequestError, RateLimitError
from pydantic import BaseModel, Field

from app.core.config import settings

DEFAULT_JUDGE_MODEL = "gpt-4.1"

# Characters of EXCERPT text per judge request. The catalog is up to
# `max_context_chunks` (60) chunks -- about 26k tokens -- and gpt-4.1's
# per-request ceiling on this account is the org's 30k TPM limit, which a
# single whole-catalog call blows outright:
#
#   429 ... Request too large for gpt-4.1 ... on tokens per min (TPM):
#   Limit 30000, Requested 40051
#
# So the catalog is SLICED and each claim is judged against every slice, with
# the verdicts merged (see `_merge`). Slicing is not sampling: no excerpt is
# withheld from the judgement, only from any single request. ~4 chars/token,
# so 45k chars is ~11k tokens, leaving room for the claims, the schema block
# and the answer.
_EXCERPT_CHARS_PER_REQUEST = 45_000

# A 429 means two completely different things on this endpoint, and treating
# them the same wasted most of the first real run:
#
#   TPM      "Rate limit reached ... Limit 30000, Used 29575, Requested 12533.
#             Please try again in 24.216s."   -> transient, wait and retry
#   CREDITS  "You have no credits remaining." (code: credit_balance_exhausted)
#             -> terminal, and every remaining case will fail identically
#
# On 2026-08-22 the run marched through 20 more cases after the credits ran
# out, producing 20 identical error rows and one unusable report. `QuotaExhausted`
# is raised for the second kind so `run_eval` can abort the whole run at the
# first occurrence instead.
_TERMINAL_QUOTA_MARKERS = ("insufficient_quota", "credit_balance_exhausted", "no credits")

# The retry hint the TPM error carries ("try again in 24.216s"). Honoured
# directly: the SDK's own exponential backoff is shorter than the window the
# server names, so its retries are spent before the minute rolls over.
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s")

_MAX_RATE_LIMIT_RETRIES = 6

# Models observed to reject `response_format={"type": "json_object"}`. Keyed by
# MODEL, not by endpoint: one OpenRouter base_url fronts hundreds of models
# with different capabilities, so a per-endpoint memo (what `structured.py`
# keeps) would disable the parameter for every model behind it after one
# refusal. Module state, reset per process, exactly like structured.py's.
_RESPONSE_FORMAT_UNSUPPORTED: set[str] = set()


class QuotaExhausted(RuntimeError):
    """The account is out of credits. Nothing later in the run can succeed."""


def _is_terminal_quota(exc: RateLimitError) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TERMINAL_QUOTA_MARKERS)


def _retry_after_seconds(exc: RateLimitError, attempt: int) -> float:
    """Seconds to wait before retrying a TPM 429.

    Prefers the server's own hint; falls back to a per-attempt back-off that
    always clears a full TPM minute, since the window is what has to roll over.
    """
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        # A second of slack: the hint is computed server-side and a retry that
        # lands on the boundary is refused again.
        return float(match.group(1)) + 1.0
    return min(60.0, 5.0 * attempt)


Verdict = Literal["supported", "unsupported", "contradicted", "no_claim"]


class ClaimVerdict(BaseModel):
    index: int = Field(description="the claim's index, copied from the input")
    verdict: Verdict
    supporting_excerpts: list[int] = Field(
        default_factory=list,
        description="excerpt numbers that support the claim; empty unless verdict is supported",
    )
    reason: str = Field(default="", description="one short sentence")


class JudgeResponse(BaseModel):
    verdicts: list[ClaimVerdict]


class AnswerStance(BaseModel):
    """Whether the answer ASSERTED anything about the corpus at all.

    The negatives need this and the per-claim verdicts cannot provide it: an
    off-topic question's correct answer is a refusal, and a refusal has no
    claims to rule on, so a per-claim score would read 0/0 -- indistinguishable
    from a confident fabrication that happened to parse as no claims.
    """

    stance: Literal["refused", "answered", "partial"]
    reason: str = ""


class AnswerRelevance(BaseModel):
    """Does the answer address the question that was asked.

    Bounded by the schema, not clamped afterwards: a judge that returns 1.4 has
    not followed the rubric, and quietly turning that into 1.0 would record a
    perfect score for a malformed response.
    """

    score: float = Field(ge=0.0, le=1.0, description="0.0 to 1.0, per the rubric")
    reason: str = Field(default="", description="one short sentence")


class ExcerptRelevance(BaseModel):
    n: int = Field(description="the excerpt's number, copied from the input")
    relevant: bool


class ContextRelevanceResponse(BaseModel):
    verdicts: list[ExcerptRelevance]


_SUPPORT_SYSTEM = """\
You grade whether each CLAIM is supported by the EXCERPTS given to you.

You are grading provenance, not truth. A claim can be a true statement about \
the world and still be UNSUPPORTED here, because these excerpts do not say it. \
That is the verdict you must give -- do not fall back on your own knowledge of \
the subject.

Verdicts:
- supported: the excerpts state the claim, or state something the claim \
follows from directly. List every excerpt number that carries it.
- unsupported: the excerpts do not carry the claim. Also use this when the \
excerpts are merely about the same topic.
- contradicted: an excerpt states the opposite.
- no_claim: the sentence asserts nothing checkable about the papers -- a \
heading, a transition, a statement about what the library contains, a request \
for clarification, or a refusal.

Be strict about numbers, names and units: a claim citing a figure the excerpts \
do not contain is unsupported, not supported.

Return one verdict per claim, keyed by the claim's index."""

_STANCE_SYSTEM = """\
You classify what an assistant's answer DID, not whether it was right.

- refused: it declined, said the library/excerpts do not cover the question, \
or asked for clarification instead of answering.
- answered: it asserted substantive content as an answer to the question.
- partial: it hedged that coverage is thin but still asserted substantive \
content.

Return only the classification."""


_ANSWER_RELEVANCE_SYSTEM = """\
You grade whether an ANSWER addresses the QUESTION it was given.

You are grading relevance, not correctness and not support. Do not reward or \
punish the answer for being true, false, cited or uncited -- other graders \
handle that. Ask only: does this text respond to what was asked?

Score from 0.0 to 1.0:
- 1.0: answers the question asked, directly and completely.
- 0.7: answers it, but pads with material the question did not ask for, or \
leaves a minor part unaddressed.
- 0.4: on the right topic but answers a different or much narrower question.
- 0.1: mentions the topic and says nothing responsive.
- 0.0: unrelated, or declines to answer.

A refusal or a request for clarification scores 0.0 here even when refusing \
was the right thing to do -- whether it was is graded separately."""

_CONTEXT_RELEVANCE_SYSTEM = """\
You grade whether each EXCERPT is relevant to a QUESTION. The excerpts were \
retrieved by a search system; you are measuring how much of what it returned \
was worth reading.

An excerpt is relevant when a person answering the question would use it: it \
states the answer, part of the answer, or a fact the answer depends on.

An excerpt is NOT relevant when it merely shares the topic or the vocabulary. \
An excerpt from the right paper that discusses something else is not \
relevant. A reference list, a header or boilerplate is not relevant.

Judge each excerpt on its own. Do not mark one relevant because another one \
is. Return one verdict per excerpt, keyed by the excerpt's number."""


# Verdict precedence when slices disagree. SUPPORTED WINS: the question each
# slice answers is "does anything HERE carry this claim", so one slice finding
# support settles it -- the slices that did not are answering about excerpts
# that were never claimed to be the source. Contradicted outranks unsupported
# for the same reason in the other direction: a slice that actively refutes the
# claim has read something, where "unsupported" is the absence of evidence.
#
# A claim both supported and contradicted across slices is a disagreement
# BETWEEN PAPERS, not a hallucination -- two papers reporting different numbers
# is the normal state of a literature corpus, and scoring it as a generation
# failure would punish the model for the library's contents.
_PRECEDENCE = {"supported": 3, "contradicted": 2, "unsupported": 1, "no_claim": 0}


def _pack(excerpts: list[tuple[int, str, str]], budget: int) -> list[list[tuple[int, str, str]]]:
    """Split the catalog into request-sized slices, preserving order.

    An excerpt larger than `budget` on its own still gets its own slice rather
    than being truncated: a truncated excerpt can turn a supported claim into
    an unsupported one, which is a silently wrong verdict, where an oversized
    request fails loudly.
    """
    slices: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []
    size = 0
    for excerpt in excerpts:
        length = len(excerpt[2])
        if current and size + length > budget:
            slices.append(current)
            current, size = [], 0
        current.append(excerpt)
        size += length
    if current:
        slices.append(current)
    return slices


def _merge(verdicts: list[dict[int, ClaimVerdict]]) -> dict[int, ClaimVerdict]:
    """Fold per-slice verdicts into one per claim, by `_PRECEDENCE`.

    Supporting excerpt numbers are unioned across every slice that found
    support, so a claim carried by two papers keeps both -- which is what
    citation correctness is later derived from.
    """
    merged: dict[int, ClaimVerdict] = {}
    for chunk in verdicts:
        for index, verdict in chunk.items():
            best = merged.get(index)
            if best is None or _PRECEDENCE[verdict.verdict] > _PRECEDENCE[best.verdict]:
                merged[index] = verdict
            elif verdict.verdict == best.verdict == "supported":
                merged[index] = best.model_copy(
                    update={
                        "supporting_excerpts": sorted(
                            set(best.supporting_excerpts) | set(verdict.supporting_excerpts)
                        )
                    }
                )
    return merged


@dataclass
# Output budget per judge call. 4000 was enough for gpt-4.1, which reports zero
# reasoning tokens and writes terse `reason` strings -- and far too small for a
# reasoning model. Measured 2026-08-23 on stealth/ox-alpha: 23 of 40 cases
# failed, half with EMPTY content (thinking consumed the whole budget) and half
# with JSON truncated mid-array ("EOF while parsing a string at line 61").
#
# Raising it is close to free: an API bills GENERATED tokens, not the cap, so a
# model that answers in 900 tokens costs the same at 12000 as at 4000. The cap
# only has to be large enough that a correct answer is never cut off.
#
# This is the same failure CLAUDE.md records for chat_answer_max_tokens on
# gemini-3.6-flash -- thinking billed against max_tokens, invisible in
# completion_tokens, answer truncated with no error anywhere.
DEFAULT_JUDGE_MAX_TOKENS = 12_000


class Judge:
    model: str = DEFAULT_JUDGE_MODEL
    max_tokens: int = DEFAULT_JUDGE_MAX_TOKENS
    excerpt_chars_per_request: int = _EXCERPT_CHARS_PER_REQUEST

    def __post_init__(self) -> None:
        # JUDGE_BASE_URL / JUDGE_API_KEY, falling back to the LLM's own when
        # unset. Separate so the judge can sit on a different vendor from the
        # system under test -- both to screen cheaper judges and because a
        # model grading its own output is self-preference bias. `config.py`
        # refuses to lend LLM_API_KEY to a judge pointed at another host.
        self._client = AsyncOpenAI(
            base_url=settings.resolved_judge_base_url,
            api_key=settings.resolved_judge_api_key,
            max_retries=settings.llm_max_retries,
        )

    def _supports_response_format(self) -> bool:
        return self.model not in _RESPONSE_FORMAT_UNSUPPORTED

    async def _structured(self, *, system: str, user: str, model_cls: type[BaseModel]):
        """One JSON-mode call, parsed against `model_cls`.

        Deliberately NOT `app.llm.structured.parse_structured`: that helper
        rides the provider pool (see this module's docstring) and remembers
        per-provider `response_format` support in module state the harness
        should not be mutating. The schema-in-prompt trick is reproduced here
        because it is what actually guarantees JSON.
        """
        schema_block = (
            "You MUST respond with a single JSON object -- no prose, no code fences -- "
            "that matches this JSON schema exactly:\n\n"
            f"{json.dumps(model_cls.model_json_schema(), indent=2)}"
        )
        messages = [
            {"role": "system", "content": f"{system}\n\n{schema_block}"},
            {"role": "user", "content": user},
        ]
        for attempt in range(1, _MAX_RATE_LIMIT_RETRIES + 1):
            extra = (
                {"response_format": {"type": "json_object"}}
                if self._supports_response_format()
                else {}
            )
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    **extra,
                )
            except BadRequestError as exc:
                # Many OpenAI-compatible endpoints reject `response_format`
                # outright -- of the free OpenRouter models screened on
                # 2026-08-22, several advertise no support for it at all.
                # Remembered per MODEL rather than per endpoint (unlike
                # `structured.py`, which is per provider), because one
                # OpenRouter base_url fronts hundreds of models that differ.
                # The schema pasted into the system prompt is the real
                # guarantor of JSON either way; `response_format` only makes
                # it likelier.
                if not self._supports_response_format():
                    raise
                log_line = str(exc)[:200]
                print(f"  [judge] {self.model} rejected response_format, retrying without it")
                print(f"          {log_line}")
                _RESPONSE_FORMAT_UNSUPPORTED.add(self.model)
                continue
            except RateLimitError as exc:
                if _is_terminal_quota(exc):
                    raise QuotaExhausted(str(exc)) from exc
                if attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise
                await asyncio.sleep(_retry_after_seconds(exc, attempt))
                continue
            content = response.choices[0].message.content or ""
            return model_cls.model_validate_json(content)
        raise AssertionError("unreachable: the loop either returns or raises")

    async def judge_claims(
        self,
        *,
        question: str,
        claims: list[tuple[int, str]],
        excerpts: list[tuple[int, str, str]],
    ) -> dict[int, ClaimVerdict]:
        """Verdict per claim index.

        `excerpts` is (n, paper_title, text) -- the SAME catalog the answering
        model saw, in the same numbering, so a verdict's `supporting_excerpts`
        can be mapped back to paper ids without another lookup.

        Raises on a malformed judge response rather than defaulting to
        "supported". A fail-open judge reports a groundedness number it never
        measured, which is worse than no number: `run_eval` records the case as
        an error and prints it, and the case is excluded from the denominator.
        """
        if not claims:
            return {}
        claim_block = "\n".join(f"{index}. {text}" for index, text in claims)
        slices = _pack(excerpts, self.excerpt_chars_per_request) or [[]]

        per_slice: list[dict[int, ClaimVerdict]] = []
        for excerpt_slice in slices:
            excerpt_block = "\n\n".join(
                f"[{n}] (from: {title})\n{text}" for n, title, text in excerpt_slice
            )
            user = (
                f"QUESTION\n{question}\n\n"
                f"EXCERPTS\n{excerpt_block or '(none were retrieved)'}\n\n"
                f"CLAIMS\n{claim_block}"
            )
            parsed = await self._structured(
                system=_SUPPORT_SYSTEM, user=user, model_cls=JudgeResponse
            )
            per_slice.append({v.index: v for v in parsed.verdicts})

        merged = _merge(per_slice)
        missing = [index for index, _ in claims if index not in merged]
        if missing:
            # Retried once, with ONLY the missing claims, before failing the
            # case. Measured 2026-08-22: two of forty cases came back with
            # verdicts for the first two claims and nothing else -- the model
            # simply stopped early -- and losing a whole case to that is worse
            # than one extra call. A second failure still raises: a fail-open
            # judge reports a groundedness number it never measured.
            retry_claims = [(index, text) for index, text in claims if index in set(missing)]
            for excerpt_slice in slices:
                excerpt_block = "\n\n".join(
                    f"[{n}] (from: {title})\n{text}" for n, title, text in excerpt_slice
                )
                parsed = await self._structured(
                    system=_SUPPORT_SYSTEM,
                    user=(
                        f"QUESTION\n{question}\n\n"
                        f"EXCERPTS\n{excerpt_block or '(none were retrieved)'}\n\n"
                        "CLAIMS\n" + "\n".join(f"{index}. {text}" for index, text in retry_claims)
                    ),
                    model_cls=JudgeResponse,
                )
                per_slice.append({v.index: v for v in parsed.verdicts})
            merged = _merge(per_slice)
            missing = [index for index, _ in claims if index not in merged]
        if missing:
            raise ValueError(f"judge returned no verdict for claim index(es) {missing}")
        return merged

    async def judge_answer_relevance(self, *, question: str, answer: str) -> AnswerRelevance:
        """Question and answer only -- never the excerpts. Whether the answer
        is SUPPORTED is `judge_claims`' question; showing the catalog here lets
        a well-grounded non-answer score high on the wrong evidence."""
        return await self._structured(
            system=_ANSWER_RELEVANCE_SYSTEM,
            user=f"QUESTION\n{question}\n\nANSWER\n{answer}",
            model_cls=AnswerRelevance,
        )

    async def judge_context_relevance(
        self, *, question: str, excerpts: list[tuple[int, str, str]]
    ) -> frozenset[int]:
        """The numbers of the excerpts a person answering `question` would use.

        The judge is NEVER shown the answer. Context relevance is a property of
        retrieval, and a judge that can see the answer marks an excerpt
        relevant because the answer used it -- which turns this into a second,
        worse citation metric.

        Sliced by the same `_pack` as the support judge and for the same
        reason; an excerpt's relevance does not depend on its neighbours, so
        slices need no merge beyond a union. Raises when any shown excerpt
        comes back without a verdict: defaulting it to "not relevant" would
        report a precision the judge never measured.
        """
        if not excerpts:
            return frozenset()
        shown = {n for n, _, _ in excerpts}
        judged: dict[int, bool] = {}
        for excerpt_slice in _pack(excerpts, self.excerpt_chars_per_request):
            excerpt_block = "\n\n".join(
                f"[{n}] (from: {title})\n{text}" for n, title, text in excerpt_slice
            )
            parsed = await self._structured(
                system=_CONTEXT_RELEVANCE_SYSTEM,
                user=f"QUESTION\n{question}\n\nEXCERPTS\n{excerpt_block}",
                model_cls=ContextRelevanceResponse,
            )
            # A number the judge invented is dropped rather than raised on: it
            # names nothing in the catalog, so it cannot move any count.
            judged.update({v.n: v.relevant for v in parsed.verdicts if v.n in shown})
        missing = sorted(shown - judged.keys())
        if missing:
            listed = ", ".join(f"[{n}]" for n in missing)
            raise ValueError(f"judge returned no relevance verdict for excerpt(s) {listed}")
        return frozenset(n for n, relevant in judged.items() if relevant)

    async def judge_stance(self, *, question: str, answer: str) -> AnswerStance:
        return await self._structured(
            system=_STANCE_SYSTEM,
            user=f"QUESTION\n{question}\n\nANSWER\n{answer}",
            model_cls=AnswerStance,
        )
