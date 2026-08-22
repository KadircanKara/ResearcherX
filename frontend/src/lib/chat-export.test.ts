import { describe, expect, it } from "vitest";
import { conversationFilename, conversationToMarkdown } from "./chat-export";
import type { ChatConversationDetail, ChatMessage } from "./types";

function message(
  partial: Partial<ChatMessage> & Pick<ChatMessage, "role" | "content">,
): ChatMessage {
  return {
    id: "m1",
    citations: [],
    mentions: [],
    created_at: "2026-08-22T10:00:00Z",
    ...partial,
  };
}

function conversation(messages: ChatMessage[]): ChatConversationDetail {
  return {
    id: "c1",
    project_id: "p1",
    title: "Findings review",
    created_by: "u1",
    created_at: "2026-08-22T10:00:00Z",
    updated_at: "2026-08-22T10:00:00Z",
    messages,
  };
}

describe("conversationToMarkdown", () => {
  it("titles the document and labels both speakers", () => {
    const md = conversationToMarkdown(
      conversation([
        message({ role: "user", content: "What did they measure?" }),
        message({ role: "assistant", content: "Coverage and connectivity." }),
      ]),
    );
    expect(md).toContain("# Findings review");
    expect(md).toContain("## You");
    expect(md).toContain("## Assistant");
    expect(md.indexOf("## You")).toBeLessThan(md.indexOf("## Assistant"));
  });

  it("passes message content through untouched", () => {
    // The content IS Markdown -- it is what the renderer renders. Escaping
    // it would corrupt every code fence the assistant produced.
    const fenced = "Use this:\n\n```tex\n\\input{chapters/intro}\n```";
    const md = conversationToMarkdown(
      conversation([message({ role: "assistant", content: fenced })]),
    );
    expect(md).toContain("```tex\n\\input{chapters/intro}\n```");
  });

  it("lists citations, because the markers in the text refer to them by number", () => {
    const md = conversationToMarkdown(
      conversation([
        message({
          role: "assistant",
          content: "Coverage improves [1].",
          citations: [
            {
              n: 1,
              paper_id: "p",
              title: "Multi-UAV Path Planning",
              chunk_index: 3,
              snippet: "...",
            },
          ],
        }),
      ]),
    );
    expect(md).toContain("**Sources**");
    expect(md).toContain("1. Multi-UAV Path Planning");
  });

  it("omits the sources block for a message with no citations", () => {
    const md = conversationToMarkdown(
      conversation([message({ role: "user", content: "Hi" })]),
    );
    expect(md).not.toContain("**Sources**");
  });

  it("ends with exactly one newline", () => {
    const md = conversationToMarkdown(
      conversation([message({ role: "user", content: "Hi" })]),
    );
    expect(md.endsWith("\n")).toBe(true);
    expect(md.endsWith("\n\n")).toBe(false);
  });

  it("survives an empty conversation", () => {
    expect(conversationToMarkdown(conversation([]))).toContain(
      "# Findings review",
    );
  });
});

describe("conversationFilename", () => {
  it("appends .md", () => {
    expect(conversationFilename("Findings review")).toBe("Findings review.md");
  });

  it("replaces characters a filesystem refuses", () => {
    // Windows rejects these outright and Finder reads ":" as a separator,
    // so the untreated name produces a file the user cannot save.
    expect(conversationFilename("Q3: what now? a/b")).toBe(
      "Q3- what now- a-b.md",
    );
  });

  it("falls back when a title reduces to nothing", () => {
    expect(conversationFilename("///")).toBe("conversation.md");
    expect(conversationFilename("   ")).toBe("conversation.md");
  });

  it("does not end a name in a dot or a space", () => {
    // Windows silently strips both, so the file the user gets back is not
    // the file we said we wrote.
    expect(conversationFilename("Draft .")).toBe("Draft.md");
  });

  it("bounds the length", () => {
    expect(conversationFilename("x".repeat(300)).length).toBeLessThanOrEqual(
      83,
    );
  });
});
