// Builds the two-inlet mixing elbow from scratch through the GUI's build ribbon (ADR-0035), with real
// presses and typing, and screenshots each step. Run from `web/`, where Playwright is installed:
//
//   node build_elbow_gui.mjs "<url-with-token>" [shots-folder]
//
// The project must be empty. The elbow (mm): a main inlet pipe r 50 along x from x = -200 to 0 at
// y = -150; a bend, a circle r 50 sketched on the yz plane 150 from the z axis and revolved 90°,
// then rotated -90° about z; an outlet pipe r 50 along y from 0 to 200 at x = 150; a small inlet
// r 12.5 along y at x = 137.5 from y = -225; their union, cut to z >= 0 by a half-space block.
// Every step waits on the UI's own state; errors and failed requests are printed at the end.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const url = process.argv[2];
const shots = process.argv[3] ?? join(process.cwd(), '..', '.local', 'elbow-gui-shots');
if (!url) { console.error('usage: node build_elbow_gui.mjs <url-with-token> [shots-folder]'); process.exit(2); }
mkdirSync(shots, { recursive: true });

const errors = [];
let step = 0;

async function shot(page, name) {
  step += 1;
  const file = join(shots, `${String(step).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file });
  console.log(`  shot: ${file}`);
}

/** Press a ribbon tool by its accessible name inside a ribbon group. */
async function tool(page, group, name) {
  const button = page.locator(`[data-ws="build-${group}"] button[aria-label="${name}"]`);
  await button.click();
  await page.locator('[data-ws="operation-card"]').waitFor();
}

const card = (page) => page.locator('[data-ws="operation-card"]');

async function setQuantity(page, label, text) {
  const field = card(page).getByLabel(label, { exact: true });
  await field.fill(text);
  await field.press('Tab');
}

async function setArray(page, label, values) {
  for (const [axis, value] of ['x', 'y', 'z'].map((a, i) => [a, values[i]])) {
    const field = card(page).getByLabel(`${label} ${axis}`, { exact: true });
    await field.fill(String(value));
    await field.press('Tab');
  }
}

async function choose(page, label, value) {
  await card(page).getByLabel(label, { exact: true }).selectOption(value);
}

/** Press the card's Run and wait until the card has closed (the run ended and selected its output). */
async function run(page, name) {
  await card(page).getByRole('button', { name: 'Run', exact: true }).click();
  await card(page).waitFor({ state: 'detached', timeout: 120000 });
  await page.waitForTimeout(600);
  await shot(page, name);
}

/** Pick parts in the tree, in order: the first plain, the rest with Ctrl. */
async function pick(page, names) {
  for (const [index, name] of names.entries()) {
    const row = page.locator('[role="tree"] [role="treeitem"]').filter({ has: page.getByText(name, { exact: true }) }).first();
    await row.click({ modifiers: index === 0 ? [] : ['Control'] });
  }
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
page.on('response', (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(/token=[^&]+/, 'token=…')}`); });

await page.goto(url);
await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
await page.locator('[data-ws="guided-create"]').click();
await page.locator('[data-ws="build-ribbon"]').waitFor();
await shot(page, 'ribbon');

// 1. Main inlet: a cylinder r 50, 200 long along x, centred at (-100, -150, 0) mm.
await tool(page, 'shape', 'Cylinder');
await setQuantity(page, 'Radius', '50 [mm]');
await setQuantity(page, 'Length', '200 [mm]');
await choose(page, 'Axis', 'x');
await setArray(page, 'Position', [-0.1, -0.15, 0]);
await shot(page, 'inlet-card');
await run(page, 'inlet');

// 2. Bend: a circle r 50 on the yz plane, revolved 90° about z, 150 from the axis.
await tool(page, 'sketch', 'Circle');
await choose(page, 'Sketch plane', 'yz');
await setQuantity(page, 'Radius', '50 [mm]');
await choose(page, 'Method', 'revolve');
await setQuantity(page, 'Revolve angle', '90 [deg]');
await setQuantity(page, 'Distance from axis', '150 [mm]');
await shot(page, 'bend-card');
await run(page, 'bend-sketch');

// 3. Turn the bend into place: rotate -90° about z (the new body is picked after its run).
await tool(page, 'transform', 'Rotate');
await choose(page, 'Axis', 'z');
await setQuantity(page, 'Angle', '-90 [deg]');
await run(page, 'bend-rotated');

// 4. Outlet: a cylinder r 50, 200 long along y, centred at (150, 100, 0) mm.
await tool(page, 'shape', 'Cylinder');
await setQuantity(page, 'Radius', '50 [mm]');
await setQuantity(page, 'Length', '200 [mm]');
await choose(page, 'Axis', 'y');
await setArray(page, 'Position', [0.15, 0.1, 0]);
await run(page, 'outlet');

// 5. Small inlet: a cylinder r 12.5, 100 long along y, centred at (137.5, -175, 0) mm.
await tool(page, 'shape', 'Cylinder');
await setQuantity(page, 'Radius', '12.5 [mm]');
await setQuantity(page, 'Length', '100 [mm]');
await choose(page, 'Axis', 'y');
await setArray(page, 'Position', [0.1375, -0.175, 0]);
await run(page, 'small-inlet');

// 6. Union of the four, picked in the tree (Full mode): the inlet first.
await page.getByRole('radio', { name: 'Full' }).click();
await page.waitForTimeout(800);
const expand = page.getByRole('button', { name: 'Expand Bodies' });
if (await expand.count()) await expand.first().click();
await pick(page, ['cylinder', 'rotate', 'cylinder_2', 'cylinder_3']);
await shot(page, 'picked-four');
await tool(page, 'combine', 'Union');
await run(page, 'union');

// 7. The symmetry cut: a half-space block of 1 m on z >= 0, intersected with the union.
await tool(page, 'shape', 'Half-space');
await choose(page, 'Normal', '-z');
await setArray(page, 'Size', [1, 1, 1]);
await run(page, 'half-space');
await pick(page, ['union', 'half_space']);
await tool(page, 'combine', 'Intersect');
await run(page, 'elbow');

await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
await page.waitForTimeout(800);
await shot(page, 'elbow-fit');

// The page never grows wider than the window (a ribbon wider than the room scrolls inside itself).
const wide = await page.evaluate(() => document.scrollingElement.scrollWidth - innerWidth);
if (wide > 0) errors.push(`the page is ${wide}px wider than the window`);
// Nor does any container scroll sideways to make room for it (the ribbon may scroll itself).
const scrolled = await page.evaluate(() =>
  Array.from(document.querySelectorAll('*'))
    .filter((e) => e.scrollLeft > 0 && !e.closest('[data-ws="build-ribbon"]'))
    .map((e) => `${e.tagName.toLowerCase()}.${e.className}`.slice(0, 80)),
);
if (scrolled.length) errors.push(`scrolled sideways: ${scrolled.join(', ')}`);

// Guided mode lists the elbow alone: its steps are not bodies of their own.
await page.getByRole('radio', { name: 'Guided' }).click();
await page.waitForTimeout(800);
const bodies = await page.locator('[data-ws="guided-bodies"] .guided-row-name').allInnerTexts();
console.log(`  guided bodies: ${bodies.join(', ')}`);
if (bodies.length !== 1 || bodies[0] !== 'intersection') errors.push(`guided lists ${bodies.join(', ')}`);
await shot(page, 'guided');

if (errors.length) {
  console.log('\nproblems:');
  for (const e of errors) console.log(`  ${e}`);
}
await browser.close();
process.exit(errors.length ? 1 : 0);
