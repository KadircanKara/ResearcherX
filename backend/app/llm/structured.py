import json
import re
from typing import TypeVar

from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import log
from app.llm.client import get_client

T = TypeVar("T", bound=BaseModel)


_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> str:
    """Best-effort JSON extraction from LLM output.

    LLM output is noisy: models sometimes wrap JSON in fences, prepend
    reasoning, or append trailing prose. Reasoning models additionally emit
    `<think>...</think>` blocks before the answer. Strip think blocks, then
    fences, then slice to the first balanced object.
    """
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        # Dangling close tag (opening tag missing or malformed): everything
        # before it is reasoning, not answer.
        text = text.rsplit("</think>", 1)[-1]
    text = _CODE_FENCE.sub("", text).strip()
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


# Some OpenAI-compatible endpoints reject response_format. JSON mode is only
# a hint (the schema-in-prompt is the real guarantor), so fall back without
# it — and remember, to skip the doomed attempt next time.
_response_format_supported = True


async def _one_shot(
    *,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    global _response_format_supported
    client = get_client()
    base: dict = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if _response_format_supported:
        try:
            response = await client.chat.completions.create(
                **base, response_format={"type": "json_object"}
            )
            return response.choices[0].message.content or ""
        except BadRequestError as exc:
            log.warning("response_format_unsupported", error=str(exc))
            _response_format_supported = False
    response = await client.chat.completions.create(**base)
    return response.choices[0].message.content or ""


async def parse_structured(
    *,
    system: str,
    user: str,
    output_model: type[T],
    max_tokens: int = 2000,
) -> T:
    """Request a JSON object matching output_model's schema and parse it.

    Models sometimes emit empty content, add reasoning prose (<think>
    blocks), or ignore the response_format hint. We retry once with a
    stricter prompt when the first attempt fails to parse.
    """
    schema = output_model.model_json_schema()
    schema_block = (
        "You MUST respond with a single JSON object — no prose, no code fences — "
        "that matches this JSON schema exactly:\n\n"
        f"{json.dumps(schema, indent=2)}"
    )
    composed_system = f"{system}\n\n{schema_block}"

    content = await _one_shot(system=composed_system, user=user, max_tokens=max_tokens)
    for candidate in (content, _extract_json(content)):
        if not candidate:
            continue
        try:
            return output_model.model_validate_json(candidate)
        except ValidationError:
            continue

    log.warning(
        "structured_retry",
        output_model=output_model.__name__,
        first_content_preview=(content or "")[:200],
    )
    retry_system = (
        f"{composed_system}\n\n"
        "Your previous response was empty or invalid. Return ONLY the JSON object now."
    )
    content = await _one_shot(system=retry_system, user=user, max_tokens=max_tokens)
    for candidate in (content, _extract_json(content)):
        if not candidate:
            continue
        try:
            return output_model.model_validate_json(candidate)
        except ValidationError:
            continue

    raise ValueError(
        f"LLM did not return valid JSON for {output_model.__name__} after retry; "
        f"last content preview: {(content or '')[:200]!r}"
    )
