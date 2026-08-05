"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessageSquarePlus, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createConversation, deleteConversation, listConversations } from "@/lib/chat";
import { getProject } from "@/lib/projects";
import type { ChatConversation, Role } from "@/lib/types";

// Matches the backend: delete_conversation requires require_member(..., "editor").
const CAN_DELETE: Role[] = ["owner", "editor"];

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });
}

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // `silent` skips the loading skeleton — used by handleDelete's error path to
  // resync without flashing the whole list away under the user.
  const load = useCallback((opts: { silent?: boolean } = {}) => {
    if (!opts.silent) setLoading(true);
    Promise.all([listConversations(projectId), getProject(projectId)])
      .then(([convs, detail]) => {
        setConversations(convs);
        setMyRole(detail.my_role);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // Deletes immediately, no confirmation — deliberate for now, matching the
  // papers list. Unlike a paper, a deleted conversation cannot be restored from
  // any source, so this should become a confirm step before production.
  async function handleDelete(conversationId: string) {
    setDeleting(conversationId);
    try {
      await deleteConversation(projectId, conversationId);
      setConversations((prev) => prev.filter((c) => c.id !== conversationId));
    } catch {
      load({ silent: true });
    } finally {
      setDeleting(null);
    }
  }

  async function handleStart() {
    const q = content.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const conv = await createConversation(projectId, q);
      setSubmitting(false);
      router.push(`/research/${projectId}/chat/${conv.id}?q=${encodeURIComponent(q)}`);
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
          // A div, not a button: the delete control is itself a button and
          // nesting one inside another is invalid HTML.
          <div
            key={conv.id}
            className="group flex w-full items-start gap-2 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:bg-muted"
          >
            <button
              type="button"
              onClick={() => router.push(`/research/${projectId}/chat/${conv.id}`)}
              className="min-w-0 flex-1 text-left"
            >
              <p className="line-clamp-2 text-sm font-medium text-foreground">
                {conv.title}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {fmtDate(conv.updated_at)}
              </p>
            </button>
            {myRole && CAN_DELETE.includes(myRole) && (
              <button
                type="button"
                onClick={() => handleDelete(conv.id)}
                disabled={deleting === conv.id}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                aria-label={`Delete conversation: ${conv.title}`}
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
