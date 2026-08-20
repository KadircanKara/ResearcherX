"""Pure unit tests for `analyse_log`, stdlib only -- the same home, and the
same reasons, as `test_tree_path.py`: neither existing suite can import this
module (the backend container never mounts `latex-compiler/`, and this image
deliberately carries no pytest), and `analyse_log` is pure once its two
filesystem questions are injected.

EVERY FIXTURE IN `fixtures/` IS A REAL LOG, captured verbatim from
`researcherx-latex-compiler-1` (TeX Live 2026, latexmk 4.88) by running the
exact argv `compile_tree` uses, with `max_print_line=10000` unless the name
says otherwise. Nothing here is hand-written, and that is the point: the
attempt this code replaces shipped green against synthetic fixtures that
agreed with the parser instead of with TeX.

Run: `python3 test_analyse_log.py` (wired into the `latex-compiler` CI job).
"""

import pathlib
import re
import unittest

from app import analyse_log, engine_errored

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

TEXLIVE = "/usr/local/texlive/"


def analyse(
    name, staged, counts, main_dir="", on_disk=(), driver=None, driver_engine="pdflatex"
):
    """Run the analyser over a captured log.

    `staged` is the tree as the compiler staged it, `counts` is each staged
    file's line count, and `on_disk` names files that exist in the compile
    directory WITHOUT having been staged -- a `\\openout` the document
    performed on itself is the real case, and the distinction is load-
    bearing (see `test_a_document_that_writes_its_own_input_...`).

    `driver` names a captured latexmk stdout under `fixtures/driver/`, from
    which the `errored` gate is COMPUTED rather than asserted by hand. When
    it is omitted the fixture is from a run that really did error and the
    gate is True -- the interesting cases pass it explicitly.
    """
    log = (FIXTURES / f"{name}.log").read_text()
    present = set(staged) | set(on_disk)

    def exists(printed):
        rel = printed[2:] if printed.startswith("./") else printed
        return rel in present or rel.startswith(TEXLIVE)

    errored = (
        engine_errored(
            (FIXTURES / "driver" / f"{driver}.out").read_text(), driver_engine
        )
        if driver
        else True
    )
    return analyse_log(
        log, set(staged), main_dir, lambda p: counts.get(p), exists, errored=errored
    )


class AttributionTests(unittest.TestCase):
    """The jump must land on the right file and line, or not be offered."""

    def test_an_error_in_a_chapter_is_attributed_to_the_chapter(self):
        for name in ("legit_chapter", "legit_chapter_xelatex"):
            with self.subTest(fixture=name):
                _log, file, line = analyse(
                    name,
                    {"main.tex", "chapters/intro.tex"},
                    {"main.tex": 5, "chapters/intro.tex": 21},
                )
                self.assertEqual((file, line), ("chapters/intro.tex", 21))

    def test_both_engines_are_covered_because_only_pdflatex_writes_a_fatal_line(self):
        """xelatex ends a failed run with `No pages of output.` and no
        `==> Fatal error occurred` line at all -- measured. An earlier
        version of this analyser anchored on that line and silently declined
        every xelatex error; the fixture pair above is what caught it."""
        pdftex = (FIXTURES / "legit_chapter.log").read_text()
        xetex = (FIXTURES / "legit_chapter_xelatex.log").read_text()
        self.assertIn("==> Fatal error occurred", pdftex)
        self.assertNotIn("==> Fatal error occurred", xetex)

    def test_a_colon_in_a_filename_is_attributed_and_not_dropped(self):
        """`chapters/a:b.tex` is a legal path here. The withdrawn regex could
        not match its error line, and with `-file-line-error` on there was no
        `^!` line either, so the client was handed TeX's memory statistics --
        wrong however attribution turned out."""
        excerpt, file, line = analyse(
            "colon_name", {"main.tex", "chapters/a:b.tex"}, {"chapters/a:b.tex": 3}
        )
        self.assertEqual((file, line), ("chapters/a:b.tex", 2))
        self.assertTrue(
            excerpt.startswith("./chapters/a:b.tex:2: Undefined control sequence.")
        )

    def test_a_space_in_a_path_is_attributed(self):
        """TeX prints paths unquoted, so a space cannot terminate one."""
        _log, file, line = analyse(
            "space_name",
            {"main.tex", "my chapter/deep.tex"},
            {"my chapter/deep.tex": 3},
        )
        self.assertEqual((file, line), ("my chapter/deep.tex", 2))

    def test_a_users_own_package_in_the_tree_is_a_staged_file_like_any_other(self):
        _log, file, line = analyse(
            "sty_error", {"main.tex", "mysty.sty"}, {"mysty.sty": 3}
        )
        self.assertEqual((file, line), ("mysty.sty", 2))

    def test_prose_echoed_by_an_overfull_hbox_does_not_become_the_answer(self):
        """The document's text contains `chapters/intro.tex:5: Undefined
        control sequence.` inside a paragraph TeX overfills and echoes back.
        With `max_print_line` raised the echo is ONE line, and it begins with
        TeX's font selector, which no path can."""
        excerpt, file, line = analyse(
            "overfull_prose",
            {"main.tex", "chapters/intro.tex"},
            {"main.tex": 8, "chapters/intro.tex": 21},
        )
        self.assertIn(
            "chap-ters/intro.tex:5:", (FIXTURES / "overfull_prose.log").read_text()
        )
        self.assertEqual((file, line), ("chapters/intro.tex", 21))
        self.assertTrue(excerpt.startswith("./chapters/intro.tex:21:"))

    def test_an_errmessage_quoting_a_path_is_attributed_to_where_it_was_raised(self):
        r"""`\errmessage{./chapters/decoy.tex:3: ...}` makes TeX print its own
        prefix in front of the user's text: `./main.tex:4: ./chapters/
        decoy.tex:3: ...`. Both splits are enumerated and only one names a
        staged file, so the compiler's fact wins over the document's text."""
        _log, file, line = analyse(
            "errmessage_forged",
            {"main.tex", "chapters/decoy.tex"},
            {"main.tex": 6, "chapters/decoy.tex": 4},
        )
        self.assertEqual((file, line), ("main.tex", 4))


