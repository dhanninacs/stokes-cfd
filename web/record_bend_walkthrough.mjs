// Records the walkthrough "a 90 degree duct bend, from a sketch to an optimised shape" through the GUI
// on a NEW project, in Guided mode: New project > Sketch (an L-shaped duct whose two corner cuts are
// the dimensions c and d) > pull 10 mm > Fluid (Air) > Boundaries (inlet 0.3 m/s, outlet 0 Pa) >
// Mesh (1 mm, snappyHexMesh) > Run > Results > Optimise (pressure loss over c and d: Check gradient,
// then the cycles) > Apply the best point > mesh and run the final shape.
//
//   set STOKES_WALK_VIDEO=<folder>   (walk_lib records the page there as an .mp4, with an installed ffmpeg)
//   node record_bend_walkthrough.mjs "<url-with-token>" "<projects folder>" [shots] [--cell=1] [--cycles=5] [--speed=0.3] [--no-check] [--fd]
//
// A server with NO project must be running. Every step is captioned on the page (a pill the script
// adds; it is not part of the app), and `marks.json` in the shots folder lists when each caption went
// up and when each wait began and ended, so `cut_walkthrough.mjs` can run the waits fast.
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const has = (name) => args.includes(`--${name}`);
const [url, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!url || !folder) { console.error('usage: node record_bend_walkthrough.mjs <url-with-token> <projects folder> [shots] [--cell=1] [--cycles=5] [--speed=0.3] [--no-check] [--fd]'); process.exit(2); }
const shots = shotsArg ?? join(process.cwd(), '..', '.local', 'walkthrough');
const cell = flag('cell', '1');
const cycles = flag('cycles', '5');
const speed = flag('speed', '0.3');

const w = await openWalk(url, folder, shots);
const { page, visible } = w;
// The marks count from the recording's first frame, so a mark's time is a time in the video.
const t0 = w.recorder?.t0 ?? Date.now();
const marks = [];
const mark = (kind, label) => { marks.push({ t: (Date.now() - t0) / 1000, kind, label }); writeFileSync(join(shots, 'marks.json'), JSON.stringify(marks, null, 1)); };
const card = page.locator('.guided-card');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
const pause = (ms) => page.waitForTimeout(ms);

/** The caption pill: what the viewer is looking at, in a sentence. */
async function caption(text, hold = 2200) {
  console.log(`\n# ${text}`);
  mark('caption', text);
  await page.evaluate((t) => {
    let el = document.getElementById('walk-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'walk-caption';
      el.style.cssText = 'position:fixed;left:50%;bottom:92px;transform:translateX(-50%);z-index:2147483000;max-width:820px;padding:10px 18px;border-radius:12px;background:rgba(16,20,26,.92);color:#fff;font:600 17px/1.35 Inter,system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.35);pointer-events:none;text-align:center;transition:opacity .25s';
      document.body.appendChild(el);
    }
    el.textContent = t;
    el.style.opacity = '1';
  }, text);
  await pause(hold);
}

/** A wait the cut runs fast: from now until `test` holds. */
async function wait(label, test, ms) {
  mark('wait-start', label);
  const started = Date.now();
  let ok = false;
  while (Date.now() - started < ms) { if (await test()) { ok = true; break; } await pause(700); }
  mark('wait-end', label);
  if (!ok) { w.fail(`timed out waiting for ${label}`); throw new Error(`timeout: ${label}`); }
  console.log(`  ${label}: ${Math.round((Date.now() - started) / 1000)} s`);
}

/** Type into a field the way a person does: click, select all, type, leave. */
async function type(locator, text, leave = 'Tab') {
  await locator.click();
  await page.keyboard.press('Control+A');
  await page.keyboard.type(String(text), { delay: 45 });
  await page.keyboard.press(leave);
  await pause(350);
}

