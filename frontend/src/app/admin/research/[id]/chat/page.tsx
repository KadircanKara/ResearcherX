"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessageSquare } from "lucide-react";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { ConversationSearch } from "@/components/chat/conversation-search";
import { ConversationTable } from "@/components/chat/conversation-table";
import { MentionComposer } from "@/components/chat/mention-composer";
import { PageHeader } from "@/components/page-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, NoMatchState } from "@/components/ui/empty-state";
import { RenameDialog } from "@/components/ui/rename-dialog";
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
import { clear, retainVisible, selectAll, toggle } from "@/lib/selection";
import type { ChatConversation, Paper, Role } from "@/lib/types";

// Project sharing is binary now: any member may delete a conversation.
// Creating a conversation and sending a message need membership alone, which
// is why the composer below is not gated on anything.
const CAN_DELETE: Role[] = ["owner", "member"];

const HELPER = "Type @ to name a paper and search only inside it";

export default function ChatPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [mentions, setMentions] = useState<Mention[]>([]);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [listError, setListError] = useState<string | null>(null);

  // `silent` skips the loading skeleton — used after a delete to resync
  // without flashing the whole list away under the user.
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
  // not just a bulk selection. One dialog serves both, as in the prototype.
  // The targets outlive `confirmOpen` so the title keeps its count while the
  // dialog animates closed.
  const [confirming, setConfirming] = useState<ChatConversation[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deletingIds, setDeletingIds] = useState<ReadonlySet<string>>(new Set());

  function askToDelete(targets: ChatConversation[]) {
    setConfirming(targets);
    setConfirmOpen(true);
  }

  async function handleDelete(targets: ChatConversation[]) {
    setConfirmOpen(false);
    setListError(null);
    const ids = targets.map((t) => t.id);
    setDeletingIds(new Set(ids));
    const results = await Promise.allSettled(
      ids.map((id) => deleteConversation(projectId, id)),
    );
    const failed = new Set(ids.filter((_, i) => results[i].status === "rejected"));
    const gone = new Set(ids.filter((id) => !failed.has(id)));
    setConversations((prev) => prev.filter((c) => !gone.has(c.id)));
    // Rows that failed stay selected, so a retry is one click.
    setSelected((prev) => new Set([...prev].filter((id) => !gone.has(id))));
    setDeletingIds(new Set());
    if (failed.size > 0) {
      setListError(
        ids.length === 1
          ? "Could not delete this conversation. Please try again."
          : `${failed.size} of ${ids.length} could not be deleted.`,
      );
    }
    // Re-fetched when anything went wrong or several went at once: what just
    // proved unreliable is precisely this client's idea of what exists.
    if (failed.size > 0 || ids.length > 1) load({ silent: true });
  }

  const [renaming, setRenaming] = useState<ChatConversation | null>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  function changeSearch(next: string) {
    setSearch(next);
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
      setListError("Could not download this conversation. Please try again.");
    } finally {
      setDownloading(null);
    }
  }

  async function handleStart() {
    const q = draft.trim();
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
  const hasAnyConversations = conversations.length > 0;

  const filtered = conversations.filter((c) => matchesQuery(search, [c.title]));
  const filteredIds = filtered.map((c) => c.id);
  const confirmCount = confirming.length;

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
        Ask something new below, or reopen a conversation to carry on where you left off. Each one
        keeps its own citations.
      </p>

      <MentionComposer
        papers={papers}
        value={draft}
        onChange={setDraft}
        mentions={mentions}
        onMentionsChange={setMentions}
        onSubmit={() => void handleStart()}
        submitting={submitting}
        submitLabel="Start the conversation"
        placeholder="Ask a question about this project's papers… use @ to mention one"
        helperText={HELPER}
      />

      {submitError && (
        <p role="alert" className="border-l-2 border-destructive pl-3 text-[13px] text-destructive">
          {submitError}
        </p>
      )}

      {loading ? (
        <div className="space-y-2" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-muted" />
          ))}
        </div>
      ) : !hasAnyConversations ? (
        <EmptyState
          icon={MessageSquare}
          title="Nothing asked yet. Start with what you actually want to know."
          body="Answers here are built only from the papers in this project, and every sentence carries the excerpt it came from. Type @ to search inside one paper instead of all of them."
        />
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ConversationSearch value={search} onChange={changeSearch} />
            {canDelete && (
              <BulkEditBar
                editing={editing}
                selectedCount={selected.size}
                busy={deletingIds.size > 0}
                onStart={() => setEditing(true)}
                onSelectAll={() => setSelected(selectAll(selected, filteredIds))}
                onClear={() => setSelected(clear())}
                onDelete={() => askToDelete(filtered.filter((c) => selected.has(c.id)))}
                onDone={() => {
                  setEditing(false);
                  // A selection that survives invisibly is a delete waiting
                  // to hit the wrong rows.
                  setSelected(clear());
                }}
              />
            )}
          </div>

          {listError && (
            <p role="alert" className="text-[13px] text-destructive">
              {listError}
            </p>
          )}

          {filtered.length === 0 ? (
            // A query that matches nothing needs saying: an empty list under a
            // filled search box otherwise reads as the chats having disappeared.
            <NoMatchState query={search} noun="conversations" />
          ) : (
            <ConversationTable
              conversations={filtered}
              projectId={projectId}
              editing={editing}
              selectedIds={selected}
              canDelete={canDelete}
              downloadingId={downloading}
              deletingIds={deletingIds}
              onToggleSelect={(conv) => setSelected(toggle(selected, conv.id))}
              onDownload={(conv) => void handleDownload(conv)}
              onRename={(conv) => {
                setRenameError(null);
                setRenaming(conv);
              }}
              onDelete={(conv) => askToDelete([conv])}
            />
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        title={`Delete ${confirmCount} conversation${confirmCount === 1 ? "" : "s"}?`}
        description="This cannot be undone."
        onConfirm={() => void handleDelete(confirming)}
      />

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
    </div>
  );
}
