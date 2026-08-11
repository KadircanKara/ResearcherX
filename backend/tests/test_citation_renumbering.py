"""renumber_citations — pure text rewriting, no service or network involved."""

from app.services.chat_service import renumber_citations


def test_renumbers_by_order_of_first_appearance():
    """Not ascending by the model's own number: the model does not cite the
    catalog in numerical order, and ascending would produce prose reading
    '...as shown in [2] ... and in [1]'."""
    text = "Coverage [27] and connectivity [8], plus revisit time [14]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Coverage [1] and connectivity [2], plus revisit time [3]."
    assert mapping == {27: 1, 8: 2, 14: 3}


def test_a_repeated_marker_keeps_one_number():
    text = "First [8], then [14], back to [8]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "First [1], then [2], back to [1]."
    assert mapping == {8: 1, 14: 2}


def test_an_already_sequential_answer_is_unchanged():
    text = "One [1] and two [2]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == text
    assert mapping == {1: 1, 2: 2}


def test_an_out_of_range_marker_becomes_source_unavailable():
    """The model can cite past the end of the catalog. That marker points at
    nothing, so it must not consume a number in the new sequence either."""
    text = "Real [8] and invented [99]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Real [1] and invented [source unavailable]."
    assert mapping == {8: 1}


def test_an_answer_with_no_citations_is_unchanged():
    text = "No markers at all."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == text
    assert mapping == {}


def test_a_marker_inside_a_fenced_block_is_left_alone():
    """The chat prompt asks for fenced snippets, so this input is routine.
    Renumbering rewrites EVERY in-range marker, so without the code guard
    `arr[8]` would silently become `arr[1]` — a citation tidy-up corrupting
    working code."""
    text = "See [14].\n\n```python\nx = arr[8]\n```\n\nAlso [27]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "See [1].\n\n```python\nx = arr[8]\n```\n\nAlso [2]."
    assert mapping == {14: 1, 27: 2}


def test_a_marker_inside_an_inline_span_is_left_alone():
    text = "Use `arr[8]` for that [14]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Use `arr[8]` for that [1]."
    assert mapping == {14: 1}


def test_an_out_of_range_marker_inside_code_is_left_alone():
    """The pre-existing bug this fixes. Verified live on 2026-08-11: asking
    for a snippet indexing arr[6] returned `arr[source unavailable]`, in both
    an inline span and a fenced block."""
    text = "Text [8].\n\n```python\ny = arr[99]\n```"
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Text [1].\n\n```python\ny = arr[99]\n```"
    assert mapping == {8: 1}


def test_a_backtick_inside_a_fenced_block_does_not_open_an_inline_span():
    """Fenced blocks are matched before inline spans. Otherwise a stray
    backtick inside a block could swallow the rest of the answer as 'code'
    and stop everything after it from being renumbered."""
    text = "```\nuse ` here\n```\n\nAfter [8]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "```\nuse ` here\n```\n\nAfter [1]."
    assert mapping == {8: 1}


def test_an_unterminated_fence_runs_to_end_of_text():
    """Review round 1: a fence with no closing ``` used to fall through to
    the inline-span alternative, which consumed two of its three backticks as
    an empty span and left the fence body classified as prose — so a marker
    inside a truncated snippet got renumbered. Truncated answers are not
    hypothetical: a chat reply hit finish_reason=length mid-sentence on
    2026-08-10. The marker before the fence is still renumbered normally."""
    text = "Intro [8].\n\n```python\nx = arr[8]\n# cut off, no closing fence"
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Intro [1].\n\n```python\nx = arr[8]\n# cut off, no closing fence"
    assert mapping == {8: 1}


def test_a_stray_backtick_before_a_fence_does_not_leak_the_fenced_marker():
    """The other half of the round-1 bug: a single unpaired backtick earlier
    in the answer used to pair with the fence's own opening backtick, leaking
    the fenced [8] out into renumbering. [14] in the surrounding prose is
    still renumbered normally."""
    text = "Odd ` mark. ```python\ncode [8]\n``` Also [14]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Odd ` mark. ```python\ncode [8]\n``` Also [1]."
    assert mapping == {14: 1}


def test_malformed_input_is_byte_identical_outside_renumbered_markers():
    """Even when a stray backtick and an unterminated fence appear together,
    every byte outside the one renumbered marker site must survive
    untouched — nothing dropped, nothing reordered — including the [14] and
    the second [8] that sit inside the unterminated fence."""
    text = "Odd ` mark [8]. ```python\narr[8] # unterminated, runs to end of text\nstill code [14]"
    got, mapping = renumber_citations(text, max_n=40)
    assert got == text.replace("mark [8]", "mark [1]", 1)
    assert mapping == {8: 1}


def test_a_marker_inside_a_tilde_fenced_block_is_left_alone():
    """Final review: ~~~ is as valid a markdown fence as ```, and remark —
    the frontend's renderer — treats both as <pre><code>. Reported repro:
    "~~~python\\narr[8]\\n~~~" used to renumber to arr[1]."""
    text = "See [14].\n\n~~~python\nx = arr[8]\n~~~\n\nAlso [27]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "See [1].\n\n~~~python\nx = arr[8]\n~~~\n\nAlso [2]."
    assert mapping == {14: 1, 27: 2}


def test_an_unterminated_tilde_fence_runs_to_end_of_text():
    """Mirrors test_an_unterminated_fence_runs_to_end_of_text for the ~~~
    form: a fence with no matching closing delimiter still consumes to end
    of text, via the same backreference-driven rule, not a second special
    case."""
    text = "Intro [8].\n\n~~~python\nx = arr[8]\n# cut off, no closing fence"
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Intro [1].\n\n~~~python\nx = arr[8]\n# cut off, no closing fence"
    assert mapping == {8: 1}


def test_an_indented_code_block_is_a_documented_gap_not_detected_as_code():
    """Deliberate, documented current behaviour — see the comment above
    _FENCE_RE. A four-space indented block is markdown code too, but
    whether an indented run is a code block or a nested bullet's
    continuation content depends on list-nesting state this function does
    not track, and this chat's system prompt asks for "-" bullets and
    fenced code, not indented blocks. So the indented marker below IS
    currently renumbered rather than left alone: a false negative here was
    judged far less likely and less bad than the false positive of treating
    routine nested-bullet content as code, which would silently skip a
    number out of the visible sequence. If this behaviour ever changes,
    update this test intentionally rather than treating a diff here as a
    regression."""
    text = "Try:\n\n    x = arr[8]\n\nAlso [14]."
    got, mapping = renumber_citations(text, max_n=40)
    assert got == "Try:\n\n    x = arr[1]\n\nAlso [2]."
    assert mapping == {8: 1, 14: 2}
