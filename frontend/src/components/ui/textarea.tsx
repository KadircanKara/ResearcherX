import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * A plain `<textarea>` rather than a Base UI primitive: Base UI ships an
 * `Input` for single-line fields and nothing for multi-line, so the shared part
 * here is the class list, kept in step with `input.tsx` by hand.
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30 dark:aria-invalid:border-destructive/50",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
