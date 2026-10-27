import inspect

import Stemmer

from app.local_rag import index
from app.services import bm25_tokenizer


def test_terms_stem_and_drop_stopwords():
    expected = Stemmer.Stemmer("english").stemWords(["drones", "flying"])
    assert bm25_tokenizer.terms("The drones flying") == expected


def test_terms_keep_order_and_duplicates():
    stem = Stemmer.Stemmer("english").stemWord
    assert bm25_tokenizer.terms("drone swarm drone") == [
        stem("drone"),
        stem("swarm"),
        stem("drone"),
    ]


def test_local_rag_uses_the_shared_tokenizer():
    assert not hasattr(index, "_tokenize")
    assert "bm25_tokenizer" in inspect.getsource(index)
    hits = index.build_bm25(["drone swarm search", "protein folding"]).search("drone swarm", 1)
    assert [doc for doc, _ in hits] == [0]


def test_config_is_versioned():
    assert bm25_tokenizer.TOKENIZER_CONFIG["version"] == 1
