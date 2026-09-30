#!/usr/bin/env node
/**
 * Records the landing page's hero video from the REAL app.
 *
 * The clip is one conversation with three answers, after a glimpse of the
 * paper library: a question answered with sentence-level citations and one
 * citation opened to its source passage; a question the library cannot
 * answer, refused rather than answered from general knowledge; and a
 * comparison scoped to two papers picked with the composer's real `@`
 * mention list, its scope line on screen while the answer is written. Every
 * frame is the running app on real data, never a mock -- so when the UI
 * changes, re-run this and the hero stays true.
 *
 * Before encoding, the script reads the conversation back and refuses to
 * write a clip whose refusal is not the app's refusal or whose scoped answer
 * cites a paper outside its two mentions.
 *
 * The default PROJECT_ID is "UAV Swarm Search", a project in the dev database
 * kept for recording: 13 papers, every one added the way the demo adds papers
 * (`source: "upload"`, then the PDF posted to `.../ingest`), so no "Linked"
 * source label appears in frame. Re-records reuse it; if the database is ever
 * rebuilt, recreate it the same way and point PROJECT_ID at it. The desktop
 * sidebar is recorded collapsed to its icon rail.
 *
 * Requirements: the dev stack up (`make up`) with a working LLM behind it,
 * Google Chrome installed, ffmpeg on PATH.
 *
 *   node scripts/record-hero.mjs
 *
 * Env overrides: APP_URL, API_URL, PROJECT_ID, QUESTION, OFF_TOPIC,
 * MENTION_A / MENTION_B (the text typed after `@` to find each paper),
 * SCOPED_QUESTION, FORMATS (default "desktop,phone"), and
 * THEMES (default "dark,light"): one full recording per theme, so the
 * landing page can play the clip that matches the visitor's theme.
 *
 * How it works: Chrome's DevTools screencast delivers a frame on every
 * repaint with its timestamp. Each frame is stamped with the playback SPEED
 * in force when it arrived, so slow waits (page loads, streaming answers)
 * play back fast while the moments that matter play in real
 * time. Frames closer than 1/30 s of playback time are dropped, then ffmpeg
 * lays the kept frames out on that remapped timeline and encodes three
 * sizes. Every conversation the recording creates is deleted again
 * afterwards, so recording leaves no conversation behind.
 */
import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = process.env.APP_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
// "UAV Swarm Search": a project kept for recording (see the header comment).
const PROJECT = process.env.PROJECT_ID ?? "e3decc1e-5af1-4afa-be61-d0da49a71c30";
const QUESTION =
  process.env.QUESTION ?? "What reward function do the multi-UAV search agents learn from?";
