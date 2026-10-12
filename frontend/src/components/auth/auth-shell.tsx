import Link from "next/link";
import { Label } from "@/components/ui/label";
import { routes } from "@/lib/routes";
import { authEnabled } from "@/lib/supabase";

/** The frame every auth page shares: title, body, the one error line. */
export function AuthShell({
  title,
  error,
  children,
}: {
  title: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  if (!authEnabled) {
    return (
      <main className="mx-auto max-w-sm p-8 text-sm">
        Sign-in is not configured here.{" "}
        <Link className="underline" href={routes.research()}>
          Open the app
        </Link>
        .
      </main>
    );
  }
  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-8">
      <h1 className="text-xl font-semibold">{title}</h1>
      {children}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </main>
  );
}

/** A labelled input row; the auth forms are nothing but these. */
export function FieldRow({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}
