// The browser half of the G-301 post-processing audit. Run from `web/`.
//
//     node shot_results.mjs "<url-with-token>" <out-dir>
//
// **It changes no product code and adds no hook.** Every step drives the real UI, records a
// screenshot, the console's errors and warnings, and every `/api/results/*` request with its status -
// because a feature that renders while throwing is not a feature that works, and a 503 cached as "no
// data" looks identical to an empty field unless the requests are written down.
//
// **It exits non-zero when a step cannot be performed**, naming the step: a control that cannot be
// found is a finding, not a reason to carry on quietly.

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const url = process.argv[2];
const OUT = process.argv[3] ?? ".";
if (!url) { console.error("usage: node shot_results.mjs <url-with-token> <out-dir>"); process.exit(2); }
mkdirSync(OUT, { recursive: true });

const console_ = [];
const requests = [];
const steps = [];

const browser = await chromium.launch({ args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "warning") console_.push({ type: m.type(), text: m.text().slice(0, 200) });
});
page.on("response", (r) => {
  const u = r.url();
  if (u.includes("/api/results") || u.includes("/api/geometry")) requests.push({ url: u.replace(/token=[^&]+/, "token=…").slice(-70), status: r.status() });
});

async function shot(name) {
  await page.screenshot({ path: join(OUT, `${name}.png`) });
  console.log(`  shot ${name}`);
}

/** One audited step: run the action, screenshot, and record what the page said. */
async function step(n, id, run) {
  const before = { console: console_.length, requests: requests.length };
  let problem = null;
  try {
    await run();
  } catch (err) {
    problem = String(err).slice(0, 200);
  }
  await shot(`${String(n).padStart(2, "0")}-${id}`);
  const record = {
    step: n, id, problem,
    console: console_.slice(before.console),
    requests: requests.slice(before.requests),
  };
  steps.push(record);
  console.log(`  ${n}. ${id}: ${problem ? "COULD NOT PERFORM - " + problem : "performed"}`);
}

await page.goto(url, { waitUntil: "load", timeout: 90000 });
await page.waitForTimeout(26000);

// The Results stage, entered the way a user enters it.
await step(1, "results-stage", async () => {
  await page.locator('button:has-text("Results")').first().click();
  await page.waitForTimeout(6000);
});

// The fields the run produced, read through the store the app already exposes.
const fields = await page.evaluate(async () => {
  const s = window.__stokes;
  if (!s) return { error: "no window.__stokes" };
  try {
    const reply = await s.getState().execute("run.fields", {}, { silent: true });
    return { hasFields: true, keys: Object.keys(reply ?? {}).slice(0, 8), fields: (reply?.fields ?? []).map((f) => f.name ?? f) };
  } catch (err) {
    return { error: String(err).slice(0, 160) };
  }
});

console.log(JSON.stringify({ audit: "G-301", steps, fields, consoleErrors: console_.length, requests: requests.length }, null, 1));
await browser.close();
const failed = steps.filter((s) => s.problem !== null);
if (failed.length > 0) {
  console.error(`could not perform ${failed.length} step(s): ${failed.map((s) => `${s.step}. ${s.id}`).join(", ")}`);
  process.exit(1);
}
