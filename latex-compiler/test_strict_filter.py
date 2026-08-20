"""Pure unit tests for `_strict_filter`, stdlib only -- the same home, and
the same reasons, as `test_tree_path.py` and `test_analyse_log.py`: neither
existing suite can import this module (the backend container never mounts
`latex-compiler/`, and this image deliberately carries no pytest), and
`_strict_filter` needs nothing but a `tarfile.TarInfo` to exercise.

These tests call `_strict_filter` DIRECTLY, not `normalize_path` in the
backend. That is the whole point: CLAUDE.md's own rule is that the backend's
`latex_archive` guard and this container's `_strict_filter` are two
INDEPENDENT traversal checks in two different processes, and "neither guard
is trusted to cover for the other being disabled or drifting." A test that
only proved the backend rejects a control character would still pass if
`_strict_filter`'s own control-character check were deleted -- exactly the
regression this file exists to catch.

Run: `python3 test_strict_filter.py` (wired into the `latex-compiler` CI job).
"""

import tarfile
import unittest

from app import _strict_filter


def _member(name: str) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name=name)
    member.type = tarfile.REGTYPE
    member.mode = 0o644
    member.size = 0
    return member


class StrictFilterTests(unittest.TestCase):
    def test_a_clean_relative_name_passes_through(self):
        member = _member("chapters/intro.tex")
        result = _strict_filter(member, "/tmp/dest")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "chapters/intro.tex")

    def test_an_absolute_name_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("/etc/passwd"), "/tmp/dest")

    def test_a_parent_relative_name_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("../../etc/passwd"), "/tmp/dest")

    def test_a_name_containing_a_newline_is_refused(self):
        # Guarded independently here -- not merely inherited from the
        # backend's `normalize_path`, which never runs in this process.
        # `data_filter` alone does not reject this: a newline is not an
        # absolute path and carries no ".." segment.
        with self.assertRaises(ValueError):
            _strict_filter(_member("chapters/intro\n.tex"), "/tmp/dest")

    def test_a_name_containing_a_carriage_return_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("chapters/intro\r.tex"), "/tmp/dest")

    def test_a_name_containing_a_nul_byte_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("chapters/intro\x00.tex"), "/tmp/dest")

    def test_a_name_containing_del_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("chapters/intro\x7f.tex"), "/tmp/dest")

    def test_a_name_containing_a_low_control_character_is_refused(self):
        with self.assertRaises(ValueError):
            _strict_filter(_member("chapters/\x01intro.tex"), "/tmp/dest")


if __name__ == "__main__":
    unittest.main()
