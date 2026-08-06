"""Recomposing PDF-mangled diacritics back into precomposed characters."""

from app.services.text_normalization import recompose_diacritics


def test_below_accent_attaches_to_the_preceding_letter():
    """Cedilla renders below, so PDF extraction emits it AFTER its base letter."""
    assert recompose_diacritics("Evs¸en Yanmaz") == "Evşen Yanmaz"


def test_above_accent_attaches_to_the_following_letter():
    """Diaeresis and dot-above render above, so they are emitted BEFORE their letter."""
    assert recompose_diacritics("˙Islam G¨uven") == "İslam Güven"


def test_handles_both_directions_in_one_string():
    assert recompose_diacritics("C¸ağrı Jos´e") == "Çağrı José"


def test_clean_text_is_unchanged():
    assert recompose_diacritics("Kadircan Kara") == "Kadircan Kara"
    assert recompose_diacritics("Evşen Yanmaz") == "Evşen Yanmaz"


def test_is_idempotent():
    """Backfill may run more than once; a second pass must not corrupt the first."""
    once = recompose_diacritics("Evs¸en Yanmaz")
    assert recompose_diacritics(once) == once


def test_ascii_backtick_is_left_alone():
    """U+0060 is Unicode category Sk, but it is a plain ASCII backtick. A
    category-based rule would turn `NSGA-II` into ǸSGA-II."""
    assert recompose_diacritics("`NSGA-II`") == "`NSGA-II`"


def test_ascii_caret_is_left_alone():
    assert recompose_diacritics("x^2 + y^2") == "x^2 + y^2"


def test_unattachable_modifier_is_preserved_not_dropped():
    """Never silently delete content. A modifier with no letter to bind to stays."""
    assert recompose_diacritics("¨") == "¨"
    assert recompose_diacritics("Kara ¸") == "Kara ¸"


def test_empty_string():
    assert recompose_diacritics("") == ""
