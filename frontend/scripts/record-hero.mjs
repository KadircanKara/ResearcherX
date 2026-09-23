#!/usr/bin/env node
/**
 * Records the landing page's hero video from the REAL app.
 *
 * The clip shows the product loop the landing page sells: a paper library,
 * a question answered with sentence-level citations, a citation opened to
 * its source passage, and the manuscript compiled in the LaTeX editor. Every
 * frame is the running app on real data, never a mock -- so when the UI
 * changes, re-run this and the hero stays true.
 *
 * Requirements: the dev stack up (`make up`), the Claude CLI proxy up
 * (`make claude-proxy`), Google Chrome installed, ffmpeg on PATH.
 *
 *   node scripts/record-hero.mjs
 *
 * Env overrides: APP_URL, API_URL, PROJECT_ID, LATEX_DOC_ID, QUESTION,
 * FORMATS (default "desktop,phone"), and
 * THEMES (default "dark,light"): one full recording per theme, so the
 * landing page can play the clip that matches the visitor's theme.
 *
 * How it works: Chrome's DevTools screencast delivers a frame on every
 * repaint with its timestamp. Each frame is stamped with the playback SPEED
 * in force when it arrived, so slow waits (page loads, a streaming answer,
 * a compile) play back fast while the moments that matter play in real
 * time. Frames closer than 1/30 s of playback time are dropped, then ffmpeg
 * lays the kept frames out on that remapped timeline and encodes three
 * sizes. The chat turn it asks is deleted again afterwards, so recording
 * leaves no conversation behind.
 */
import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = process.env.APP_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
const PROJECT = process.env.PROJECT_ID ?? "fa2ab869-6b13-4b31-be5e-ff0c22652922";
const LATEX_DOC = process.env.LATEX_DOC_ID ?? "8940cadd-73d4-47f6-8f80-b666857737a1";
const QUESTION =
  process.env.QUESTION ?? "What reward function do the multi-UAV search agents learn from?";

const FPS = 30;
const THEMES = (process.env.THEMES ?? "dark,light").split(",").map((t) => t.trim());
/*
 * Two recordings per theme. Desktop is recorded at 1280 wide so the app's
 * own text lands near full size in the landing page's ~1180px frame. Phones
 * get their OWN recording at a phone viewport, where the app lays itself
 * out for a narrow screen -- a downscaled desktop frame would leave its text
 * about 3px tall.
 */
const FORMATS = {
  desktop: {
    viewport: { width: 1280, height: 800 },
    dpr: 2,
    outputs: [
      { tier: "xl", width: 1920, crf: 21 },
      { tier: "md", width: 1280, crf: 23 },
    ],
    poster: (theme) => `hero-${theme}-poster.jpg`,
    posterWidth: 1280,
  },
  phone: {
    viewport: { width: 400, height: 700 },
    dpr: 3,
    outputs: [{ tier: "sm", width: 600, crf: 24 }],
    poster: (theme) => `hero-${theme}-sm-poster.jpg`,
    posterWidth: 600,
  },
};
const FORMAT_NAMES = (process.env.FORMATS ?? "desktop,phone").split(",").map((f) => f.trim());

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.resolve(here, "../public/landing");
const FRAME_DIR = path.resolve(here, "../.hero-frames");

// Sidebar entries that are test fixtures in the dev database, not a
// researcher's projects; hidden so the clip shows a believable library.
const HIDDEN_PROJECTS = String.raw`Task\d|curl export|PerfProbe|Browser Verify`;

