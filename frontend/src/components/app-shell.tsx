"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Binoculars, BookOpen, ChevronLeft, ChevronRight, Menu } from "lucide-react";
import { routes } from "@/lib/routes";
import { cn } from "@/lib/utils";
import { listProjects } from "@/lib/projects";
import { colorFor } from "@/lib/project-colors";
import { subscribeProjectColor, subscribeProjectListChanged } from "@/lib/project-store";
import { initials } from "@/lib/format";
import { useIdentity } from "@/lib/identity";
import type { Project } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeToggle } from "@/components/theme-toggle";
import { UserMenu } from "@/components/user-menu";

/**
 * The app's frame, ported from the prototype's `app-shell.tsx`: a fixed
 * sidebar (w-60, or w-16 collapsed) on `lg` and up, a sticky header with a
 * navigation sheet below it, and the page in `main`.
 *
 * The top bar with the theme toggle is NOT here: in the prototype each page
 * draws its own (`hidden h-14 … lg:flex` with `<ThemeToggle />`), because the
 * project shell puts its tab band directly under it.
 */

const COLLAPSE_KEY = "rx.sidebar.collapsed";

/** Whether `pathname` is `href` or somewhere below it. */
function within(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SidebarContent({
  compact = false,
  projects,
  close,
}: {
  compact?: boolean;
  projects: Project[];
  close?: () => void;
}) {
  const pathname = usePathname();
  const { me } = useIdentity();
  const onResearch = within(pathname, routes.research());

  return (
    <div className="flex h-full flex-col">
      <div
        className={cn(
          "flex h-14 items-center border-b border-sidebar-border",
          compact ? "justify-center" : "px-4",
        )}
      >
        <span className="grid size-7 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
          R
        </span>
        {!compact && <span className="ml-2.5 text-sm font-semibold">ResearcherX</span>}
      </div>

      <nav className="space-y-1 p-2" aria-label="Primary">
        <Button
          variant="ghost"
          render={<Link href={routes.research()} onClick={close} />}
          aria-label={compact ? "Research" : undefined}
          className={cn(
            "w-full justify-start rounded-md",
            onResearch ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground",
            compact && "justify-center px-0",
          )}
        >
          <BookOpen />
          {!compact && "Research"}
        </Button>
        <Button
          variant="ghost"
          render={<Link href={routes.explorer()} onClick={close} />}
          aria-label={compact ? "Explorer" : undefined}
          className={cn(
            "w-full justify-start rounded-md",
            !onResearch ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground",
            compact && "justify-center px-0",
          )}
        >
          <Binoculars />
          {!compact && "Explorer"}
        </Button>
      </nav>

      {!compact && (
        // `min-h-0 overflow-y-auto`: the prototype's list is short mock data;
        // a real library can outgrow the rail, and the user block below must
        // stay on screen.
        <div className="mt-4 min-h-0 overflow-y-auto px-4">
          <p className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground">Projects</p>
          <div className="space-y-0.5">
            {projects.map((project) => {
              const active = within(pathname, routes.project(project.id));
              return (
                <Link
                  key={project.id}
                  href={routes.chat(project.id)}
                  onClick={close}
                  className={cn(
                    "flex items-center gap-2 truncate rounded-md px-2 py-1.5 text-[13px] transition-colors hover:bg-sidebar-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                    active
                      ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {/* `colorFor`, never the raw field: it is the guard that
                      keeps an unknown string out of the style attribute. */}
                  <span
                    aria-hidden
                    className="size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: colorFor(project) }}
                  />
                  <span className="truncate">{project.title}</span>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      <div className="mt-auto border-t border-sidebar-border p-3">
        {!compact &&
          (me ? (
            // The user block opens the dev identity switcher; sharing is only
            // exercisable because a second teammate can be picked there.
            <UserMenu className="-m-1 flex w-[calc(100%+0.5rem)] items-center gap-2 rounded-md p-1 transition-colors hover:bg-sidebar-accent">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-secondary text-xs font-semibold">
                {initials(me.name)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">{me.name}</p>
                <p className="truncate text-[11px] text-muted-foreground">{me.email}</p>
              </div>
            </UserMenu>
          ) : (
            <div className="flex items-center gap-2" aria-hidden>
              <span className="size-7 rounded-full bg-secondary" />
              <span className="h-3 w-24 rounded bg-secondary" />
            </div>
          ))}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me } = useIdentity();
  const [projects, setProjects] = useState<Project[]>([]);
  const [mobileOpen, setMobileOpen] = useState(false);
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
    const load = () =>
      listProjects()
        .then((rows) => {
          if (!cancelled) setProjects(rows);
        })
        .catch(() => {
          if (!cancelled) setProjects([]);
        });
    void load();
    // A project created elsewhere (the projects page) must reach the rail
    // without a reload; the rail owns no other refresh trigger.
    const off = subscribeProjectListChanged(() => void load());
    return () => {
      cancelled = true;
      off();
    };
  }, [me?.id]);

  // The rail's copy of the project list is fetched once, so a colour picked on
  // the project page has no other way to reach it.
  useEffect(
    () =>
      subscribeProjectColor(({ id, color }) => {
        setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, color } : p)));
      }),
    [],
  );

  return (
    <TooltipProvider delayDuration={300}>
      <div className="min-h-screen bg-background text-foreground">
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-30 hidden border-r border-sidebar-border bg-sidebar transition-[width] duration-200 lg:block",
            collapsed ? "w-16" : "w-60",
          )}
        >
          <SidebarContent compact={collapsed} projects={projects} />
          <Button
            variant="outline"
            size="icon"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
            className="absolute -right-3 top-20 size-6 rounded-full bg-background"
          >
            {collapsed ? <ChevronRight className="size-3" /> : <ChevronLeft className="size-3" />}
          </Button>
        </aside>

        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/95 px-3 backdrop-blur lg:hidden">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger
              render={<Button variant="ghost" size="icon" aria-label="Open navigation" />}
            >
              <Menu />
            </SheetTrigger>
            <SheetContent side="left" className="w-60 p-0">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <SidebarContent projects={projects} close={() => setMobileOpen(false)} />
            </SheetContent>
          </Sheet>
          <div className="flex items-center gap-2 text-sm font-semibold">
            <span className="grid size-7 place-items-center rounded-md bg-primary text-xs text-primary-foreground">
              R
            </span>
            ResearcherX
          </div>
          <ThemeToggle />
        </header>

        <main
          className={cn(
            "min-h-screen transition-[margin] duration-200",
            collapsed ? "lg:ml-16" : "lg:ml-60",
          )}
        >
          {children}
        </main>
      </div>
    </TooltipProvider>
  );
}
