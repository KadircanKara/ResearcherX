"use client"

/**
 * Progress, with the prototype's class strings. Base UI's Progress root gives
 * the `progressbar` role and aria values; the bar itself is a plain div moved
 * with the prototype's translate rather than Base UI's width-based indicator,
 * so it animates the same way. `value` is 0-100; `null` is indeterminate.
 */
import * as React from "react"
import { Progress as ProgressPrimitive } from "@base-ui/react/progress"

import { cn } from "@/lib/utils"

function Progress({ className, value, ...props }: ProgressPrimitive.Root.Props) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={value}
      className={cn("relative h-2 w-full overflow-hidden rounded-full bg-primary/20", className)}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className="h-full w-full flex-1 bg-primary transition-all"
        style={{ transform: `translateX(-${100 - (value || 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
