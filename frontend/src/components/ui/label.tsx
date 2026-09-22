import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * A plain `<label>` with the prototype's classes. Radix's Label only adds
 * double-click text-selection prevention; Base UI's labels belong to `Field`,
 * which nothing here uses, so the element itself is the primitive. Associate
 * it with `htmlFor`, exactly as the prototype does.
 */
function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn(
        "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
        className
      )}
      {...props}
    />
  )
}

export { Label }
