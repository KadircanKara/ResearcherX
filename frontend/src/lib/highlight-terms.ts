import type { Element, Root, RootContent } from "hast";

// Kept identical to the class list the card used before markdown rendering,
// so the highlight looks the same as it always has.
const MARK_CLASS = ["rounded", "bg-amber-300/30", "px-0.5", "text-inherit"];

/** Rehype plugin wrapping query terms in <mark>.
 *
 * This used to be a function that split a plain string and returned React
 * nodes. That stops being possible once react-markdown owns the tree, so the
 * same matching rules move here: case-insensitive, word-bounded, longest term
 * first, and every term regex-escaped.
 *
 * Never dangerouslySetInnerHTML — this text is paper-derived. Same reasoning
 * as the rehype-raw prohibition on the report renderer.
 */
export function highlightTerms(options: { terms: string[] }) {
  return function transform(tree: Root): void {
    if (options.terms.length === 0) return;

    // Longest first so "connectivity" wins over "connect".
    const escaped = [...options.terms]
      .sort((a, b) => b.length - a.length)
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    // \b word boundaries so "search" doesn't light up inside "researcher".
    // \b matches at any word/non-word transition, so it still holds next to
    // punctuation — "reward," and "(reward)" both keep "reward" highlighted.
    const pattern = new RegExp(`\\b(${escaped.join("|")})\\b`, "gi");

    function walk(node: Root | Element): void {
      const next: RootContent[] = [];
      let changed = false;
      for (const child of node.children as RootContent[]) {
        if (child.type === "text") {
          const parts = child.value.split(pattern);
          if (parts.length === 1) {
            next.push(child);
            continue;
          }
          changed = true;
          // String.split with a capturing group alternates non-match, match,
          // non-match... so odd indices are the hits.
          parts.forEach((part, i) => {
            if (part === "") return;
            if (i % 2 === 1) {
              next.push({
                type: "element",
                tagName: "mark",
                properties: { className: [...MARK_CLASS] },
                children: [{ type: "text", value: part }],
              } as Element);
            } else {
              next.push({ type: "text", value: part });
            }
          });
          continue;
        }
        if (child.type === "element") walk(child);
        next.push(child);
      }
      if (changed) node.children = next as typeof node.children;
    }

    walk(tree);
  };
}
