/** Height of the fixed landing nav, which every section scroll must clear. */
export const NAV_HEIGHT = 64;

/**
 * Glide to an in-page section instead of jumping to it.
 *
 * Anchor links lean on `scroll-behavior: smooth`, which the browser applies
 * only when it feels like it: a same-document hash change is instant in some
 * engines and after a router has touched the history in others. Driving the
 * scroll from JS makes the animation deterministic, keeps the fixed nav out
 * of the way, and still writes the hash so the address bar and the back
 * button behave as they would for a plain anchor. `animate` is false under
 * prefers-reduced-motion, where the jump is the correct behaviour.
 *
 * Returns false when the target does not exist, so the caller can let the
 * browser handle the click as an ordinary link.
 */
export function scrollToHash(hash: string, animate: boolean): boolean {
  const id = hash.replace(/^#/, "");
  const target = id === "top" ? document.body : document.getElementById(id);
  if (!target) return false;
  const top =
    id === "top" ? 0 : target.getBoundingClientRect().top + window.scrollY - NAV_HEIGHT;
  window.scrollTo({ top: Math.max(0, top), behavior: animate ? "smooth" : "auto" });
  const url = id === "top" ? window.location.pathname : `#${id}`;
  if (window.location.hash !== `#${id}`) window.history.pushState(null, "", url);
  return true;
}
