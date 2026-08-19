"""Picking the main file and the engine. Detection NEVER picks between
equals -- the same discipline paper_resolver.py holds to, for the same
reason: a confident wrong answer costs more than an honest question."""

import pytest

from app.services.latex_detect import (
    AmbiguousMain,
    NoMainFile,
    detect_engine,
    detect_main,
)

DOC = b"\\documentclass{article}\n\\begin{document}\\end{document}"


def test_the_only_file_with_documentclass_wins():
    assert detect_main([("paper.tex", DOC), ("refs.bib", b"@book{}")]) == "paper.tex"


def test_a_chapter_without_documentclass_is_not_a_candidate():
    assert detect_main([("main.tex", DOC), ("chapters/intro.tex", b"\\section{I}")]) == "main.tex"


def test_a_root_level_main_tex_wins_a_tie():
    assert detect_main([("main.tex", DOC), ("other.tex", DOC)]) == "main.tex"


def test_a_root_level_paper_tex_wins_a_tie():
    assert detect_main([("paper.tex", DOC), ("appendix.tex", DOC)]) == "paper.tex"


def test_a_nested_main_tex_does_not_win_by_name_alone():
    """The preferred names only win at the ROOT; a src/main.tex against a
    root-level other.tex is a genuine tie on depth."""
    with pytest.raises(AmbiguousMain):
        detect_main([("src/main.tex", DOC), ("other.tex", DOC), ("third.tex", DOC)])


def test_the_shallowest_documentclass_file_wins():
    assert detect_main([("deep/nested/a.tex", DOC), ("b.tex", DOC)]) == "b.tex"


def test_a_genuine_tie_raises_with_every_candidate_listed():
    with pytest.raises(AmbiguousMain) as exc:
        detect_main([("a.tex", DOC), ("b.tex", DOC)])
    assert sorted(exc.value.paths) == ["a.tex", "b.tex"]


def test_no_documentclass_anywhere_raises():
    with pytest.raises(NoMainFile):
        detect_main([("chapters/intro.tex", b"\\section{I}"), ("refs.bib", b"@book{}")])


def test_a_commented_out_documentclass_does_not_count():
    """main.tex's declaration is commented out, so it must not be a
    candidate at all -- and it must not win on its preferred name either."""
    assert (
        detect_main(
            [
                ("main.tex", b"% \\documentclass{article}\n\\section{old draft}"),
                ("real.tex", DOC),
            ]
        )
        == "real.tex"
    )


def test_a_binary_file_named_tex_does_not_crash_detection():
    assert detect_main([("main.tex", DOC), ("weird.tex", b"\xff\xfe\x00\x00")]) == "main.tex"


def test_fontspec_selects_xelatex():
    assert detect_engine("\\usepackage{fontspec}") == "xelatex"


def test_unicode_math_selects_xelatex():
    assert detect_engine("\\usepackage{unicode-math}") == "xelatex"


def test_polyglossia_selects_xelatex():
    assert detect_engine("\\usepackage{polyglossia}") == "xelatex"


def test_a_plain_article_selects_pdflatex():
    assert detect_engine("\\documentclass{article}\\usepackage{graphicx}") == "pdflatex"


def test_a_commented_fontspec_does_not_select_xelatex():
    assert detect_engine("% \\usepackage{fontspec}") == "pdflatex"


def test_fontspec_among_several_packages_selects_xelatex():
    assert detect_engine("\\usepackage{amsmath,fontspec,graphicx}") == "xelatex"


def test_a_non_tex_file_containing_documentclass_text_is_not_a_candidate():
    """The .tex extension filter is load-bearing: a .bib or .txt file that
    happens to contain the literal string is never a candidate."""
    assert (
        detect_main(
            [
                ("real.tex", DOC),
                ("notes.txt", b"\\documentclass{article}"),
                ("refs.bib", b"% \\documentclass{article}"),
            ]
        )
        == "real.tex"
    )


def test_two_different_preferred_root_names_both_declaring_is_a_genuine_tie():
    """A leftover main.tex beside a renamed paper.tex is a common real shape.
    Neither name is more canonical than the other -- detection must not
    guess between them."""
    with pytest.raises(AmbiguousMain) as exc:
        detect_main([("main.tex", DOC), ("paper.tex", DOC)])
    assert sorted(exc.value.paths) == ["main.tex", "paper.tex"]


def test_a_package_name_that_merely_contains_fontspec_does_not_select_xelatex():
    assert detect_engine("\\usepackage{nofontspec}") == "pdflatex"
    assert detect_engine("\\usepackage{fontspec-xyz}") == "pdflatex"
    assert detect_engine("We discuss unicode-math notation in the appendix.") == "pdflatex"
