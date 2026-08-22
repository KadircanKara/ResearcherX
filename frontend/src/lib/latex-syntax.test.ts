import { describe, expect, it } from "vitest";
import { StringStream } from "@codemirror/language";
import { texParser } from "./latex-syntax";

/**
 * Tokenises one line the way `StreamLanguage` does, returning
 * `[text, style]` for every non-blank token.
 *
 * These tests exist because `texParser` reads `state.cmdState`, an INTERNAL
 * of the frozen `stex` legacy mode. Asserting on real LaTeX rather than on
 * that shape is the point: if upstream ever changes it, the failure lands
 * here as "a label is no longer coloured as a label", which is the thing
 * that actually matters, instead of passing while the editor quietly goes
 * back to one colour.
 */
function tokenize(line: string, state = texParser.startState!(4)) {
  const stream = new StringStream(line, 4, 4);
  const out: [string, string | null][] = [];
  while (!stream.eol()) {
    const style = texParser.token(stream, state);
    const text = stream.current();
    stream.start = stream.pos;
    if (text.trim() !== "") out.push([text, style]);
  }
  return out;
}

/** The style stex/`texParser` gave the first token whose text matches. */
function styleOf(line: string, text: string) {
  return tokenize(line).find(([t]) => t === text)?.[1];
}

describe("texParser", () => {
  it("marks a % comment as a comment", () => {
    expect(tokenize("% a note")[0][1]).toBe("comment");
  });

  it("separates the four things stex lumps together as one 'atom'", () => {
    // The whole reason this module wraps stex: out of the box every one of
    // these four is "atom" and therefore one indistinguishable colour.
    expect(styleOf("\\documentclass{article}", "article")).toBe("packageName");
    expect(styleOf("\\usepackage{amsmath}", "amsmath")).toBe("packageName");
    expect(styleOf("\\begin{figure}", "figure")).toBe("envName");
    expect(styleOf("\\label{sec:intro}", "sec")).toBe("labelKey");
    expect(styleOf("\\cite{knuth1984}", "knuth1984")).toBe("labelKey");
  });

  it("leaves ordinary prose alone", () => {
    // Every re-label above hangs off an OWNING command; prose has none, so
    // nothing here may pick up a colour.
    expect(tokenize("we ran 42 trials").every(([, style]) => style === null)).toBe(true);
  });

  it("colours a class or package named in a bracket stex does not style", () => {
    // `\\documentclass`'s own style list opens with an empty entry, so stex
    // gives `article` no style at all -- and every command loses styling
    // past its first bracket, which is where the package in
    // `\\usepackage[T1]{fontenc}` lands.
    expect(styleOf("\\usepackage[T1]{fontenc}", "fontenc")).toBe("packageName");
    expect(styleOf("\\usepackage[T1]{fontenc}", "T1")).toBe("packageName");
  });

  it("does not paint a float placement as an environment name", () => {
    // Same unstyled-bracket case as above, deliberately NOT re-labelled:
    // `[h]` names a placement, not an environment.
    expect(styleOf("\\begin{figure}[h]", "figure")).toBe("envName");
    expect(styleOf("\\begin{figure}[h]", "h")).toBe(null);
  });

  it("colours structural commands apart from ordinary ones", () => {
    expect(styleOf("\\section{Intro}", "\\section")).toBe("structure");
    expect(styleOf("\\begin{figure}", "\\begin")).toBe("structure");
    expect(styleOf("\\textbf{bold}", "\\textbf")).toBe("tag");
  });

  it("does not mistake a command with a structural PREFIX for one", () => {
    // `\sectionmark` is not `\section`; matching on the consumed text as a
    // whole is what keeps it an ordinary command.
    expect(styleOf("\\sectionmark{x}", "\\sectionmark")).toBe("tag");
  });

  it("marks math delimiters, and leaves math letters as math variables", () => {
    const toks = tokenize("$x + 1$");
    expect(toks[0]).toEqual(["$", "keyword"]);
    expect(styleOf("$x + 1$", "x")).toBe("variableName.special");
    expect(styleOf("$x + 1$", "1")).toBe("number");
  });

  it("still labels a nested argument by its own command", () => {
    // `\ref` inside `\caption` -- the owning command is the INNERMOST named
    // one, not the outermost.
    expect(styleOf("\\caption{see \\ref{fig:a}}", "fig")).toBe("labelKey");
  });

  it("carries state across lines, so a label split over two lines still resolves", () => {
    const state = texParser.startState!(4);
    tokenize("\\begin{", state);
    expect(tokenize("figure}", state)[0]).toEqual(["figure", "envName"]);
  });
});
