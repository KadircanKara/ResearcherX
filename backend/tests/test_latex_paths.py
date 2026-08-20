"""Path rules for the LaTeX file tree.

One test per rejection rule. These are the guards a malicious zip meets first
in plan 2, so each one is asserted on its own rather than folded into a table
test where a silently-passing case would hide.
"""

import pytest

from app.services.latex_paths import (
    MAX_DEPTH,
    MAX_NAME_LENGTH,
    MAX_PATH_LENGTH,
    InvalidPath,
    collision_key,
    normalize_path,
)


def test_a_plain_relative_path_survives_unchanged():
    assert normalize_path("chapters/intro.tex") == "chapters/intro.tex"


def test_a_leading_dot_slash_is_stripped():
    assert normalize_path("./main.tex") == "main.tex"


def test_interior_dot_segments_are_stripped():
    assert normalize_path("figures/./fig1.png") == "figures/fig1.png"


def test_a_parent_segment_is_rejected():
    with pytest.raises(InvalidPath) as exc:
        normalize_path("../../etc/passwd")
    assert exc.value.path == "../../etc/passwd"


def test_a_parent_segment_buried_mid_path_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("figures/../../etc/passwd")


def test_an_absolute_path_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("/app/.env")


def test_a_drive_letter_path_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("C:/Windows/system32/drivers/etc/hosts")


def test_a_backslash_is_rejected_rather_than_treated_as_a_separator():
    with pytest.raises(InvalidPath):
        normalize_path("figures\\fig1.png")


def test_a_nul_byte_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("main.tex\x00.png")


def test_a_newline_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("main\n.tex")


def test_a_double_slash_is_rejected_rather_than_collapsed():
    with pytest.raises(InvalidPath):
        normalize_path("figures//fig1.png")


def test_a_trailing_slash_is_rejected_because_directories_are_not_stored():
    with pytest.raises(InvalidPath):
        normalize_path("figures/")


def test_an_empty_path_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("")


def test_a_whitespace_only_path_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("   ")


def test_a_path_of_only_dot_segments_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("./.")


def test_a_segment_longer_than_the_name_limit_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("a" * (MAX_NAME_LENGTH + 1) + ".tex")


def test_a_path_deeper_than_the_depth_limit_is_rejected():
    with pytest.raises(InvalidPath):
        normalize_path("/".join(["d"] * (MAX_DEPTH + 1)) + "/main.tex")


def test_a_path_longer_than_the_total_limit_is_rejected():
    """Every segment is inside MAX_NAME_LENGTH and the depth is inside
    MAX_DEPTH, so only the TOTAL length rule can reject this. A test that
    trips the segment rule instead would leave the total rule unexercised."""
    path = "/".join(["s" * 25] * MAX_DEPTH)  # 16*25 + 15 separators = 415
    assert len(path) > MAX_PATH_LENGTH
    with pytest.raises(InvalidPath):
        normalize_path(path)


def test_the_reason_is_carried_for_the_error_message():
    with pytest.raises(InvalidPath) as exc:
        normalize_path("/app/.env")
    assert exc.value.reason
    assert "/app/.env" in str(exc.value)


def test_collision_key_folds_case_so_two_spellings_of_one_file_collide():
    assert collision_key("Figures/Fig1.PNG") == collision_key("figures/fig1.png")
