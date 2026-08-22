"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  Download,
  MessageSquarePlus,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RenameDialog } from "@/components/ui/rename-dialog";
import { SearchInput } from "@/components/ui/search-input";
import { MentionTextarea } from "@/components/mention-textarea";
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from "@/lib/chat";
import {
  conversationFilename,
  conversationToMarkdown,
} from "@/lib/chat-export";
import { saveBlob } from "@/lib/download";
import type { Mention } from "@/lib/mentions";
import { getProject, listPapers } from "@/lib/projects";
import {
  clear,
  isAllSelected,
  retainVisible,
  selectAll,
  toggle,
} from "@/lib/selection";
import { matchesQuery } from "@/lib/search";
import type { ChatConversation, Paper, Role } from "@/lib/types";

// Project sharing is binary now: any member may delete a conversation.
const CAN_DELETE: Role[] = ["owner", "member"];

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
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
  const [mentions, setMentions] = useState<Mention[]>([]);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [editingMode, setEditingMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  // `silent` skips the loading skeleton — used by handleDelete's error path to
  // resync without flashing the whole list away under the user.
  const load = useCallback(
    (opts: { silent?: boolean } = {}) => {
      if (!opts.silent) setLoading(true);
      Promise.all([listConversations(projectId), getProject(projectId)])
        .then(([convs, detail]) => {
          setConversations(convs);
          setMyRole(detail.my_role);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    },
    [projectId],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    listPapers(projectId)
      .then(setPapers)
      .catch(() => {});
  }, [projectId]);

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

  const [renaming, setRenaming] = useState<ChatConversation | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  function changeQuery(next: string) {
    setQuery(next);
    // Selections that just left the screen go with it -- Delete must never
    // reach a row the user cannot see.
    const stillVisible = conversations
      .filter((c) => matchesQuery(next, [c.title]))
      .map((c) => c.id);
    setSelected((prev) => retainVisible(prev, stillVisible));
  }

  async function handleRename(title: string) {
    const target = renaming;
    if (!target) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      const updated = await renameConversation(projectId, target.id, title);
      setConversations((prev) =>
        prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)),
      );
      setRenaming(null);
    } catch {
      // Generic on purpose: a rename fails only on a request the user
      // cannot act on, and the server's text for those is an
      // implementation detail.
      setRenameError("Could not rename this conversation. Please try again.");
    } finally {
      setRenameBusy(false);
    }
  }

  async function handleDownload(conv: ChatConversation) {
    setDownloading(conv.id);
    try {
      // Fetched fresh rather than exported from the list: the list carries
      // titles and dates only, and a transcript without its messages is not
      // a transcript.
      const detail = await getConversation(projectId, conv.id);
      saveBlob(
        new Blob([conversationToMarkdown(detail)], {
          type: "text/markdown;charset=utf-8",
        }),
        conversationFilename(detail.title),
      );
    } catch {
      setBulkError("Could not download this conversation. Please try again.");
    } finally {
      setDownloading(null);
    }
  }

  // Asked through a real dialog, never `window.confirm` -- see
  // `ConfirmDialog`: a page that fires several native dialogs gets them
  // SUPPRESSED by Chrome, after which `confirm()` returns false without
  // opening anything and the delete silently does nothing.
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);

  async function handleBulkDelete() {
    setPendingBulkDelete(false);
    setBulkBusy(true);
    setBulkError(null);
    const ids = [...selected];
    const results = await Promise.allSettled(
      ids.map((id) => deleteConversation(projectId, id)),
    );
    const failed = ids.filter((_, i) => results[i].status === "rejected");
    setSelected(new Set(failed));
    if (failed.length > 0) {
      setBulkError(`${failed.length} of ${ids.length} could not be deleted.`);
    }
    setBulkBusy(false);
    // Re-fetched unconditionally: what just proved unreliable is precisely
    // this client's idea of what exists.
    load({ silent: true });
  }

  async function handleStart() {
    const q = content.trim();
    if (!q || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const conv = await createConversation(projectId, q);
      const ids = mentions.map((mention) => mention.paperId);
      const m = ids.length ? `&m=${ids.map(encodeURIComponent).join(",")}` : "";
      setSubmitting(false);
      router.push(
        `/research/${projectId}/chat/${conv.id}?q=${encodeURIComponent(q)}${m}`,
      );
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

  const visible = conversations.filter((c) => matchesQuery(query, [c.title]));
  const visibleIds = visible.map((c) => c.id);

  return (
    <div>
      {/* Count and actions on one line, the search box on its own beneath
          them at full width -- see the papers page for why. */}
      <div className="mb-4 flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {conversations.length === 0
              ? "No conversations yet"
              : query
                ? `${visible.length} of ${conversations.length} conversations`
                : `${conversations.length} conversation${conversations.length !== 1 ? "s" : ""}`}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <BulkEditBar
              active={editingMode}
              count={selected.size}
              total={visibleIds.length}
              allSelected={isAllSelected(selected, visibleIds)}
              busy={bulkBusy}
              onEnter={() => setEditingMode(true)}
              onSelectAll={() => setSelected(selectAll(selected, visibleIds))}
              onClear={() => setSelected(clear())}
              onDelete={() => setPendingBulkDelete(true)}
              onDone={() => {
                setEditingMode(false);
                // A selection that survives invisibly is a delete waiting to
                // hit the wrong rows.
                setSelected(clear());
              }}
            />
            {!showForm && (
              <Button onClick={() => setShowForm(true)}>
                <Plus className="size-4" />
                New Chat
              </Button>
            )}
          </div>
        </div>
        {conversations.length > 0 && (
          <SearchInput
            value={query}
            onChange={changeQuery}
            placeholder="Search conversations…"
            label="Search conversations by title"
          />
        )}
      </div>

      {bulkError && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {bulkError}
        </p>
      )}

      {showForm && (
        <div className="mb-4 rounded-xl border border-border bg-card p-4">
          <MentionTextarea
            value={content}
            onChange={setContent}
            mentions={mentions}
            onMentionsChange={setMentions}
            papers={papers}
            disabled={submitting}
            onSubmit={handleStart}
          />
          {submitError && (
            <p className="mt-1 text-xs text-destructive">{submitError}</p>
          )}
          <div className="mt-3 flex gap-2">
            <Button
              onClick={handleStart}
              disabled={!content.trim() || submitting}
            >
              <MessageSquarePlus className="size-4" />
              {submitting ? "Starting…" : "Start Chat"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setShowForm(false);
                setContent("");
                setMentions([]);
                setSubmitError(null);
              }}
            >
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

      {/* A query that matches nothing needs saying: an empty list under a
          filled search box otherwise reads as the chats having disappeared. */}
      {conversations.length > 0 && visible.length === 0 && (
        <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          No conversations match “{query}”.
        </p>
      )}

      <div className="space-y-2">
        {visible.map((conv) => (
          // A div, not a button: the delete control is itself a button and
          // nesting one inside another is invalid HTML.
          <div
            key={conv.id}
            className="group flex w-full items-start gap-2 rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:bg-muted"
          >
            {editingMode && (
              <input
                type="checkbox"
                checked={selected.has(conv.id)}
                onChange={() => setSelected(toggle(selected, conv.id))}
                aria-label={`Select ${conv.title}`}
                className="mt-1 size-4 shrink-0"
              />
            )}
            <button
              type="button"
              onClick={() =>
                router.push(`/research/${projectId}/chat/${conv.id}`)
              }
              className="min-w-0 flex-1 text-left"
            >
              <p className="line-clamp-2 text-sm font-medium text-foreground">
                {conv.title}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {fmtDate(conv.updated_at)}
              </p>
            </button>
            <button
              type="button"
              onClick={() => void handleDownload(conv)}
              disabled={downloading === conv.id}
              className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              aria-label={`Download conversation as Markdown: ${conv.title}`}
              title="Download as .md"
            >
              <Download className="size-3.5" />
            </button>
            {myRole && CAN_DELETE.includes(myRole) && (
              <button
                type="button"
                onClick={() => {
                  setRenameError(null);
                  setRenaming(conv);
                }}
                className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground"
                aria-label={`Rename conversation: ${conv.title}`}
                title="Rename"
              >
                <Pencil className="size-3.5" />
              </button>
            )}
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

      <RenameDialog
        open={renaming !== null}
        title="Rename conversation"
        label="Title"
        initialValue={renaming?.title ?? ""}
        busy={renameBusy}
        error={renameError}
        onCancel={() => {
          setRenaming(null);
          setRenameError(null);
        }}
        onSubmit={(value) => void handleRename(value)}
      />

      <ConfirmDialog
        open={pendingBulkDelete}
        title={`Delete ${selected.size} conversation${selected.size !== 1 ? "s" : ""}?`}
        description="This cannot be undone."
        confirmLabel="Delete"
        busy={bulkBusy}
        onCancel={() => setPendingBulkDelete(false)}
        onConfirm={() => void handleBulkDelete()}
      />
    </div>
  );
}
