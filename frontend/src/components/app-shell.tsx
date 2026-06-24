"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Network, Brain, Compass } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/theme-toggle";
import { DensityToggle } from "@/components/density-toggle";
import { UserMenu } from "@/components/user-menu";

const NAV_LINKS = [
  { href: "/research", label: "Research", icon: Brain },
  { href: "/explorer", label: "Explorer", icon: Compass },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen flex-col font-sans">
      {/* Top bar */}
      <header className="sticky top-0 z-30 border-b border-border bg-background">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-4 px-4">
          {/* Brand */}
          <Link href="/research" className="flex shrink-0 items-center gap-2 mr-2">
            <span className="flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Network className="size-4" />
            </span>
            <span className="text-sm font-semibold tracking-tight text-foreground">
              ResearcherX
            </span>
          </Link>

          {/* Nav links */}
          <nav className="flex items-center gap-1">
            {NAV_LINKS.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(href + "/");
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm transition-colors",
                    active
                      ? "bg-muted text-primary font-medium"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                  )}
                >
                  <Icon className="size-3.5" />
                  {label}
                </Link>
              );
            })}
          </nav>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Right controls */}
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <DensityToggle />
            <div className="ml-1">
              <UserMenu />
            </div>
          </div>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1">{children}</main>
    </div>
  );
}
