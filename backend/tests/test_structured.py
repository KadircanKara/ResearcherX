"""Table-driven tests for the tolerant JSON extraction + parse retry logic."""

import json

import pytest
from pydantic import BaseModel

import app.llm.structured as structured
from app.llm.structured import _extract_json, parse_structured

EXTRACT_CASES = [
    pytest.param('{"a": 1}', {"a": 1}, id="plain-object"),
    pytest.param('```json\n{"a": 1}\n```', {"a": 1}, id="json-fence"),
    pytest.param('```\n{"a": 1}\n```', {"a": 1}, id="bare-fence"),
    pytest.param('<think>I should return {1: 2}...</think>{"a": 1}', {"a": 1}, id="think-block"),
    pytest.param('<THINK>reasoning</THINK>{"a": 1}', {"a": 1}, id="think-case-insensitive"),
    pytest.param('reasoning without open tag</think>{"a": 1}', {"a": 1}, id="dangling-close-tag"),
    pytest.param('Here you go: {"a": 1} — hope that helps!', {"a": 1}, id="prose-wrapped"),
    pytest.param('{"a": {"b": {"c": 3}}} trailing', {"a": {"b": {"c": 3}}}, id="nested-objects"),
    pytest.param('{"a": "br}ace in string"}', {"a": "br}ace in string"}, id="brace-inside-string"),
    pytest.param('{"a": "esc \\" quote}"}', {"a": 'esc " quote}'}, id="escaped-quote"),
    pytest.param('{"a": 1} {"b": 2}', {"a": 1}, id="first-of-multiple"),
    pytest.param(
        '<think>{"draft": true}</think>\n```json\n{"a": 1}\n```',
        {"a": 1},
        id="think-then-fence",
    ),
]


@pytest.mark.parametrize(("raw", "expected"), EXTRACT_CASES)
def test_extract_json(raw: str, expected: dict):
    assert json.loads(_extract_json(raw)) == expected


def test_extract_json_no_braces_passes_through():
    assert _extract_json("no json here at all") == "no json here at all"


def test_extract_json_unbalanced_returns_tail():
    # Slice-from-start fallback: still hands the parser its best shot.
    assert _extract_json('prefix {"a": 1') == '{"a": 1'


class _Out(BaseModel):
    a: int


async def test_parse_structured_retries_once_then_succeeds(monkeypatch):
    responses = iter(["not json at all", '{"a": 7}'])
    calls = []

    async def fake_one_shot(*, system: str, user: str, max_tokens: int) -> str:
        calls.append(system)
        return next(responses)

    monkeypatch.setattr(structured, "_one_shot", fake_one_shot)
    result = await parse_structured(system="s", user="u", output_model=_Out)
    assert result.a == 7
    assert len(calls) == 2
    assert "previous response was empty or invalid" in calls[1]


async def test_parse_structured_raises_after_two_failures(monkeypatch):
    async def fake_one_shot(*, system: str, user: str, max_tokens: int) -> str:
        return "<think>still thinking</think>"

    monkeypatch.setattr(structured, "_one_shot", fake_one_shot)
    with pytest.raises(ValueError, match="did not return valid JSON"):
        await parse_structured(system="s", user="u", output_model=_Out)


async def test_parse_structured_accepts_noisy_but_valid(monkeypatch):
    async def fake_one_shot(*, system: str, user: str, max_tokens: int) -> str:
        return '<think>hmm</think>```json\n{"a": 3}\n```'

    monkeypatch.setattr(structured, "_one_shot", fake_one_shot)
    result = await parse_structured(system="s", user="u", output_model=_Out)
    assert result.a == 3
