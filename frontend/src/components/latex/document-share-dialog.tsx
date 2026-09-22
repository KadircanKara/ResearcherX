"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { addGrant, errorText, listGrants, removeGrant, type LatexGrant } from "@/lib/latex";
import { listMembers } from "@/lib/projects";
import type { Member } from "@/lib/types";

type Level = "editor" | "viewer";

interface DocumentShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  documentId: string;
  /** Only a document editor may change grants; the server enforces it too. */
  canEdit: boolean;
  /**
   * People the server refuses a grant for -- the project owner and the
   * document's creator already resolve to editor ahead of the grant table.
   * They are listed with their access fixed at "Can edit".
   */
  fullAccessUserIds: readonly string[];
}

// "Can view" is the DEFAULT, not a restriction this dialog can impose: every
// project member reads every document in the project. So "viewer" is the
// absence of a grant, never a stored row.
const LEVEL_LABELS: Record<Level, string> = { viewer: "Can view", editor: "Can edit" };

/**
 * Per-document access, ported from the prototype's `LatexShareDialog`: one
 * row per project member, a draft the user edits, and one Save that applies
 * only what changed.
 */
export function DocumentShareDialog({
  open,
  onOpenChange,
  projectId,
  documentId,
  canEdit,
  fullAccessUserIds,
}: DocumentShareDialogProps) {
  const [members, setMembers] = useState<Member[]>([]);
  const [grants, setGrants] = useState<LatexGrant[]>([]);
  const [draft, setDraft] = useState<Record<string, Level>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const levelFor = useCallback(
    (userId: string, from: LatexGrant[]): Level =>
      from.find((g) => g.user.id === userId)?.role ?? "viewer",
    []
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const [m, g] = await Promise.all([
        listMembers(projectId),
        listGrants(projectId, documentId),
      ]);
      setMembers(m);
      setGrants(g);
      setDraft(Object.fromEntries(m.map((member) => [member.user.id, levelFor(member.user.id, g)])));
    } catch (err) {
      setError(errorText(err));
    }
  }, [projectId, documentId, levelFor]);

  // Re-read every time the dialog opens, so the draft starts from the
  // server's current grants rather than whatever an earlier visit left.
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      // One request per CHANGED row, in order. "viewer" removes the grant
      // (the resolver already falls back to viewer, so storing one would be
      // a row with no effect); "editor" adds one.
      for (const member of members) {
        const id = member.user.id;
        if (fullAccessUserIds.includes(id)) continue;
        const next = draft[id] ?? "viewer";
        if (next === levelFor(id, grants)) continue;
        if (next === "viewer") await removeGrant(projectId, documentId, id);
        else await addGrant(projectId, documentId, { user_id: id, role: next });
      }
      onOpenChange(false);
    } catch (err) {
      // A 422 here names the real problem ("not a member of this project",
      // "already has full access") and is shown verbatim; a 5xx degrades to
      // the generic line inside `errorText`. The dialog stays open on the
      // server's current state, so a partly applied save is visible as such.
      setError(errorText(err));
      await load();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Share</DialogTitle>
          <DialogDescription>Set who can edit this document.</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {members.map((member) => {
            const id = member.user.id;
            const full = fullAccessUserIds.includes(id);
            return (
              <div key={id} className="flex items-center justify-between gap-2 text-[13px]">
                <span className="min-w-0 truncate" title={member.user.email}>
                  {member.user.name}
                </span>
                <Select
                  items={LEVEL_LABELS}
                  value={full ? "editor" : (draft[id] ?? "viewer")}
                  disabled={full || !canEdit || saving}
                  onValueChange={(v) => {
                    if (v === "editor" || v === "viewer") setDraft((prev) => ({ ...prev, [id]: v }));
                  }}
                >
                  <SelectTrigger
                    className="h-8 w-28 shrink-0 text-[12px]"
                    aria-label={`Access for ${member.user.name}`}
                    title={full ? "Always has edit access" : undefined}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="viewer">Can view</SelectItem>
                    <SelectItem value="editor">Can edit</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </div>
        {error && <p className="text-[12px] text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canEdit || saving} onClick={() => void save()}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
