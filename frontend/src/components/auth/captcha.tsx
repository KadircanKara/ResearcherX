"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Cloudflare Turnstile, the CAPTCHA Supabase's Attack Protection checks on
 * sign-up, sign-in, code and reset requests. Off unless the site key is set
 * at build time; with it off the forms send no token, which is right only
 * while the Supabase project has CAPTCHA protection off too (RUNBOOK §4).
 */
const siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
export const captchaEnabled = siteKey !== "";

type Turnstile = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
};

declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<Turnstile> | null = null;

function loadTurnstile(): Promise<Turnstile> {
  loading ??= new Promise((resolve, reject) => {
    if (window.turnstile) return resolve(window.turnstile);
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.onload = () =>
      window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile missing"));
    script.onerror = () => {
      loading = null;
      reject(new Error("turnstile failed to load"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

function Captcha({
  onToken,
  resetSignal,
}: {
  onToken: (token: string | null) => void;
  resetSignal: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  // Held in a ref so a parent re-render never re-mounts the widget.
  const report = useRef(onToken);
  report.current = onToken;

  useEffect(() => {
    let cancelled = false;
    loadTurnstile()
      .then((ts) => {
        if (cancelled || !box.current) return;
        widget.current = ts.render(box.current, {
          sitekey: siteKey,
          theme: "auto",
          callback: (token: string) => report.current(token),
          "expired-callback": () => report.current(null),
          "error-callback": () => report.current(null),
        });
      })
      .catch(() => report.current(null));
    return () => {
      cancelled = true;
      if (widget.current) window.turnstile?.remove(widget.current);
      widget.current = null;
    };
  }, []);

  // A token is single-use: every submit asks for a fresh one.
  useEffect(() => {
    if (resetSignal === 0 || !widget.current) return;
    window.turnstile?.reset(widget.current);
    report.current(null);
  }, [resetSignal]);

  return <div ref={box} className="min-h-[65px]" />;
}

/**
 * The form-side handle. `token` goes into the Supabase call's `captchaToken`;
 * `reset()` after every attempt, success or not.
 */
export function useCaptcha() {
  const [token, setToken] = useState<string | null>(null);
  const [signal, setSignal] = useState(0);
  return {
    token: token ?? undefined,
    ready: !captchaEnabled || token !== null,
    reset: () => setSignal((n) => n + 1),
    widget: captchaEnabled ? <Captcha onToken={setToken} resetSignal={signal} /> : null,
  };
}
