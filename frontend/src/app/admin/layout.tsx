import { IdentityProvider } from "@/lib/identity";
import { AppShell } from "@/components/app-shell";
import { DebugPanel } from "@/components/debug-panel";

/** The application proper: everything under `/admin` gets the shell. */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <IdentityProvider>
      <AppShell>{children}</AppShell>
      {process.env.NODE_ENV !== "production" && <DebugPanel />}
    </IdentityProvider>
  );
}