try {
  // ------------------------------------------------------------------ 1. a new project
  await page.goto(url);
  await pause(1200);
  // A project left open by an earlier take is closed, so the take starts on the start screen.
  const open = await w.rpc('project.info').catch(() => null);
  if (open && (open.path || open.name)) {
    await w.rpc('project.close', { discard: true }).catch(() => w.rpc('project.close').catch(() => {}));
    await page.goto(url);
    await pause(1200);
  }
  await caption('Stokes: optimise a 90° duct bend, from a sketch to the adjoint', 3200);
  await caption('1 · A new project');
  await visible(page.getByText('New project', { exact: true })).click();
  await pause(800);
  const inputs = page.locator('input:visible');
  await type(inputs.nth(0), `bend-${Date.now() % 100000}`);
  await page.evaluate((dir) => {
    const input = Array.from(document.querySelectorAll('input')).filter((x) => x.offsetParent)[1];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, dir);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, folder);
  await pause(700);
  await visible(page.getByText('Create', { exact: true })).click();
  await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  await pause(1000);
  // The tree stays put away: the cards do the set-up.
  if (await page.locator('[role="tree"]').count()) { await page.locator('[data-ws="tree-toggle"]').click(); await pause(600); }

  // ------------------------------------------------------------------ 2. the sketch
  await caption('2 · Geometry: sketch the duct on the XY plane');
  await page.locator('[data-ws="guided-create"]').click();
  await page.locator('[data-ws="build-ribbon"]').waitFor();
  await pause(500);
  await page.locator('[data-ws="build-sketch"] button[aria-label^="Sketch"]').first().click();
  await page.locator('[data-ws="sketch-editor"]').waitFor();
  await pause(900);
  await page.locator('[data-ws="sketch-plane-xy"]').click();
  await pause(900);
  await caption('Two named dimensions: the inner corner cut c and the outer corner cut d');
  for (const [name, value] of [['c', '3'], ['d', '3']]) {
    await type(page.getByLabel('New dimension name'), name);
    await type(page.getByLabel('New dimension value'), value, 'Enter');
    await pause(500);
  }
  await caption('An L-shaped duct, 10 mm wide, drawn with the Line tool');
  await page.locator('[data-ws="sketch-tool-line"]').click();
  for (const [u, v] of [[0, 0], [35, 0], [40, 5], [40, 40], [30, 40], [30, 15], [25, 10], [0, 10], [0, 0]]) { await w.at(u, v); await pause(420); }
  // The chain ended when it met its start; Select is where the table's cells are typed.
  await page.locator('[data-ws="sketch-tool-select"]').click();
  await pause(600);
  await caption('The corner points follow the dimensions: 40 − d, d, 10 + c and 30 − c');
  for (const [label, text] of [['line 1 end u', '40 - d'], ['line 2 end v', 'd'], ['line 5 end v', '10 + c'], ['line 6 end u', '30 - c']]) {
    await type(page.getByLabel(label, { exact: true }), text);
    await pause(500);
  }
  const status = await page.locator('[data-ws="sketch-status"]').innerText();
  console.log(`  sketch: ${status.split('\n')[0]}`);
  if (!/^1 closed loop/.test(status)) w.fail(`the sketch is not one closed loop: ${status}`);
  await w.shot('sketch');
  await caption('Finish pulls the profile into a body');
  await page.locator('[data-ws="sketch-finish"]').click();
  await page.locator('[data-ws="sketch-editor"]').waitFor({ state: 'detached', timeout: 20000 });
  await wait('the body', async () => /is the fluid/.test(await cardText()) || (await page.locator('[data-ws="pull-chip"]').count()) > 0, 120000);
  await pause(1500);
  // The pull handle shows with the ribbon closed and the Geometry card open: close, frame, then pull.
  await page.locator('[data-ws="build-close"]').click().catch(() => {});
  await pause(600);
  await visible(page.getByText('Geometry', { exact: true })).click().catch(() => {});
  await wait('the fluid body', async () => /is the fluid/.test(await cardText()), 60000);
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await pause(1500);
  const chip = page.locator('[data-ws="pull-chip"]');
  // The depth decides the areas the two end faces are picked by.
  await chip.waitFor({ timeout: 15000 }).catch(() => {});
  let depth = 20;
  if (await chip.count()) {
    depth = 10;
    await caption('The depth is typed in the view: 10 mm');
    await chip.click();
    await type(page.locator('[data-ws="pull-field"]'), '10', 'Enter');
    await wait('the 10 mm pull', async () => { await pause(2500); return !(await page.locator('[data-ws="build-status"]').count()); }, 120000);
    await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
    await pause(1500);
  }
  await w.shot('geometry');

  // ------------------------------------------------------------------ 3. fluid
  await caption('3 · Fluid: air, laminar');
  await visible(card.getByText('Next: Fluid', { exact: true })).click();
  await pause(900);
  await visible(card.getByText('Air', { exact: true })).click();
  await wait('the material', async () => /Next: Boundaries/.test(await cardText()), 30000);
  await pause(900);

  // ------------------------------------------------------------------ 4. boundaries
  await caption(`4 · Boundaries: an inlet at ${speed} m/s and a pressure outlet; the rest are walls`);
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  await pause(1200);
  const pickFace = async (pattern) => { await card.getByText(pattern).first().locator('xpath=..').getByRole('button', { name: 'Pick' }).click(); await pause(900); };
  // The two 10 mm x depth ends: "100 mm²" below a square centimetre, "2 cm²" above.
  const ends = depth === 10 ? '(?:1 cm²|100 mm²)' : `${depth / 10} cm²`;
  await pickFace(new RegExp(`facing −X · ${ends}`));
  await type(visible(card.locator('input')), speed);
  await visible(card.getByRole('button', { name: 'Inlet' })).click();
  await pause(1500);
  await pickFace(new RegExp(`facing \\+Y · ${ends}`));
  await visible(card.getByRole('button', { name: 'Outlet' })).click();
  await wait('two boundaries', async () => /2 boundaries/.test(await cardText()), 30000);
  await pause(1200);
  await w.shot('boundaries');

  // ------------------------------------------------------------------ 5. mesh
  await caption(`5 · Mesh: ${cell} mm cells with snappyHexMesh`);
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  await pause(1200);
  await type(card.getByLabel('Cell size in millimetres'), cell);
  await type(card.locator('[data-ws="guided-mesh-cores"]'), '4');
  await pause(900);
  await card.locator('[data-ws="guided-generate"]').click();
  await wait('the mesh', async () => /Next: Run/.test(await cardText()), 900000);
  await card.getByText('Show mesh cells').click().catch(() => {});
  await pause(2500);
  await w.shot('mesh');
  await card.getByText('Show mesh cells').click().catch(() => {});

  // ------------------------------------------------------------------ 6. run
  await caption('6 · Run: 500 iterations at most, stopping at a residual of 1e-5');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await pause(1200);
  await type(card.locator('[data-ws="guided-iterations"]'), '500');
  await type(card.locator('[data-ws="guided-residual-target"]'), '1e-5');
  await type(card.locator('[data-ws="guided-cores"]'), '4');
  await pause(800);
  await card.locator('[data-ws="guided-run"]').click();
  await wait('the run', async () => /Next: Results/.test(await cardText()), 900000);
  await pause(1500);
  await w.shot('run');

  // ------------------------------------------------------------------ 7. results
  await caption('7 · Results: the speed on the mid-plane, and the pressure drop');
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await pause(5000);
  await w.shot('results-speed');
  const before = await page.locator('.field-legend').innerText().catch(() => '');
  console.log(`  legend: ${before.split('\n').join(' | ')}`);
  await visible(card.getByText('Pressure', { exact: true })).click().catch(() => {});
  await pause(3500);
  await w.shot('results-pressure');
  await visible(card.getByText('Velocity', { exact: true })).click().catch(() => {});
  await pause(1500);

  // ------------------------------------------------------------------ 8. optimise
  await caption('8 · Optimise: lower the pressure loss by changing c and d');
  await visible(card.getByText('Next: Optimise', { exact: true })).click();
  await pause(1500);
  await card.locator('[data-ws="guided-optimise-shape"]').click();
  const panel = page.locator('[data-ws="optimise-panel"]');
  await panel.waitFor({ timeout: 15000 });
  await pause(1500);
  const choose = async (n, name, low, high) => {
    const select = panel.getByLabel(`Variable ${n}`, { exact: true });
    const value = await select.locator('option').evaluateAll((opts, wanted) => (opts.find((o) => o.label.endsWith(wanted)) ?? {}).value ?? '', name);
    if (!value) { w.fail(`the Optimise panel does not offer "${name}"`); return; }
    await select.selectOption(value);
    await pause(700);
    await type(panel.getByLabel(new RegExp(`^Variable ${n} lower bound`)), low);
    await type(panel.getByLabel(new RegExp(`^Variable ${n} upper bound`)), high);
  };
  await choose(1, 'dimension c', '1', '8');
  await panel.getByText('Add variable').click();
  await pause(600);
  await choose(2, 'dimension d', '0.5', '8');
  await type(panel.getByLabel('Maximum cycles'), cycles);
  if (has('fd')) await panel.locator('[data-ws="optimise-gradient-fd"]').check();
  await w.shot('optimise-ready');

  let adjointOk = !has('fd');
  if (!has('no-check') && !has('fd')) {
    await caption('Check gradient: the adjoint against finite differences, before trusting it');
    await panel.locator('[data-ws="optimise-check"]').click();
    await wait('the gradient check', async () => (await panel.locator('[data-ws="optimise-check-result"]').count()) > 0 || (await panel.locator('[role="alert"]').count()) > 0, 3600000);
    const result = (await panel.locator('[data-ws="optimise-check-result"]').allInnerTexts()).join(' | ');
    const verdict = await panel.locator('[data-ws="optimise-check-verdict"]').innerText().catch(() => '');
    console.log(`  check: ${result} // ${verdict}`);
    await panel.locator('[data-ws="optimise-check-result"]').first().scrollIntoViewIfNeeded().catch(() => {});
    await pause(2500);
    await w.shot('gradient-check');
    const ratios = [...result.matchAll(/ratio ([-0-9.]+)/g)].map((m) => Number(m[1]));
    adjointOk = ratios.length > 0 && ratios.every((r) => r > 0.5 && r < 2);
    if (!adjointOk) {
      await caption(ratios.length > 0 ? 'The adjoint does not pass here, so the cycles use finite differences' : /inconclusive/.test(result) ? 'The check is inconclusive (a change is within the mesh noise), so the cycles use finite differences' : 'The check did not finish, so the cycles use finite differences');
      await panel.locator('[data-ws="optimise-gradient-fd"]').check();
      await pause(800);
    } else {
      await caption('The adjoint agrees with finite differences: one flow and one adjoint solve per cycle');
    }
  }

  await caption(`Start: up to ${cycles} cycles, each one meshes and solves the changed shape`);
  await panel.locator('[data-ws="optimise-start"]').scrollIntoViewIfNeeded().catch(() => {});
  await panel.locator('[data-ws="optimise-start"]').click();
  await pause(3000);
  await wait('the optimisation', async () => (await panel.locator('[data-ws="optimise-apply"]').count()) > 0 && (await panel.locator('[data-ws="optimise-stop"]').count()) === 0 && (await panel.locator('[data-ws="optimise-start"]:not([disabled])').count()) > 0, 5 * 3600000);
  const best = (await panel.locator('[data-ws="optimise-best"]').innerText().catch(() => '')).split('\n').join(' | ');
  console.log(`  best: ${best}`);
  await panel.locator('[data-ws="optimise-best"]').scrollIntoViewIfNeeded().catch(() => {});
  await pause(2500);
  await w.shot('optimised');
  await caption('The convergence of the objective, and the best point found');
  await pause(2500);

  await caption('Apply the best point: the body is rebuilt and its boundaries are found again');
  await panel.locator('[data-ws="optimise-apply"]').scrollIntoViewIfNeeded().catch(() => {});
  await panel.locator('[data-ws="optimise-apply"]').click();
  await wait('the applied shape', async () => /applied|rebuilt|found again/i.test(await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '')), 600000);
  console.log(`  applied: ${await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '')}`);
  await pause(2500);
  await w.shot('applied');

  // ------------------------------------------------------------------ 9. the final shape, solved again
  await caption('9 · The optimised bend, meshed and solved again');
  await page.locator('[data-ws="guided-strip"] [data-step="mesh"]').click();
  await pause(1200);
  await card.locator('[data-ws="guided-generate"]').click();
  await pause(4000);
  await wait('the final mesh', async () => { const t = await cardText(); return /Next: Run/.test(t) && !/Out of date|Generating/.test(t); }, 900000);
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await pause(1000);
  await card.locator('[data-ws="guided-run"]').click();
  await pause(3000);
  await wait('the final run', async () => /Next: Results/.test(await cardText()) && /Finished|Converged|Stopped/i.test(await cardText()), 900000);
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await pause(5000);
  const after = await page.locator('.field-legend').innerText().catch(() => '');
  console.log(`  legend after: ${after.split('\n').join(' | ')}`);
  await w.shot('final-results');
  await caption('Done: the same flow through the optimised bend', 4500);
  await w.checkAlerts('walkthrough');
} catch (e) {
  console.log(`STOPPED: ${e.message}`);
  await w.shot('stopped').catch(() => {});
}
mark('end', 'end');
const video = w.recorder?.file ?? null;
const problems = await w.finish();
if (video) console.log(`video: ${video}`);
process.exit(problems.length ? 1 : 0);
