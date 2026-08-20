"""LaTeX compilation, isolated from everything worth stealing.

This service exists because compiling user-authored LaTeX is arbitrary code
execution and the engine flags do not close it:

- `-no-shell-escape` blocks `\\write18`, but not LuaTeX's `\\directlua`.
  LuaTeX's `--safer` would, and it aborts every real compile of a normal
  preamble (`luaotfload.lua:105: error("safer_option used")`) -- so the flag
  that would help is unusable. (This service does not offer lualatex as an
  engine -- see `_ENGINE_FLAG` below for why -- but the point generalizes:
  no engine's own flags are a trustworthy boundary, which is why the
  containment below has to be the container, not the flags.)
- File READS survive every engine flag. `\\input{/app/.env}` pulls secrets
  straight into the compiled PDF.
- A ten-line macro exhausts RAM or spins forever, usually by accident.

So the containment is the container, not the flags: no secrets in this
process's environment, no route to the database, no egress, read-only rootfs,
tmpfs workdir, non-root, dropped capabilities, and hard resource limits. The
flags below are defence in depth on top of that, not the control.

STATELESS on purpose. Artifacts are returned to the caller and nothing is kept
between requests, so a restart loses nothing and there is no cache here to
poison.
"""

import base64
import json
import os
import posixpath
import re
import signal
import subprocess
import tarfile
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

# Wall-clock ceiling for one compile. A runaway document is the common case
# (a recursive macro), not the rare one.
COMPILE_TIMEOUT = 30
SYNCTEX_TIMEOUT = 10

# LaTeX source is text; a request claiming more than this is a mistake or an
# attempt to make us buffer a huge body before compilation even starts.
#
# /synctex carries the PDF (and the SyncTeX map) base64-encoded, which
# inflates the wire size 4/3 over the raw bytes. 5MB used to cap SyncTeX
# navigation for any PDF over ~3.7MB -- measured live: a 3.5MB PDF (4.67MB
# body) worked, a 4MB PDF (5.33MB body) was rejected. 16MB gives a ~10MB PDF
# room for its base64 body (~13.3MB) plus the synctex map and source text
# alongside it.
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# A tar of a 25MB tree plus headers. The JSON limit above still governs
# /synctex, which carries base64 and is bounded by the PDF, not the tree.
#
# Measured, not assumed: the worst LEGAL tree the backend can produce --
# 2000 files (`latex_archive`'s own cap) at 400-char non-ASCII paths, which
# forces a PAX extended header per member -- tars to 29.71MB. Against this
# 32MB cap that is a 2.3MB margin, not the "ample" headroom the comment
# below (`MAX_TAR_MEMBERS`/`MAX_EXTRACTED_BYTES`) describes for the
# EXTRACTION-side limits; this wire-side margin is comfortable but not wide.
MAX_TAR_LENGTH = 32 * 1024 * 1024

# Drain at most this much of an over-declared/over-long body. The bound
# exists to cap WORK, not to distinguish good clients from bad ones -- a
# byte bound cannot tell the two things it's balancing apart:
#
# - Bytes the client has ALREADY WRITTEN into the socket (real trailing
#   data under an honest Content-Length, or the tail of a lying one).
#   Draining these is fast and is the entire point: leaving them unread is
#   what makes the OS answer a close with an RST instead of a clean FIN,
#   which swallows the response the client is waiting for.
# - Bytes the client will NEVER send (a Content-Length that over-declares
#   and then goes silent). Waiting for these is the stall the guarded
#   try/except below exists to survive without losing the response.
#
# A smaller bound (1MB, this file's first attempt) narrowed the FIRST
# failure mode instead of eliminating it: a valid tar plus 5MB of REAL,
# already-sent trailing bytes under an HONEST Content-Length reproduced the
# exact RST-swallows-the-response bug at that larger size (3/3 runs,
# ConnectionResetError/BrokenPipeError, no response delivered) -- the same
# defect rounds 2 through 4 kept finding, just past whatever bound was
# tried. Raising the bound to MAX_TAR_LENGTH costs nothing new because both
# failure modes are already bounded independently of this constant: no
# legitimate OR hostile request can ever contain more real trailing bytes
# than MAX_TAR_LENGTH (anything larger was already refused with a 413
# before either drain call runs), so a bound at the wire cap drains
# everything a request can actually carry and the RST class disappears
# entirely within it. A client that stops sending is bounded by
# `Handler.timeout` and the surrounding guard, not by this number -- that
# path was already "bounded, not prompt" before this change and stays that
# way; verified live, a 30MB over-declaration with the socket held open
# blocks roughly 30s and then still delivers a correct `200 ok:true`.
MAX_DRAIN_BYTES = MAX_TAR_LENGTH

# latexmk engine flag per document engine. latexmk rather than a bare engine
# call because a paper has citations and cross-references: without its rerun
# and bibtex passes every reference renders as [?] and every \ref as ??.
#
# lualatex is unsupported in v1. luaotfload (LuaTeX's font loader) demands a
# writable cache path during format load, before any document content runs --
# confirmed live: every TeX Live writable-path env var (TEXMFVAR, TEXMFCACHE,
# TEXMFHOME, TEXMFCONFIG, TEXMFSYSVAR, TEXMFSYSCONFIG) pointed at the tmpfs,
# and lualatex still fails identically ("no writeable cache path, quiting")
# even when its own format file is freshly regenerated onto that same tmpfs.
# This is a read-only-rootfs interaction with luaotfload's own cache-path
# resolution, not a document problem, so no per-document workaround exists.
# An unknown engine already falls back to pdflatex rather than erroring, so
# dropping this entry degrades safely.
_ENGINE_FLAG = {
    "pdflatex": "-pdf",
    "xelatex": "-pdfxe",
}


# Environment for the compile subprocess.
#
# `max_print_line` is what stops TeX wrapping log lines at its 79-column
# default. That wrapping is not cosmetic: a path longer than ~77 characters
# is SPLIT, and the continuation fragment is a SUFFIX of the real path that
# still parses as a `path:line:` error line -- measured in this container, a
# tree with one 76-character directory produced `chapters/intro.tex:1:` for
# an error in `chapters/dddd.../chapters/intro.tex:1`, and because
# `chapters/intro.tex` genuinely existed in that tree every "is it a real
# file?" check passed and the wrong file was opened. Deep Overleaf-style
# trees exceed 77 characters routinely, so this needed no adversary at all.
#
# Raising it also removes the continuation lines that echoed prose used to
# hide behind: an `Overfull \hbox` echo is now a single line, and a single
# echo line always begins with TeX's font selector (`[]\OT1/cmr/m/n/10 `),
# which no path can.
#
# 10000 was verified to TAKE EFFECT rather than merely being accepted:
# compiled in-container, a log line of 3019 characters came back unwrapped,
# against the 79 the default produces. Both engines honour it (it is read by
# kpathsea, not by our argv). A document CAN still exceed 10000 columns in
# an echo, which is why it is one of four independent guards and not the
# guard.
#
# `os.environ` is inherited deliberately: this container holds no secrets
# (that is its containment story -- see the module docstring), and TeX needs
# its own TEXMF* paths, which the image sets. Nothing is added here beyond
# the print width.
_COMPILE_ENV = {**os.environ, "max_print_line": "10000"}


