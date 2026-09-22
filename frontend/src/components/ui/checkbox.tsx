"use client";

/**
 * Checkbox, with the prototype's class strings on Base UI's Checkbox.
 *
 * A primitive rather than a bare `<input type="checkbox">`: the native control
 * paints itself from the OS accent colour and ignores the theme entirely.
 *
 * Radix → Base UI: Radix's `checked="indeterminate"` is Base UI's separate
 * `indeterminate` boolean, and `onCheckedChange` receives a plain boolean.
 * Base UI marks state with `data-checked` / `data-indeterminate` rather than
 * `data-state`.
 */
import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";
import { Check, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer grid h-4 w-4 shrink-0 cursor-pointer place-content-center rounded-sm border border-primary shadow focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[checked]:bg-primary data-[checked]:text-primary-foreground data-[indeterminate]:bg-primary data-[indeterminate]:text-primary-foreground",
        className
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="grid place-content-center text-current">
        {props.indeterminate ? <Minus className="h-4 w-4" /> : <Check className="h-4 w-4" />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
