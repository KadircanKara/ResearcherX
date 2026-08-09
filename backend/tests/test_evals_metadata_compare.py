"""Metadata verdicts. Pure functions — no DB, no LLM."""

import pytest

from evals.metadata.compare import compare_authors, compare_scalar, normalize


def test_normalize_folds_pdf_mangled_diacritics():
    """PDF extraction stores 'G¨uven'; the real name is 'Güven'. Same person."""
    assert normalize("˙Islam G¨uven") == normalize("İslam Güven")
    assert normalize("Evs¸en Yanmaz") == normalize("Evşen Yanmaz")
    assert normalize("Evşen Yanmaz") == normalize("Evsen Yanmaz")


def test_normalize_strips_spacing_modifiers_before_decomposing():
    """NFKD expands U+00A8 to space + combining mark. Decomposing first would
    turn 'G¨uven' into 'G uven' and it would never match 'Güven'."""
    assert normalize("G¨uven") == "guven"


def test_normalize_is_case_and_whitespace_insensitive():
    assert normalize("  KADIRCAN   KARA ") == "kadircan kara"


def test_authors_correct_ignores_order():
    assert compare_authors(["A One", "B Two"], ["B Two", "A One"]) == "correct"


def test_authors_correct_across_encodings():
    assert compare_authors(["İslam Güven"], ["˙Islam G¨uven"]) == "correct"


def test_authors_missing_one_is_wrong_not_partial():
    """A list is one answer. Two of three authors is a wrong author list."""
    assert compare_authors(["A One", "B Two", "C Three"], ["A One", "B Two"]) == "wrong"


def test_authors_extra_one_is_wrong():
    assert compare_authors(["A One"], ["A One", "B Two"]) == "wrong"


def test_authors_missed_when_truth_exists_and_nothing_extracted():
    assert compare_authors(["A One"], []) == "missed"


def test_authors_hallucinated_when_truth_is_absent():
    assert compare_authors([], ["A One"]) == "hallucinated"


def test_authors_correct_when_both_absent():
    assert compare_authors([], []) == "correct"


@pytest.mark.parametrize(
    "truth,got,verdict",
    [
        (2024, 2024, "correct"),
        (2024, 2019, "wrong"),
        (2024, None, "missed"),
        (None, 2024, "hallucinated"),
        (None, None, "correct"),
    ],
)
def test_scalar_verdicts(truth, got, verdict):
    assert compare_scalar(truth, got) == verdict


def test_scalar_venue_comparison_is_normalised():
    assert compare_scalar("IEEE ICRA", "ieee   icra") == "correct"


def test_scalar_blank_string_counts_as_absent():
    """An empty venue is absence, not a value that happens to be empty."""
    assert compare_scalar(None, "   ") == "correct"
    assert compare_scalar("ICRA", "") == "missed"
