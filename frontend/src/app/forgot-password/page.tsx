"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthShell, FieldRow } from "@/components/auth/auth-shell";
import { useCaptcha } from "@/components/auth/captcha";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RESET_NOTICE, authErrorMessage } from "@/lib/auth-flow";
import { isValidEmail, normalizeEmail } from "@/lib/login-flow";
import { routes } from "@/lib/routes";
import { supabase } from "@/lib/supabase";

/** Asks for a reset link. Answers the same whether or not the email has an account. */
export default function ForgotPasswordPage() {
  const captcha = useCaptcha();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function requestReset() {
    const target = normalizeEmail(email);
    if (!isValidEmail(target)) return setError("Enter a valid email address.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase().auth.resetPasswordForEmail(target, {
      redirectTo: `${window.location.origin}${routes.authReset()}`,
      captchaToken: captcha.token,
    });
    captcha.reset();
    setBusy(false);
    // Only the errors that say nothing about the account are shown.
    if (err && (err.status === 429 || err.code === "captcha_failed" || err.code?.startsWith("over_"))) {
      return setError(authErrorMessage(err));
    }
    setSent(true);
  }

  return (
    <AuthShell title="Reset your password" error={error}>
      {sent ? (
        <p className="text-sm text-muted-foreground">{RESET_NOTICE}</p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void requestReset();
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
          {captcha.widget}
          <Button type="submit" className="w-full" disabled={busy || !captcha.ready}>
            Email me a reset link
          </Button>
        </form>
      )}
      <Link className="text-xs underline" href={routes.login()}>
        Back to sign in
      </Link>
    </AuthShell>
  );
}
