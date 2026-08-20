"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Network, Brain, Compass, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { listProjects } from "@/lib/projects";
import { colorFor } from "@/lib/project-colors";
import { useIdentity } from "@/lib/identity";
import type { Project } from "@/lib/types";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";

const NAV_LINKS = [
  { href: "/research", label: "Research", icon: Brain },
  { href: "/explorer", label: "Explorer", icon: Compass },
];

function initials(name: string): string {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** Gradient brand mark — used in the sidebar and the mobile topbar. */
function BrandMark() {
  return (
    <span className="grid size-7 place-items-center rounded-lg gradient-brand text-white shadow-[0_2px_8px_-2px_#3B82F6]">
      <Network className="size-4" />
    </span>
  );
}

const COLLAPSE_KEY = "rx.sidebar.collapsed";

function breadcrumbLabel(pathname: string): string {
  if (pathname.startsWith("/research")) return "Research";
  if (pathname.startsWith("/explorer")) return "Explorer";
  return "ResearcherX";
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me } = useIdentity();
  const [projects, setProjects] = useState<Project[]>([]);
  // Seeded in an effect rather than from a `useState` initializer: the server
  // render has no localStorage, so reading it during the first render would
  // produce markup the client immediately contradicts -- a hydration
  // mismatch. The rail therefore starts expanded and snaps closed once,
  // which is the same trade `next-themes` makes here.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(COLLAPSE_KEY) === "1");
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  }

  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((rows) => {
        if (!cancelled) setProjects(rows);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [me?.id]);

  return (
    <div className="flex min-h-screen font-sans">
      {/* Sidebar */}
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 md:flex",
          collapsed ? "w-14" : "w-60"
        )}
      >
        {/* Brand */}
        <Link
          href="/research"
          title={collapsed ? "ResearcherX" : undefined}
          className={cn(
            "flex items-center gap-2 py-3",
            collapsed ? "justify-center px-0" : "px-3"
          )}
        >
          <BrandMark />
          {!collapsed && (
            <span className="text-sm font-semibold tracking-tight">ResearcherX</span>
          )}
        </Link>

        {/* Primary nav */}
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV_LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                // The label is the only thing naming this link once it is an
                // icon, so the tooltip is not decoration -- it is the
                // accessible name a collapsed rail would otherwise lose.
                title={collapsed ? label : undefined}
                aria-label={collapsed ? label : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-md py-2 text-sm font-medium transition-colors",
                  collapsed ? "justify-center px-0" : "px-2.5",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                <Icon className="size-[17px] shrink-0" />
                {!collapsed && label}
              </Link>
            );
          })}
        </nav>

        {/* Projects */}
        {collapsed ? (
          <div className="mx-auto mt-4 mb-1 h-px w-6 bg-border" />
        ) : (
          <div className="px-2.5 pt-4 pb-1 font-mono text-[11px] font-medium text-muted-foreground">
            Projects
          </div>
        )}
        <div className="flex min-h-0 flex-col gap-0.5 overflow-y-auto px-2">
          {projects.length === 0 ? (
            collapsed ? null : (
              <span className="px-2.5 py-1.5 text-[13px] text-muted-foreground">
                No projects yet
              </span>
            )
          ) : (
            projects.slice(0, 8).map((p) => {
              const href = `/research/${p.id}`;
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={p.id}
                  href={href}
                  // The title is the ONLY thing naming a project in the
                  // collapsed rail, where the link is a bare coloured dot.
                  title={p.title}
                  aria-label={collapsed ? p.title : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md py-1.5 text-[13px] transition-colors",
                    collapsed ? "justify-center px-0" : "px-2.5",
                    active
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  {/* The project's own colour, not the brand gradient:
                      telling projects apart is the dot's entire job once the
                      labels are gone. `colorFor` guarantees a palette entry
                      even for a response that predates the field. */}
                  <span
                    className={cn("shrink-0 rounded-full", collapsed ? "size-2.5" : "size-1.5")}
                    style={{ backgroundColor: colorFor(p) }}
                  />
                  {!collapsed && <span className="truncate">{p.title}</span>}
                </Link>
              );
            })
          )}
        </div>

        {/* Identity footer */}
        <div
          className={cn(
            "mt-auto flex items-center gap-2 border-t border-sidebar-border py-3",
            collapsed ? "justify-center px-0" : "px-2"
          )}
          title={collapsed && me ? `${me.name} · ${me.email}` : undefined}
        >
          {me ? (
            <>
              <span
                className={cn(
                  "grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white",
                  !me.avatar_color && "gradient-brand"
                )}
                style={me.avatar_color ? { backgroundColor: me.avatar_color } : undefined}
              >
                {initials(me.name)}
              </span>
              {!collapsed && (
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium text-foreground">
                    {me.name}
                  </div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {me.email}
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <span className="size-7 shrink-0 rounded-full bg-muted" />
              {!collapsed && (
                <div className="min-w-0 flex-1">
                  <div className="h-3 w-20 rounded bg-muted" />
                </div>
              )}
            </>
          )}
        </div>
      </aside>

      {/* Content column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Sticky glass topbar */}
        <header className="sticky top-0 z-30 flex h-12 items-center justify-between gap-4 border-b border-border bg-background/80 px-4 backdrop-blur-md">
          <div className="flex items-center gap-2">
            <Link href="/research" className="md:hidden">
              <BrandMark />
            </Link>
            {/* `md:` only -- below that breakpoint the sidebar is not
                rendered at all, so a toggle there would control nothing. */}
            <button
              type="button"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="hidden rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:inline-flex"
            >
              {collapsed ? (
                <PanelLeftOpen className="size-4" />
              ) : (
                <PanelLeftClose className="size-4" />
              )}
            </button>
            <span className="text-sm text-muted-foreground">
              <b className="font-medium text-foreground">{breadcrumbLabel(pathname)}</b>
            </span>
          </div>

          <div className="flex items-center gap-1">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
