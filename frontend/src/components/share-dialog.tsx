"use client"

import { useState, useEffect, useCallback } from "react"
import { XIcon, Share2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { useIdentity } from "@/lib/identity"
import {
  listMembers,
  addMember,
  removeMember,
} from "@/lib/projects"
import type { Member, Role, Project } from "@/lib/types"

function initials(name: string) {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .toUpperCase()
    .slice(0, 2)
}

interface ShareDialogProps {
  project: Project
  initialMembers: Member[]
}

export function ShareDialog({ project, initialMembers }: ShareDialogProps) {
  const { me, users } = useIdentity()
  const [open, setOpen] = useState(false)
  const [members, setMembers] = useState<Member[]>(initialMembers)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Add collaborator form state. Sharing is binary now (owner/member) --
  // there is no role to pick, so Task 10 owns whatever replaces this row.
  const [addUserId, setAddUserId] = useState("")
  const [adding, setAdding] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const fresh = await listMembers(project.id)
      setMembers(fresh)
    } catch {
      // silently keep stale data
    } finally {
      setLoading(false)
    }
  }, [project.id])

  // Re-fetch on open
  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  // Determine acting user's role in this project
  const myRole: Role | null = me
    ? (members.find((m) => m.user.id === me.id)?.role ?? null)
    : null
  const isOwner = myRole === "owner"

  // Count owners for last-owner protection
  const ownerCount = members.filter((m) => m.role === "owner").length

  // Users not yet members (for picker)
  const memberIds = new Set(members.map((m) => m.user.id))
  const nonMembers = users.filter((u) => !memberIds.has(u.id))

  // Reset add-form when members change
  useEffect(() => {
    if (nonMembers.length > 0 && !nonMembers.find((u) => u.id === addUserId)) {
      setAddUserId(nonMembers[0]?.id ?? "")
    }
    if (nonMembers.length === 0) setAddUserId("")
  }, [members]) // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRemove(userId: string) {
    setError(null)
    try {
      await removeMember(project.id, userId)
      await refresh()
    } catch {
      setError("Failed to remove member.")
    }
  }

  async function handleAdd() {
    if (!addUserId) return
    setAdding(true)
    setError(null)
    try {
      await addMember(project.id, { user_id: addUserId, role: "member" })
      await refresh()
    } catch {
      setError("Failed to add collaborator.")
    } finally {
      setAdding(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Share2 className="size-3.5" />
        Share
      </DialogTrigger>

      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share &ldquo;{project.title}&rdquo;</DialogTitle>
          <DialogDescription>
            Manage collaborators and their access to chats, papers and LaTeX docs.
          </DialogDescription>
        </DialogHeader>

        {/* Project sharing is binary. Finer access lives on individual LaTeX
            projects, in each document's own share dialog. */}
        {/* Members list */}
        <div className="flex flex-col gap-1">
          {loading && members.length === 0 && (
            <p className="py-2 text-sm text-muted-foreground">Loading…</p>
          )}
          {members.map((member) => {
            const isThisOwner = member.role === "owner"
            const canRemove =
              isOwner && !isThisOwner && !(isThisOwner && ownerCount <= 1)

            return (
              <div
                key={member.user.id}
                className="flex items-center gap-3 rounded-lg py-1.5"
              >
                {/* Avatar + presence dot (decorative) */}
                <span className="relative shrink-0">
                  <Avatar size="sm">
                    <AvatarFallback
                      style={{ backgroundColor: member.user.avatar_color }}
                      className="text-white"
                    >
                      {initials(member.user.name)}
                    </AvatarFallback>
                  </Avatar>
                  <span className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-card bg-emerald-500" />
                </span>

                {/* Name + email */}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium leading-tight">
                    {member.user.name}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {member.user.email}
                  </p>
                </div>

                {/* Role display -- binary now, so there is nothing to pick. */}
                <span className="shrink-0 text-xs text-muted-foreground">
                  {isThisOwner ? "Owner" : "Member"}
                </span>

                {/* Remove button — owners only, not for owners */}
                {canRemove ? (
                  <button
                    type="button"
                    aria-label={`Remove ${member.user.name}`}
                    onClick={() => handleRemove(member.user.id)}
                    className="ml-1 shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:text-destructive focus:outline-none focus:ring-2 focus:ring-ring/50"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                ) : (
                  /* spacer to keep layout stable */
                  <span className="ml-1 size-5 shrink-0" />
                )}
              </div>
            )
          })}
        </div>

        {/* Add collaborator row — owner only, only if non-members exist */}
        {isOwner && nonMembers.length > 0 && (
          <div className="flex items-center gap-2 border-t border-border pt-3">
            <label className="sr-only" htmlFor="pick-user">
              Add collaborator
            </label>
            <select
              id="pick-user"
              value={addUserId}
              onChange={(e) => setAddUserId(e.target.value)}
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring/50"
            >
              {nonMembers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.email})
                </option>
              ))}
            </select>

            <Button
              size="sm"
              disabled={!addUserId || adding}
              onClick={handleAdd}
            >
              {adding ? "Adding…" : "Add"}
            </Button>
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  )
}
