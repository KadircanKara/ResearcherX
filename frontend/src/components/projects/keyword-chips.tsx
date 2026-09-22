import { Badge } from "@/components/ui/badge";

/**
 * Up to `max` keyword chips plus a "+N" chip for the rest.
 *
 * Keyed by position as well as text: keywords are typed by hand and the
 * server does not deduplicate them, so the same word can appear twice.
 */
export function KeywordChips({ keywords, max = 3 }: { keywords: string[]; max?: number }) {
  if (keywords.length === 0) return null;
  const shown = keywords.slice(0, max);
  const rest = keywords.length - shown.length;
  return (
    <ul className="flex flex-wrap items-center gap-1">
      {shown.map((keyword, i) => (
        <li key={`${i}-${keyword}`}>
          <Badge variant="secondary" className="rounded-md text-[11px] font-normal">
            {keyword}
          </Badge>
        </li>
      ))}
      {rest > 0 && (
        <li>
          <Badge variant="outline" className="rounded-md text-[11px] font-normal">
            +{rest}
          </Badge>
        </li>
      )}
    </ul>
  );
}
