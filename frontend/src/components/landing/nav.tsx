"use client";

import { useEffect, useState, type MouseEvent } from "react";
import { usePrefersReducedMotion } from "@/hooks/use-reveal";
import { routes } from "@/lib/routes";
import { scrollToHash } from "./scroll-to";

const links = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Features", href: "#features" },
  { label: "LaTeX", href: "#latex" },
];

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
    const onScroll = () => setScrolled(window.scrollY > 24);
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
      // full-screen menu would shrink to the header's own box and the hero
      // would show through behind the links.
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        open
          ? "bg-lp-ink border-b border-lp-paper/15"
          : scrolled
            ? "bg-lp-ink/90 backdrop-blur-md border-b border-lp-paper/15"
            : "bg-transparent border-b border-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[1240px] items-center justify-between px-5 sm:px-8">
        <a
          href="#top"
          onClick={(e) => go(e, "#top")}
          className="text-lp-paper text-base font-semibold tracking-[-0.02em]"
        >
          ResearcherX
        </a>

        <nav aria-label="Main" className="hidden items-center gap-8 md:flex">
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={(e) => go(e, l.href)}
              className="text-lp-paper/70 hover:text-lp-paper text-sm transition-colors"
            >
              {l.label}
            </a>
          ))}
          <a href={routes.home()} className="pill-light px-5 py-2 text-sm font-medium">
            Open the app
          </a>
        </nav>

        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-menu"
          className="text-lp-paper label-xs md:hidden"
        >
          {open ? "Close" : "Menu"}
        </button>
      </div>

      {open && (
        <div
          id="mobile-menu"
          className="bg-lp-ink fixed inset-0 top-16 z-40 flex flex-col gap-8 px-6 pt-14 md:hidden"
        >
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={(e) => go(e, l.href)}
              className="text-lp-paper text-3xl font-medium tracking-[-0.03em]"
            >
              {l.label}
            </a>
          ))}
          <a
            href={routes.home()}
            className="pill-light mt-2 w-full px-6 py-3.5 text-base font-medium"
          >
            Open the app
          </a>
        </div>
      )}
    </header>
  );
}
