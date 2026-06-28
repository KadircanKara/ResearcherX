import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { IdentityProvider } from "@/lib/identity";
import { AppShell } from "@/components/app-shell";
import { DebugPanel } from "@/components/debug-panel";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "ResearcherX",
  description: "Autonomous multi-agent research assistant.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="min-h-screen font-sans">
        <ThemeProvider>
          <IdentityProvider>
            <AppShell>{children}</AppShell>
          </IdentityProvider>
          {process.env.NODE_ENV !== "production" && <DebugPanel />}
        </ThemeProvider>
      </body>
    </html>
  );
}
