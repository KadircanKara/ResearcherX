"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessageSquarePlus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createConversation, listConversations } from "@/lib/chat";
import type { ChatConversation } from "@/lib/types";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });
}

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    listConversations(projectId)
      .then(setConversations)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId]);

  async function handleStart() {
    const q = content.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const conv = await createConversation(projectId, q);
      setSubmitting(false);
      router.push(`/research/${projectId}/chat/${conv.id}`);
    } catch {
      setSubmitError("Failed to start chat. Please try again.");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3 py-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-14 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {conversations.length === 0
            ? "No conversations yet"
            : `${conversations.length} conversation${conversations.length !== 1 ? "s" : ""}`}
        </p>
        {!showForm && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            <Plus className="mr-1.5 size-3.5" />
            New Chat
          </Button>
        )}
      </div>

      {showForm && (
        <div className="mb-4 rounded-xl border border-border bg-card p-4">
          <textarea
            autoFocus
            rows={3}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleStart();
              }
            }}
            placeholder="Ask a question about the assigned papers…"
            className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {submitError && (
            <p className="mt-1 text-xs text-destructive">{submitError}</p>
          )}
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={handleStart} disabled={!content.trim() || submitting}>
              <MessageSquarePlus className="mr-1.5 size-3.5" />
              {submitting ? "Starting…" : "Start Chat"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setShowForm(false); setContent(""); setSubmitError(null); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {conversations.length === 0 && !showForm && (
        <div className="flex flex-col items-center gap-2 py-24 text-center">
          <p className="text-sm text-muted-foreground">
            Start a conversation to ask questions about the assigned papers.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {conversations.map((conv) => (
          <button
            key={conv.id}
            type="button"
            onClick={() => router.push(`/research/${projectId}/chat/${conv.id}`)}
            className="group flex w-full items-start rounded-xl border border-border bg-card px-4 py-3 text-left transition-colors hover:bg-muted"
          >
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-sm font-medium text-foreground">
                {conv.title}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {fmtDate(conv.updated_at)}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
