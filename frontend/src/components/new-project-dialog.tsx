"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { PlusIcon } from "lucide-react"
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
import { Input } from "@/components/ui/input"
import { createProject } from "@/lib/projects"

export function NewProjectDialog() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [keywords, setKeywords] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setTitle("")
    setDescription("")
    setKeywords("")
    setError(null)
    setSubmitting(false)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const topicKeywords = keywords
        .split(",")
        .map((k) => k.trim())
        .filter(Boolean)
      const project = await createProject({
        title: title.trim(),
        description: description.trim() || null,
        topic_keywords: topicKeywords,
      })
      setOpen(false)
      reset()
      router.push(`/research/${project.id}`)
    } catch {
      setError("Failed to create project. Please try again.")
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen)
        if (!isOpen) reset()
      }}
    >
      <DialogTrigger
        render={<Button className="shrink-0 gap-1.5" />}
      >
        <PlusIcon />
        New project
      </DialogTrigger>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            Create a workspace to organise your research.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="proj-title">
              Title <span className="text-destructive">*</span>
            </label>
            <Input
              id="proj-title"
              placeholder="e.g. Climate policy 2024"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="proj-description">
              Description
            </label>
            <Input
              id="proj-description"
              placeholder="Optional short description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium" htmlFor="proj-keywords">
              Topic keywords
            </label>
            <Input
              id="proj-keywords"
              placeholder="carbon, energy, policy (comma-separated)"
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          <DialogFooter>
            <Button
              type="submit"
              disabled={!title.trim() || submitting}
            >
              {submitting ? "Creating…" : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
