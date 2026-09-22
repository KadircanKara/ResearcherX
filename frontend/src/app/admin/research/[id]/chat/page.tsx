"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessageSquare } from "lucide-react";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { Composer } from "@/components/chat/composer";
import { ConversationTable } from "@/components/chat/conversation-table";
import { PageHeader } from "@/components/page-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { RenameDialog } from "@/components/ui/rename-dialog";
import { SearchInput } from "@/components/ui/search-input";
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from "@/lib/chat";
import { conversationFilename, conversationToMarkdown } from "@/lib/chat-export";
import { conversationCount } from "@/lib/conversations";
import { saveBlob } from "@/lib/download";
import type { Mention } from "@/lib/mentions";
import { getProject, listPapers } from "@/lib/projects";
import { routes } from "@/lib/routes";
import { matchesQuery } from "@/lib/search";
import { clear, isAllSelected, retainVisible, selectAll, toggle } from "@/lib/selection";
import type { ChatConversation, Paper, Role } from "@/lib/types";

// Project sharing is binary now: any member may delete a conversation.
// Creating a conversation and sending a message need membership alone, which
// is why the composer below is not gated on anything.
const CAN_DELETE: Role[] = ["owner", "member"];

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
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

  // Asked through a real dialog, never `window.confirm` -- see
  // `ConfirmDialog`: a page that fires several native dialogs gets them
  // SUPPRESSED by Chrome, after which `confirm()` returns false without
  // opening anything and the delete silently does nothing. A conversation
  // cannot be restored from any source, so a single one is confirmed too,
  // not just a bulk selection.
  const [pendingDelete, setPendingDelete] = useState<ChatConversation | null>(null);
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);

  async function handleDelete(conversationId: string) {
    setPendingDelete(null);
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
        `${routes.conversation(projectId, conv.id)}?q=${encodeURIComponent(q)}${m}`,
      );
    } catch {
      setSubmitError("Failed to start chat. Please try again.");
      setSubmitting(false);
    }
  }

  const canDelete = myRole !== null && CAN_DELETE.includes(myRole);
  const empty = !loading && conversations.length === 0;

  const visible = conversations.filter((c) => matchesQuery(query, [c.title]));
  const visibleIds = visible.map((c) => c.id);

  return (
    <div className="fade-block space-y-5">
      <PageHeader
        eyebrow="Chat"
        title="Conversations"
        meta={
          <>
            <p>{loading ? "Reading the conversations" : conversationCount(conversations.length)}</p>
            {canDelete && <p>You can delete any of them.</p>}
          </>
        }
      />

      <p className="max-w-2xl text-[13px] text-muted-foreground">
        Ask something new below, or reopen a conversation to carry on where you left off.
        Each one keeps its own citations.
      </p>

      <Composer
        papers={papers}
        value={content}
        onChange={setContent}
        mentions={mentions}
        onMentionsChange={setMentions}
        onSubmit={handleStart}
        disabled={submitting}
        submitLabel={submitting ? "Starting…" : "Start the conversation"}
        placeholder="Ask a question about this project's papers…  Type @ to mention one"
        error={submitError}
      />

      {bulkError && (
        <p role="alert" className="text-[13px] text-destructive">
          {bulkError}
        </p>
      )}

      {loading ? (
        <div className="space-y-2" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-muted" />
          ))}
        </div>
      ) : empty ? (
        <EmptyState
          icon={MessageSquare}
          title="Nothing asked yet. Start with what you actually want to know."
          body="Answers here are built only from the papers in this project, and every sentence carries the excerpt it came from. Type @ to search inside one paper instead of all of them."
        />
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="w-full sm:w-72">
              <SearchInput
                value={query}
                onChange={changeQuery}
                placeholder="Search conversations…"
                label="Search conversations by title"
              />
            </div>
            {canDelete && (
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
                  // A selection that survives invisibly is a delete waiting
                  // to hit the wrong rows.
                  setSelected(clear());
                }}
              />
            )}
          </div>

          {visible.length === 0 ? (
            // A query that matches nothing needs saying: an empty list under a
            // filled search box otherwise reads as the chats having disappeared.
            <NoMatchState query={query} noun="conversations" />
          ) : (
            <ConversationTable
              conversations={visible}
              projectId={projectId}
              editing={editingMode}
              selectedIds={selected}
              canDelete={canDelete}
              downloadingId={downloading}
              deletingId={deleting}
              onToggleSelect={(conv) => setSelected(toggle(selected, conv.id))}
              onDownload={(conv) => void handleDownload(conv)}
              onRename={(conv) => {
                setRenameError(null);
                setRenaming(conv);
              }}
              onDelete={setPendingDelete}
            />
          )}
        </div>
      )}

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
        open={pendingDelete !== null}
        title="Delete this conversation?"
        description={`“${pendingDelete?.title ?? ""}” and its answers will be deleted. This cannot be undone.`}
        confirmLabel="Delete"
        busy={deleting !== null}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => pendingDelete && void handleDelete(pendingDelete.id)}
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
