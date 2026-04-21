import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "ResearcherX",
  description: "Autonomous multi-agent research assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <header className="border-b border-ink/10">
          <div className="mx-auto max-w-4xl px-6 py-4 flex items-center justify-between">
            <Link href="/" className="font-mono text-lg font-semibold tracking-tight">
              researcher<span className="text-ink/40">x</span>
            </Link>
            <span className="text-xs text-ink/50 font-mono">multi-agent research</span>
          </div>
        </header>
        <main className="mx-auto max-w-4xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
