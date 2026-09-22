"use client";

import { ConversationRow } from "@/components/chat/conversation-row";
import type { ChatConversation } from "@/lib/types";

/**
 * The conversation list.
 *
 * Three columns of content — Conversation, Started, Last activity — because
 * that is all `GET /projects/{id}/conversations` returns. The concept's row
 * also carried the last question asked, the conversation's scope and its
 * length; none of those has a source in that response, so they are left out
 * rather than invented.
 */
export function ConversationTable({
  conversations,
  projectId,
  editing,
  selectedIds,
  canDelete,
  downloadingId,
  deletingId,
  onToggleSelect,
  onDownload,
  onRename,
  onDelete,
}: {
  conversations: ChatConversation[];
  projectId: string;
  editing: boolean;
  selectedIds: ReadonlySet<string>;
  canDelete: boolean;
  downloadingId: string | null;
  deletingId: string | null;
  onToggleSelect: (conversation: ChatConversation) => void;
  onDownload: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  onDelete: (conversation: ChatConversation) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="hidden grid-cols-[1.5rem_minmax(0,1fr)_8rem_8rem_6.5rem] gap-3 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase sm:grid">
        <div />
        <div>Conversation</div>
        <div>Started</div>
        <div>Last activity</div>
        <div />
      </div>
      <div>
        {conversations.map((conversation) => (
          <ConversationRow
            key={conversation.id}
            conversation={conversation}
            projectId={projectId}
            editing={editing}
            selected={selectedIds.has(conversation.id)}
            canDelete={canDelete}
            downloading={downloadingId === conversation.id}
            deleting={deletingId === conversation.id}
            onToggleSelect={() => onToggleSelect(conversation)}
            onDownload={() => onDownload(conversation)}
            onRename={() => onRename(conversation)}
            onDelete={() => onDelete(conversation)}
          />
        ))}
      </div>
    </div>
  );
}
