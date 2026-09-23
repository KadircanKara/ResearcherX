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
 * Env overrides: APP_URL, API_URL, PROJECT_ID, LATEX_DOC_ID, QUESTION.
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

const VIEWPORT = { width: 1440, height: 900 };
const DPR = 2;
const FPS = 30;
const OUTPUTS = [
  { name: "hero-xl.mp4", width: 1920, crf: 21 },
  { name: "hero-md.mp4", width: 1280, crf: 23 },
  { name: "hero-sm.mp4", width: 854, crf: 25 },
];

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.resolve(here, "../public/landing");
const FRAME_DIR = path.resolve(here, "../.hero-frames");

// Sidebar entries that are test fixtures in the dev database, not a
// researcher's projects; hidden so the clip shows a believable library.
const HIDDEN_PROJECTS = String.raw`Task\d|curl export|PerfProbe|Browser Verify`;

/** Runs in the page before any app script: dark theme, fake cursor, no dev chrome. */
function pageSetup(hiddenProjects) {
  try {
    localStorage.setItem("theme", "dark");
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

async function main() {
  await rm(FRAME_DIR, { recursive: true, force: true });
  await mkdir(FRAME_DIR, { recursive: true });
  await mkdir(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DPR,
    colorScheme: "dark",
    reducedMotion: "no-preference",
  });
  await context.addInitScript(pageSetup, HIDDEN_PROJECTS);
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
    await page.mouse.move(900, 700);
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
    await hold(2800);
    await page.mouse.move(1200, 820, { steps: 20 });
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
    await hold(900);
    const firstPage = page.locator("canvas").first();
    const pbox = await firstPage.boundingBox();
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

  for (const { name, width, crf } of OUTPUTS) {
    const height = Math.round((width * VIEWPORT.height) / VIEWPORT.width / 2) * 2;
    const vf = [
      `fps=${FPS}`,
      `scale=${width}:${height}:flags=lanczos`,
      `fade=t=in:st=0:d=0.4:color=black`,
      `fade=t=out:st=${(total - 0.5).toFixed(2)}:d=0.5:color=black`,
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
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", "1", "-i", path.join(OUT_DIR, "hero-md.mp4"),
    "-frames:v", "1", "-q:v", "3", path.join(OUT_DIR, "hero-poster.jpg")]);
  await rm(FRAME_DIR, { recursive: true, force: true });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
