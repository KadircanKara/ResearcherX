import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "ResearcherX",
  description: "Ask your papers. Get answers with receipts.",
};

/**
 * The root layout carries only what every page shares: the document, the
 * font and the colour scheme. The app shell, the identity lookup and the
 * debug panel belong to the app alone and live in `admin/layout.tsx`, so the
 * landing page at "/" renders without a sidebar and without calling the API.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="min-h-screen font-sans">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
