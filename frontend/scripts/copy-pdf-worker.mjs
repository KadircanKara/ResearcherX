// PDF.js renders on a worker, and the worker file must be served as a static
// asset. Copying it out of node_modules at dev/build time -- rather than
// vendoring a committed copy -- is what guarantees the worker and the API are
// the same version. They are checked at runtime: a mismatch throws "The API
// version does not match the Worker version" and nothing renders.
import { createRequire } from "node:module";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const publicDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public");

// The filename moved between major versions (.js -> .mjs, minified or not),
// so resolve rather than assume. Whatever is found is written under one
// stable name that the app can reference.
const CANDIDATES = [
  "pdfjs-dist/build/pdf.worker.min.mjs",
  "pdfjs-dist/build/pdf.worker.mjs",
  "pdfjs-dist/build/pdf.worker.min.js",
  "pdfjs-dist/build/pdf.worker.js",
];

let source = null;
for (const candidate of CANDIDATES) {
  try {
    source = require.resolve(candidate);
    break;
  } catch {
    // try the next name
  }
}

if (!source) {
  console.error(
    `[copy-pdf-worker] no worker found in pdfjs-dist. Tried:\n  ${CANDIDATES.join("\n  ")}`
  );
  process.exit(1);
}

mkdirSync(publicDir, { recursive: true });
copyFileSync(source, join(publicDir, "pdf.worker.min.mjs"));
console.log(`[copy-pdf-worker] ${source} -> public/pdf.worker.min.mjs`);
