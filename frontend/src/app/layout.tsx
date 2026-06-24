import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { IdentityProvider } from "@/lib/identity";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "ResearcherX",
  description: "Autonomous multi-agent research assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen font-sans">
        <ThemeProvider>
          <IdentityProvider>
            <AppShell>{children}</AppShell>
          </IdentityProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
