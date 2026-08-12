from app.services.text_matching import normalize_for_match, word_tokens


def test_normalize_folds_case_accents_and_punctuation():
    assert normalize_for_match("Güven's Multi-Agent RL: A Survey!") == (
        "guven s multi agent rl a survey"
    )


def test_normalize_collapses_whitespace_including_newlines():
    assert normalize_for_match("swarm\n  coordination\tunder   fire") == (
        "swarm coordination under fire"
    )


def test_normalize_is_idempotent():
    once = normalize_for_match("Évsen Yanmaz — UAV Networks")
    assert normalize_for_match(once) == once


def test_normalize_handles_empty_and_whitespace_only():
    assert normalize_for_match("") == ""
    assert normalize_for_match("   \n\t ") == ""


def test_word_tokens_splits_a_normalized_string():
    assert word_tokens("deep reinforcement learning") == [
        "deep",
        "reinforcement",
        "learning",
    ]


def test_word_tokens_normalizes_first():
    assert word_tokens("Deep  Reinforcement-Learning!") == [
        "deep",
        "reinforcement",
        "learning",
    ]
