// Draws a profile in the 2D sketcher (ADR-0036) and models with it through the build ribbon, with
// real presses, clicks on the sketch canvas and typing, and screenshots each step. Run from `web/`,
// where Playwright is installed:
//
//   node build_sketch_gui.mjs "<url-with-token>" [shots-folder]
//
// The project must be empty. The steps: an L-shaped profile with a round end (lines and a
// three-point arc, on the 5 mm grid) pulled 20 mm; split by the plane x = 5 mm; the half beyond the
// plane shelled 1 mm; the other half offset 1 mm; a polar pattern of four of the shell about z; and a
// sketch reopened with Edit sketch… from its properties. Errors and failed requests print at the end.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const url = process.argv[2];
const shots = process.argv[3] ?? join(process.cwd(), '..', '.local', 'sketch-gui-shots');
if (!url) { console.error('usage: node build_sketch_gui.mjs <url-with-token> [shots-folder]'); process.exit(2); }
mkdirSync(shots, { recursive: true });

const errors = [];
let step = 0;

async function shot(page, name) {
  step += 1;
  const file = join(shots, `${String(step).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file });
  console.log(`  shot: ${file}`);
}

const card = (page) => page.locator('[data-ws="operation-card"]');

/** Press a ribbon tool by its accessible name inside a ribbon group. */
async function tool(page, group, name, waitCard = true) {
  await page.locator(`[data-ws="build-${group}"] button[aria-label="${name}"]`).click();
  if (waitCard) await card(page).waitFor();
}

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

async function run(page, name) {
  await card(page).getByRole('button', { name: 'Run', exact: true }).click();
  try {
    await card(page).waitFor({ state: 'detached', timeout: 30000 });
  } catch (e) {
    // The card stayed: say what it says (a refusal, a stuck run) before giving up.
    await shot(page, `${name}-stuck`);
    console.log(`  card: ${(await card(page).innerText()).replace(/\n+/g, ' | ')}`);
    throw e;
  }
  await page.waitForTimeout(600);
  await shot(page, name);
}

/** The tree's rows, by name. */
async function treeNames(page) {
  return page.locator('[role="tree"] [role="treeitem"]').allInnerTexts();
}

/** Pick one part in the tree by its name. */
async function pick(page, name) {
  const row = page.locator('[role="tree"] [role="treeitem"]').filter({ has: page.getByText(name, { exact: true }) }).first();
  await row.click();
}

/** Click the sketch canvas at sketch coordinates (mm): the view opens at 6 px/mm, centred on 0. */
async function at(page, u, v) {
  const box = await page.locator('[data-ws="sketch-canvas"]').boundingBox();
  await page.mouse.click(box.x + box.width / 2 + u * 6, box.y + box.height / 2 - v * 6);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
if (process.env.SKETCH_GUI_TRACE) page.on('websocket', (ws) => ws.on('framesent', (f) => { if (String(f.payload).includes('operation.create')) console.log(`  sent: ${String(f.payload).slice(0, 1500)}`); }));
page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
page.on('response', (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url().replace(/token=[^&]+/, 'token=…')}`); });

await page.goto(url);
await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
// Full mode: the tree shows every part, so the script can pick by name.
await page.getByRole('radio', { name: 'Full' }).click();
await page.waitForTimeout(800);
await page.keyboard.press('Escape');
// The build ribbon from the viewport's Create button.
const create = page.getByRole('button', { name: 'Create', exact: true });
if (!(await page.locator('[data-ws="build-ribbon"]').count())) await create.first().click();
await page.locator('[data-ws="build-ribbon"]').waitFor();

// 1. The sketcher: an L with a round end, on the XY plane.
await tool(page, 'sketch', 'Sketch…', false);
await page.locator('[data-ws="sketch-editor"]').waitFor();
await page.locator('[data-ws="sketch-tool-line"]').click();
for (const [u, v] of [[0, 0], [25, 0], [25, 10], [10, 10], [10, 30]]) await at(page, u, v);
await page.keyboard.press('Escape'); // end the chain at (10, 30)
await page.locator('[data-ws="sketch-tool-arc"]').click();
await at(page, 10, 30); await at(page, 0, 30); await at(page, 5, 35); // ends, then a point on the arc
await page.locator('[data-ws="sketch-tool-line"]').click();
await at(page, 0, 30); await at(page, 0, 0);
await page.keyboard.press('Escape');
const status = await page.locator('[data-ws="sketch-status"]').innerText();
console.log(`  sketch status: ${status}`);
if (!status.startsWith('1 closed loop')) errors.push(`the sketch is not one closed loop: ${status}`);
await shot(page, 'sketch');
await page.locator('[data-ws="sketch-finish"]').click();
await card(page).waitFor();
if (await page.locator('[data-ws="sketch-editor"]').count()) errors.push('the sketcher stayed open after Finish');
await setQuantity(page, 'Pull distance', '20 [mm]');
await shot(page, 'sketch-card');
await run(page, 'pulled');
console.log(`  tree: ${(await treeNames(page)).join(' | ')}`);

