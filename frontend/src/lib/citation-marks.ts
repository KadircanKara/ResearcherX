import type { Element, Root, RootContent, Text } from "hast";

export type CitationToken =
  | { kind: "text"; value: string }
  | { kind: "cite"; n: number; group: number[] };

// A marker is a bracketed run of digits. Anything else in brackets — a year,
// a section reference, a note — is prose and must survive untouched.
const MARKER = /\[(\d+)\]/g;

// Two markers belong to the same run when only whitespace and separating
// punctuation sit between them: "[7], [8]" is one run, "[6] and later [7]"
// is two. The run is what the hover card's arrows step through.
const SEPARATOR = /^[\s,;]*$/;

/** Split text into plain-text and citation tokens.
 *
 * Pure and hast-free on purpose: this is the part that rewrites answer text,
 * so it is the part worth testing in isolation.
 */
export function tokenizeCitations(text: string, valid: Set<number>): CitationToken[] {
  const hits: { n: number; start: number; end: number }[] = [];
  for (const m of text.matchAll(MARKER)) {
    const n = Number(m[1]);
    // Only numbers this message actually cites. The model can emit a marker
    // past the end of the list; the server rewrites those, but a bracketed
    // number that was never a citation must stay prose either way.
    if (valid.has(n)) {
      hits.push({ n, start: m.index, end: m.index + m[0].length });
    }
  }
  if (hits.length === 0) return [{ kind: "text", value: text }];

  // Group adjacent hits into runs before emitting, so every marker in a run
  // carries the whole run — a marker cannot know its siblings otherwise.
  const runs: number[][] = [];
  let current = [0];
  for (let i = 1; i < hits.length; i++) {
    const between = text.slice(hits[i - 1].end, hits[i].start);
    if (SEPARATOR.test(between)) current.push(i);
    else {
      runs.push(current);
      current = [i];
    }
  }
  runs.push(current);
  const runOf = new Map<number, number[]>();
  for (const run of runs) {
    const numbers = run.map((i) => hits[i].n);
    for (const i of run) runOf.set(i, numbers);
  }

  const tokens: CitationToken[] = [];
  let cursor = 0;
  hits.forEach((hit, i) => {
    if (hit.start > cursor) {
      tokens.push({ kind: "text", value: text.slice(cursor, hit.start) });
    }
    tokens.push({ kind: "cite", n: hit.n, group: runOf.get(i) ?? [hit.n] });
    cursor = hit.end;
  });
  if (cursor < text.length) tokens.push({ kind: "text", value: text.slice(cursor) });
  return tokens;
}

// Markers inside these never become citations: the chat prompt asks for
// backticks around identifiers, so `arr[6]` arrives routinely.
const CODE_TAGS = new Set(["code", "pre"]);

function toNode(token: CitationToken): RootContent {
  if (token.kind === "text") return { type: "text", value: token.value } as Text;
  return {
    type: "element",
    tagName: "span",
    properties: {
      dataCitationN: String(token.n),
      dataCitationGroup: token.group.join(","),
    },
    children: [{ type: "text", value: `[${token.n}]` }],
  } as Element;
}

/** Rehype plugin turning citation markers into tagged spans.
 *
 * A `span` with data attributes rather than a custom tag name: react-markdown
 * maps hast elements to React components by tag, and a boring standard tag
 * removes any question about whether an unknown one survives the pipeline.
 * Markdown itself produces essentially no spans, so overriding it costs
 * nothing.
 */
export function citationMarks(options: { valid: Set<number> }) {
  return function transform(tree: Root): void {
    if (options.valid.size === 0) return;

    function walk(node: Root | Element): void {
      const next: RootContent[] = [];
      let changed = false;
      for (const child of node.children as RootContent[]) {
        if (child.type === "text") {
          const tokens = tokenizeCitations(child.value, options.valid);
          if (tokens.length === 1 && tokens[0].kind === "text") {
            next.push(child);
          } else {
            changed = true;
            for (const token of tokens) next.push(toNode(token));
          }
          continue;
        }
        if (child.type === "element" && !CODE_TAGS.has(child.tagName)) {
          walk(child);
        }
        next.push(child);
      }
      if (changed) node.children = next as typeof node.children;
    }

    walk(tree);
  };
}
