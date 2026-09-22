"use client";

import Link from "next/link";
import { Download, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { activityLabel, startedDay } from "@/lib/conversations";
import { routes } from "@/lib/routes";
import type { ChatConversation } from "@/lib/types";

function RowActions({
  conversation,
  canDelete,
  downloading,
  deleting,
  onDownload,
  onRename,
  onDelete,
  className,
}: {
  conversation: ChatConversation;
  canDelete: boolean;
  downloading: boolean;
  deleting: boolean;
  onDownload: () => void;
  onRename: () => void;
  onDelete: () => void;
  className?: string;
}) {
  return (
    <div className={className}>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={downloading}
        onClick={onDownload}
        aria-label={`Download conversation as Markdown: ${conversation.title}`}
        title="Download as .md"
      >
        <Download className="size-3.5" aria-hidden />
      </Button>
      {canDelete && (
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onRename}
          aria-label={`Rename conversation: ${conversation.title}`}
          title="Rename"
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      )}
      {canDelete && (
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={deleting}
          onClick={onDelete}
          aria-label={`Delete conversation: ${conversation.title}`}
          title="Delete"
          className="text-destructive hover:text-destructive"
        >
          <Trash2 className="size-3.5" aria-hidden />
        </Button>
      )}
    </div>
  );
}

/**
 * One conversation.
 *
 * The title is a real link, not a click handler on the row: a conversation
 * is a page, and a link is what lets it be opened in a new tab. The tools
 * beside it are buttons, which is why the row is not itself a button —
 * nesting one inside another is invalid HTML.
 */
export function ConversationRow({
  conversation,
  projectId,
  editing,
  selected,
  canDelete,
  downloading,
  deleting,
  onToggleSelect,
  onDownload,
  onRename,
  onDelete,
}: {
  conversation: ChatConversation;
  projectId: string;
  editing: boolean;
  selected: boolean;
  canDelete: boolean;
  downloading: boolean;
  deleting: boolean;
  onToggleSelect: () => void;
  onDownload: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const href = routes.conversation(projectId, conversation.id);
  const titleClass =
    "truncate rounded-sm text-[14px] font-medium outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <div className="group border-b last:border-b-0">
      {/* Desktop */}
      <div className="hidden items-center gap-3 px-4 py-3 sm:grid sm:grid-cols-[1.5rem_minmax(0,1fr)_8rem_8rem_6.5rem]">
        <div>
          {editing && (
            <Checkbox
              checked={selected}
              onCheckedChange={onToggleSelect}
              aria-label={`Select ${conversation.title}`}
            />
          )}
        </div>
        <Link href={href} className={titleClass}>
          {conversation.title}
        </Link>
        <div className="text-[13px] text-muted-foreground">
          {startedDay(conversation.created_at)}
        </div>
        <div className="text-[13px] text-muted-foreground">
          {activityLabel(conversation.updated_at)}
        </div>
        <RowActions
          conversation={conversation}
          canDelete={canDelete}
          downloading={downloading}
          deleting={deleting}
          onDownload={onDownload}
          onRename={onRename}
          onDelete={onDelete}
          className="flex justify-end gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
        />
      </div>

      {/* Mobile: the same row, stacked, with the tools always visible —
          there is no hover on a touch screen. */}
      <div className="flex flex-col gap-2 px-4 py-3 sm:hidden">
        <div className="flex items-start gap-2">
          {editing && (
            <Checkbox
              className="mt-1"
              checked={selected}
              onCheckedChange={onToggleSelect}
              aria-label={`Select ${conversation.title}`}
            />
          )}
          <Link href={href} className={`min-w-0 flex-1 ${titleClass}`}>
            {conversation.title}
          </Link>
        </div>
        <p className="text-[12px] text-muted-foreground">
          Started {startedDay(conversation.created_at)} · Last activity{" "}
          {activityLabel(conversation.updated_at)}
        </p>
        <RowActions
          conversation={conversation}
          canDelete={canDelete}
          downloading={downloading}
          deleting={deleting}
          onDownload={onDownload}
          onRename={onRename}
          onDelete={onDelete}
          className="flex gap-1"
        />
      </div>
    </div>
  );
}
