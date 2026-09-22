"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { InitialsAvatar } from "@/components/initials-avatar"
import { useIdentity } from "@/lib/identity"
import { addMember, listMembers, removeMember } from "@/lib/projects"
import type { Member, Project } from "@/lib/types"

/**
 * The prototype's "Share project" dialog on the real membership API.
 *
 * Project sharing is binary (owner / member); finer access lives on each
 * LaTeX document's own share dialog. Only an owner can add or remove, so the
 * Remove buttons and the add row render for owners only -- for anyone else
 * the server would refuse both. The member list is re-read on every open and
 * after every change, and reported through `onMembersChange` so the header's
 * member count follows it.
 */
export function ShareProjectDialog({
  open,
  onOpenChange,
  project,
  members: initialMembers,
  onMembersChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  project: Project
  members: Member[]
  onMembersChange?: (members: Member[]) => void
}) {
  const { me, users } = useIdentity()
  const [members, setMembers] = useState<Member[]>(initialMembers)
  const [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Read through a ref: a caller's inline callback is a new function every
  // render, and `refresh` depending on it would re-run the open effect after
  // every report -- a fetch loop.
  const report = useRef(onMembersChange)
  report.current = onMembersChange

  const refresh = useCallback(async () => {
    try {
      const fresh = await listMembers(project.id)
      setMembers(fresh)
      report.current?.(fresh)
    } catch {
      // Keep the list we have; a failed re-read is not worth an error line.
    }
  }, [project.id])

  useEffect(() => {
    if (open) {
      setError(null)
      setPending(null)
      void refresh()
    }
  }, [open, refresh])

  const isOwner = !!me && members.some((m) => m.user.id === me.id && m.role === "owner")
  const candidates = users.filter((u) => !members.some((m) => m.user.id === u.id))

  async function remove(userId: string) {
    setBusy(true)
    setError(null)
    try {
      await removeMember(project.id, userId)
      await refresh()
    } catch {
      setError("Failed to remove member.")
    } finally {
      setBusy(false)
    }
  }

  async function add() {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      await addMember(project.id, { user_id: pending, role: "member" })
      setPending(null)
      await refresh()
    } catch {
      setError("Failed to add member.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share project</DialogTitle>
          <DialogDescription>
            Everyone here can open every conversation, paper and LaTeX project in{" "}
            {project.title}.
          </DialogDescription>
        </DialogHeader>

        <ul className="divide-y rounded-md border">
          {members.map((member) => (
            <li key={member.user.id} className="flex items-center gap-3 px-3 py-2.5">
              <InitialsAvatar person={member.user} size={28} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium">{member.user.name}</p>
                <p className="truncate text-[12px] text-muted-foreground">{member.user.email}</p>
              </div>
              <span className="text-[12px] capitalize text-muted-foreground">{member.role}</span>
              {isOwner && member.role !== "owner" && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void remove(member.user.id)}
                >
                  Remove
                </Button>
              )}
            </li>
          ))}
        </ul>

        {isOwner && (
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1.5">
              <p className="text-[12px] font-medium">Add a member</p>
              <Select
                value={pending}
                onValueChange={(value) => setPending(value as string | null)}
                disabled={candidates.length === 0}
                items={candidates.map((p) => ({ value: p.id, label: p.name }))}
              >
                <SelectTrigger aria-label="Choose a person to add">
                  <SelectValue
                    placeholder={candidates.length ? "Choose a person" : "Everyone is already here"}
                  />
                </SelectTrigger>
                <SelectContent>
                  {candidates.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button disabled={!pending || busy} onClick={() => void add()}>
              Add
            </Button>
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}
      </DialogContent>
    </Dialog>
  )
}