# --- Error attribution -------------------------------------------------
#
# THE LESSON THIS CODE EXISTS TO ENCODE: a TeX log is not structured
# output. The user's own source flows into it -- through `Overfull \hbox`
# echoes, through `\typeout`, through the error context TeX prints under
# every error -- so ANY rule that infers structure from the log's TEXT
# ALONE can be forged by the log's own content. Two shipped attempts were
# broken exactly that way:
#
#   1. Counting `(`/`)` to track TeX's file stack. A literal `)` inside an
#      Overfull echo of the user's prose popped a real frame, and the
#      editor opened the wrong file with full confidence.
#   2. Parsing the `-file-line-error` `path:line: message` shape out of the
#      text. Broken four ways, all measured end to end in this container:
#      a wrapped long path whose CONTINUATION fragment is a suffix that
#      matches a different real file; prose inside an Overfull echo's
#      continuation lines; a `\typeout` naming any path the author likes;
#      and a colon in a filename, which matched nothing at all and handed
#      the client TeX's memory statistics.
#
# So attribution here is built out of facts, not out of shapes:
#
#   A. `max_print_line` is raised (see `_COMPILE_ENV`) so TeX never wraps a
#      line. That kills the suffix-fragment class outright, and with it the
#      continuation lines the prose class hid behind.
#   B. The named path is cross-checked against the tree THIS SERVICE JUST
#      STAGED. The compiler extracted the tar; it knows every real file.
#      A path naming something that was never staged attributes nothing.
#      This check lives here and not in the frontend on purpose -- the
#      frontend cannot do it, because it does not know what was staged.
#   C. TeX must corroborate the line itself. A genuinely located error is
#      followed by an `l.<n>` context line whose number agrees, and the
#      blamed file must actually be that long. Where TeX legitimately emits
#      no `l.<n>` (some `LaTeX Error:` forms), the message is reported with
#      no jump -- which is already this codebase's chosen behaviour for the
#      missing-package case.
#   D. AMBIGUITY DECLINES, and this is the rule that does the real work.
#      `-halt-on-error` means TeX reports ONE error and stops, so its log
#      should hold exactly one thing that names a real file with a line
#      number. If it holds two, one of them is the document talking, and
#      there is no honest way to tell which -- so nothing is attributed.
#      Anything else this cannot identify shows an honest, bounded excerpt
#      and offers no jump.
#
# WHY THAT IS ENOUGH, and what it took to believe it. A forgery cannot be
# the only candidate while a real error exists, because a real error is
# always a candidate. Two weaker anchors were tried against the container
# and both were broken by a measured attack:
#
#   - "Attribute from the `==> Fatal error occurred` line": xelatex does not
#     write one at all. It survives as a secondary witness that must agree.
#   - "Count candidates in a window above the statistics": `\errhelp` lets a
#     document choose how long TeX's help paragraph is, so the real error can
#     be padded out of any fixed window, leaving the forgery alone inside it.
#     Measured at a 45-line pad; hence whole-log counting.
#
# The honest residual: the log is the only channel there is, so a document
# can always make the log ambiguous and cost itself a jump. It cannot make
# the log say something FALSE and be believed, which is the property that
# matters. And nothing here crosses a tenancy boundary in any case -- a
# document can only ever misdirect its own author's editor within their own
# project.

# --- The gate: did the run actually DIE AT AN ERROR? -------------------
#
# Everything below this line about candidates and corroboration rests on
# one assumption that was NOT checked, and shipped past review because of
# it: that a real error exists. "A forgery can never be the only candidate,
# because a real error always is one" is true only on a run that errored.
#
# A document can fail WITHOUT raising an error -- `\begin{document}` with
# nothing in it produces "No pages of output.", no PDF, and no error of any
# kind. The candidate scan then finds the document's own forged line
# standing ALONE, every honest check passes (the path is staged, `l.<n>`
# corroborates, the line exists, and there is no fatal line to contradict
# it because neither engine writes one here), and the editor jumps into a
# file the error is not in. Reproduced on both engines with four lines of
# LaTeX.
#
# So attribution is gated on a witness that says the ENGINE ITSELF exited
# nonzero. Measured across both engines and five run shapes (see
# `fixtures/driver/`), latexmk's own post-run summary distinguishes them
# exactly:
#
#   run                     both engines
#   ----------------------  -----------------------------------------------
#   error in main           `<eng>: Command for '<eng>' gave return code 1`
#   error in an \input      same
#   missing package         same
#   "No pages of output."   `<eng>: failed to create output file` -- no code
#   success                 no summary block at all
#
# latexmk writes that block after the engine process has exited, on its own
# stdout, so nothing the DOCUMENT printed can appear below it. That
# argument is about the stdout stream ALONE and was false of stdout+stderr
# concatenated -- see `engine_errored` for the attack that exploited the
# difference, which is why the pipes are kept apart and the block is parsed
# rather than searched.
#
# The cost is stated plainly: if a future latexmk reworded this line, the
# gate would close permanently and the feature would silently become
# "never jump". That is the safe direction, and it is not silent --
# `test_a_genuine_error_still_attributes_and_still_jumps` in the container
# suite fails the moment it stops matching.
# TeX's own words for "this document typeset nothing", written on a run
# that produced no output and raised no error. Matched EXACTLY and as a
# whole line, and read only when the gate is already closed, so the worst a
# document can do by printing it itself is relabel its own failed compile.
_NO_PAGES = "No pages of output."

_SUMMARY_MARKER = "Collected error summary"


# ONE ENTRY of latexmk's summary block, for the engine WE RAN.
#
# Anchored end to end, with the engine's name interpolated at BOTH ends,
# because every loose part of this pattern has been shown to be
# attacker-reachable:
#
#   - The block's deeper-indented detail line is
#     `      Refer to '<jobname>.log' and/or above output for details`, and
#     the jobname is the user's file name.
#   - A failing `bibtex` rule contributes its OWN entry,
#     `  bibtex <jobname>: Bibtex errors: See file '<jobname>.blg'` --
#     an indented entry line, inside the block, carrying the jobname twice.
#     Requiring the rule prefix to be the engine also refuses to read a
#     BIBTEX failure as a TeX error, which it is not.
#
# So the rule prefix, the quoted command and the trailing code are all
# pinned, and `$` closes the line. `latex_paths.normalize_path` permits
# spaces, quotes and colons in a file name, so a jobname can contain this
# entire sentence -- it just cannot be an entire line of it.
def _summary_entry(rule: str) -> "re.Pattern[str]":
    quoted = re.escape(rule)
    return re.compile(
        rf"^  {quoted}: Command for '{quoted}' gave return code [1-9]\d*$"
    )


