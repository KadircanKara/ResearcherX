"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell, FieldRow } from "@/components/auth/auth-shell";
import { useCaptcha } from "@/components/auth/captcha";
import { ResendActivation } from "@/components/auth/resend-activation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PASSWORD_MIN, SIGNUP_NOTICE, authErrorMessage, passwordProblem } from "@/lib/auth-flow";
import { isValidEmail, normalizeEmail } from "@/lib/login-flow";
import { routes } from "@/lib/routes";
import { supabase } from "@/lib/supabase";

/**
 * Sign-up creates an UNCONFIRMED account and Supabase emails the activation
 * link ("Confirm email" on). No session exists until that link is opened at
 * /auth/confirm, so nothing here signs anyone in.
 */
export default function SignupPage() {
  const router = useRouter();
  const captcha = useCaptcha();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<{ email: string; at: number } | null>(null);

  async function signUp() {
    const target = normalizeEmail(email);
    if (!isValidEmail(target)) return setError("Enter a valid email address.");
    const problem = passwordProblem(password, target);
    if (problem) return setError(problem);
    if (password !== repeat) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    const { data, error: err } = await supabase().auth.signUp({
      email: target,
      password,
      options: {
        emailRedirectTo: `${window.location.origin}${routes.authConfirm()}`,
        captchaToken: captcha.token,
      },
    });
    captcha.reset();
    setBusy(false);
    if (err) return setError(authErrorMessage(err));
    // A session here means the project has "Confirm email" OFF. The account
    // is usable, so go in -- but the RUNBOOK requires it on.
    if (data.session) return router.replace(routes.research());
    setPassword("");
    setRepeat("");
    setSentTo({ email: target, at: Date.now() });
  }

  if (sentTo) {
    return (
      <AuthShell title="Check your email">
        <p className="text-sm text-muted-foreground">
          {SIGNUP_NOTICE} We sent it to <span className="font-medium">{sentTo.email}</span>.
        </p>
        <ResendActivation email={sentTo.email} sentAt={sentTo.at} />
        <Link className="text-xs underline" href={routes.login()}>
          Back to sign in
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create your ResearcherX account" error={error}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void signUp();
        }}
      >
        <FieldRow id="email" label="Email">
          <Input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </FieldRow>
        <FieldRow id="password" label="Password">
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby="password-hint"
          />
        </FieldRow>
        <p id="password-hint" className="text-xs text-muted-foreground">
          At least {PASSWORD_MIN} characters.
        </p>
        <FieldRow id="repeat" label="Repeat password">
          <Input
            id="repeat"
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
          />
        </FieldRow>
        {captcha.widget}
        <Button type="submit" className="w-full" disabled={busy || !captcha.ready}>
          Create account
        </Button>
      </form>
      <p className="text-xs">
        Already have an account?{" "}
        <Link className="underline" href={routes.login()}>
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
