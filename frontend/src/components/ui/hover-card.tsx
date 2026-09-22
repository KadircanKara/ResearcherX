"use client"

/**
 * HoverCard, with the prototype's class strings on Base UI's PreviewCard.
 *
 * Radix → Base UI:
 * - `<HoverCardTrigger asChild><button …/></HoverCardTrigger>` is
 *   `<HoverCardTrigger render={<button …/>} />`. The default element is an
 *   `<a>`; a trigger that is not a link should pass `render`.
 * - Radix puts `openDelay` / `closeDelay` on the root; Base UI puts `delay` /
 *   `closeDelay` on the trigger. The root here accepts Radix's names and hands
 *   them to its trigger, so `<HoverCard openDelay={100} closeDelay={80}>`
 *   ports unchanged. A trigger's own `delay` / `closeDelay` still wins.
 * - Base UI opens on hover with a mouse only; focus also opens it.
 */
import * as React from "react"
import { PreviewCard as PreviewCardPrimitive } from "@base-ui/react/preview-card"

import { cn } from "@/lib/utils"

type Delays = { delay?: number; closeDelay?: number }
const HoverCardDelays = React.createContext<Delays>({})

function HoverCard({
  openDelay,
  closeDelay,
  ...props
}: PreviewCardPrimitive.Root.Props & { openDelay?: number; closeDelay?: number }) {
  const delays = React.useMemo(() => ({ delay: openDelay, closeDelay }), [openDelay, closeDelay])
  return (
    <HoverCardDelays.Provider value={delays}>
      <PreviewCardPrimitive.Root data-slot="hover-card" {...props} />
    </HoverCardDelays.Provider>
  )
}

function HoverCardTrigger({ delay, closeDelay, ...props }: PreviewCardPrimitive.Trigger.Props) {
  const inherited = React.useContext(HoverCardDelays)
  return (
    <PreviewCardPrimitive.Trigger
      data-slot="hover-card-trigger"
      delay={delay ?? inherited.delay}
      closeDelay={closeDelay ?? inherited.closeDelay}
      {...props}
    />
  )
}

function HoverCardContent({
  className,
  side = "bottom",
  align = "center",
  sideOffset = 4,
  alignOffset = 0,
  ...props
}: PreviewCardPrimitive.Popup.Props &
  Pick<PreviewCardPrimitive.Positioner.Props, "align" | "alignOffset" | "side" | "sideOffset">) {
  return (
    <PreviewCardPrimitive.Portal>
      <PreviewCardPrimitive.Positioner
        className="isolate z-50"
        side={side}
        align={align}
        sideOffset={sideOffset}
        alignOffset={alignOffset}
      >
        <PreviewCardPrimitive.Popup
          data-slot="hover-card-content"
          className={cn(
            "z-50 w-64 origin-[var(--transform-origin)] rounded-md border bg-popover p-4 text-popover-foreground shadow-md outline-none data-[open]:animate-in data-[closed]:animate-out data-[closed]:fade-out-0 data-[open]:fade-in-0 data-[closed]:zoom-out-95 data-[open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2",
            className
          )}
          {...props}
        />
      </PreviewCardPrimitive.Positioner>
    </PreviewCardPrimitive.Portal>
  )
}

export { HoverCard, HoverCardTrigger, HoverCardContent }
