"""The one BM25 tokenisation rule, shared by every BM25 index and query.

Index and query MUST tokenise identically, or a query term never matches its
own indexed form. `local_rag/index.py` and the retrieval harness both import
this module rather than repeating the stopword list and stemmer. PURE: only
`bm25s` and `Stemmer`, no DB, ORM or service imports.

`TOKENIZER_CONFIG` is stamped onto anything persisted from this tokenizer, so
a change here (stopwords, stemmer, version bump) can be detected as a stale
index instead of silently mixing two vocabularies.
"""

from __future__ import annotations

import bm25s
import Stemmer

TOKENIZER_CONFIG: dict = {"stopwords": "english", "stemmer": "english", "version": 1}


def _stemmer() -> Stemmer.Stemmer:
    return Stemmer.Stemmer(TOKENIZER_CONFIG["stemmer"])


def tokenize(texts: list[str], *, return_ids: bool):
    return bm25s.tokenize(
        texts,
        stopwords=TOKENIZER_CONFIG["stopwords"],
        stemmer=_stemmer(),
        return_ids=return_ids,
        show_progress=False,
    )


def terms(text: str) -> list[str]:
    """Stemmed, stopword-free tokens of one text; order and duplicates kept."""
    return list(tokenize([text], return_ids=False)[0])
