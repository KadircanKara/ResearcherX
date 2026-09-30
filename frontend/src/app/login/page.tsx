"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  SENT_NOTICE,
  isValidCode,
  isValidEmail,
  loginErrorMessage,
  normalizeCode,
  normalizeEmail,
  resendWaitSeconds,
} from "@/lib/login-flow";
import { routes } from "@/lib/routes";
import { authEnabled, supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [sentAt, setSentAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in: straight into the app.
  useEffect(() => {
    if (!authEnabled) return;
    supabase()
      .auth.getSession()
      .then(({ data }) => {
        if (data.session) router.replace(routes.research());
      });
  }, [router]);

  useEffect(() => {
    if (step !== "code") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [step]);

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

  async function sendCode() {
    const target = normalizeEmail(email);
    if (!isValidEmail(target)) return setError("Enter a valid email address.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase().auth.signInWithOtp({
      email: target,
      options: { shouldCreateUser: false },
    });
    setBusy(false);
    // An uninvited email errors too; the page says the same thing either way.
    if (err?.status === 429) return setError(loginErrorMessage(err));
    setEmail(target);
    setSentAt(Date.now());
    setNow(Date.now());
    setStep("code");
  }

  async function verify() {
    const token = normalizeCode(code);
    if (!isValidCode(token)) return setError("Enter the 6-digit code from the email.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase().auth.verifyOtp({ email, token, type: "email" });
    setBusy(false);
    if (err) return setError(loginErrorMessage(err));
    router.replace(routes.research());
  }

  const wait = resendWaitSeconds(sentAt, now);

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-8">
      <h1 className="text-xl font-semibold">Sign in to ResearcherX</h1>
      {step === "email" ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void sendCode();
          }}
        >
          <Input
            type="email"
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-label="Email"
          />
          <Button type="submit" className="w-full" disabled={busy}>
            Send sign-in code
          </Button>
        </form>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void verify();
          }}
        >
          <p className="text-sm text-muted-foreground">
            {SENT_NOTICE} ({email})
          </p>
          <Input
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="123456"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-label="Sign-in code"
          />
          <Button type="submit" className="w-full" disabled={busy}>
            Sign in
          </Button>
          <div className="flex justify-between text-xs">
            <button
              type="button"
              className="underline"
              onClick={() => {
                setStep("email");
                setCode("");
                setError(null);
              }}
            >
              Use a different email
            </button>
            <button
              type="button"
              className="underline disabled:no-underline disabled:opacity-60"
              disabled={wait > 0 || busy}
              onClick={() => void sendCode()}
            >
              {wait > 0 ? `Resend in ${wait}s` : "Resend code"}
            </button>
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </main>
  );
}
