"use client";

import { routes } from "@/lib/routes";
import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { BulkEditBar } from "@/components/bulk-edit-bar";
import { MentionTextarea } from "@/components/mention-textarea";
import { RxTheme } from "@/components/rx-theme";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
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
import { activityLabel, conversationCount, startedDay } from "@/lib/conversations";
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
import "./chat.css";

// Project sharing is binary now: any member may delete a conversation.
// Creating a conversation and sending a message need membership alone, which
// is why the composer below is not gated on anything.
const CAN_DELETE: Role[] = ["owner", "member"];

function TrashGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8h5.8l.6-8" />
    </svg>
  );
}

function DownloadGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <path d="M8 2.5v8M4.8 7.3 8 10.5l3.2-3.2M3 13.5h10" />
    </svg>
  );
}

function PencilGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <path d="M10.5 2.8l2.7 2.7L6 12.7l-3.2.5.5-3.2z" />
    </svg>
  );
}

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
    <RxTheme className="rx-ch" typeface="app">
      <div className="rx-shell">
        <header className="rx-head">
          <div>
            <div className="rx-eyebrow">Chat</div>
            <h1>Conversations</h1>
          </div>
          <div className="rx-meta">
            {loading ? "Reading the conversations" : conversationCount(conversations.length)}
            {canDelete && (
              <>
                <br />
                You can delete any of them
              </>
            )}
          </div>
        </header>

        {!empty && (
          <p className="rx-lede">
            Ask something new below, or reopen a conversation to carry on where you left
            off. Each one keeps its own citations.
          </p>
        )}

        {!empty && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
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
        )}

        {bulkError && (
          <p role="alert" className="rx-cherror" style={{ margin: "0 0 12px" }}>
            {bulkError}
          </p>
        )}

        <div className="rx-newq">
          <div className="rx-composer">
            <MentionTextarea
              value={content}
              onChange={setContent}
              mentions={mentions}
              onMentionsChange={setMentions}
              papers={papers}
              disabled={submitting}
              onSubmit={handleStart}
            />
            <div className="rx-bar">
              <span>
                Type <b>@</b> to name a paper and search only inside it
              </span>
              {submitError && (
                <span role="status" className="rx-cherr">
                  {submitError}
                </span>
              )}
              <button
                type="button"
                className="rx-btn rx-push"
                onClick={handleStart}
                disabled={!content.trim() || submitting}
              >
                {submitting ? "Starting…" : "Start the conversation"}
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="rx-clist" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="rx-chskel" />
            ))}
          </div>
        ) : empty ? (
          <div className="rx-empty">
            <h2>Nothing asked yet. Start with what you actually want to know.</h2>
            <p>
              Answers here are built only from the papers in this project, and every
              sentence carries the excerpt it came from. Type @ to search inside one
              paper instead of all of them.
            </p>
          </div>
        ) : visible.length === 0 ? (
          // A query that matches nothing needs saying: an empty list under a
          // filled search box otherwise reads as the chats having disappeared.
          <p className="rx-lede">No conversations match “{query}”.</p>
        ) : (
          <div className="rx-clist">
            {/* The concept's row also carries the last question asked, the
                conversation's scope and its length. `GET
                /projects/{id}/conversations` returns id, project_id, title,
                created_by, created_at and updated_at — no messages, no counts,
                no scope — so those three columns have no source and are left
                out rather than invented. */}
            <div className="rx-ccols" aria-hidden="true">
              <span>Conversation</span>
              <span>Started</span>
              <span>Last activity</span>
              <span />
            </div>
            {visible.map((conv) => (
              // A div, not a button: the delete control is itself a button and
              // nesting one inside another is invalid HTML. `.rx-copen::after`
              // is what makes the whole row clickable anyway.
              <div key={conv.id} className="rx-crow">
                <span style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
                  {editingMode && (
                    // Above the row's click-through overlay, so ticking the
                    // box never opens the conversation.
                    <input
                      type="checkbox"
                      checked={selected.has(conv.id)}
                      onChange={() => setSelected(toggle(selected, conv.id))}
                      aria-label={`Select ${conv.title}`}
                      style={{ position: "relative", zIndex: 1, flexShrink: 0 }}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => router.push(routes.conversation(projectId, conv.id))}
                    className="rx-copen"
                  >
                    <span className="rx-ct">{conv.title}</span>
                  </button>
                </span>
                <span className="rx-cmeta">
                  <span className="rx-cd">{startedDay(conv.created_at)}</span>
                  <span className="rx-cd">{activityLabel(conv.updated_at)}</span>
                </span>
                <span style={{ display: "flex", gap: 2, justifySelf: "end" }}>
                  <button
                    type="button"
                    onClick={() => void handleDownload(conv)}
                    disabled={downloading === conv.id}
                    className="rx-cdel rx-ctool"
                    aria-label={`Download conversation as Markdown: ${conv.title}`}
                    title="Download as .md"
                  >
                    <DownloadGlyph />
                  </button>
                  {canDelete && (
                    <button
                      type="button"
                      onClick={() => {
                        setRenameError(null);
                        setRenaming(conv);
                      }}
                      className="rx-cdel rx-ctool"
                      aria-label={`Rename conversation: ${conv.title}`}
                      title="Rename"
                    >
                      <PencilGlyph />
                    </button>
                  )}
                  {canDelete && (
                    <button
                      type="button"
                      onClick={() => void handleDelete(conv.id)}
                      disabled={deleting === conv.id}
                      className="rx-cdel"
                      aria-label={`Delete conversation: ${conv.title}`}
                    >
                      <TrashGlyph />
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
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
    </RxTheme>
  );
}
