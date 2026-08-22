import type { ChatConversationDetail } from "./types";

/**
 * A conversation as Markdown.
 *
 * PURE, and in `src/lib/` for the usual reason: vitest here runs in the node
 * environment with no jsdom, so logic worth a test cannot live in a
 * component. Formatting a transcript is exactly the kind of thing that
 * silently loses a message when it lives inside a click handler.
 *
 * The message `content` is written through UNCHANGED. It is already
 * Markdown -- it is what the report renderer renders -- so escaping it here
 * would corrupt every code fence and list the assistant produced.
 */

/** `2026-08-22` — the date alone; a transcript is not a log file. */
function isoDay(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toISOString().slice(0, 10);
}

export function conversationToMarkdown(
  conversation: ChatConversationDetail,
): string {
  const lines: string[] = [
    `# ${conversation.title}`,
    "",
    `_${isoDay(conversation.created_at)}_`,
    "",
  ];

  for (const message of conversation.messages) {
    lines.push(message.role === "user" ? "## You" : "## Assistant", "");
    lines.push(message.content.trim(), "");

    // Citations are kept because the markers in the text refer to them by
    // number: a transcript carrying "[1]" with no table saying what [1] was
    // is strictly worse than one with no markers at all.
    if (message.citations.length > 0) {
      lines.push("**Sources**", "");
      for (const citation of message.citations) {
        lines.push(`${citation.n}. ${citation.title}`);
      }
      lines.push("");
    }
  }

  // Exactly one trailing newline: most editors add one anyway, and two
  // makes every round trip through a formatter a diff.
  return `${lines.join("\n").trimEnd()}\n`;
}

/**
 * A filename that survives every filesystem.
 *
 * Windows refuses `\ / : * ? " < > |` outright and Finder treats `:` as a
 * separator, so a title like `Q3: what now?` would otherwise produce a file
 * the user cannot save. Falls back to a fixed name when a title reduces to
 * nothing -- an emoji-only title is not hypothetical.
 */
export function conversationFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80)
    .replace(/[. ]+$/, "");
  // A name has to carry at least one letter or digit to be worth keeping:
  // "///" cleans to "---", which is a legal filename that tells the user
  // nothing about what is in it.
  return `${/[\p{L}\p{N}]/u.test(cleaned) ? cleaned : "conversation"}.md`;
}
