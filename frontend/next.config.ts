import type { NextConfig } from "next";

// Security headers for every route. The CSP is deliberately narrow: it
// confines images (rendered answers and paper text are untrusted) and
// framing, and leaves scripts and styles alone, because App Router injects
// inline scripts that a script-src without nonces would block.
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: [
      "img-src 'self' data: blob:",
      "frame-ancestors 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
];

const config: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
