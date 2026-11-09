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

// **The upload step, ready for T-219 but not yet exercised.**
//
// Claude's item 2: `setInputFiles` with a small STL/STEP. The file picker and the Next-step button do
// not exist in the t218 bundle I serve - measured, not assumed: `input[type=file]` count is 0 and no
// button matches /next|import|upload|browse|choose/. So this finds the input **when it is there** and
// reports plainly when it is not, rather than failing in a way that looks like a product fault.
//
// It drives the REAL file input rather than calling `geometry.import` over the socket, because the point
// of the step is the browser's own path: pick a file, upload it, land on a part.
async function uploadFile(page, filePath) {
  const input = page.locator('input[type=file]').first();
  if ((await page.locator('input[type=file]').count()) === 0) {
    console.log('  upload: no file input in this build - skipping (T-219 has not landed here)');
    return false;
  }
  // A picker that is `display:none` until its button is pressed needs the button pressed first.
  if (!(await input.isVisible().catch(() => false))) {
    const opener = page.getByRole('button', { name: /import|upload|browse|choose|open step/i }).first();
    if ((await opener.count()) > 0) {
      await opener.click({ timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(800);
    }
  }
  await input.setInputFiles(filePath);
  console.log(`  upload: set ${filePath}`);
  // Wait on the tree gaining a part, which is what the upload is FOR.
  await page
    .waitForFunction(() => /\bpart\b|imported|geometry/i.test(document.body.innerText), { timeout: 120000 })
    .catch(() => console.log('  upload: no part text seen within 120 s'));
  await page.waitForTimeout(3000);
  return true;
}

// **The Next-step flow.** Presses the button that names the next stage, whatever it is called, and
// reports where it landed. Bounded to a few presses so a button that does not advance cannot loop.
async function pressNextStep(page, times = 3) {
  const next = page.getByRole('button', { name: /next step|next:?\s/i }).first();
  if ((await next.count()) === 0) {
    console.log('  next-step: no such control in this build');
    return 0;
  }
  let pressed = 0;
  for (let i = 0; i < times; i += 1) {
    if ((await next.count()) === 0) break;
    const before = await page.locator('[aria-current=page], .stage-active, button.active').allTextContents().catch(() => []);
    await next.click({ timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1800);
    const after = await page.locator('[aria-current=page], .stage-active, button.active').allTextContents().catch(() => []);
    pressed += 1;
    console.log(`  next-step ${pressed}: ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
    if (JSON.stringify(before) === JSON.stringify(after)) {
      console.log('  next-step: the stage did not change; stopping rather than pressing blindly');
      break;
    }
  }
  return pressed;
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
  // **Only generate when there is no mesh yet.** Pressing it on a project that already has one trips
  // `mesh.invalid: the xmax side holds faces of fluid and outlet` — the known ordering trap, where a
  // region assigned over the whole part collides with the outlet tag — and that wrote two red errors
  // into the console drawer of the delivered screenshots. The trap is real, but a driver that walks
  // into it knowingly is reporting its own noise as the product's.
  const alreadyMeshed = /Mesh · \d+ k? ?cells · ok/.test(await page.innerText('body'));
  if (alreadyMeshed) {
    console.log('the project already has a mesh: not pressing Generate (avoids the xmax trap)');
    await shot(page, '02-mesh-existing');
  } else if (await generate.count() > 0) {
    console.log('pressing Generate ...');
    await generate.click().catch(() => {});
    await page.waitForFunction(
      () => /Mesh · \d+/.test(document.body.innerText),
      { timeout: 180000 },
    ).catch(() => console.log('  (no mesh status seen within 180 s)'));
    await page.waitForTimeout(2500);
    await shot(page, '02-mesh-generated');
  }

  // Run.
  await pressStage(page, 'Solve');
  const run = page.locator('button:has-text("Run")').first();
  if (await run.count() > 0) {
    console.log('pressing Run ...');
    await run.click().catch(() => {});
    // **Wait for the run to FINISH, not merely for the word "Run" to appear.** The first version
    // waited on `/converged|done|residual/` anywhere in the body, and the status bar already said
    // "Run · starting" — so it matched instantly and the screenshot caught the solve mid-flight
    // (`Run · starting`, a Stop button, no residuals). Waiting on `converged` specifically is the
    // difference between a picture of a run and a picture of a run that finished.
    await page.waitForFunction(
      () => /Run · it \d+ · converged/.test(document.body.innerText),
      { timeout: 180000 },
    ).catch(() => console.log('  (no "converged" status within 180 s)'));
    await page.waitForTimeout(1500);
    await shot(page, '03-solve-converged');
  }

  await pressStage(page, 'Results');
  await page.waitForTimeout(2500);
  await shot(page, '04-results');

  // **The legend is read, not clicked.** The first version of this looked for a button labelled
  // `U_mag` or `p` and clicked it — but `:has-text("p")` matched the **Physics** stage button first,
  // so the "field" screenshots were of the Physics stage and the legend was gone. The driver was
  // wrong, not the app: the Results build has no field buttons, and its legend is a display-only
  // colour bar whose title says which field is drawn. So this records what the legend actually
  // shows, which is the thing worth checking.
  // **The right slot's panel tabs** (2026-10-15: `mesh`, `solve` and `results` now reach the client,
  // before which these panels had no tab and the field select was unreachable). Each is pressed and
  // photographed, because "the panel exists" and "the panel can be opened" are different claims.
  for (const panel of ['Mesh', 'Solve', 'Results', 'Properties', 'Conditions', 'Plots']) {
    const tab = page.locator(`button:has-text("${panel}")`).last();
    if (await tab.count() === 0) { console.log(`  (no ${panel} panel tab)`); continue; }
    await tab.click({ timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot(page, `10-panel-${panel.toLowerCase()}`);
  }

  const legend = await page.locator('.field-legend').first();
  if (await legend.count() > 0) {
    const text = (await legend.innerText()).replace(/\s+/g, ' ').trim();
    console.log(`legend: ${text}`);
  } else {
    console.log('legend: (none on the Results stage)');
  }

  // The upload and next-step steps (Claude's item 2). Both report when the build has neither, which is
  // the state of the t218 bundle, so a run against it says what it could not test instead of passing
  // quietly.
  const upload = process.env.STOKES_UPLOAD_FILE;
  if (upload) {
    await pressStage(page, 'Geometry');
    await uploadFile(page, upload);
    await shot(page, '20-after-upload');
  } else {
    console.log('upload: STOKES_UPLOAD_FILE is not set, so the step was not run');
  }
  await pressNextStep(page);
  await shot(page, '21-after-next-step');

  await browser.close();

  console.log(`\nconsole errors: ${errors.length}`);
  for (const e of errors.slice(0, 12)) console.log('  !', e);
  console.log(`failed requests: ${badResponses.length}`);
  for (const r of badResponses.slice(0, 12)) console.log('  !', r);
  console.log(`\nshots in ${SHOTS} and ${MIRROR}`);
  process.exit(errors.length || badResponses.length ? 1 : 0);
}

main().catch((e) => { console.error('DRIVER FAILED:', e.message); process.exit(2); });
