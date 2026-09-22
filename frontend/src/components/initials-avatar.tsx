import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Who a person is, as the prototype draws it: a coloured circle with their
 * initials. Takes the app's `User` shape directly (`name`, `avatar_color`);
 * a person with no colour gets the neutral slate.
 */
export interface AvatarPerson {
  id?: string;
  name: string;
  avatar_color?: string | null;
}

export function InitialsAvatar({
  person,
  size = 24,
  className,
}: {
  person: AvatarPerson;
  size?: number;
  className?: string;
}) {
  return (
    <span
      title={person.name}
      className={cn(
        "grid shrink-0 place-items-center rounded-full font-semibold text-white",
        className,
      )}
      style={{
        backgroundColor: person.avatar_color || "#64748B",
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
      }}
    >
      {initials(person.name)}
    </span>
  );
}

/** Up to `max` overlapping avatars, then a "+N" tile for the rest. */
export function AvatarStack({ people, max = 4 }: { people: AvatarPerson[]; max?: number }) {
  const shown = people.slice(0, max);
  return (
    <div className="flex -space-x-1.5">
      {shown.map((p, i) => (
        <InitialsAvatar key={p.id ?? i} person={p} className="ring-2 ring-background" />
      ))}
      {people.length > max && (
        <span className="grid size-6 place-items-center rounded-full bg-muted text-[10px] font-semibold text-muted-foreground ring-2 ring-background">
          +{people.length - max}
        </span>
      )}
    </div>
  );
}
