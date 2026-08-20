"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MentionTextarea } from "@/components/mention-textarea";
import { RxTheme } from "@/components/rx-theme";
import { createConversation, deleteConversation, listConversations } from "@/lib/chat";
import { activityLabel, conversationCount, startedDay } from "@/lib/conversations";
import type { Mention } from "@/lib/mentions";
import { getProject, listPapers } from "@/lib/projects";
import type { ChatConversation, Paper, Role } from "@/lib/types";
import "./chat.css";

// Matches the backend: delete_conversation requires require_member(..., "editor").
// Creating a conversation and sending a message only require "viewer", which is
// why the composer below is not gated on anything.
const CAN_DELETE: Role[] = ["owner", "editor"];

function TrashGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true">
      <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8h5.8l.6-8" />
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

  useEffect(() => {
    listPapers(projectId).then(setPapers).catch(() => {});
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
      router.push(`/research/${projectId}/chat/${conv.id}?q=${encodeURIComponent(q)}${m}`);
    } catch {
      setSubmitError("Failed to start chat. Please try again.");
      setSubmitting(false);
    }
  }

  const canDelete = myRole !== null && CAN_DELETE.includes(myRole);
  const empty = !loading && conversations.length === 0;

  return (
    <RxTheme className="rx-ch">
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
            {conversations.map((conv) => (
              // A div, not a button: the delete control is itself a button and
              // nesting one inside another is invalid HTML. `.rx-copen::after`
              // is what makes the whole row clickable anyway.
              <div key={conv.id} className="rx-crow">
                <button
                  type="button"
                  onClick={() => router.push(`/research/${projectId}/chat/${conv.id}`)}
                  className="rx-copen"
                >
                  <span className="rx-ct">{conv.title}</span>
                </button>
                <span className="rx-cmeta">
                  <span className="rx-cd">{startedDay(conv.created_at)}</span>
                  <span className="rx-cd">{activityLabel(conv.updated_at)}</span>
                </span>
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
              </div>
            ))}
          </div>
        )}
      </div>
    </RxTheme>
  );
}