class DeclineTests(unittest.TestCase):
    """A jump that never happens is a mild disappointment. A jump to the
    wrong file is the failure this whole module exists to prevent."""

    def test_a_typeout_forging_a_whole_error_block_wins_nothing(self):
        r"""`\typeout{./chapters/intro.tex:5: Undefined control sequence.^^Jl.5
        \fakemacro}` in the preamble. It names a file that really is in the
        tree, at a line that really exists, with a matching `l.<n>` -- every
        text-level check passes. It loses because the search runs at the END
        of the log and because a second candidate makes the block
        ambiguous."""
        for name in ("typeout_forged", "typeout_forged_xelatex"):
            with self.subTest(fixture=name):
                _log, file, line = analyse(
                    name,
                    {"main.tex", "chapters/intro.tex"},
                    {"main.tex": 6, "chapters/intro.tex": 21},
                )
                self.assertEqual((file, line), (None, None))

    def test_an_errhelp_forgery_inside_texs_own_closing_block_declines(self):
        r"""The one attack that survived every other rule. `\errhelp` puts
        arbitrary text into the help paragraph TeX prints AFTER the error --
        inside the closing block, below the real error, fully shaped and
        fully corroborated -- and under xelatex there is no fatal line to
        contradict it. Two candidates in the block is unknowable, and
        unknowable declines."""
        for name in ("errhelp_pdflatex", "errhelp_xelatex"):
            with self.subTest(fixture=name):
                _log, file, line = analyse(
                    name,
                    {"main.tex", "chapters/decoy.tex"},
                    {"main.tex": 7, "chapters/decoy.tex": 4},
                )
                self.assertEqual((file, line), (None, None))

    def test_an_errhelp_forgery_cannot_be_pushed_out_of_range_by_padding_it(self):
        r"""The reason candidates are counted over the WHOLE log rather than a
        window above the statistics. `\errhelp` sets how long TeX's help
        paragraph is, so a document can pad it until the real error falls
        outside any fixed window and only the forgery is left inside.
        Measured: with a 45-line pad the real error sits 47 lines above the
        statistics, and a 40-line window sees exactly ONE candidate -- the
        forgery -- which under xelatex (no fatal line to contradict it) is a
        confident jump into `chapters/decoy.tex`. Whole-log counting has
        nothing to be pushed out of."""
        for name in ("errhelp_padded_pdflatex", "errhelp_padded_xelatex"):
            with self.subTest(fixture=name):
                _log, file, line = analyse(
                    name,
                    {"main.tex", "chapters/decoy.tex"},
                    {"main.tex": 7, "chapters/decoy.tex": 4},
                )
                self.assertEqual((file, line), (None, None))

    def test_a_realistic_paper_still_gets_its_jump(self):
        """The cost of counting over the whole log is false declines, so this
        pins that a REAL paper does not pay it: IEEEtran with graphicx,
        amsmath and hyperref, a 377-line log, one candidate, one jump."""
        _log, file, line = analyse(
            "ieee_paper",
            {"paper.tex", "chapters/body.tex"},
            {"paper.tex": 15, "chapters/body.tex": 5},
        )
        self.assertEqual((file, line), ("chapters/body.tex", 5))

    def test_a_document_that_writes_its_own_input_cannot_launder_a_forgery(self):
        r"""`\openout` lets a document create a file mid-run and `\input` it.
        That file EXISTS but was never STAGED, so it can never be attributed
        -- and it still COUNTS as a candidate, which is what stops a forgery
        beside it from being the only one left. The two questions are
        deliberately different: `staged` decides attribution, `exists`
        decides counting."""
        _log, file, line = analyse(
            "generated_errhelp_pdflatex",
            {"main.tex", "chapters/decoy.tex"},
            {"main.tex": 9, "chapters/decoy.tex": 4},
            on_disk={"generated.tex"},
        )
        self.assertEqual((file, line), (None, None))

    def test_a_wrapped_long_path_is_refused_even_though_its_suffix_is_a_real_file(self):
        """Class 1, and it needs no adversary: at TeX's default 79-column
        print width a path over ~77 characters is SPLIT, and the continuation
        fragment is a suffix that still parses. This fixture's tree also
        contains `chapters/intro.tex`, so the fragment names a file that
        really exists. `max_print_line` is what removes the wrap; this proves
        the tree cross-check catches it anyway if that ever regresses."""
        deep = "chapters/" + "d" * 76 + "/chapters/intro.tex"
        staged = {"main.tex", deep, "chapters/intro.tex"}
        counts = {"main.tex": 1, deep: 3, "chapters/intro.tex": 2}
        _log, file, line = analyse("long_path_wrapped_on", staged, counts)
        self.assertEqual((file, line), (None, None))

    def test_the_same_tree_is_attributed_correctly_once_wrapping_is_off(self):
        deep = "chapters/" + "d" * 76 + "/chapters/intro.tex"
        staged = {"main.tex", deep, "chapters/intro.tex"}
        counts = {"main.tex": 1, deep: 3, "chapters/intro.tex": 2}
        _log, file, line = analyse("long_path_wrapped_off", staged, counts)
        self.assertEqual((file, line), (deep, 2))

    def test_a_missing_package_reports_the_cause_and_offers_no_jump(self):
        """`Emergency stop.` is fallout: TeX reports it at whatever line it
        had reached, several lines from the `\\usepackage` that failed. The
        cause names no line at all. Reporting the cause without a jump is
        this codebase's existing choice for exactly this case."""
        excerpt, file, line = analyse("missing_pkg", {"main.tex"}, {"main.tex": 6})
        self.assertEqual((file, line), (None, None))
        self.assertTrue(
            excerpt.startswith("! LaTeX Error: File `nopesuchpkg.sty' not found.")
        )

    def test_an_error_with_no_position_at_all_reports_its_message_only(self):
        """`! File ended while scanning use of \\textbf .` -- TeX names no
        file and no line, and the fatal line loses its path too. The document
        also forges a located block in its preamble, which changes nothing."""
        excerpt, file, line = analyse(
            "runaway_forged",
            {"main.tex", "chapters/intro.tex"},
            {"main.tex": 8, "chapters/intro.tex": 2},
        )
        self.assertEqual((file, line), (None, None))
        # The document ALSO forged a located block close enough to the real
        # error to make the closing block ambiguous, so the excerpt shows the
        # whole block rather than headlining either one.
        self.assertIn("! File ended while scanning use of", excerpt)


