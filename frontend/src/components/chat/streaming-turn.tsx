"use client";

import { StreamingAnswer } from "@/components/chat/assistant-answer";
import { ScopeBanner } from "@/components/chat/scope-banner";
import { StatusLine } from "@/components/chat/status-line";
import type { ChatStatus, ScopeSegment } from "@/lib/chat-scope";

/**
 * The answer in flight, laid out as the app prototype's streaming turn:
 *
 *   thinking    the status line
 *   retrieving  the status line, with the scope line under it
 *   streaming   the answer as it arrives, with the scope line — and the
 *               note about named papers that returned nothing — under it
 *
 * The phases are the backend's own SSE events, never a timer.
 */
export function StreamingTurn({
  status,
  label,
  scope,
  note,
  text,
  projectId,
}: {
  status: ChatStatus;
  label: string | null;
  scope: ScopeSegment[] | null;
  note: string | null;
  text: string;
  projectId: string;
}) {
  const writing = status === "streaming";
  return (
    <div className="max-w-[85%] space-y-3">
      {!writing && label ? <StatusLine label={label} /> : null}
      {status === "retrieving" && scope ? <ScopeBanner segments={scope} /> : null}
      {writing ? <StreamingAnswer text={text} projectId={projectId} /> : null}
      {writing && scope ? <ScopeBanner segments={scope} note={note} /> : null}
    </div>
  );
}
