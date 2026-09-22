"use client";

import type { MouseEvent } from "react";
import Link from "next/link";
import { Download, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { activityDay, rowClickAction, startedDay } from "@/lib/conversations";
import { routes } from "@/lib/routes";
import type { ChatConversation } from "@/lib/types";

function RowActions({
  canDelete,
  downloading,
  deleting,
  onDownload,
  onRename,
  onDelete,
  className = "",
}: {
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
        size="icon"
        className="relative z-10 size-7"
        disabled={downloading}
        onClick={onDownload}
        aria-label="Download as Markdown"
      >
        <Download className="size-3.5" aria-hidden />
      </Button>
      {canDelete && (
        <Button
          variant="ghost"
          size="icon"
          className="relative z-10 size-7"
          onClick={onRename}
          aria-label="Rename conversation"
        >
          <Pencil className="size-3.5" aria-hidden />
        </Button>
      )}
      {canDelete && (
        <Button
          variant="ghost"
          size="icon"
          className="relative z-10 size-7 text-destructive hover:text-destructive"
          disabled={deleting}
          onClick={onDelete}
          aria-label="Delete conversation"
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
 * The whole row is the click target, but it is still ONE real link — the
 * title's, stretched over the row by an `after:absolute after:inset-0`
 * pseudo-element against the `relative` row. A click handler on a div would
 * lose what makes a conversation a page: cmd/ctrl-click and middle-click
 * opening a new tab, the URL in the status bar, and a single tab stop.
 * The checkbox and each tool button sit above the stretched link
 * (`relative z-10` on the control itself, not on its container, so the gaps
 * between them still open the row), and keep working without navigating;
 * nesting them inside the link instead would be invalid HTML.
 *
 * In edit mode a plain click selects the row instead of opening it — the
 * rule is `rowClickAction`, pure and tested in `lib/conversations.ts`.
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

  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    const action = rowClickAction({
      editing,
      button: event.button,
      metaKey: event.metaKey,
      ctrlKey: event.ctrlKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
    });
    if (action === "toggle") {
      event.preventDefault();
      onToggleSelect();
    }
  }

  const actions = {
    canDelete,
    downloading,
    deleting,
    onDownload,
    onRename,
    onDelete,
  };

  return (
    <div className="group relative cursor-pointer border-b transition-colors last:border-b-0 hover:bg-muted/50">
      {/* Desktop row */}
      <div className="hidden items-center gap-3 px-4 py-3 sm:grid sm:grid-cols-[1.5rem_1fr_8rem_8rem_6.5rem]">
        <div>
          {editing && (
            <Checkbox
              className="relative z-10"
              checked={selected}
              onCheckedChange={onToggleSelect}
              aria-label={`Select ${conversation.title}`}
            />
          )}
        </div>
        <Link
          href={href}
          onClick={handleClick}
          className="truncate rounded-sm text-[14px] font-medium outline-none after:absolute after:inset-0 focus-visible:ring-2 focus-visible:ring-ring"
        >
          {conversation.title}
        </Link>
        <div className="text-[13px] text-muted-foreground">
          {startedDay(conversation.created_at)}
        </div>
        <div className="text-[13px] text-muted-foreground">
          {activityDay(conversation.updated_at)}
        </div>
        <RowActions
          {...actions}
          className="flex justify-end gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100"
        />
      </div>

      {/* Mobile stacked row */}
      <div className="flex flex-col gap-2 px-4 py-3 sm:hidden">
        <div className="flex items-start gap-2">
          {editing && (
            <Checkbox
              className="relative z-10 mt-1"
              checked={selected}
              onCheckedChange={onToggleSelect}
              aria-label={`Select ${conversation.title}`}
            />
          )}
          <Link
            href={href}
            onClick={handleClick}
            className="min-w-0 flex-1 truncate text-[14px] font-medium outline-none after:absolute after:inset-0 focus-visible:ring-2 focus-visible:ring-ring"
          >
            {conversation.title}
          </Link>
        </div>
        <p className="text-[12px] text-muted-foreground">
          Started {startedDay(conversation.created_at)} · Last activity{" "}
          {activityDay(conversation.updated_at)}
        </p>
        <RowActions {...actions} className="flex gap-1" />
      </div>
    </div>
  );
}
