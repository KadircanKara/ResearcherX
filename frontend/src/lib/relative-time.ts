/**
 * Coarse "how long ago" label for a timestamp.
 *
 * Shared by the project card and the project row rather than copied: two
 * copies drift, and a list and a grid of the SAME projects disagreeing about
 * the age of one of them is the visible symptom.
 */
export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const days = Math.floor(diff / 86_400_000)
  if (days === 0) return "Today"
  if (days === 1) return "1 day ago"
  if (days < 30) return `${days} days ago`
  const months = Math.floor(days / 30)
  if (months === 1) return "1 month ago"
  if (months < 12) return `${months} months ago`
  const years = Math.floor(months / 12)
  return years === 1 ? "1 year ago" : `${years} years ago`
}