/** Runs in the page before any app script: the theme, a fake cursor, no dev chrome. */
function pageSetup({ hiddenProjects, theme }) {
  try {
    localStorage.setItem("theme", theme);
  } catch {}
  // Passed as a string: a RegExp does not survive serialisation into the page.
  const hide = new RegExp(hiddenProjects, "i");
  const install = () => {
    const style = document.createElement("style");
    style.textContent = `
      nextjs-portal, button.fixed.bottom-4.right-4 { display: none !important; }
      #rx-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647;
        pointer-events: none; width: 22px; height: 22px;
        transform: translate(-100px, -100px); transition: none; }
      #rx-cursor .ring { position: absolute; left: -9px; top: -9px; width: 18px;
        height: 18px; border-radius: 9999px; border: 2px solid rgba(96,165,250,.9);
        opacity: 0; transform: scale(.4); }
      #rx-cursor.down .ring { animation: rx-click .45s ease-out; }
      @keyframes rx-click { 0% { opacity: 1; transform: scale(.4); }
        100% { opacity: 0; transform: scale(1.8); } }`;
    document.head.appendChild(style);
    const cursor = document.createElement("div");
    cursor.id = "rx-cursor";
    cursor.innerHTML = `<div class="ring"></div><svg width="22" height="22" viewBox="0 0 24 24">
      <path d="M4 2.5 L4 19 L8.6 14.8 L11.6 21.5 L14.3 20.3 L11.4 13.8 L17.6 13.8 Z"
        fill="white" stroke="black" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.body.appendChild(cursor);
    const saved = sessionStorage.getItem("rx-cursor");
    if (saved) cursor.style.transform = saved;
    document.addEventListener(
      "mousemove",
      (e) => {
        const t = `translate(${e.clientX}px, ${e.clientY}px)`;
        cursor.style.transform = t;
        sessionStorage.setItem("rx-cursor", t);
      },
      true,
    );
    document.addEventListener(
      "mousedown",
      () => {
        cursor.classList.remove("down");
        void cursor.offsetWidth;
        cursor.classList.add("down");
      },
      true,
    );
    const prune = () => {
      for (const a of document.querySelectorAll("aside a")) {
        if (hide.test(a.textContent ?? "")) a.style.display = "none";
      }
    };
    prune();
    new MutationObserver(prune).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
}

async function record(theme, formatName) {
  const fmt = FORMATS[formatName];
  const VIEWPORT = fmt.viewport;
  const DPR = fmt.dpr;
  await rm(FRAME_DIR, { recursive: true, force: true });
  await mkdir(FRAME_DIR, { recursive: true });
  await mkdir(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DPR,
    colorScheme: theme,
    reducedMotion: "no-preference",
  });
  await context.addInitScript(pageSetup, { hiddenProjects: HIDDEN_PROJECTS, theme });
  const page = await context.newPage();

  // ── frame capture on a speed-remapped timeline ─────────────────────────────
  let speed = 1;
  let lastTs = null;
  let virtual = 0;
  let lastKept = -Infinity;
  const kept = []; // { file, at } -- `at` is playback seconds
  const cdp = await context.newCDPSession(page);
  cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
    const ts = metadata.timestamp;
    if (lastTs !== null) virtual += (ts - lastTs) / speed;
    lastTs = ts;
    if (virtual - lastKept < 1 / FPS) return;
    lastKept = virtual;
    const file = path.join(FRAME_DIR, `f${String(kept.length).padStart(5, "0")}.jpg`);
    kept.push({ file, at: virtual });
    await writeFile(file, Buffer.from(data, "base64"));
  });
  const startCapture = () =>
    cdp.send("Page.startScreencast", {
      format: "jpeg",
      quality: 88,
      maxWidth: VIEWPORT.width * DPR,
      maxHeight: VIEWPORT.height * DPR,
    });

  const hold = (ms) => page.waitForTimeout(ms);
  /** Glide the fake cursor to the centre of an element. */
  async function glideTo(locator, { steps = 28, dx = 0, dy = 0 } = {}) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) throw new Error(`no box for ${locator}`);
    await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps });
  }
  async function fastNavigate(url, ready) {
    speed = 25;
    await page.goto(url);
    await ready();
    await hold(400);
    speed = 1;
  }

  let conversationId = null;
  try {
    // Warm every route once, uncaptured, so dev-mode compiles never show.
    for (const p of ["papers", "chat", `latex/${LATEX_DOC}`]) {
      await page.goto(`${APP}/admin/research/${PROJECT}/${p}`);
      await page.waitForLoadState("networkidle").catch(() => {});
    }

    // ── scene 1: the library ─────────────────────────────────────────────────
    await page.goto(`${APP}/admin/research/${PROJECT}/papers`);
    await page.getByText("papers in this library").waitFor();
    await page.mouse.move(VIEWPORT.width * 0.62, VIEWPORT.height * 0.78);
    await hold(600);
    await startCapture();
    await hold(1400);
    const row = page.locator("main").getByText("Cooperative Multi-Target Search", { exact: false }).first();
    await row.scrollIntoViewIfNeeded().catch(() => {});
    const target = (await row.count()) ? row : page.locator("main table tbody tr, main [role=row]").nth(2);
    await glideTo(target);
    await hold(250);
    await target.click();
    await page.getByText("searchable").first().waitFor({ timeout: 15000 }).catch(() => {});
    await hold(1800);

    // ── scene 2: ask, and watch the cited answer arrive ─────────────────────
    const chatTab = page.getByRole("link", { name: "Chat" }).first();
    await glideTo(chatTab);
    await hold(200);
    speed = 25;
    await chatTab.click();
    const box = page.getByPlaceholder(/Ask a question about this project/);
    await box.waitFor();
    await hold(300);
    speed = 1;
    await glideTo(box);
    await box.click();
    await hold(300);
    await page.keyboard.type(QUESTION, { delay: 38 });
    await hold(500);
    const start = page.getByRole("button", { name: "Start the conversation" });
    await glideTo(start);
    await hold(200);
    await start.click();
    await page.waitForURL(/\/chat\/[0-9a-f-]{36}/, { timeout: 30000 });
    conversationId = page.url().split("/chat/")[1].split(/[?#]/)[0];
    await hold(1500); // the "thinking / searching" status, in real time
    speed = 7; // the answer streams at seven times speed
    await page.getByText("Sources", { exact: true }).last().waitFor({ timeout: 240000 });
    await hold(1500);
    speed = 1;
    await hold(600);

    // ── scene 3: open a citation to its source passage ──────────────────────
    const chip = page.locator('button[aria-label^="Citation 1,"]').first();
    await chip.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
    await hold(400);
    await glideTo(chip, { steps: 32 });
    await hold(1400);
    // The poster frame: the cited answer with its passage open -- the moment
    // the page's caption promises -- without the fake cursor over it.
    await page.evaluate(() => {
      const c = document.getElementById("rx-cursor");
      if (c) c.style.visibility = "hidden";
    });
    const posterPng = path.join(FRAME_DIR, "poster.png");
    await page.screenshot({ path: posterPng });
    await page.evaluate(() => {
      const c = document.getElementById("rx-cursor");
      if (c) c.style.visibility = "";
    });
    await hold(1400);
    await page.mouse.move(VIEWPORT.width * 0.83, VIEWPORT.height * 0.91, { steps: 20 });
    await hold(400);

    // ── scene 4: write it up -- compile the manuscript, jump PDF to source ──
    await fastNavigate(`${APP}/admin/research/${PROJECT}/latex/${LATEX_DOC}`, () =>
      page.getByRole("button", { name: /^Compile/ }).waitFor(),
    );
    await hold(700);
    const compile = page.getByRole("button", { name: /^Compile/ });
    await glideTo(compile);
    await hold(200);
    await compile.click();
    speed = 4;
    await page.waitForFunction(
      () => [...document.querySelectorAll("canvas")].some((c) => c.width > 300),
      null,
      { timeout: 90000 },
    );
    await hold(1200);
    speed = 1;
    if (formatName === "phone") {
      // A phone stacks source over PDF, so the PDF would compile off screen:
      // switch the editor to its PDF-only view instead of jumping to source.
      const pdfView = page.getByRole("tab", { name: "PDF", exact: true });
      await glideTo(pdfView);
      await hold(200);
      await pdfView.click();
    }
    await hold(900);
    const firstPage = page.locator("canvas").first();
    const pbox = formatName === "phone" ? null : await firstPage.boundingBox();
    if (pbox) {
      const x = pbox.x + pbox.width * 0.5;
      const y = pbox.y + pbox.height * 0.1;
      await page.mouse.move(x, y, { steps: 30 });
      await hold(300);
      await page.mouse.dblclick(x, y);
    }
    await hold(2600);

    await cdp.send("Page.stopScreencast");
    await hold(300);
  } finally {
    if (conversationId) {
      const res = await fetch(`${API}/v1/projects/${PROJECT}/conversations/${conversationId}`, {
        method: "DELETE",
      }).catch((e) => e);
      console.log(`deleted recording conversation ${conversationId}:`, res.status ?? res);
    }
    await browser.close();
  }

  if (kept.length < 10) throw new Error(`only ${kept.length} frames captured`);

  // ── encode ─────────────────────────────────────────────────────────────────
  const lines = [];
  for (let i = 0; i < kept.length; i++) {
    const next = i + 1 < kept.length ? kept[i + 1].at : kept[i].at + 0.6;
    lines.push(`file '${kept[i].file}'`, `duration ${Math.max(next - kept[i].at, 1 / FPS).toFixed(4)}`);
  }
  lines.push(`file '${kept[kept.length - 1].file}'`);
  const list = path.join(FRAME_DIR, "frames.txt");
  await writeFile(list, lines.join("\n"));
  const total = kept[kept.length - 1].at - kept[0].at + 0.6;
  console.log(`${kept.length} frames, ${total.toFixed(1)} s of playback`);

  const fade = theme === "light" ? "white" : "black";
  for (const { tier, width, crf } of fmt.outputs) {
    const name = `hero-${theme}-${tier}.mp4`;
    const height = Math.round((width * VIEWPORT.height) / VIEWPORT.width / 2) * 2;
    const vf = [
      `fps=${FPS}`,
      `scale=${width}:${height}:flags=lanczos`,
      `fade=t=in:st=0:d=0.4:color=${fade}`,
      `fade=t=out:st=${(total - 0.5).toFixed(2)}:d=0.5:color=${fade}`,
      "format=yuv420p",
    ].join(",");
    execFileSync(
      "ffmpeg",
      ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list, "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", String(crf), "-movflags", "+faststart",
        "-an", path.join(OUT_DIR, name)],
      { stdio: "inherit" },
    );
    console.log(`wrote public/landing/${name}`);
  }
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", path.join(FRAME_DIR, "poster.png"),
    "-vf", `scale=${fmt.posterWidth}:-2:flags=lanczos`, "-q:v", "3", path.join(OUT_DIR, fmt.poster(theme))]);
  console.log(`wrote public/landing/${fmt.poster(theme)}`);
  await rm(FRAME_DIR, { recursive: true, force: true });
}

async function main() {
  for (const theme of THEMES) {
    if (theme !== "dark" && theme !== "light") throw new Error(`unknown theme ${theme}`);
    for (const formatName of FORMAT_NAMES) {
      if (!FORMATS[formatName]) throw new Error(`unknown format ${formatName}`);
      console.log(`recording ${theme} ${formatName}`);
      await record(theme, formatName);
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
