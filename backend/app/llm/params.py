"""Per-model request shaping for OpenAI's reasoning models. PURE.

The app writes every chat call in the gpt-4.1 dialect (`max_tokens`), which
most OpenAI-compatible endpoints accept. OpenAI's reasoning families do not:
every gpt-5 model rejects `max_tokens` outright ("Unsupported parameter:
'max_tokens' is not supported with this model. Use 'max_completion_tokens'
instead.", verified live on gpt-5.4-mini and gpt-5.6-sol), and they accept
only the default temperature. So the translation happens at the one place
the model is known -- per provider, inside the failover loop, because a pool
can mix a gpt-4.1 primary with a gpt-5 fallback.

`max_completion_tokens` is the SAME budget under a new name, and on these
models it is shared with the hidden reasoning: thinking is billed as output
and counts against the cap (the failure `chat_answer_max_tokens` documents
for gemini). `reasoning_effort` is the lever that bounds it, and unlike the
gemini endpoint OpenAI honours it -- hence `LLM_REASONING_EFFORT`.

Matched on the BARE model name only. A vendor-prefixed id
(`openai/gpt-5-mini` on OpenRouter) goes through a router with its own
parameter normalisation, and rewriting its request here would be a guess.
"""

from __future__ import annotations

from urllib.parse import urlparse

_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    name = model.strip().lower()
    return "/" not in name and name.startswith(_REASONING_PREFIXES)


def is_openai_host(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "") == "api.openai.com"


def adapt_request(*, model: str, base_url: str, kwargs: dict, reasoning_effort: str = "") -> dict:
    """The kwargs to send `model` at `base_url`. Never mutates the input."""
    out = dict(kwargs)
    if is_reasoning_model(model):
        if "max_tokens" in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
        out.pop("temperature", None)
        if reasoning_effort and "reasoning_effort" not in out:
            out["reasoning_effort"] = reasoning_effort
    # OpenAI streams carry no token usage unless asked. The ask is limited to
    # OpenAI's own host because other OpenAI-compatible servers may reject
    # the field; the final chunk it adds has empty `choices`, which every
    # stream consumer here already skips.
    if out.get("stream") and "stream_options" not in out and is_openai_host(base_url):
        out["stream_options"] = {"include_usage": True}
    return out