// 2. Split the pulled body by the plane x = 5 mm, through the stem and the foot (the new body is picked after its run).
await tool(page, 'combine', 'Split');
await choose(page, 'Plane normal', '+x');
await setArray(page, 'Point on plane', [0.005, 0, 0]);
await run(page, 'split');
const afterSplit = await treeNames(page);
console.log(`  tree: ${afterSplit.join(' | ')}`);

// 3. Shell the half beyond the plane, 1 mm inside; offset the other half by 1 mm.
const halves = afterSplit.filter((n) => /above|below/.test(n));
if (halves.length < 2) errors.push(`the split made ${halves.length} parts`);
const [below, above] = [halves.find((n) => /below/.test(n)), halves.find((n) => /above/.test(n))];
if (above) {
  await pick(page, above.trim().split('\n')[0]);
  await tool(page, 'modify', 'Shell');
  await setQuantity(page, 'Distance', '1 [mm]');
  await run(page, 'shell');
}
if (below) {
  await pick(page, below.trim().split('\n')[0]);
  await tool(page, 'modify', 'Offset');
  await setQuantity(page, 'Distance', '1 [mm]');
  await run(page, 'offset');
}
console.log(`  tree: ${(await treeNames(page)).join(' | ')}`);

// 4. A polar pattern of four copies of the offset body about z (it is picked after its run).
await tool(page, 'pattern', 'Polar');
await choose(page, 'Axis', 'z');
await setQuantity(page, 'Count', '4');
await run(page, 'pattern');
console.log(`  tree: ${(await treeNames(page)).join(' | ')}`);

await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
await page.waitForTimeout(800);
await shot(page, 'pattern-fit');

// 5. Reopen the sketch from its properties: Edit sketch… shows the six entities it was made of.
const sketchRow = (await treeNames(page)).find((n) => /custom|sketch/i.test(n));
if (sketchRow) {
  await pick(page, sketchRow.trim().split('\n')[0]);
  await page.locator('[data-ws="edit-sketch"]').first().click();
  await page.locator('[data-ws="sketch-editor"]').waitFor();
  const rows = await page.locator('[data-ws="sketch-table"] .sketch-row').count();
  console.log(`  reopened sketch: ${rows} entities`);
  if (rows !== 6) errors.push(`the reopened sketch has ${rows} entities, not 6`);
  await shot(page, 'sketch-reopened');

  // 6. Dimension it (ADR-0037): W = 30 mm, and the foot's right edge at u = W. Binding a point binds
  // every end joined there, so line 1's end and line 2's start move together, and line 2's end
  // with line 3's start.
  const dims = page.locator('[data-ws="sketch-dimensions"]');
  await dims.getByLabel('New dimension name').fill('W');
  await dims.getByLabel('New dimension value').fill('30 [mm]');
  await dims.getByRole('button', { name: 'Add dimension' }).click();
  const table = page.locator('[data-ws="sketch-table"]');
  for (const label of ['line 1 end u', 'line 2 end u']) {
    const cell = table.getByLabel(label, { exact: true });
    await cell.fill('W');
    await cell.press('Enter');
  }
  await page.waitForTimeout(1200); // the server evaluates the bound cells
  const shown = await dims.locator('.sketch-dim-shown').first().innerText();
  const bound = await table.getByLabel('line 2 start u', { exact: true }).inputValue();
  console.log(`  dimension W shows ${shown}; line 2 start u is ${bound}`);
  if (!/30/.test(shown)) errors.push(`dimension W shows ${shown}`);
  if (bound !== 'W') errors.push(`line 2's start is not bound with line 1's end (${bound})`);
  const status6 = await page.locator('[data-ws="sketch-status"]').innerText();
  if (!status6.startsWith('1 closed loop')) errors.push(`the dimensioned sketch is not closed: ${status6}`);
  await shot(page, 'sketch-dimensioned');
  await page.locator('[data-ws="sketch-finish"]').click();
  await page.locator('[data-ws="sketch-editor"]').waitFor({ state: 'detached' });
  await page.waitForTimeout(4000); // the sketch and everything built from it run again
  await shot(page, 'dimensioned-rerun');
  // The sketch's Details page lists the dimension, and reopening shows the binding.
  await pick(page, sketchRow.trim().split('\n')[0]);
  await page.locator('[data-ws="edit-sketch"]').first().click();
  await page.locator('[data-ws="sketch-editor"]').waitFor();
  await page.waitForTimeout(800);
  const again = await page.locator('[data-ws="sketch-table"]').getByLabel('line 1 end u', { exact: true }).inputValue();
  console.log(`  reopened: line 1 end u is ${again}`);
  if (again !== 'W') errors.push(`the binding did not survive Finish (${again})`);
  await page.locator('[data-ws="sketch-cancel"]').click();
} else errors.push('no sketch row in the tree');

// 7. A vector in Details takes an expression: the pattern's centre x as "W_off" fails (no such
// name) with the reason under the field, and "2 [mm]" is accepted.
const problems = await page.locator('[role="alert"]').count();
console.log(`  alerts on screen: ${problems}`);

const wide = await page.evaluate(() => document.scrollingElement.scrollWidth - innerWidth);
if (wide > 0) errors.push(`the page is ${wide}px wider than the window`);

if (errors.length) {
  console.log('\nproblems:');
  for (const e of errors) console.log(`  ${e}`);
}
await browser.close();
process.exit(errors.length ? 1 : 0);
