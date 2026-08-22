"use client";

import { useCallback, useEffect, useState } from "react";
import { Share2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { addGrant, errorText, listGrants, removeGrant, type LatexGrant } from "@/lib/latex";
import { listMembers } from "@/lib/projects";
import type { Member } from "@/lib/types";

interface DocumentShareDialogProps {
  projectId: string;
  documentId: string;
  /** Only a document editor may change grants; the server enforces it too. */
  canEdit: boolean;
  /** Rendered with no control: the server refuses grants naming these people. */
  fullAccessUserIds: readonly string[];
}

// "Can view" is the DEFAULT, not a restriction this dialog can impose: every
// project member reads every document in the project. Labelling it otherwise
// would promise something the model does not do.
const LEVELS = [
  { value: "viewer", label: "Can view (default)" },
  { value: "editor", label: "Can edit" },
] as const;

export function DocumentShareDialog({
  projectId,
  documentId,
  canEdit,
  fullAccessUserIds,
}: DocumentShareDialogProps) {
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [grants, setGrants] = useState<LatexGrant[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [m, g] = await Promise.all([
        listMembers(projectId),
        listGrants(projectId, documentId),
      ]);
      setMembers(m);
      setGrants(g);
    } catch (err) {
      setError(errorText(err));
    }
  }, [projectId, documentId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  function levelFor(userId: string): "editor" | "viewer" {
    return grants.find((g) => g.user.id === userId)?.role ?? "viewer";
  }

  async function setLevel(userId: string, level: "editor" | "viewer") {
    setBusyUserId(userId);
    setError(null);
    try {
      // "viewer" is the absence of a grant, not a stored row: the resolver
      // already falls back to viewer, so storing one would be a row with no
      // effect.
      if (level === "viewer") {
        await removeGrant(projectId, documentId, userId);
      } else {
        await addGrant(projectId, documentId, { user_id: userId, role: level });
      }
      await load();
    } catch (err) {
      // A 422 here names the real problem ("not a member of this project",
      // "already has full access") and is shown verbatim; a 5xx degrades to
      // the generic line inside `errorText`.
      setError(errorText(err));
    } finally {
      setBusyUserId(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" className="shrink-0 gap-1.5" />}>
        <Share2 className="size-4" />
        Share
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share this LaTeX project</DialogTitle>
          <DialogDescription>
            Everyone in the research project can read it. Give edit access to
            the people who should be able to change it.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          {members.map((member) => {
            const full = fullAccessUserIds.includes(member.user.id);
            return (
              <div
                key={member.user.id}
                className="flex items-center justify-between gap-3 rounded-md border border-input px-2.5 py-1.5"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">{member.user.name}</p>
                  <p className="truncate text-xs text-muted-foreground">{member.user.email}</p>
                </div>
                {full ? (
                  <span className="shrink-0 text-xs text-muted-foreground">Full access</span>
                ) : (
                  <select
                    value={levelFor(member.user.id)}
                    disabled={!canEdit || busyUserId === member.user.id}
                    aria-label={`Access for ${member.user.name}`}
                    onChange={(e) =>
                      void setLevel(member.user.id, e.target.value as "editor" | "viewer")
                    }
                    className="shrink-0 rounded-md border border-input bg-background px-1.5 py-0.5 text-xs"
                  >
                    {LEVELS.map((level) => (
                      <option key={level.value} value={level.value}>
                        {level.label}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            );
          })}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
      </DialogContent>
    </Dialog>
  );
}