// In the library's own domain, but a detail none of its papers reports: the
// answer must be the refusal (5 of 5 probes, 2026-09-30).
const OFF_TOPIC = process.env.OFF_TOPIC ?? "What battery chemistry do the UAVs in these papers use?";
const REFUSAL = "The ingested documents do not cover this.";
// Typed after `@` to filter the mention list; each must match one title first.
const MENTION_A = process.env.MENTION_A ?? "Evolutionary";
const MENTION_B = process.env.MENTION_B ?? "Pursuit";
const SCOPED_QUESTION =
  process.env.SCOPED_QUESTION ?? "How do these two approaches coordinate the UAVs?";

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
      { tier: "xl", width: 1920, crf: 26 },
      { tier: "md", width: 1280, crf: 26 },
    ],
    poster: (theme) => `hero-${theme}-poster.jpg`,
    posterWidth: 1280,
  },
  phone: {
    viewport: { width: 400, height: 700 },
    dpr: 3,
    outputs: [{ tier: "sm", width: 600, crf: 27 }],
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

/**
 * Runs in the page before any app script: the theme, the sidebar collapsed to
 * its icon rail, a fake cursor, no dev chrome.
 */
function pageSetup({ hiddenProjects, theme }) {
  try {
    localStorage.setItem("theme", theme);
    localStorage.setItem("rx.sidebar.collapsed", "1");
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
      #rx-tick { position: fixed; right: 0; bottom: 0; width: 1px; height: 1px;
        z-index: 2147483647; pointer-events: none; opacity: .02; background: #000; }
      #rx-tick.on { background: #fff; }
      @keyframes rx-click { 0% { opacity: 1; transform: scale(.4); }
        100% { opacity: 0; transform: scale(1.8); } }`;
    document.head.appendChild(style);
    const cursor = document.createElement("div");
    cursor.id = "rx-cursor";
    cursor.innerHTML = `<div class="ring"></div><svg width="22" height="22" viewBox="0 0 24 24">
      <path d="M4 2.5 L4 19 L8.6 14.8 L11.6 21.5 L14.3 20.3 L11.4 13.8 L17.6 13.8 Z"
        fill="white" stroke="black" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.body.appendChild(cursor);
    const tick = document.createElement("div");
    tick.id = "rx-tick";
    document.body.appendChild(tick);
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

  const conversationsUrl = `${API}/v1/projects/${PROJECT}/conversations`;
  // Every conversation that exists before recording; anything else in the
  // project afterwards was created by this run and is deleted in `finally`,
  // even when the run dies before its URL was read.
  const before = new Set((await (await fetch(conversationsUrl)).json()).map((c) => c.id));

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
  /**
   * Change the playback speed. A frame only arrives on a repaint, and the gap
   * before it is charged at the speed in force when it ARRIVES -- so a still
   * wait at 7x followed by a switch to 1x would play the whole wait in real
   * time. Forcing one repaint first closes the gap at the old speed.
   */
  async function setSpeed(next) {
    await page
      .evaluate(() => document.getElementById("rx-tick")?.classList.toggle("on"))
      .catch(() => {});
    await hold(120);
    speed = next;
  }
  /** Glide the fake cursor to the centre of an element. */
  async function glideTo(locator, { steps = 28, dx = 0, dy = 0 } = {}) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) throw new Error(`no box for ${locator}`);
    await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps });
  }

  let conversationId = null;
  const problems = [];

  /** Wait for the turn just sent to finish: the composer locks, then unlocks. */
  async function turnDone() {
    const composer = page.locator("textarea");
    await page
      .waitForFunction(() => document.querySelector("textarea")?.disabled, null, { timeout: 3000 })
      .catch(() => {});
    await page.waitForFunction(() => !document.querySelector("textarea")?.disabled, null, {
      timeout: 240000,
    });
    return composer;
  }
  /** Type `@query` into the composer and pick the first paper the list offers. */
  async function mention(query) {
    await page.keyboard.type(`@${query}`, { delay: 60 });
    const option = page.getByRole("listbox", { name: "Mention a paper" }).getByRole("option").first();
    await option.waitFor();
    await hold(500);
    await glideTo(option, { steps: 18 });
    await hold(250);
    await option.click();
    await hold(500);
  }

  try {
    // Warm every route once, uncaptured, so dev-mode compiles never show.
    for (const p of ["papers", "chat"]) {
      await page.goto(`${APP}/admin/research/${PROJECT}/${p}`);
      await page.waitForLoadState("networkidle").catch(() => {});
    }

    // ── scene 1: the library ─────────────────────────────────────────────────
    await page.goto(`${APP}/admin/research/${PROJECT}/papers`);
    await page.getByText("papers in this library").waitFor();
    await page.mouse.move(VIEWPORT.width * 0.62, VIEWPORT.height * 0.78);
    await hold(600);
    await startCapture();
    await hold(1200);
    const row = page.locator("main").getByText("Cooperative Multi-Target Search", { exact: false }).first();
    await row.scrollIntoViewIfNeeded().catch(() => {});
    const target = (await row.count()) ? row : page.locator("main table tbody tr, main [role=row]").nth(2);
    await glideTo(target);
    await hold(250);
    await target.click();
    await page.getByText("searchable").first().waitFor({ timeout: 15000 }).catch(() => {});
    await hold(1500);

    // ── scene 2: ask, and watch the cited answer arrive ─────────────────────
    const chatTab = page.getByRole("link", { name: "Chat" }).first();
    await glideTo(chatTab);
    await hold(200);
    await setSpeed(25);
    await chatTab.click();
    const box = page.getByPlaceholder(/Ask a question about this project/);
    await box.waitFor();
    await hold(300);
    await setSpeed(1);
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
    await setSpeed(7); // the answer streams at seven times speed
    await turnDone();
    if (!(await page.locator('button[aria-label^="Citation 1,"]').count())) {
      const answer = await page.locator("main").innerText().catch(() => "");
      throw new Error(`the first answer carries no citations:\n${answer.slice(-800)}`);
    }
    await hold(1500);
    await setSpeed(1);
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
    await hold(1600);

    // ── scene 4: a question the library cannot answer is refused ────────────
    const composer = page.locator("textarea");
    await glideTo(composer, { steps: 24 });
    await composer.click();
    await hold(300);
    await page.keyboard.type(OFF_TOPIC, { delay: 45 });
    await hold(500);
    const ask = page.getByRole("button", { name: "Ask", exact: true });
    await glideTo(ask);
    await hold(200);
    await ask.click();
    await hold(900);
    await setSpeed(7);
    await turnDone();
    await hold(400);
    await setSpeed(1);
    const refusal = page.locator("main").getByText(REFUSAL, { exact: false }).last();
    await refusal.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
    await hold(2600);

    // ── scene 5: a comparison scoped to two papers named with `@` ───────────
    await glideTo(composer, { steps: 24 });
    await composer.click();
    await hold(300);
    await mention(MENTION_A);
    await mention(MENTION_B);
    await page.keyboard.type(SCOPED_QUESTION, { delay: 40 });
    await hold(600);
    await glideTo(ask);
    await hold(200);
    await ask.click();
    await hold(700);
    // The scope line shows while the turn is retrieving and writing; hold it
    // in real time, because on a mention turn it does not outlive the answer.
    await setSpeed(7);
    const scopeLine = page.getByText(/You named 2 papers/).last();
    await scopeLine.waitFor({ timeout: 120000 });
    await setSpeed(1);
    await scopeLine.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
    await hold(2400);
    await setSpeed(7);
    await turnDone();
    await hold(400);
    await setSpeed(1);
    // The answer from its top, then the two papers it cites.
    const lastQuestion = page.locator("main").getByText(SCOPED_QUESTION, { exact: false }).last();
    await lastQuestion.evaluate((el) => el.scrollIntoView({ block: "start", behavior: "smooth" }));
    await hold(2600);
    const sources = page.getByText("Sources", { exact: true }).last();
    await sources.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
    await hold(2800);

    await cdp.send("Page.stopScreencast");
    await hold(300);

    // ── read the conversation back: the clip must show what it claims ───────
    const conv = await (await fetch(`${conversationsUrl}/${conversationId}`)).json();
    const msgs = conv.messages ?? [];
    const answers = msgs.filter((m) => m.role === "assistant");
    const scopedAsk = msgs.filter((m) => m.role === "user").at(-1);
    if (answers.length !== 3) problems.push(`expected 3 answers, got ${answers.length}`);
    const [cited, refused, scoped] = answers;
    if (!cited?.citations?.length) problems.push("the first answer carries no citations");
    console.log(`refusal answer: ${JSON.stringify(refused?.content)}`);
    if (refused?.content.trim() !== REFUSAL) problems.push("the off-topic answer is not the refusal");
    const named = new Set(scopedAsk?.mentions ?? []);
    const citedPapers = new Set((scoped?.citations ?? []).map((c) => c.paper_id));
    console.log(`scoped turn: mentions ${[...named].join(", ")}; cited ${[...citedPapers].join(", ")}`);
    if (named.size !== 2) problems.push(`the scoped question names ${named.size} papers`);
    if (!citedPapers.size) problems.push("the scoped answer carries no citations");
    for (const id of citedPapers) {
      if (!named.has(id)) problems.push(`the scoped answer cites ${id}, outside its mentions`);
    }
  } finally {
    const after = await fetch(conversationsUrl)
      .then((r) => r.json())
      .catch(() => []);
    const created = new Set(after.map((c) => c.id).filter((id) => !before.has(id)));
    if (conversationId) created.add(conversationId);
    for (const id of created) {
      const res = await fetch(`${conversationsUrl}/${id}`, { method: "DELETE" }).catch((e) => e);
      console.log(`deleted recording conversation ${id}:`, res.status ?? res);
    }
    await browser.close();
  }

  if (problems.length) throw new Error(`not encoding this take:\n  ${problems.join("\n  ")}`);

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
