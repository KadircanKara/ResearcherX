"use client";
import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { useIdentity } from "@/lib/identity";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InitialsAvatar } from "@/components/initials-avatar";

/**
 * The dev identity switcher: who the app is acting as (`X-Dev-User-Id`).
 * Sharing is only exercisable because a second seeded teammate can be picked
 * here, so it must stay reachable -- the sidebar's user block is its trigger.
 *
 * `children` is the trigger's content and `className` its box; the menu opens
 * on `side` / `align` of it.
 */
export function UserMenu({
  children,
  className,
  side = "top",
  align = "start",
}: {
  children: React.ReactNode;
  className?: string;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
}) {
  const { me, users, actAs } = useIdentity();
  // Which user we're acting as (null = acting as self / first user)
  const [actingId, setActingId] = useState<string | null>(null);

  useEffect(() => {
    setActingId(localStorage.getItem("devUserId"));
  }, []);

  if (!me) return <div className={className}>{children}</div>;

  // The first user in the list is "You" (default). If devUserId isn't set, we act as the first user.
  const selfId = users[0]?.id ?? me.id;
  const currentActingId = actingId ?? selfId;

  function handleActAs(id: string) {
    setActingId(id);
    actAs(id);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Switch user"
        className={cn(
          "text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
          className
        )}
      >
        {children}
      </DropdownMenuTrigger>

      <DropdownMenuContent side={side} align={align} sideOffset={8} className="min-w-52">
        <div className="px-2 pt-1.5 text-sm font-semibold">{me.name}</div>
        <div className="px-2 pb-1.5 text-xs text-muted-foreground">{me.email}</div>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
            Acting as
          </DropdownMenuLabel>
          {users.map((u, i) => (
            <DropdownMenuItem key={u.id} onClick={() => handleActAs(u.id)}>
              <InitialsAvatar person={u} size={20} />
              <span className="flex-1">{i === 0 ? `You (${u.name})` : u.name}</span>
              {u.id === currentActingId && <Check className="text-primary" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