def engine_errored(driver_stdout: str, engine: str) -> bool:
    """Did the TeX engine exit nonzero, per latexmk's own summary block?

    `driver_stdout` is latexmk's STDOUT ALONE. Not stdout+stderr -- that
    concatenation is what broke this gate on review, and the evidence was
    already sitting in this repository's own captured fixtures:

        Collected error summary (may duplicate other messages):
          pdflatex: failed to create output file
        Latexmk: Undoing directory change
                                       <- stdout ends here
        Failure to make 'main.pdf'     <- STDERR, appended after everything

    Two separately buffered pipes concatenated put every stderr line after
    every stdout line whatever their real order, so "latexmk writes the
    block last, nothing can follow it" was never true of the merged stream.
    `Failure to make '<target>'` names the user's own file, so a project
    whose main file is called `Command for 'x' gave return code 1.tex`
    made a free regex over the tail report an error on a run where nothing
    errored -- and that name arrives through an ordinary file create,
    rename or zip import.

    The two defences are independent on purpose, so both must fail before
    the gate can open: the stream is stdout only, AND the block is parsed
    structurally rather than searched. Measured shape, identical on both
    engines (`fixtures/driver/`):

        Collected error summary (may duplicate other messages):   <- col 0
          pdflatex: Command for 'pdflatex' gave return code 1     <- entry
              Refer to 'main.log' and/or above output for details <- detail
        Latexmk: Undoing directory change                         <- ends it

    The marker is matched at the START OF A LINE, so a file NAMED
    `Collected error summary (may duplicate other messages).tex` -- which
    latexmk echoes in its `Running '...'` line -- cannot move where the
    block is thought to begin. The block ends at the first line that is not
    indented. Every entry is checked, not just the first: a multi-pass run
    with a failing bibliography contributes several.
    """
    entry = _summary_entry(engine if engine in _ENGINE_FLAG else "pdflatex")
    lines = driver_stdout.splitlines()
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(_SUMMARY_MARKER):
            start = i
            break
    if start is None:
        return False
    for line in lines[start + 1 :]:
        if not line.startswith(" "):
            return False
        if entry.match(line):
            return True
    return False


# A `path:line: message` split of one log line. Every candidate split is
# enumerated rather than assuming a path holds no colon: `chapters/a:b.tex`
# is a legal path here (`latex_paths.normalize_path` accepts it), it is
# printed unquoted, and a single-split regex silently failed to match it --
# which is how a colon in a filename used to end with the client being
# shown TeX's memory statistics.
_LOCATED = re.compile(r":(\d+): ")

# TeX's own last words. Written after the memory statistics, once the run is
# already over; in the located form it carries the same path and line as the
# error that killed the run, and in the bare form (`! ==> Fatal error ...`)
# it carries none, which is a decline.
_FATAL_MARKER = "==> Fatal error occurred"

# Fallout, not a cause: TeX reports `Emergency stop.` at whatever line it
# happened to be reading when an EARLIER problem made it give up -- for a
# missing package that is the `\begin{document}` line, several lines away
# from the `\usepackage` that actually failed. The useful message is the
# `! LaTeX Error: File ...' not found.` above it, which names no line at
# all. Reporting the cause with no jump is this codebase's existing,
# deliberate choice for that case (see `latex_detect.py` and
# `paper_resolver.py` for the same line held elsewhere), so a corroborated
# `Emergency stop.` attributes the message and declines the jump.
_FALLOUT_MESSAGES = ("Emergency stop.",)

# How far below a located error line TeX's context block may run before its
# `l.<n>` line appears. Measured in-container: 1 line for an undefined
# control sequence at top level, 3 with a `<recently read>` context, 6 for
# `LaTeX Error: Environment nosuchenv undefined.`. 10 is that with room,
# and it is bounded so a forged `l.<n>` far below an unrelated line cannot
# be adopted as corroboration.
_CONTEXT_WINDOW = 10

# TeX's closing block, written on every run once typesetting is over. It is
# the anchor: on a failed `-halt-on-error` run the error that killed the
# compile is reported immediately above it, and nothing the document can
# emit runs after the error.
_STATS_MARKER = "Here is how much of TeX's memory you used:"

# How much of the log an excerpt may show when nothing can be attributed.
# A bound, not a rule about where errors live: the log is thousands of lines
# of font loading and the reader needs the end of it, not all of it.
_EXCERPT_SPAN = 40


def _splits(line: str) -> list[tuple[str, int, str]]:
    """Every `(path, line, message)` reading of one log line."""
    out = []
    for match in _LOCATED.finditer(line):
        path = line[: match.start()]
        if path:
            out.append((path, int(match.group(1)), line[match.end() :].strip()))
    return out


def _staged_path(printed: str, main_dir: str, staged: set[str]) -> str | None:
    """The tree-relative path a log line names, or None if it names nothing
    that was staged.

    SyncTeX's asymmetry applies here too: `latexmk -cd` chdirs into the main
    file's directory, so TeX prints paths relative to THAT, while the wire
    protocol this service speaks is tree-relative. The absolute form is
    tried as well -- nothing in a normal run prints one, but a document is
    free to `\\input` an absolute path and a staged file reached that way is
    still a staged file.
    """
    candidates = []
    if printed.startswith("/"):
        candidates.append(posixpath.normpath(printed))
    else:
        rel = printed[2:] if printed.startswith("./") else printed
        candidates.append(
            posixpath.normpath(posixpath.join(main_dir, rel) if main_dir else rel)
        )
    for candidate in candidates:
        if candidate in staged:
            return candidate
    return None


def _located_at(
    line: str, main_dir: str, staged: set[str]
) -> tuple[str, int, str] | None:
    """The one staged `(file, line, message)` a log line names, or None.

    EVERY split of the line is enumerated (a path may contain a colon) and
    the staged set decides which one is real. Two different staged readings
    of one line is an AMBIGUITY, and an ambiguity attributes nothing --
    `\\errmessage{./chapters/decoy.tex:3: boom}` makes TeX print its own
    `./main.tex:9: ` prefix in front of the user's text, and a rule that
    picked "the first" or "the longest" would be choosing between the
    compiler's fact and the document's forgery on a coin toss. This is the
    same line `paper_resolver.py` holds: one candidate or none.
    """
    found = {}
    for path, number, message in _splits(line):
        resolved = _staged_path(path, main_dir, staged)
        if resolved is not None:
            found[(resolved, number)] = message
    if len(found) != 1:
        return None
    (resolved, number), message = next(iter(found.items()))
    return resolved, number, message


def _is_error_shaped(line: str) -> bool:
    """Whether a line could be an error report at all. Shape only -- which
    is exactly why nothing is attributed on the strength of it."""
    return line.startswith("!") or bool(_splits(line))


# TeX's error CONTEXT, printed under every error: the source line it had
# reached (`l.21 \bogusmacro`), what it was in the middle of reading
# (`<recently read> \bogusinsty`), and the continuation of either, indented
# to line up under it. All three carry the user's own source text, so any of
# them can be shaped like a located error -- measured: `\errmessage{./
# chapters/decoy.tex:3: ...}` makes TeX quote the argument back as
# `l.4 ...s/decoy.tex:3: ...`, which reads as a located error for a file the
# error is not in. They are skipped when walking back to the error block,
# and they are recognisable without inference: a real error report starts
# with `!` or with a path at column zero, never with whitespace, an `l.<n>`
# marker or an angle bracket.
_CONTEXT_LINE = re.compile(r"^(\s|<|l\.\d)")


def _is_context_line(line: str) -> bool:
    return bool(_CONTEXT_LINE.match(line))


