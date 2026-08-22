"""The `(n)` suffix rule. Pure string work -- no database anywhere in here."""

from app.services import latex_dedupe as dedupe


def test_a_free_path_is_returned_unchanged():
    assert dedupe.suffix_path("chapters/intro.tex", set()) == "chapters/intro.tex"


def test_the_suffix_goes_before_the_extension():
    assert dedupe.suffix_path("chapters/intro.tex", {"chapters/intro.tex"}) == (
        "chapters/intro (1).tex"
    )


def test_only_the_last_dot_counts_as_the_extension():
    # Chrome's rule, which is the behaviour this feature was specified
    # against: `data.tar.gz` keeps `.gz` and suffixes `data.tar`.
    assert dedupe.suffix_path("data.tar.gz", {"data.tar.gz"}) == "data.tar (1).gz"


def test_a_name_with_no_extension_gets_a_bare_suffix():
    assert dedupe.suffix_path("Makefile", {"Makefile"}) == "Makefile (1)"


def test_a_dotfile_is_not_treated_as_an_extension():
    # A leading dot names the file, it does not separate an extension.
    assert dedupe.suffix_path(".gitignore", {".gitignore"}) == ".gitignore (1)"


def test_the_counter_climbs_past_every_taken_suffix():
    taken = {"intro.tex", "intro (1).tex"}
    assert dedupe.suffix_path("intro.tex", taken) == "intro (2).tex"


def test_an_already_suffixed_name_is_not_unwrapped():
    # Chrome does this too. Unwrapping would let an upload silently claim a
    # slot the user never named.
    assert dedupe.suffix_path("intro (1).tex", {"intro (1).tex"}) == "intro (1) (1).tex"


def test_collision_testing_is_case_folded():
    # Case-only collisions now flow through here, so the counter must not
    # hand out a name that collides case-insensitively with a taken one.
    assert dedupe.suffix_path("Fig.png", {"fig.png"}) == "Fig (1).png"
    assert dedupe.suffix_path("Fig.png", {"fig.png", "FIG (1).PNG"}) == "Fig (2).png"


def test_the_directory_part_is_never_touched():
    # Only files collide. Two projects sharing a `figures/` directory is not
    # a collision and must not become `figures (1)/`.
    assert dedupe.suffix_path("figures/plot.png", {"figures/plot.png"}) == ("figures/plot (1).png")


def test_a_whole_batch_is_numbered_against_one_growing_set():
    # Two incoming files with the same name must not both claim `(1)`.
    result = dedupe.plan_writes(["plot.png", "plot.png"], {"plot.png"})
    assert [c.suggestion for c in result] == ["plot (1).png", "plot (2).png"]


def test_plan_writes_reports_only_the_paths_that_collide():
    result = dedupe.plan_writes(["a.tex", "b.tex"], {"a.tex"})
    assert [(c.path, c.existing, c.suggestion) for c in result] == [("a.tex", "a.tex", "a (1).tex")]


def test_plan_writes_reports_the_existing_path_in_its_own_spelling():
    # The user needs to see the file they actually have, not a folded key.
    result = dedupe.plan_writes(["Fig.png"], {"fig.PNG"})
    assert result[0].existing == "fig.PNG"


def test_a_document_name_is_suffixed_without_extension_handling():
    # `My Paper v1.2` must not become `My Paper v1 (1).2`.
    assert dedupe.suffix_name("My Paper v1.2", {"My Paper v1.2"}) == "My Paper v1.2 (1)"