class NoErrorHappenedTests(unittest.TestCase):
    """A compile can FAIL without any error being raised, and everything the
    candidate scan does assumes one was. This is the hole that shipped."""

    def test_the_witness_separates_a_real_error_from_every_other_outcome(self):
        """Measured across BOTH engines and every run shape -- the whole
        table is in `fixtures/driver/`, captured from latexmk itself with
        stdout and stderr kept in SEPARATE files (`.out` / `.err`), which is
        how they arrive and how they must be read."""
        for case in (
            "genuine_error",
            "chapter_error",
            "missing_pkg",
            "bibtex_and_error",
        ):
            for eng in ("pdflatex", "xelatex"):
                with self.subTest(case=case, engine=eng):
                    out = (FIXTURES / "driver" / f"{case}_{eng}.out").read_text()
                    self.assertTrue(engine_errored(out, eng))
        for case in (
            "no_pages_forged",
            "no_pages_plain",
            "success",
            "filename_attack",
            "marker_named_file",
            "bibtex_fail",
            "bibtex_jobname_collision",
        ):
            for eng in ("pdflatex", "xelatex"):
                with self.subTest(case=case, engine=eng):
                    out = (FIXTURES / "driver" / f"{case}_{eng}.out").read_text()
                    self.assertFalse(engine_errored(out, eng))

    def test_the_witness_is_read_from_stdout_alone(self):
        r"""THE round-5 break. stdout and stderr are separately buffered
        pipes: concatenating them puts EVERY stderr line after EVERY stdout
        line whatever their real order, and latexmk writes
        `Failure to make '<target>'` -- which names the user's own file --
        to stderr. So a project whose main file is called
        `Command for 'x' gave return code 1.tex` (spaces and quotes are
        legal here) put the witness sentence after latexmk's summary on the
        merged stream.

        This asserts the DEFECT is real on the merged stream under the old
        free-regex rule, and that reading stdout alone closes it. Without
        the first half the test could pass for an unrelated reason.
        """
        old_rule = re.compile(r"Command for '[^']*' gave return code [1-9]\d*")
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                out = (FIXTURES / "driver" / f"filename_attack_{eng}.out").read_text()
                err = (FIXTURES / "driver" / f"filename_attack_{eng}.err").read_text()
                merged = out + err
                marker = merged.rfind("Collected error summary")
                self.assertTrue(
                    old_rule.search(merged[marker:]), "attack no longer reproduces"
                )
                self.assertFalse(
                    old_rule.search(out[out.rfind("Collected error summary") :])
                )
                self.assertFalse(engine_errored(out, eng))

    def test_the_block_parse_holds_even_if_the_streams_were_merged_again(self):
        """The second, INDEPENDENT defence. Reading stdout alone and parsing
        the block structurally must both fail before the gate can open, so
        this feeds the analyser exactly what broke it -- the merged stream --
        and requires the answer to still be no."""
        for case in (
            "filename_attack",
            "no_pages_forged",
            "no_pages_plain",
            "bibtex_fail",
        ):
            for eng in ("pdflatex", "xelatex"):
                with self.subTest(case=case, engine=eng):
                    out = (FIXTURES / "driver" / f"{case}_{eng}.out").read_text()
                    err = (FIXTURES / "driver" / f"{case}_{eng}.err").read_text()
                    self.assertFalse(engine_errored(out + err, eng))

    def test_a_failing_bibliography_is_not_read_as_a_tex_error(self):
        r"""latexmk's block carries one entry per failing RULE, and a failing
        `bibtex` contributes its own: `  bibtex <jobname>: Bibtex errors:
        See file '<jobname>.blg'`. That is an indented line inside the block
        carrying the jobname TWICE -- so with the jobname
        `Command for 'pdflatex' gave return code 1` the witness sentence
        appears inside a legitimate entry. Requiring the entry's RULE to be
        the engine refuses it, and refuses to call a bibliography failure a
        TeX error at the same time."""
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                out = (
                    FIXTURES / "driver" / f"bibtex_jobname_collision_{eng}.out"
                ).read_text()
                self.assertIn("bibtex Command for 'pdflatex' gave return code 1:", out)
                self.assertFalse(engine_errored(out, eng))
                # ... while a bibliography failure ALONGSIDE a real error
                # still opens the gate: every entry is checked, not the first.
                both = (FIXTURES / "driver" / f"bibtex_and_error_{eng}.out").read_text()
                self.assertTrue(engine_errored(both, eng))

    def test_an_engine_this_service_does_not_offer_falls_back_like_the_flag_does(self):
        """`_ENGINE_FLAG` maps an unknown engine to pdflatex, so the rule
        name latexmk prints is pdflatex too. The witness has to resolve it
        the same way or every compile under a mistyped engine would decline."""
        out = (FIXTURES / "driver" / "genuine_error_pdflatex.out").read_text()
        self.assertTrue(engine_errored(out, "lualatex"))
        self.assertFalse(engine_errored(out, "xelatex"))

    def test_a_forgery_standing_alone_on_a_no_pages_run_is_not_attributed(self):
        r"""THE round-4 regression, and the configuration the forgery rule was
        never tested in. Four lines of LaTeX -- two `	ypeout`s and an empty
        document -- fail with NO error and NO PDF, so the forged block is the
        ONLY candidate in the log and every honest check passes: the path is
        staged, `l.3` corroborates, the file really has 5 lines, and neither
        engine writes a fatal line here to contradict it."""
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                _log, file, line = analyse(
                    f"no_pages_forged_{eng}",
                    {"main.tex", "chapters/decoy.tex"},
                    {"main.tex": 5, "chapters/decoy.tex": 5},
                    driver=f"no_pages_forged_{eng}",
                    driver_engine=eng,
                )
                self.assertEqual((file, line), (None, None))

    def test_the_gate_is_the_only_thing_stopping_that_forgery(self):
        """The same fixture with the gate forced open attributes the forgery
        in full. Without this, a later change could make the decline above
        pass for some unrelated reason and nobody would notice the gate had
        stopped doing anything."""
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                _log, file, line = analyse(
                    f"no_pages_forged_{eng}",
                    {"main.tex", "chapters/decoy.tex"},
                    {"main.tex": 5, "chapters/decoy.tex": 5},
                )
                self.assertEqual((file, line), ("chapters/decoy.tex", 3))

    def test_a_no_pages_failure_says_so_instead_of_headlining_the_forgery(self):
        """Declining the jump must not decline the explanation. On this path
        the only error-shaped line in the log is the document's own, so
        headlining it would state an error that never happened; TeX's own
        `No pages of output.` is the truth and is what the user needs."""
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                excerpt, _file, _line = analyse(
                    f"no_pages_forged_{eng}",
                    {"main.tex", "chapters/decoy.tex"},
                    {"main.tex": 5, "chapters/decoy.tex": 5},
                    driver=f"no_pages_forged_{eng}",
                    driver_engine=eng,
                )
                self.assertTrue(
                    excerpt.startswith("The document produced no pages"), excerpt[:80]
                )
                self.assertNotIn("Undefined control sequence", excerpt.splitlines()[0])

    def test_a_file_named_after_the_marker_cannot_move_where_the_block_begins(self):
        """latexmk echoes the file name in its `Running '...'` line, so a file
        called `Collected error summary (may duplicate other messages).tex`
        puts the marker text in the middle of a line. The marker is matched
        at the START of a line for exactly that reason."""
        for eng in ("pdflatex", "xelatex"):
            with self.subTest(engine=eng):
                out = (FIXTURES / "driver" / f"marker_named_file_{eng}.out").read_text()
                self.assertIn(
                    "Collected error summary (may duplicate other messages).tex", out
                )
                self.assertFalse(engine_errored(out, eng))

    def test_the_gate_defaults_to_closed(self):
        """A caller that forgets the argument declines rather than guesses."""
        log = (FIXTURES / "legit_chapter.log").read_text()
        self.assertEqual(
            analyse_log(
                log,
                {"main.tex", "chapters/intro.tex"},
                "",
                lambda p: 21,
                lambda p: True,
            )[1:],
            (None, None),
        )


class ExcerptTests(unittest.TestCase):
    def test_a_log_with_no_error_is_labelled_rather_than_passed_off_as_one(self):
        """Finding D. The old fallback returned the last 40 lines unlabelled,
        so a case it could not parse showed TeX's memory statistics under a
        'first error' heading. The tail is still the most useful thing
        available; it is simply no longer presented as something it is not."""
        log = (FIXTURES / "missing_pkg.log").read_text()
        head = "\n".join(log.splitlines()[:20])  # everything before the error
        excerpt, file, line = analyse_log(head, {"main.tex"}, "", lambda p: 6)
        self.assertEqual((file, line), (None, None))
        self.assertTrue(excerpt.startswith("No TeX error line was found in the log."))

    def test_an_empty_log_does_not_raise(self):
        self.assertEqual(analyse_log("", set(), "", lambda p: None)[1:], (None, None))


if __name__ == "__main__":
    unittest.main()