def _is_candidate(line: str, main_dir: str, staged: set[str], exists) -> bool:
    """Whether a line is an error report the closing-block scan must COUNT.

    Deliberately wider than what can be ATTRIBUTED: counting is what makes
    an ambiguous closing block decline, so a rule that counts too much costs
    a jump and a rule that counts too little costs the wrong file. An error
    inside a TeX Live package is not attributable (the file was never
    staged) but must still count, or a forged block alongside it would be
    the only candidate left and would win by default.

    `exists(path)` -- does this printed path name a real file the compiler
    can see -- is what separates "TeX naming a file" from an `Overfull
    \\hbox` echo of the user's prose. The echo's own text reads as a path
    (`[]\\OT1/cmr/m/n/10 chap-ters/intro.tex:5: Un-de-fined ...`), but no
    such file is on disk, whereas both a staged chapter and
    `/usr/local/texlive/.../foo.sty` are. Shape cannot tell those apart;
    the filesystem can.
    """
    if _is_context_line(line):
        return False
    if line.startswith("!"):
        return True
    return any(
        _staged_path(path, main_dir, staged) is not None or exists(path)
        for path, _number, _message in _splits(line)
    )


def analyse_log(
    log_text: str,
    staged: set[str],
    main_dir: str,
    line_count,
    exists=lambda path: False,
    errored: bool = False,
) -> tuple[str, str | None, int | None]:
    """`(excerpt, file, line)` for a compile log. `file` is tree-relative.

    `line_count(path)` returns how many lines a staged file has, or None --
    the one fact this needs from the filesystem, injected so the rest of the
    function stays pure and unit-testable without a container.

    `errored` is the gate: unless the ENGINE ITSELF exited nonzero, nothing
    is attributed however clean the log looks. See `engine_errored` above --
    a run can fail with no error at all ("No pages of output."), and on such
    a run a single forged line is the only candidate there is. It defaults
    to False so a caller that forgets it declines rather than guesses.

    `file` and `line` are set or null TOGETHER. A line number with no file
    is worse than nothing in a multi-file project: the caller would jump
    that far into whatever buffer happens to be on screen.

    THE SEARCH RUNS AT THE END OF THE LOG, NOT THE TOP. `-halt-on-error`
    stops the run at the first real error, so TeX's closing block --
    `Here is how much of TeX's memory you used:` and what precedes it -- is
    where that error is reported. Both withdrawn attempts scanned FORWARD
    from the top and therefore met the document's own text first: a
    `\\typeout` forging a whole error block, or an `Overfull \\hbox` echoing
    the user's prose, wins every forward scan and neither can reach the
    closing block, because after the real error TeX writes only its own
    output.

    Measured engine asymmetry: pdflatex ends a failed run with
    `./chapters/intro.tex:21:  ==> Fatal error occurred, ...` AFTER the
    statistics, and xelatex writes no such line at all -- so that line is an
    optional extra witness that must AGREE, never the anchor. Anchoring on
    it would have silently declined every xelatex error.

    AMBIGUITY DECLINES, and that is what closes the one attack that survived
    every other rule here: `\\errhelp` lets a document inject arbitrary text
    into the help paragraph TeX prints AFTER the error, i.e. inside the
    closing block itself. Measured in this container, `\\errhelp{./chapters/
    decoy.tex:3: Undefined control sequence.^^Jl.3 \\zz}` puts a complete,
    perfectly shaped, fully corroborated error block for another real file
    below the real one -- under xelatex, with no fatal line to contradict
    it. So the closing block is required to contain EXACTLY ONE attributable
    candidate; two make the answer unknowable and unknowable means no jump.
    """
    lines = log_text.splitlines()

    stats = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(_STATS_MARKER):
            stats = i
            break

    error_index = None
    candidates: list[int] = []
    if stats is not None:
        # Counted over the WHOLE log up to the statistics, not a window above
        # them. A window was tried and is not safe: `\errhelp` lets a
        # document choose how long the help paragraph TeX prints after an
        # error is, so any fixed window can be pushed off the real error's
        # line by padding it. Whole-log counting cannot be pushed anywhere.
        #
        # The cost is false DECLINES, which is the direction this whole
        # module errs in: any second thing in the log that names a real file
        # with a line number gives up the jump. Measured against a realistic
        # paper -- IEEEtran with graphicx, amsmath, hyperref, a 377-line log
        # -- there is exactly ONE candidate, so the cost in practice is
        # nil. `-halt-on-error` is what makes that true: TeX reports one
        # error and stops.
        candidates = [
            i for i in range(stats) if _is_candidate(lines[i], main_dir, staged, exists)
        ]
        if len(candidates) == 1:
            error_index = candidates[0]

    if error_index is None and candidates:
        # Ambiguous: the log holds more than one thing shaped like an error
        # report and there is no honest way to pick. Show TeX's closing
        # block -- from the first candidate, bounded -- rather than picking
        # one of them to headline. No jump.
        return (
            "\n".join(lines[max(candidates[0], stats - _EXCERPT_SPAN) : stats]),
            None,
            None,
        )

    # The excerpt starts at the first error-shaped line, so a cause that
    # names no line of its own is not lost -- for a missing package the
    # useful message is `! LaTeX Error: File \`nopesuchpkg.sty\' not found.`
    # and the located line below it says only `Emergency stop.`. It is
    # pulled down to the identified error when that sits further away than
    # the excerpt is long, so the excerpt always contains the error it
    # describes.
    start = _first_error_index(lines, staged, main_dir)
    if error_index is not None and (start is None or error_index > start + 11):
        start = error_index
    if start is None:
        return _undirected(lines, staged, main_dir), None, None
    excerpt = "\n".join(lines[start : start + 12])

    if error_index is None:
        return excerpt, None, None

    if not errored:
        # THE GATE. No error happened, so there is nothing to attribute --
        # whatever the candidate scan found is the document talking to
        # itself. See `engine_errored` above for the witness and its cost.
        #
        # The jump goes; the explanation must not. The common shape here is
        # a document that typeset nothing, and TeX says so in as many words
        # -- so say that, rather than headlining whatever error-shaped line
        # the scan happened to land on, which on this path is unverified
        # text by definition. Any OTHER reason the gate is closed keeps the
        # excerpt it would have shown, so a latexmk rewording costs the jump
        # and not the message.
        if _NO_PAGES in lines:
            return (
                "The document produced no pages, and TeX reported no error.\n\n"
                + "\n".join(lines[-_EXCERPT_SPAN:]),
                None,
                None,
            )
        return excerpt, None, None

    located = _located_at(lines[error_index], main_dir, staged)
    if located is None:
        # A bare `! ...` error (`File ended while scanning use of \\textbf`),
        # an error inside a package under /usr/share, or a line naming a
        # path that was never staged. The message is worth showing; the
        # position is not knowable, so no jump is offered.
        return excerpt, None, None
    file, number, message = located

    if message in _FALLOUT_MESSAGES:
        return excerpt, None, None

    # pdflatex's own last words, if this engine wrote them. They must name
    # the same place: two witnesses that disagree are one witness too few.
    for i in range(len(lines) - 1, -1, -1):
        if _FATAL_MARKER in lines[i]:
            fatal_at = _located_at(lines[i], main_dir, staged)
            if fatal_at is None or fatal_at[:2] != (file, number):
                return excerpt, None, None
            break

    # TeX's own corroboration: the error context ends in an `l.<n>` line
    # whose number must be the one being attributed. A located error with no
    # such line is a form TeX raises without a source position it can quote
    # (some `LaTeX Error:` shapes), and those attribute the message only.
    corroborated = any(
        lines[i] == f"l.{number}" or lines[i].startswith(f"l.{number} ")
        for i in range(
            error_index + 1, min(error_index + 1 + _CONTEXT_WINDOW, len(lines))
        )
    )
    if not corroborated:
        return excerpt, None, None

    # And the line has to exist in the file being blamed. A staged file
    # shorter than the line number named is proof the two do not belong
    # together, whatever the log says.
    total = line_count(file)
    if total is None or not 1 <= number <= total:
        return excerpt, None, None

    return excerpt, file, number


