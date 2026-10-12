"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell, FieldRow } from "@/components/auth/auth-shell";
import { useCaptcha } from "@/components/auth/captcha";
import { ResendActivation } from "@/components/auth/resend-activation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { authErrorMessage } from "@/lib/auth-flow";
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

/**
 * Email + password is the front door. "Email me a code" stays as a second
 * way in for an EXISTING account (`shouldCreateUser: false`): accounts are
 * created only through /signup, which is what makes activation mandatory.
 */
export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"password" | "code">("password");
  const [email, setEmail] = useState("");
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

  function switchTo(next: "password" | "code") {
    setMode(next);
    setError(null);
  }

  return (
    <AuthShell title="Sign in to ResearcherX" error={error}>
      {mode === "password" ? (
        <PasswordSignIn
          email={email}
          setEmail={setEmail}
          setError={setError}
          onDone={() => router.replace(routes.research())}
        />
      ) : (
        <CodeSignIn
          email={email}
          setEmail={setEmail}
          setError={setError}
          onDone={() => router.replace(routes.research())}
        />
      )}
      <div className="flex flex-col gap-2 text-xs">
        <button
          type="button"
          className="self-start underline"
          onClick={() => switchTo(mode === "password" ? "code" : "password")}
        >
          {mode === "password" ? "Email me a sign-in code instead" : "Sign in with a password"}
        </button>
        <p>
          New here?{" "}
          <Link className="underline" href={routes.signup()}>
            Create an account
          </Link>
        </p>
      </div>
    </AuthShell>
  );
}

type StepProps = {
  email: string;
  setEmail: (s: string) => void;
  setError: (s: string | null) => void;
  onDone: () => void;
};

function PasswordSignIn({ email, setEmail, setError, onDone }: StepProps) {
  const captcha = useCaptcha();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState(false);

  async function signIn() {
    const target = normalizeEmail(email);
    if (!isValidEmail(target)) return setError("Enter a valid email address.");
    if (!password) return setError("Enter your password.");
    setBusy(true);
    setError(null);
    setUnconfirmed(false);
    const { error } = await supabase().auth.signInWithPassword({
      email: target,
      password,
      options: { captchaToken: captcha.token },
    });
    captcha.reset();
    setBusy(false);
    if (error) {
      setEmail(target);
      setUnconfirmed(error.code === "email_not_confirmed");
      return setError(authErrorMessage(error));
    }
    onDone();
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        void signIn();
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
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </FieldRow>
      <div className="text-right text-xs">
        <Link className="underline" href={routes.forgotPassword()}>
          Forgot password?
        </Link>
      </div>
      {captcha.widget}
      <Button type="submit" className="w-full" disabled={busy || !captcha.ready}>
        Sign in
      </Button>
      {unconfirmed && <ResendActivation email={normalizeEmail(email)} sentAt={0} />}
    </form>
  );
}

function CodeSignIn({ email, setEmail, setError, onDone }: StepProps) {
  const captcha = useCaptcha();
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [sentAt, setSentAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (step !== "code") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [step]);

  async function sendCode() {
    const target = normalizeEmail(email);
    if (!isValidEmail(target)) return setError("Enter a valid email address.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase().auth.signInWithOtp({
      email: target,
      options: { shouldCreateUser: false, captchaToken: captcha.token },
    });
    captcha.reset();
    setBusy(false);
    // An unknown email errors too; the page says the same thing either way.
    if (err?.status === 429) return setError(loginErrorMessage(err));
    if (err?.code === "captcha_failed") return setError(authErrorMessage(err));
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
    onDone();
  }

  const wait = resendWaitSeconds(sentAt, now);

  if (step === "email") {
    return (
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void sendCode();
        }}
      >
        <FieldRow id="code-email" label="Email">
          <Input
            id="code-email"
            type="email"
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </FieldRow>
        {captcha.widget}
        <Button type="submit" className="w-full" disabled={busy || !captcha.ready}>
          Send sign-in code
        </Button>
      </form>
    );
  }

  return (
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
      {captcha.widget}
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
          disabled={wait > 0 || busy || !captcha.ready}
          onClick={() => void sendCode()}
        >
          {wait > 0 ? `Resend in ${wait}s` : "Resend code"}
        </button>
      </div>
    </form>
  );
}
