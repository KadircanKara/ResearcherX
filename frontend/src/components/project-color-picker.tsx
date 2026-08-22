"use client";

import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { colorFor, PROJECT_COLORS, type ProjectColor } from "@/lib/project-colors";
import { updateProject } from "@/lib/projects";

interface ProjectColorPickerProps {
  projectId: string;
  color: string;
  /** Owners and editors only; a viewer sees the swatch but cannot change it. */
  canEdit: boolean;
}

export function ProjectColorPicker({ projectId, color, canEdit }: ProjectColorPickerProps) {
  const [open, setOpen] = useState(false);
  // Applied locally the moment it is picked, so the swatch and the sidebar dot
  // do not wait on a round trip. Reverted if the PATCH fails -- silently
  // keeping a colour the server rejected would be a lie the next reload
  // undoes.
  const [current, setCurrent] = useState(() => colorFor({ id: projectId, color }));
  const popover = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setCurrent(colorFor({ id: projectId, color }));
  }, [projectId, color]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!popover.current?.contains(e.target as Node)) setOpen(false);
    }
    // Captured on the document rather than an overlay: this popover is small
    // and an overlay would swallow the first click on anything else in the
    // header.
    window.document.addEventListener("pointerdown", onPointerDown);
    return () => window.document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function pick(next: ProjectColor) {
    const previous = current;
    setCurrent(next);
    setOpen(false);
    updateProject(projectId, { color: next }).catch(() => setCurrent(previous));
  }

  return (
    <div ref={popover} className="relative">
      <button
        type="button"
        disabled={!canEdit}
        onClick={() => setOpen((prev) => !prev)}
        aria-label={canEdit ? "Change project colour" : "Project colour"}
        aria-expanded={open}
        title={canEdit ? "Change project colour" : undefined}
        className={cn(
          "size-4 rounded-full ring-offset-2 ring-offset-background transition-shadow",
          canEdit && "hover:ring-2 hover:ring-ring"
        )}
        style={{ backgroundColor: current }}
      />

      {open && (
        <div className="absolute left-0 z-30 mt-2 grid w-max grid-cols-5 gap-1.5 rounded-lg border border-border bg-popover p-2 shadow-md">
          {PROJECT_COLORS.map((swatch) => (
            <button
              key={swatch}
              type="button"
              onClick={() => pick(swatch)}
              aria-label={swatch}
              className="grid size-5 place-items-center rounded-full"
              style={{ backgroundColor: swatch }}
            >
              {swatch === current && <Check className="size-3 text-white" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
