import { HighlightStyle, StreamLanguage, type StreamParser } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { Tag, tags } from "@lezer/highlight";

/**
 * Syntax colouring for the LaTeX editor.
 *
 * `stex` is a CodeMirror 5 legacy stream mode, so it emits the CM5 token
 * NAMES ("comment", "tag", "atom", …) which `StreamLanguage` maps onto lezer
 * highlight tags. That mapping is the whole reason this module exists: stex
 * labels the braced argument of `\label`, `\begin`, `\cite` and
 * `\usepackage` with ONE token -- "atom" -- so out of the box a section
 * label, an environment name and a package name are all the same colour.
 * That is exactly the distinction a writer scanning a source file needs, so
 * the parser below re-labels those four cases apart before they reach the
 * highlight style.
 */

/** Tags stex has no token for. Deliberately distinct objects rather than
 * reuses of a stock tag: the stock tags are already spoken for by the token
 * names stex DOES emit, and doubling one up would make both colours move
 * together the next time either is retuned. */
const texTags = {
  /** `article` in `\documentclass{article}`, `amsmath` in `\usepackage{…}`. */
  packageName: Tag.define(),
  /** `figure` in `\begin{figure}` / `\end{figure}`. */
  envName: Tag.define(),
  /** `sec:intro` in `\label{…}`, `\ref{…}`, `\cite{…}`. */
  labelKey: Tag.define(),
  /** The command word itself for the handful that give a file its shape. */
  structure: Tag.define(),
};

const ENV_COMMANDS = new Set(["begin", "end"]);
const PACKAGE_COMMANDS = new Set(["documentclass", "usepackage", "importmodule"]);
const LABEL_COMMANDS = new Set([
  "label",
  "ref",
  "eqref",
  "cite",
  "bibitem",
  "Bibitem",
  "RBibitem",
]);

/**
 * The commands that carry a document's STRUCTURE, matched on the literal
 * text the stream just consumed rather than on any parser state -- these are
 * the lines a writer navigates by, and they read as ordinary commands
 * otherwise. `\section` and friends are included; `\textbf` and the other
 * ten thousand ordinary commands are deliberately not, because colouring
 * everything highlights nothing.
 */
const STRUCTURE_COMMANDS = new Set([
  "\\documentclass",
  "\\usepackage",
  "\\begin",
  "\\end",
  "\\input",
  "\\include",
  "\\includeonly",
  "\\bibliography",
  "\\bibliographystyle",
  "\\newcommand",
  "\\renewcommand",
  "\\providecommand",
  "\\newenvironment",
  "\\renewenvironment",
  "\\def",
  "\\part",
  "\\chapter",
  "\\section",
  "\\subsection",
  "\\subsubsection",
  "\\paragraph",
  "\\subparagraph",
]);

/**
 * Which command owns the argument the parser is currently inside.
 *
 * Read off `state.cmdState`, which is stex's own stack of "command plugins"
 * -- an internal of a legacy mode, not a documented API. Two things make
 * that acceptable: the mode is frozen legacy code, and every read here is
 * defensive (a shape that is not the expected stack of named objects simply
 * yields `null`, which leaves the token at stex's own "atom"). It is pinned
 * by `latex-syntax.test.ts`, which tokenises real LaTeX rather than
 * asserting on the shape -- so an upstream change surfaces as a failing
 * colour expectation instead of silently un-colouring the editor.
 *
 * Mirrors stex's own `getMostPowerful`: the innermost plugin that is not the
 * anonymous "DEFAULT" one pushed for a bare `{`.
 */
function owningCommand(state: unknown): string | null {
  const stack = (state as { cmdState?: unknown }).cmdState;
  if (!Array.isArray(stack)) return null;
  for (let i = stack.length - 1; i >= 0; i--) {
    const name = (stack[i] as { name?: unknown } | null)?.name;
    if (typeof name !== "string" || name === "DEFAULT") continue;
    return name;
  }
  return null;
}

/**
 * stex, with the two re-labelings above layered on top. Everything else
 * (`comment`, `bracket`, `number`, the math-mode tokens) passes through
 * untouched -- this wrapper only ever splits ONE stex token into several,
 * never invents a token where stex found none.
 */
export const texParser: StreamParser<unknown> = {
  ...stex,
  token(stream, state) {
    const style = stex.token(stream, state);
    if (style === "tag" && STRUCTURE_COMMANDS.has(stream.current())) return "structure";
    // "atom" is stex's label for a command's braced identifier argument. It
    // is not the ONLY thing that reaches here unstyled, though: stex gives
    // an argument no style at all once it runs past the end of that
    // command's own `styles` list -- which is every bracket after the first
    // for most commands, and the very FIRST bracket of `\documentclass`
    // (its list opens with an empty entry). So a null style is checked too,
    // but only for the commands whose every argument names a package or a
    // class either way; doing it for `\begin` would paint a float
    // placement like `[h]` as if it were an environment name.
    const bare = style === null && stream.current().trim() !== "";
    if (style !== "atom" && !bare) return style;
    const cmd = owningCommand(state);
    if (cmd === null) return style;
    if (PACKAGE_COMMANDS.has(cmd)) return "packageName";
    if (bare) return style;
    if (ENV_COMMANDS.has(cmd)) return "envName";
    if (LABEL_COMMANDS.has(cmd)) return "labelKey";
    return style;
  },
  tokenTable: {
    packageName: texTags.packageName,
    envName: texTags.envName,
    labelKey: texTags.labelKey,
    structure: texTags.structure,
  },
};

export const texLanguage = StreamLanguage.define(texParser);

/**
 * Every colour is a CSS variable, never a literal.
 *
 * A HighlightStyle is compiled into a stylesheet ONCE, at module load, so a
 * literal colour here could not answer to the theme -- the editor would keep
 * light-theme syntax colours on the dark background. The `--tex-*` variables
 * are defined for both themes in `app/globals.css` and resolve per element
 * at paint time, which is the only mechanism that tracks a theme switch with
 * no CodeMirror reconfiguration at all.
 */
export const latexHighlightStyle = HighlightStyle.define([
  { tag: tags.comment, color: "var(--tex-comment)", fontStyle: "italic" },
  { tag: texTags.structure, color: "var(--tex-structure)", fontWeight: "600" },
  { tag: tags.tagName, color: "var(--tex-command)" },
  { tag: texTags.envName, color: "var(--tex-env)" },
  { tag: texTags.labelKey, color: "var(--tex-label)" },
  { tag: texTags.packageName, color: "var(--tex-package)" },
  // stex's "keyword" is only ever a math-mode delimiter (`$`, `$$`, `\[`,
  // `\(` and their closers) -- there are no keywords in TeX otherwise.
  { tag: tags.keyword, color: "var(--tex-math)", fontWeight: "600" },
  {
    tag: tags.special(tags.variableName),
    color: "var(--tex-math-var)",
    fontStyle: "italic",
  },
  { tag: tags.number, color: "var(--tex-number)" },
  { tag: tags.bracket, color: "var(--tex-bracket)" },
  { tag: tags.string, color: "var(--tex-package)" },
  { tag: tags.standard(tags.variableName), color: "var(--tex-env)" },
  // Whatever `owningCommand` could not attribute: a bare number in text, or
  // an argument of a command stex tracks but this module does not group.
  { tag: tags.atom, color: "var(--tex-arg)" },
  // stex reports an unmatched `}` and an unrecognised character in math
  // mode this way. Coloured, never given a background: it is a hint, and
  // stex is not a TeX parser -- a false positive must not look like an
  // error the compiler agreed with.
  { tag: tags.invalid, color: "var(--tex-invalid)" },
]);
