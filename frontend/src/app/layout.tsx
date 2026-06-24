import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  title: "ResearcherX",
  description: "Autonomous multi-agent research assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen font-sans">
        <ThemeProvider>
          <header className="border-b border-border">
            <div className="mx-auto max-w-4xl px-6 py-4 flex items-center justify-between">
              <Link href="/" className="font-mono text-lg font-semibold tracking-tight">
                researcher<span className="text-muted-foreground">x</span>
              </Link>
              <span className="text-xs text-muted-foreground font-mono">multi-agent research</span>
            </div>
          </header>
          <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
        </ThemeProvider>
      </body>
    </html>
  );
}
