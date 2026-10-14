"""stage_config_errors: a run must not measure a stage that did not build."""

from types import SimpleNamespace

from evals.groundedness.stages import stage_config_errors


def _s(**kw):
    base = dict(fusion_mode="rrf", rerank_enabled=True, rerank_provider="cohere")
    base.update(kw)
    return SimpleNamespace(**base)


def test_built_stages_pass():
    assert stage_config_errors(_s(), object(), None) == []
    assert stage_config_errors(_s(fusion_mode="jev", rerank_enabled=False), None, object()) == []


def test_arm_a_with_no_reranker_passes():
    assert stage_config_errors(_s(rerank_enabled=False), None, None) == []


def test_jev_fusion_without_scorer_is_refused():
    (err,) = stage_config_errors(_s(fusion_mode="jev", rerank_enabled=False), None, None)
    assert "FUSION_MODE=jev" in err and "DECISIONS_API_KEY" in err


def test_jev_rerank_without_key_is_refused():
    (err,) = stage_config_errors(_s(rerank_provider="jev"), None, None)
    assert "DECISIONS_API_KEY" in err


def test_cohere_without_key_names_arm_a():
    (err,) = stage_config_errors(_s(), None, None)
    assert "COHERE_API_KEY" in err and "RERANK_ENABLED=false" in err
