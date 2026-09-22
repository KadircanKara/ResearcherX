"use client";

import { ConversationRow } from "@/components/chat/conversation-row";
import type { ChatConversation } from "@/lib/types";

/**
 * The conversation list, drawn as the app prototype's table.
 *
 * Three columns of content — Conversation, Started, Last activity — because
 * that is all `GET /projects/{id}/conversations` returns. `overflow-hidden`
 * is the one addition to the prototype's frame: rows carry a hover background
 * (the whole row opens the conversation), and without the clip the last row's
 * background would square off the table's rounded corners.
 */
export function ConversationTable({
  conversations,
  projectId,
  editing,
  selectedIds,
  canDelete,
  downloadingId,
  deletingIds,
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
  deletingIds: ReadonlySet<string>;
  onToggleSelect: (conversation: ChatConversation) => void;
  onDownload: (conversation: ChatConversation) => void;
  onRename: (conversation: ChatConversation) => void;
  onDelete: (conversation: ChatConversation) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="hidden grid-cols-[1.5rem_1fr_8rem_8rem_6.5rem] gap-3 border-b bg-muted/40 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground sm:grid">
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
            deleting={deletingIds.has(conversation.id)}
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
