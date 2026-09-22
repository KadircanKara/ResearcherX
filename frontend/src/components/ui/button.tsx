/**
 * Button, with the prototype's (stock shadcn "new-york") class strings on a
 * Base UI button.
 *
 * Radix → Base UI: the prototype's `<Button asChild><Link …/></Button>` is
 * `<Button render={<Link …/>} />` here. A `render` element that is not a
 * `<button>` (a Link, an `<a>`, a `<span>`) gets the button's classes and
 * keeps its OWN semantics -- a link stays a link, with no `role="button"` --
 * because it is rendered through `useRender`, not Base UI's button behaviour.
 * To force Base UI's button behaviour onto a custom element, pass
 * `nativeButton={false}` explicitly.
 *
 * Sizes `default | sm | lg | icon` are the prototype's. `xs`, `icon-xs`,
 * `icon-sm` and `icon-lg` are this app's older sizes, kept so existing callers
 * compile; new code should use the prototype's four.
 */
import * as React from "react"
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 disabled:cursor-not-allowed [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline:
          "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary: "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-8",
        icon: "h-9 w-9",
        // Legacy sizes (see the header comment).
        xs: "h-6 gap-1 rounded-md px-2 text-xs [&_svg]:size-3",
        "icon-xs": "size-6 [&_svg]:size-3",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

type ButtonProps = ButtonPrimitive.Props & VariantProps<typeof buttonVariants>

function Button({ className, variant, size, render, nativeButton, ...props }: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size, className: className as string }))
  if (nativeButton === undefined && React.isValidElement(render) && render.type !== "button") {
    return <ElementButton render={render} className={classes} {...(props as ElementProps)} />
  }
  return (
    <ButtonPrimitive
      data-slot="button"
      className={classes}
      render={render}
      nativeButton={nativeButton ?? true}
      {...props}
    />
  )
}

type ElementProps = React.ComponentPropsWithRef<"a">

/** A Link / `<a>` / `<span>` dressed as a button, keeping its own semantics. */
function ElementButton({
  render,
  className,
  ref,
  ...props
}: ElementProps & { render: React.ReactElement; className: string }) {
  return useRender({
    render,
    ref,
    props: mergeProps<"a">({ className, "data-slot": "button" } as ElementProps, props),
  })
}

export { Button, buttonVariants, type ButtonProps }