def _first_error_index(lines: list[str], staged: set[str], main_dir: str) -> int | None:
    """The first line that LOOKS like an error, for the excerpt only.

    Deliberately not used for attribution: "looks like an error" is exactly
    the inference this module refuses to navigate on. A located line counts
    only if it names a file that was staged, so ordinary chatter and echoed
    prose do not start the excerpt in the middle of nowhere.
    """
    for i, line in enumerate(lines):
        if line.startswith("!"):
            return i
        if any(
            _staged_path(path, main_dir, staged) is not None
            for path, _n, _m in _splits(line)
        ):
            return i
    return None


def _undirected(lines: list[str], staged: set[str], main_dir: str) -> str:
    """What to show when no error can be identified.

    NEVER a bare tail. The old fallback returned `lines[-40:]` unlabelled,
    which in the colon-in-a-filename case meant the client was handed TeX's
    memory statistics presented as "the first error" -- plainly wrong
    whatever attribution decides. The tail is still the most useful thing
    available, so it is still shown; it is just no longer passed off as
    something it is not.
    """
    start = _first_error_index(lines, staged, main_dir)
    if start is not None:
        return "\n".join(lines[start : start + 12])
    return (
        "No TeX error line was found in the log. Its last lines were:\n\n"
        + "\n".join(lines[-40:])
    )


class _Bounded:
    """Reads at most `limit` bytes from `stream`. tarfile in stream mode will
    happily read as much as it is given, and nothing else stops it reading
    past the declared body.

    That does not smuggle bytes into a NEXT request today: `Handler` never
    sets `protocol_version`, so every response here is HTTP/1.0 and each
    connection serves exactly one request -- verified live, a pipelined
    `GET /health` sent after a short-`Content-Length` tar body gets exactly
    one response, not two. This class is defence in depth against that
    assumption changing (keep-alive, a future `protocol_version` bump), not
    a fix for a smuggling window that exists today.

    `remaining` exposes how much of `limit` is still unread so a caller can
    drain it before answering -- see the 413 branch's comment on why an
    unread remainder in the kernel buffer can turn a clean response into a
    TCP RST on the client.
    """

    def __init__(self, stream, limit: int) -> None:
        self._stream = stream
        self._left = limit

    @property
    def remaining(self) -> int:
        return self._left

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        want = self._left if size is None or size < 0 else min(size, self._left)
        chunk = self._stream.read(want)
        self._left -= len(chunk)
        return chunk


def _strict_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
    """Refuse what `data_filter` would merely contain.

    CPython's `data_filter` strips a leading slash and rewrites `/etc/passwd`
    to `<dest>/etc/passwd` -- contained, but accepted. Nothing this service is
    sent should ever hold an absolute or parent-relative name: `latex_archive`
    rejects both in the backend process before a tar is ever built. Refusing
    here keeps the two guards saying the same thing, so a tar that violates
    the first is not quietly normalised by the second.
    """
    name = member.name
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"absolute path in archive: {name}")
    if ".." in PurePosixPath(name).parts:
        raise ValueError(f"parent-directory segment in archive: {name}")
    return tarfile.data_filter(member, path)


# The backend caps an uploaded tree at 25MB and 2000 files (latex_archive).
# 4000 members and 64MB extracted leaves ample headroom for anything
# legitimate while turning a pathological tar into one clean rejection
# instead of exhausting the container's mem_limit for everyone. The member
# count is the load-bearing half of this pair: measured in-container, a
# 31.3MB tar of 32,000 one-byte files extracted to 125MB of tmpfs -- each
# file costs a 4096-byte page regardless of its declared size, so summing
# declared sizes alone would not have caught it. Tar itself is uncompressed,
# so there is no separate decompression-bomb case to guard against here.
#
# MAX_EXTRACTED_BYTES is UNREACHABLE over HTTP today, and that is fine: the
# 32MB wire cap (MAX_TAR_LENGTH) binds first for an uncompressed tar, so no
# request can ever declare a byte total this filter would refuse before the
# wire cap already rejected it with a 413. It stays as a second, independent
# ceiling in case that relationship ever changes (a compressed transport, a
# raised wire cap) -- this is not dead code to be pruned, it is a guard for
# an invariant (uncompressed tar, 32MB cap) that lives in a different part
# of this file and could drift out of sync with this one.
MAX_TAR_MEMBERS = 4000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024


def _extract_tree(bounded: "_Bounded", directory: Path) -> None:
    """Extract the posted tar into `directory`.

    `_strict_filter` runs our own absolute/`..` refusal first, then delegates
    to `filter="data"` -- CPython's own guard against symlinks and hardlinks
    pointing outside, and device nodes. `data_filter` alone is the SECOND
    independent traversal guard -- `latex_archive` already validated every
    path in the backend process -- and it is deliberately not hand-rolled
    here, in the one container where being wrong about it is worst; the
    strict layer on top only tightens what it accepts, never replaces it.
    A running member count and declared-byte total wrap that filter so a
    pathological tar (too many entries, or a declared size too large) is
    refused mid-stream rather than fully extracted first.

    Streamed with mode "r|" so the archive is never materialised: a 32MB tar
    read into memory before extraction would double the tmpfs cost of every
    compile, and /tmp is RAM charged against this container's mem_limit.
    `bounded` is passed in already constructed so the caller can inspect
    `.remaining` afterwards and drain any unconsumed request body.
    """
    member_count = 0
    extracted_bytes = 0

    def _filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
        nonlocal member_count, extracted_bytes
        member_count += 1
        if member_count > MAX_TAR_MEMBERS:
            raise ValueError(f"archive has more than {MAX_TAR_MEMBERS} entries")
        extracted_bytes += max(member.size, 0)
        if extracted_bytes > MAX_EXTRACTED_BYTES:
            raise ValueError(
                f"archive extracts to more than {MAX_EXTRACTED_BYTES} bytes"
            )
        return _strict_filter(member, path)

    with tarfile.open(fileobj=bounded, mode="r|") as tf:
        tf.extractall(directory, filter=_filter)


