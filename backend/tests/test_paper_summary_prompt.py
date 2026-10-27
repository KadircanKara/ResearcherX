import hashlib
import json

from app.services.paper_summary_prompt import (
    SYSTEM,
    clean_keywords,
    embed_text,
    full_text,
    messages,
    parse,
    response_format,
)


def test_full_text_prefers_pages_in_page_order():
    pages = [{"page": 2, "text": "two"}, {"page": 1, "text": "one"}]
    assert full_text(pages, "body", "abs", cap=100) == "one\n\ntwo"


def test_full_text_falls_back_to_body_then_abstract():
    assert full_text(None, "body", "abs", cap=100) == "body"
    assert full_text([], None, "abs", cap=100) == "abs"
    assert full_text(None, None, None, cap=100) == ""


def test_full_text_is_capped():
    assert full_text([{"page": 1, "text": "x" * 50}], None, None, cap=10) == "x" * 10


def test_parse_rejects_bad_json_and_blank_summary():
    ok = {"summary": " S ", "keywords": ["a"]}
    rec = parse(json.dumps(ok), max_keywords=5)
    assert rec is not None and rec.summary == "S" and rec.keywords == ("a",)
    bad = [
        None,
        "",
        "{nope",
        "[]",
        json.dumps({"summary": "s"}),
        json.dumps({"summary": 3, "keywords": []}),
        json.dumps({"summary": "s", "keywords": "a"}),
        json.dumps({"summary": "s", "keywords": [1]}),
        json.dumps({"summary": "s", "keywords": [], "extra": 1}),
        json.dumps({"summary": "   ", "keywords": []}),
    ]
    for content in bad:
        assert parse(content, max_keywords=5) is None, content


def test_clean_keywords_dedupes_case_and_hyphen_variants():
    assert clean_keywords(["Non-convex", "non convex", " NON_CONVEX "], max_keywords=10) == (
        "Non-convex",
    )


def test_clean_keywords_drops_numbers_keeps_5G_and_60_GHz_dropped():
    raw = ["0.05 m", "1e-04", "5G", "3GPP", "60 GHz", "", "  ", "12%"]
    assert clean_keywords(raw, max_keywords=10) == ("5G", "3GPP")


def test_clean_keywords_caps_at_max():
    assert clean_keywords([f"k{i}x" for i in range(10)], max_keywords=3) == ("k0x", "k1x", "k2x")


def test_messages_response_format_embed_text():
    m = messages("T", None, "body")
    assert m[0] == {"role": "system", "content": SYSTEM}
    assert m[1]["content"] == "Title: T\n\nAbstract: \n\nFull text:\nbody"
    assert response_format()["json_schema"]["strict"] is True
    assert embed_text("T", None, "S") == "T\n\n\n\nS"


def test_system_prompt_is_the_spike_prompt():
    assert (
        hashlib.sha256(SYSTEM.encode()).hexdigest()
        == "6dbf5a4c52f0530e21a4172745dbf31db79889ae9a55c18f7935b05aec2b1809"
    )
