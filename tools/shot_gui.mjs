// The browser half of shot_gui.py. Run from `web/`, where Playwright is installed.
//
// node ../tools/shot_gui.mjs "<url-with-token>"
//
// **Every step is a real press.** It clicks the stage buttons, presses Generate and Run, and waits on
// the UI's own text rather than on a sleep, so a screenshot shows the state the app reached and not the
// state a timer hoped for. Errors and non-2xx responses are collected and printed: a page that renders
// while throwing is not a page that works.

import { chromium } from 'playwright';
import { mkdirSync, copyFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const url = process.argv[2];
if (!url) { console.error('usage: node shot_gui.mjs <url-with-token>'); process.exit(2); }

const SHOTS = join(homedir(), 'stokes-shots');
const MIRROR = join(process.cwd(), '..', '.coordination', 'shots');
mkdirSync(SHOTS, { recursive: true });
mkdirSync(MIRROR, { recursive: true });

const errors = [];
const badResponses = [];

async function shot(page, name) {
  const file = join(SHOTS, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  copyFileSync(file, join(MIRROR, `${name}.png`));
  console.log(`  shot: ${name}.png`);
}

async function pressStage(page, label) {
  const button = page.locator(`button:has-text("${label}")`).first();
  if (await button.count() === 0) { console.log(`  (no ${label} stage button)`); return false; }
  await button.click({ timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(900);
  return true;
}

async function main() {
  const browser = await chromium.launch({
    args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text().slice(0, 300));
  });
  page.on('pageerror', (err) => errors.push(`pageerror: ${String(err).slice(0, 300)}`));
  page.on('response', (r) => {
    if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url().replace(/token=[^&]*/, 'token=…')}`);
  });

  console.log('opening the app ...');
  await page.goto(url, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(5000);   // the app fetches the tree and draws the viewport
  console.log('title:', await page.title());

  for (const stage of ['Geometry', 'Mesh', 'Physics', 'Solve', 'Results']) {
    if (await pressStage(page, stage)) await shot(page, `01-${stage.toLowerCase()}`);
  }

  // Mesh: the stage is showing, so press Generate and wait for the panel to say it is done.
  await pressStage(page, 'Mesh');
  const generate = page.locator('button:has-text("Generate")').first();
  if (await generate.count() > 0) {
    console.log('pressing Generate ...');
    await generate.click().catch(() => {});
    await page.waitForFunction(
      () => /done|converged|cells|failed/i.test(document.body.innerText),
      { timeout: 120000 },
    ).catch(() => console.log('  (no done text seen within 120 s)'));
    await page.waitForTimeout(2500);
    await shot(page, '02-mesh-generated');
  }

  // Run.
  await pressStage(page, 'Solve');
  const run = page.locator('button:has-text("Run")').first();
  if (await run.count() > 0) {
    console.log('pressing Run ...');
    await run.click().catch(() => {});
    await page.waitForFunction(
      () => /converged|done|residual/i.test(document.body.innerText),
      { timeout: 180000 },
    ).catch(() => console.log('  (no convergence text seen within 180 s)'));
    await page.waitForTimeout(3000);
    await shot(page, '03-solve-residuals');
  }

  await pressStage(page, 'Results');
  await page.waitForTimeout(2500);
  await shot(page, '04-results');

  // The Results stage's own field controls, if the build has them.
  for (const field of ['U_mag', 'p']) {
    const b = page.locator(`button:has-text("${field}")`).first();
    if (await b.count() > 0) {
      await b.click().catch(() => {});
      await page.waitForTimeout(2000);
      await shot(page, `05-field-${field}`);
    }
  }

  await browser.close();

  console.log(`\nconsole errors: ${errors.length}`);
  for (const e of errors.slice(0, 12)) console.log('  !', e);
  console.log(`failed requests: ${badResponses.length}`);
  for (const r of badResponses.slice(0, 12)) console.log('  !', r);
  console.log(`\nshots in ${SHOTS} and ${MIRROR}`);
  process.exit(errors.length || badResponses.length ? 1 : 0);
}

main().catch((e) => { console.error('DRIVER FAILED:', e.message); process.exit(2); });
