"""Pure unit tests for `_tree_path`, stdlib only -- no home elsewhere.

Neither existing suite can reach this function directly: the backend
container never mounts or installs `latex-compiler/`, and this container
deliberately carries no pip/pytest (see the Dockerfile's "NO pip install"
comment -- a security boundary, not an oversight). `_tree_path` itself is
pure (no I/O, no subprocess), so it needs no container round trip to test --
just something that can import it. `unittest` is stdlib, so this adds no
dependency to the image.

Run: `docker compose exec -T latex-compiler python3 -m unittest test_tree_path -v`
(or `python3 test_tree_path.py` directly -- unittest.main() below makes both
work). Not wired into any CI job today; nothing currently invokes it
automatically.
"""

import unittest

from app import _tree_path


class TreePathTests(unittest.TestCase):
    def test_a_marker_at_position_zero_is_stripped_by_the_leading_dot_slash_check(self):
        # tail after the root-strip is "./main.tex" -- a LEADING "./", not an
        # interior "/./" -- so `replace("/./", "/")` is a no-op here and the
        # separate `startswith("./")` branch is what strips it. Proves that
        # branch is not dead code.
        self.assertEqual(_tree_path("/tmp/R/./main.tex", "/tmp/R"), "main.tex")

    def test_an_interior_marker_is_collapsed_and_the_main_dir_prefix_is_kept(self):
        self.assertEqual(
            _tree_path("/tmp/R/src/./chapters/intro.tex", "/tmp/R"),
            "src/chapters/intro.tex",
        )

    def test_a_parent_relative_input_normalises_to_its_real_tree_path(self):
        self.assertEqual(_tree_path("/tmp/R/src/../shared/x.tex", "/tmp/R"), "shared/x.tex")

    def test_an_input_that_still_escapes_after_normalisation_is_refused(self):
        self.assertIsNone(_tree_path("/tmp/R/../../etc/hostname", "/tmp/R"))

    def test_a_system_input_outside_the_compile_root_is_refused(self):
        self.assertIsNone(
            _tree_path(
                "/usr/local/texlive/2024/texmf-dist/tex/latex/base/article.cls",
                "/tmp/R",
            )
        )

    def test_an_empty_input_is_refused(self):
        self.assertIsNone(_tree_path("", "/tmp/R"))

    def test_multiple_markers_all_collapse(self):
        self.assertEqual(_tree_path("/tmp/R/./a/./b/./c.tex", "/tmp/R"), "a/b/c.tex")

    def test_the_root_alone_with_a_trailing_slash_and_nothing_after_it_is_refused(self):
        self.assertIsNone(_tree_path("/tmp/R/", "/tmp/R"))

    def test_a_sibling_directory_whose_name_merely_extends_the_root_is_not_a_prefix_match(self):
        # `/tmp/RX` is not `/tmp/R` plus a path separator -- a bare
        # `raw_input.startswith(root)` would wrongly accept it (eating "RX"'s
        # leading "R" as if it were the root, and returning "X/main.tex").
        # `compile_tree`'s own containment check compares against
        # `str(directory.resolve()) + os.sep` for exactly this reason; this
        # pins the same discipline here.
        self.assertIsNone(_tree_path("/tmp/RX/main.tex", "/tmp/R"))

    def test_the_root_with_no_trailing_content_at_all_is_refused(self):
        self.assertIsNone(_tree_path("/tmp/R", "/tmp/R"))


if __name__ == "__main__":
    unittest.main()
