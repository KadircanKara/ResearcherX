"use client";
import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { useIdentity } from "@/lib/identity";
import {
  Avatar,
  AvatarFallback,
} from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

function initials(name: string): string {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function UserMenu() {
  const { me, users, actAs } = useIdentity();
  // Track which user we're currently acting as (null = acting as self / first user)
  const [actingId, setActingId] = useState<string | null>(null);

  useEffect(() => {
    setActingId(localStorage.getItem("devUserId"));
  }, []);

  if (!me) {
    return (
      <Avatar size="sm">
        <AvatarFallback>?</AvatarFallback>
      </Avatar>
    );
  }

  // The first user in the list is "You" (default). If devUserId isn't set, we act as the first user.
  const firstUser = users[0];
  const selfId = firstUser?.id ?? me.id;
  const currentActingId = actingId ?? selfId;

  function handleActAs(id: string) {
    setActingId(id);
    actAs(id);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="User menu"
        className="rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Avatar size="sm">
          <AvatarFallback
            className="text-xs font-semibold"
            style={{ backgroundColor: me.avatar_color, color: "#fff" }}
          >
            {initials(me.name)}
          </AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" side="bottom" sideOffset={8} className="min-w-48">
        <div className="px-2 pt-1.5 pb-0.5 text-sm font-semibold text-foreground">
          {me.name}
        </div>
        <div className="px-2 pb-1.5 text-xs text-muted-foreground">{me.email}</div>

        <DropdownMenuSeparator />

        <DropdownMenuLabel>Acting as</DropdownMenuLabel>

        <DropdownMenuGroup>
          {users.map((u, i) => {
            const label = i === 0 ? `You (${u.name})` : u.name;
            const isCurrent = u.id === currentActingId;
            return (
              <DropdownMenuItem
                key={u.id}
                onClick={() => handleActAs(u.id)}
                className="gap-2"
              >
                <Avatar size="sm">
                  <AvatarFallback
                    className="text-xs"
                    style={{ backgroundColor: u.avatar_color, color: "#fff" }}
                  >
                    {initials(u.name)}
                  </AvatarFallback>
                </Avatar>
                <span className="flex-1 text-sm">{label}</span>
                {isCurrent && <Check className="size-3.5 text-primary" />}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
