"use client";

import { useEffect, useState } from "react";
import { authErrorMessage } from "@/lib/auth-flow";
import { resendWaitSeconds } from "@/lib/login-flow";
import { routes } from "@/lib/routes";
import { supabase } from "@/lib/supabase";
import { useCaptcha } from "./captcha";

/**
 * "Didn't get it? Resend" for an activation email, with Supabase's 60s
 * window counted down so the button never invites a guaranteed 429.
 * `sentAt` is when the last email went out (0 = never from this page).
 */
export function ResendActivation({
  email,
  sentAt: initialSentAt,
}: {
  email: string;
  sentAt: number;
}) {
  const captcha = useCaptcha();
  const [sentAt, setSentAt] = useState(initialSentAt);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  async function resend() {
    setBusy(true);
    setNote(null);
    const { error } = await supabase().auth.resend({
      type: "signup",
      email,
      options: {
        emailRedirectTo: `${window.location.origin}${routes.authConfirm()}`,
        captchaToken: captcha.token,
      },
    });
    captcha.reset();
    setBusy(false);
    if (error) return setNote(authErrorMessage(error));
    setSentAt(Date.now());
    setNow(Date.now());
    setNote("Sent. Check your inbox and spam folder.");
  }

  const wait = sentAt ? resendWaitSeconds(sentAt, now) : 0;
  return (
    <div className="space-y-2 text-xs">
      {captcha.widget}
      <button
        type="button"
        className="underline disabled:no-underline disabled:opacity-60"
        disabled={wait > 0 || busy || !captcha.ready}
        onClick={() => void resend()}
      >
        {wait > 0 ? `Resend activation email in ${wait}s` : "Resend activation email"}
      </button>
      {note && <p className="text-muted-foreground">{note}</p>}
    </div>
  );
}