def compile_tree(bounded: "_Bounded", engine: str, main_path: str) -> dict:
    """Compile a whole tree. `main_path` is tree-relative.

    `bounded` is a `_Bounded` wrapper the caller already constructed around
    the request body, so its `.remaining` is meaningful to the caller after
    this returns (or raises) -- see the `finally: self._drain(...)` in
    `do_POST` for why the caller needs that.

    latexmk runs with `-cd`, which chdirs into the main file's directory --
    WITHOUT it, a `\\input{chapters/intro}` in `src/paper.tex` resolves
    against the tree root and fails (`rc=12`, measured). The artifacts then
    land BESIDE the main file, not at the root, which is why the PDF is read
    from `main.parent`.
    """
    flag = _ENGINE_FLAG.get(engine, "-pdf")
    with tempfile.TemporaryDirectory(prefix="rx-latex-") as tmp:
        directory = Path(tmp)
        try:
            _extract_tree(bounded, directory)
        except Exception:
            return {
                "ok": False,
                "log": "The uploaded project could not be unpacked.",
                "error_file": None,
                "error_line": None,
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        main = (directory / main_path).resolve()
        # Belt and braces with filter="data": the main path is a separate
        # input from the tar's member names and gets its own containment
        # check. `resolve()` collapses any `..` before the comparison.
        if (
            not str(main).startswith(str(directory.resolve()) + os.sep)
            or not main.is_file()
        ):
            return {
                "ok": False,
                "log": "The document's main file is not in the project.",
                "error_file": None,
                "error_line": None,
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        stem = main.stem
        # The staged tree is the USER'S. `<stem>.pdf`, `.log` and
        # `.synctex.gz` beside the main file are legitimate uploads -- an
        # imported Overleaf or arXiv archive routinely ships its own compiled
        # PDF, and `latex_archive` deliberately does not skip `.pdf` members
        # (it cannot: `\includegraphics{fig.pdf}` is a legitimate input) --
        # so "the file exists" cannot mean "latexmk produced it" the way it
        # could when the tree held exactly one file. Remove them first, and
        # existence after this point becomes proof of THIS run: a failed
        # compile that leaves none of the three behind now correctly reports
        # `ok: False` instead of serving back the user's own stale upload.
        for suffix in (".pdf", ".log", ".synctex.gz"):
            (main.parent / f"{stem}{suffix}").unlink(missing_ok=True)

        # The tree as STAGED, read now -- before latexmk writes its own
        # artifacts into it -- so `.aux`/`.log`/`.pdf` files this run
        # produces can never be mistaken for files the user sent. This set
        # is the FACT that error attribution is cross-checked against: the
        # compiler extracted the tar, so it alone knows what really exists.
        # See `analyse_log` for why that check cannot live in the frontend.
        staged = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }
        main_dir = posixpath.dirname(posixpath.normpath(main_path))

        proc = subprocess.Popen(
            [
                "latexmk",
                flag,
                "-cd",
                "-synctex=1",
                "-interaction=nonstopmode",
                # Makes every TeX error self-describing:
                # `./chapters/intro.tex:3: Undefined control sequence.`
                # instead of a bare `! Undefined control sequence.` whose
                # file the reader has to INFER.
                #
                # This is a diagnostic flag, nothing more -- it grants the
                # document no capability, reads no new path and opens no
                # socket, so it changes nothing about this container's
                # containment story.
                #
                # It exists because the frontend previously inferred the file
                # by tracking TeX's `(`/`)` file stack, and that was
                # disproved against THIS container: TeX echoes the offending
                # typeset text inside `Overfull \hbox` warnings, and a
                # literal `)` in the user's own source pops a real file
                # frame. The parser then attributed an error in
                # `chapters/intro.tex` to `main.tex` and the editor opened
                # the wrong file and jumped. There is no way to parse that
                # reliably -- a TeX log interleaves structure with arbitrary
                # user text -- so the fix is to make the compiler state the
                # fact instead. Verified in-container for BOTH engines:
                # latexmk passes the flag through to pdflatex and to
                # xelatex, and both write the `path:line:` form.
                #
                # `analyse_log` had to learn this form at the same time:
                # with the flag on, the error line no longer starts with `!`
                # (not even `==> Fatal error occurred`), so the old
                # `startswith("!")` scan found nothing and fell through to
                # the tail of the log.
                #
                # The flag is NOT, on its own, a trustworthy attribution:
                # parsing this shape out of the log text was the SECOND
                # attempt this thread had to withdraw. See the "Error
                # attribution" block above for the four measured ways user
                # text forges it, and for the facts that replaced it.
                "-file-line-error",
                "-no-shell-escape",
                # LOAD-BEARING FOR ATTRIBUTION as well as for speed: the run
                # stops at the FIRST real error, so the `==> Fatal error
                # occurred` line TeX writes last reports that error's
                # position and nothing the document emits can follow it.
                # `analyse_log` scans backwards from there; without
                # -halt-on-error the log would hold several real errors and
                # the last one would not be the cause.
                "-halt-on-error",
                str(main),
            ],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            # Raises TeX's print width so it never wraps a log line -- see
            # `_COMPILE_ENV` for the measured wrong-file jump that wrapping
            # caused.
            env=_COMPILE_ENV,
        )
        try:
            stdout, stderr = proc.communicate(timeout=COMPILE_TIMEOUT)
            # THREE DIFFERENT STREAMS, and keeping them apart is
            # load-bearing. `stdout` alone carries latexmk's post-run
            # summary block -- the gate `analyse_log` gets as `errored`.
            # CONCATENATING stderr onto it is what broke that gate on
            # review: two separately buffered pipes joined end to end put
            # `Failure to make '<the user's own file name>'` after
            # latexmk's summary, so a project whose main file is called
            # `Command for 'x' gave return code 1.tex` reported an error on
            # a run where nothing errored. See `engine_errored`.
            #
            # `log_text` keeps BOTH, because when no `.log` file exists it
            # is all the user has to read; it is replaced by the `.log`
            # itself below whenever there is one.
            log_text = stdout + stderr
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            return {
                "ok": False,
                "log": f"Compilation exceeded {COMPILE_TIMEOUT}s and was stopped.",
                "error_file": None,
                "error_line": None,
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }

        log_file = main.parent / f"{stem}.log"
        if log_file.exists():
            log_text = log_file.read_text(encoding="utf-8", errors="replace")

        def _line_count(tree_path: str) -> int | None:
            """How many lines a staged file has, or None if it cannot be read.

            The last of `analyse_log`'s cross-checks: a file shorter than
            the line number being blamed cannot be where that error is,
            whatever the log claims.
            """
            try:
                return len((directory / tree_path).read_bytes().split(b"\n"))
            except OSError:
                return None

        def _exists(printed: str) -> bool:
            """Does a path printed in the log name a real file this compile
            can see? Staged files, and TeX Live's own packages, do; an
            `Overfull \\hbox` echo of the user's prose does not. See
            `_is_candidate`."""
            rel = printed[2:] if printed.startswith("./") else printed
            try:
                if rel.startswith("/"):
                    return Path(rel).is_file()
                joined = posixpath.join(main_dir, rel) if main_dir else rel
                return (directory / joined).is_file()
            except (OSError, ValueError):
                return False

        excerpt, error_file, error_line = analyse_log(
            log_text,
            staged,
            main_dir,
            _line_count,
            _exists,
            # STDOUT ONLY, and the engine name, both deliberate.
            errored=engine_errored(stdout, engine),
        )

        pdf = main.parent / f"{stem}.pdf"
        synctex = main.parent / f"{stem}.synctex.gz"
        if not pdf.exists():
            return {
                "ok": False,
                "log": excerpt,
                "error_file": error_file,
                "error_line": error_line,
                "pdf_b64": None,
                "synctex_b64": None,
                "root": None,
            }
        return {
            "ok": True,
            "log": excerpt if proc.returncode else "",
            "error_file": error_file if proc.returncode else None,
            "error_line": error_line if proc.returncode else None,
            "pdf_b64": base64.b64encode(pdf.read_bytes()).decode(),
            "synctex_b64": (
                base64.b64encode(synctex.read_bytes()).decode()
                if synctex.exists()
                else None
            ),
            "root": str(directory),
        }


def _records(output: str) -> list[dict]:
    """Parse the synctex client's Field:value blocks into records."""
    records: list[dict] = []
    current: dict = {}
    for line in output.splitlines():
        if line.startswith("SyncTeX result begin"):
            current = {}
            continue
        if line.startswith("SyncTeX result end"):
            if current:
                records.append(current)
            current = {}
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    if current:
        records.append(current)
    return records


def query_synctex(body: dict) -> dict:
    """`body`: direction, pdf_b64, synctex_b64, main_path, root, plus the
    coordinates for that direction.

    NO SOURCE. Measured in plan 1: both directions answer from the PDF and the
    map alone, with no .tex staged at all. Shipping the tree here would mean
    sending up to 25MB to answer one double-click.

    The wire protocol is TREE-RELATIVE. synctex itself speaks paths relative
    to the main file's DIRECTORY (measured: `-i 2:0:chapters/intro.tex`
    resolves, `-i 2:0:src/chapters/intro.tex` does not), so this function owns
    both conversions. When the main file is at the tree root -- the common
    case -- every conversion is the identity.

    LIMITATION, not fixed here: forward sync cannot address a file outside
    the main file's directory. `posixpath.relpath` on such a file yields a
    `../`-prefixed path, and synctex's `view` returns NOT FOUND for it
    (measured) -- so a subdirectory main file can forward-sync anything
    under that subdirectory, but not a file reached only via `..`. Reverse
    sync has no such restriction (see `_tree_path`), so the two directions
    are asymmetric.
    """
    direction = body.get("direction")
    if direction not in ("forward", "reverse"):
        return {"found": False}

    # Type-confusion guard: these three arrive as untyped JSON. A non-string
    # value (a list, a number, null) must be treated as absent rather than
    # reaching string operations below and raising a 500 -- pdf_b64/
    # synctex_b64/the coordinates already fail correctly (base64/int/float
    # parsing raises, caught by the try/except 500 in do_POST) so only the
    # path-shaped fields need this.
    raw_main_path = body.get("main_path")
    main_path = raw_main_path if isinstance(raw_main_path, str) else ""
    raw_root = body.get("root")
    root = raw_root if isinstance(raw_root, str) else None
    raw_file = body.get("file")
    file_param = raw_file if isinstance(raw_file, str) else None

    main_dir = posixpath.dirname(main_path)
    stem = posixpath.splitext(posixpath.basename(main_path))[0]

    with tempfile.TemporaryDirectory(prefix="rx-synctex-") as tmp:
        directory = Path(tmp)
        pdf = directory / f"{stem}.pdf"
        pdf.write_bytes(base64.b64decode(body["pdf_b64"]))
        (directory / f"{stem}.synctex.gz").write_bytes(
            base64.b64decode(body["synctex_b64"])
        )

        # `-d <dir>` is load-bearing: the map records ABSOLUTE paths from the
        # directory it was compiled in, which no longer exists by the time
        # anyone queries it. Verified in jobfit against a real document --
        # both view and edit answer correctly from a directory the document
        # was never compiled in.
        if direction == "forward":
            tree_file = file_param or main_path
            # tree-relative -> main-dir-relative
            rel = posixpath.relpath(tree_file, main_dir) if main_dir else tree_file
            args = [
                "synctex",
                "view",
                "-i",
                f"{body['line']}:0:{rel}",
                "-o",
                str(pdf),
                "-d",
                str(directory),
            ]
        else:
            args = [
                "synctex",
                "edit",
                "-o",
                f"{body['page']}:{body['x']}:{body['y']}:{pdf}",
                "-d",
                str(directory),
            ]
        # Same process-group treatment as compile_tree's subprocess call, for
        # consistency: synctex is much less likely to fork a runaway child,
        # but the next person reading this file should not have to work out
        # why one of the two subprocess calls here is guarded against
        # leaving an orphan behind and the other is not.
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=SYNCTEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()  # reap, do not leave a zombie
            return {"found": False}

        records = _records(stdout)
        if direction == "forward":
            boxes = [r for r in records if "Page" in r and "x" in r and "y" in r]
            if not boxes:
                return {"found": False}
            box = boxes[0]
            return {
                "found": True,
                "page": int(box["Page"]),
                "x": float(box["x"]),
                "y": float(box["y"]),
                "width": float(box.get("W") or 0),
                "height": float(box.get("H") or 0),
            }
        lines = [r for r in records if "Line" in r]
        if not lines:
            return {"found": False}
        record = lines[0]
        resolved = _tree_path(record.get("Input", ""), root)
        if resolved is None:
            return {"found": False}
        return {"found": True, "file": resolved, "line": int(record["Line"])}


def _tree_path(raw_input: str, root: str | None) -> str | None:
    """Turn synctex's `Input:` into a TREE-RELATIVE path, or None.

    `Input:` is `<root>/<whatever the engine recorded>`. Once the recorded
    root is stripped, the remainder is already tree-relative -- an earlier
    version rejoined the main file's directory on top of it, which
    double-counted the prefix and silently returned the wrong file for any
    project whose main file sits in a subdirectory.

    Returns None rather than guessing. A wrong file opens the wrong document
    with full confidence; `found: false` renders cleanly. Refused: anything
    not under the compile root (system inputs like `article.cls` live in the
    TeX Live tree), and anything that still escapes after normalisation
    (`\\input{../../etc/hostname}` is a document the user can write).
    """
    if not raw_input or not root:
        return None
    # A bare `startswith(root)` would also match a SIBLING directory whose
    # name merely extends root's characters (`/tmp/RX/...` starts with
    # `/tmp/R`) -- the same class of bug `compile_tree`'s containment check
    # guards against with `str(directory.resolve()) + os.sep`. Requiring the
    # separator keeps both checks in this module consistent.
    prefix = root.rstrip("/") + "/"
    if not raw_input.startswith(prefix):
        return None
    tail = raw_input[len(prefix) :]
    # `/./` is how synctex separates the recorded cwd from the relative path.
    # Collapse it IN PLACE; do not treat it as a split point.
    tail = tail.replace("/./", "/")
    if tail.startswith("./"):
        tail = tail[2:]
    if not tail:
        return None
    candidate = posixpath.normpath(tail)
    if candidate.startswith("/") or candidate == ".." or candidate.startswith("../"):
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    """Two POST routes and a health check, on the standard library.

    ThreadingHTTPServer, not the single-threaded default: two members
    compiling at once would otherwise serialise behind a 30-second run, and
    the container's `pids_limit` is what bounds concurrency instead.
    """

    server_version = "latex-compiler"

    # BaseHTTPRequestHandler sets no socket timeout, so a client that claims a
    # large Content-Length and then stalls pins its worker thread for as long
    # as it likes. With ThreadingHTTPServer that is a thread leak: enough
    # stalled connections and no member can compile at all. The timeout is per
    # socket operation, not per request, so it never interrupts a compile --
    # nothing reads or writes this socket during the 30s COMPILE_TIMEOUT -- and
    # a client that keeps making progress is never cut off. On expiry the
    # handler closes the connection and the thread is returned.
    timeout = 30

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self, length: int) -> None:
        """Read and discard up to `length` bytes of the request body without
        ever materialising it all at once. See the 413 branch in do_POST for
        why this has to happen before that response is sent. Callers are
        responsible for bounding `length` by MAX_DRAIN_BYTES themselves --
        the two call sites bound different things (a hostile DECLARED
        length vs. a tar's genuinely unread remainder), so the bound is kept
        explicit at each call site rather than hidden as a default here.
        Neither call site guards this with try/except internally either --
        that is each caller's job too, since draining is a courtesy that
        must never cost an already-decided response (see both call sites)."""
        remaining = length
        chunk_size = 64 * 1024
        while remaining > 0:
            chunk = self.rfile.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        # All three malformed-header cases are guarded BEFORE the body is
        # ever read: an unparseable header would otherwise reach rfile.read()
        # as an uncaught exception (a raw connection drop instead of the
        # clean JSON every other path returns, plus a full traceback dumped
        # to container logs by socketserver's default handler -- routing
        # around both the internals-free 500 below and log_message's
        # override). A negative length is parseable but must never reach
        # rfile.read(): read(-1) on a live socket blocks until the peer
        # closes, hanging the thread. A too-large length is rejected before
        # buffering that many bytes at all.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid content-length"})
            return
        if length < 0:
            self._send(400, {"error": "invalid content-length"})
            return
        # /compile speaks only the tar transport now, so it always gets the
        # larger cap (MAX_TAR_LENGTH, 32MB) regardless of what Content-Type
        # the client claims -- a mislabelled or missing header must not let
        # a 20MB tar sneak in under the smaller limit. MAX_CONTENT_LENGTH
        # (16MB) governs everything else, which today is only /synctex's
        # base64 JSON body.
        is_tar = (
            self.path == "/compile"
            and self.headers.get("Content-Type") == "application/x-tar"
        )
        limit = MAX_TAR_LENGTH if self.path == "/compile" else MAX_CONTENT_LENGTH
        if length > limit:
            # Drain the body before answering. By the time we know it is too
            # large, the client has typically already written most or all of
            # it into the socket. Responding and closing without reading
            # those bytes leaves them unread in the kernel's receive buffer,
            # and the OS answers a close-with-unread-data by sending a TCP
            # RST instead of a clean FIN. That RST can land mid-write or
            # mid-read on the client, so instead of a readable 413 it sees a
            # bare connection error -- reproduced live as
            # `httpx.ReadError('')`, logged as `latex_compiler_unavailable
            # error=` with an empty error string: an undelivered 413 is
            # indistinguishable from the service being down. Read in bounded
            # chunks rather than one `rfile.read(length)` call so a hostile
            # Content-Length cannot force a single huge allocation. Bounded
            # by MAX_DRAIN_BYTES (not the full declared `length`, which can
            # be enormous for exactly the hostile client this branch exists
            # for) and guarded: this predates the tar transport, and it had
            # the same unguarded-drain defect Finding 1 found and fixed
            # below -- reproduced live, a request declaring Content-Length
            # 34MB with zero body bytes ever sent made this hang for the
            # full 30s and then drop the connection with no 413 delivered
            # at all. Draining here is the same courtesy as the tar path's:
            # never worth losing the response that is already decided.
            try:
                self._drain(min(length, MAX_DRAIN_BYTES))
            except Exception:
                pass
            self._send(413, {"error": "request too large"})
            return
        if is_tar:
            # Inside a try/except -> 500 just like the JSON dispatch below --
            # `compile_tree` can raise on attacker-controlled input it does
            # not fully validate itself (a NUL byte in X-Main-Path reaches
            # Path.resolve() as ValueError: embedded null character in
            # path). Left outside this handler, that exception used to
            # propagate out of do_POST entirely: no HTTP response is ever
            # written, the client sees a bare dropped connection (the same
            # "indistinguishable from the service being down" failure the
            # 413 branch above exists to avoid), and the traceback goes to
            # container logs via socketserver's default handler, routing
            # around log_message's override that exists to keep request
            # data out of logs.
            engine = self.headers.get("X-Engine", "pdflatex")
            # Percent-decoded: httpx (every client) encodes header values as
            # ASCII, so a non-ASCII main file name (plan 2 supports these --
            # measured, e.g. resume/cafe.tex) arrives percent-encoded. The tar
            # itself carries non-ASCII paths natively (PAX); only this header
            # needs the round trip.
            main_path = unquote(self.headers.get("X-Main-Path", ""))
            bounded = _Bounded(self.rfile, length)
            try:
                result = compile_tree(bounded, engine, main_path)
            except Exception:
                result = None
            # Drain before responding, for the same reason the 413 branch
            # does: `compile_tree` stops reading at the tar's end-of-archive
            # marker, so any declared bytes beyond that (padding, or a lying
            # Content-Length) are still sitting unread in the kernel receive
            # buffer. Responding and closing without draining them answers
            # with a TCP RST instead of a clean FIN, which can land
            # mid-write on the client and turn a valid response into a bare
            # connection error -- measured live: a tar followed by ~25MB of
            # trailing bytes under a 32MB Content-Length produced
            # BrokenPipeError on the client with no response delivered.
            #
            # But draining is a COURTESY, never a reason to lose an already-
            # computed response, so this is both guarded AND bounded by
            # MAX_DRAIN_BYTES rather than by a socket deadline -- an earlier
            # version of this fix called `self.connection.settimeout(...)`
            # before draining and never restored it, which is a stateful
            # mistake on a shared socket: that same deadline then silently
            # governed `_send`'s write of the response a few lines below,
            # truncating every multi-MB PDF sent to a client that could not
            # read faster than the timeout allowed -- measured live, a
            # 5.6MB PDF to a slow reader delivered only 556,905 of 5,619,935
            # bytes over the tar path, while a control request that never
            # touches this drain (measured at the time against the JSON
            # /compile form this service has since retired) delivered all of
            # it.
            # A byte bound cannot leak into unrelated code the way a
            # deadline on the connection itself can. See MAX_DRAIN_BYTES's
            # own comment for what this bound does and does not close.
            try:
                self._drain(min(bounded.remaining, MAX_DRAIN_BYTES))
            except Exception:
                pass
            if result is None:
                self._send(500, {"error": "compile service failure"})
            else:
                self._send(200, result)
            return
        if self.path == "/compile":
            # /compile no longer speaks JSON at all -- the tar transport
            # (handled above) is the only form. Drain for the same reason
            # the 413 branch does: the body is still sitting unread in the
            # kernel receive buffer, and answering without draining it risks
            # the same RST-swallows-the-response failure.
            try:
                self._drain(min(length, MAX_DRAIN_BYTES))
            except Exception:
                pass
            self._send(400, {"error": "expected application/x-tar"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "expected an object"})
            return

        try:
            if self.path == "/synctex":
                self._send(200, query_synctex(body))
            else:
                self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(400, {"error": f"missing field {exc}"})
        except Exception:
            # The caller degrades on any non-answer; the detail stays here.
            self._send(500, {"error": "compile service failure"})

    def log_message(self, fmt: str, *args) -> None:
        # Default logging prints the full request line to stderr. The request
        # body is a user's LaTeX; keep it out of container logs.
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
