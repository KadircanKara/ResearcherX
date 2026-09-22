"use client"

/**
 * Resizable panels, with the prototype's class strings, hand-rolled instead
 * of the prototype's `react-resizable-panels` dependency: Base UI has no
 * splitter, and the prototype needs only a two-pane drag.
 *
 * API (a subset of react-resizable-panels):
 * - `ResizablePanelGroup orientation="horizontal" | "vertical"` (`direction`
 *   is accepted as an alias).
 * - `ResizablePanel defaultSize minSize maxSize`, all PERCENT of the group.
 * - `ResizableHandle withHandle` between two panels.
 * Panels and handles must be DIRECT children of the group; that is how each
 * handle knows which two panels it sits between. Sizes are not persisted.
 *
 * The clamping arithmetic lives in `lib/resizable.ts`, which is tested.
 */
import * as React from "react"
import { GripVertical } from "lucide-react"

import { cn } from "@/lib/utils"
import { initialSizes, resizeAt, type PanelConstraint } from "@/lib/resizable"

type Orientation = "horizontal" | "vertical"

/** How far one arrow-key press moves a handle, in percent. */
const KEY_STEP = 5

interface GroupState {
  orientation: Orientation
  sizes: number[]
  constraints: PanelConstraint[]
  groupRef: React.RefObject<HTMLDivElement | null>
  setSizes: (sizes: number[]) => void
  setDragging: (dragging: boolean) => void
}

const GroupContext = React.createContext<GroupState | null>(null)
/** A panel's own index, or for a handle the index of the panel before it. */
const SlotIndex = React.createContext(0)

function useGroup(part: string): GroupState {
  const group = React.useContext(GroupContext)
  if (!group) throw new Error(`${part} must be a direct child of ResizablePanelGroup`)
  return group
}

type PanelProps = React.ComponentProps<"div"> & {
  defaultSize?: number
  minSize?: number
  maxSize?: number
}

function ResizablePanel({ className, style, ...props }: PanelProps) {
  // The sizing props are read by the group from this element; they must not
  // reach the DOM as unknown attributes.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { defaultSize, minSize, maxSize, ...domProps } = props
  const group = useGroup("ResizablePanel")
  const index = React.useContext(SlotIndex)
  const size = group.sizes[index] ?? 0
  return (
    <div
      data-slot="resizable-panel"
      className={cn("min-h-0 min-w-0 overflow-hidden", className)}
      style={{ flexGrow: size, flexShrink: 1, flexBasis: 0, ...style }}
      {...domProps}
    />
  )
}

function ResizablePanelGroup({
  orientation,
  direction,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { orientation?: Orientation; direction?: Orientation }) {
  const resolved: Orientation = orientation ?? direction ?? "horizontal"
  const groupRef = React.useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = React.useState(false)

  const items = React.Children.toArray(children)
  const panels = items.filter(
    (child): child is React.ReactElement<PanelProps> =>
      React.isValidElement(child) && child.type === ResizablePanel
  )
  const constraints = panels.map((p) => ({ min: p.props.minSize ?? 0, max: p.props.maxSize ?? 100 }))
  const defaultsKey = panels.map((p) => p.props.defaultSize ?? "").join(",")
  const [sizes, setSizes] = React.useState(() =>
    initialSizes(panels.map((p) => p.props.defaultSize))
  )
  // Re-seed only when the panel set itself changes (a panel added or
  // removed), never on an ordinary re-render, which would undo a drag.
  const [seededFor, setSeededFor] = React.useState(defaultsKey)
  if (seededFor !== defaultsKey || sizes.length !== panels.length) {
    setSeededFor(defaultsKey)
    setSizes(initialSizes(panels.map((p) => p.props.defaultSize)))
  }

  let panelIndex = -1
  const slotted = items.map((child, i) => {
    if (React.isValidElement(child) && child.type === ResizablePanel) panelIndex += 1
    return (
      <SlotIndex.Provider key={i} value={Math.max(0, panelIndex)}>
        {child}
      </SlotIndex.Provider>
    )
  })

  return (
    <GroupContext.Provider
      value={{ orientation: resolved, sizes, constraints, groupRef, setSizes, setDragging }}
    >
      <div
        ref={groupRef}
        data-slot="resizable-panel-group"
        data-panel-group-direction={resolved}
        className={cn(
          "flex h-full w-full data-[panel-group-direction=vertical]:flex-col",
          dragging && "select-none",
          className
        )}
        {...props}
      >
        {slotted}
      </div>
    </GroupContext.Provider>
  )
}

function ResizableHandle({
  withHandle,
  className,
  ...props
}: React.ComponentProps<"div"> & { withHandle?: boolean }) {
  const group = useGroup("ResizableHandle")
  const index = React.useContext(SlotIndex)
  const horizontal = group.orientation === "horizontal"
  const drag = React.useRef<{ start: number; sizes: number[] } | null>(null)

  function move(delta: number, from: number[]) {
    group.setSizes(resizeAt(from, index, delta, group.constraints))
  }

  return (
    <div
      role="separator"
      tabIndex={0}
      aria-orientation={horizontal ? "vertical" : "horizontal"}
      aria-valuenow={Math.round(group.sizes[index] ?? 0)}
      aria-valuemin={group.constraints[index]?.min ?? 0}
      aria-valuemax={group.constraints[index]?.max ?? 100}
      data-slot="resizable-handle"
      data-panel-group-direction={group.orientation}
      style={{ cursor: horizontal ? "col-resize" : "row-resize", touchAction: "none" }}
      className={cn(
        "relative flex w-px items-center justify-center bg-border after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-offset-1 data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full data-[panel-group-direction=vertical]:after:left-0 data-[panel-group-direction=vertical]:after:h-1 data-[panel-group-direction=vertical]:after:w-full data-[panel-group-direction=vertical]:after:-translate-y-1/2 data-[panel-group-direction=vertical]:after:translate-x-0 [&[data-panel-group-direction=vertical]>div]:rotate-90",
        className
      )}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        e.currentTarget.setPointerCapture(e.pointerId)
        drag.current = { start: horizontal ? e.clientX : e.clientY, sizes: group.sizes }
        group.setDragging(true)
      }}
      onPointerMove={(e) => {
        const from = drag.current
        const rect = group.groupRef.current?.getBoundingClientRect()
        if (!from || !rect) return
        const length = horizontal ? rect.width : rect.height
        if (length <= 0) return
        const pos = horizontal ? e.clientX : e.clientY
        move(((pos - from.start) / length) * 100, from.sizes)
      }}
      onPointerUp={() => {
        drag.current = null
        group.setDragging(false)
      }}
      onPointerCancel={() => {
        drag.current = null
        group.setDragging(false)
      }}
      onKeyDown={(e) => {
        const back = horizontal ? "ArrowLeft" : "ArrowUp"
        const forward = horizontal ? "ArrowRight" : "ArrowDown"
        if (e.key === back) {
          e.preventDefault()
          move(-KEY_STEP, group.sizes)
        } else if (e.key === forward) {
          e.preventDefault()
          move(KEY_STEP, group.sizes)
        }
      }}
      {...props}
    >
      {withHandle && (
        <div className="z-10 flex h-4 w-3 items-center justify-center rounded-sm border bg-border">
          <GripVertical className="h-2.5 w-2.5" />
        </div>
      )}
    </div>
  )
}

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
