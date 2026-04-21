import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import log
from app.llm.client import get_client

T = TypeVar("T", bound=BaseModel)


_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    """Best-effort JSON extraction from LLM output.

    Free models are noisy: they sometimes wrap JSON in fences, prepend
    reasoning, or append trailing prose. Strip fences, then slice to the
    first balanced object.
    """
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


async def _one_shot(
    *,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    client = get_client()
    response = await client.chat.completions.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


async def parse_structured(
    *,
    system: str,
    user: str,
    output_model: type[T],
    max_tokens: int = 2000,
) -> T:
    """Request a JSON object matching output_model's schema and parse it.

    Free OpenRouter models are inconsistent — they sometimes emit empty
    content, add reasoning prose, or ignore the response_format hint. We
    retry once with a stricter prompt when the first attempt fails to parse.
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
