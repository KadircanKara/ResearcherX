"use client";

import { useEffect, useState, type MouseEvent } from "react";
import { Menu, Moon, Sun, X } from "lucide-react";
import { usePrefersReducedMotion } from "@/hooks/use-reveal";
import { routes } from "@/lib/routes";
import { LogoMark } from "./logo-mark";
import { useLandingTheme } from "./landing-theme";
import { scrollToHash } from "./scroll-to";

const links = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Writing", href: "#writing" },
  { label: "Why trust it", href: "#why" },
];

function ThemeButton() {
  const { theme, toggle } = useLandingTheme();
  const next = theme === "light" ? "dark" : "light";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="text-site-muted hover:text-site-fg hover:bg-site-panel grid size-11 place-items-center rounded-lg transition-colors md:size-9"
    >
      {theme === "light" ? <Moon className="size-[18px]" aria-hidden /> : <Sun className="size-[18px]" aria-hidden />}
    </button>
  );
}

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const reduced = usePrefersReducedMotion();

  function go(event: MouseEvent<HTMLAnchorElement>, hash: string) {
    // The open menu locks body scrolling; release it before the glide starts
    // rather than a render later, or the first frames go nowhere.
    document.body.style.overflow = "";
    setOpen(false);
    if (scrollToHash(hash, !reduced)) event.preventDefault();
  }

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header
      // No backdrop blur while the menu is open: `backdrop-filter` makes the
      // header the containing block for its fixed descendants, so the
      // full-screen menu would shrink to the header's own box.
      className={`fixed inset-x-0 top-0 z-50 border-b transition-colors duration-200 ${
        open
          ? "bg-site-bg border-site-line"
          : scrolled
            ? "bg-site-bg/85 border-site-line backdrop-blur-md"
            : "border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[1180px] items-center justify-between px-5 sm:px-8">
        <a
          href="#top"
          onClick={(e) => go(e, "#top")}
          className="text-site-fg inline-flex items-center gap-2.5 text-[15px] font-semibold"
        >
          <LogoMark />
          ResearcherX
        </a>

        <nav aria-label="Main" className="hidden items-center gap-8 md:flex">
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={(e) => go(e, l.href)}
              className="text-site-muted hover:text-site-fg text-[14px] transition-colors"
            >
              {l.label}
            </a>
          ))}
          <div className="flex items-center gap-2">
            <ThemeButton />
            <a
              href={routes.home()}
              className="bg-site-accent text-site-accent-fg inline-flex h-9 items-center rounded-lg px-4 text-[14px] font-medium transition-[filter] hover:brightness-110"
            >
              Open the app
            </a>
          </div>
        </nav>

        <div className="-mr-2 flex items-center md:hidden">
          <ThemeButton />
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="mobile-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            className="text-site-fg grid size-11 place-items-center rounded-lg"
          >
            {open ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
          </button>
        </div>
      </div>

      {open && (
        <div
          id="mobile-menu"
          className="bg-site-bg fixed inset-0 top-16 z-40 flex flex-col gap-2 px-5 pt-6 md:hidden"
        >
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={(e) => go(e, l.href)}
              className="text-site-fg border-site-line border-b py-4 text-xl font-medium"
            >
              {l.label}
            </a>
          ))}
          <a
            href={routes.home()}
            className="bg-site-accent text-site-accent-fg mt-6 inline-flex h-12 items-center justify-center rounded-lg text-[15px] font-medium"
          >
            Open the app
          </a>
        </div>
      )}
    </header>
  );
}
