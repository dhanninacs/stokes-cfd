// The whole path through the GUI on a NEW project, ending in the adjoint shape optimisation:
// start screen > New project > a 60 x 20 x 20 mm box minus a 4 mm sphere (build ribbon, Subtract) >
// Fluid (Air) > Boundaries (inlet 0.05 m/s, outlet 0 Pa, the rest wall) > Mesh (2 mm, snappyHexMesh) >
// Run > Results > Optimise (pressure loss, the sphere's radius 3 to 5 mm) > Check gradient > Apply.
// A server with NO project must be running (`python -m stokes.server --port N`); the folder is given.
//
//   node e2e_adjoint_gui.mjs "<url-with-token>" "<empty folder for projects>" [shots-folder] [--no-check]
//
// Takes about ten minutes (every optimisation cycle meshes and solves the flow). Prints what each
// step shows and every problem seen: page errors, failed requests, alerts, a layout overlap.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const [url, folder, shots = join(process.cwd(), '..', '.local', 'e2e-shots'), flag] = process.argv.slice(2);
if (!url || !folder) { console.error('usage: node e2e_adjoint_gui.mjs <url> <projects folder> [shots] [--no-check]'); process.exit(2); }
mkdirSync(shots, { recursive: true });
const problems = [];
const note = (m) => console.log(`  ${m}`);
const fail = (m) => { problems.push(m); console.log(`  PROBLEM: ${m}`); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (e) => fail(`page error: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') fail(`console: ${m.text()}`); });
page.on('response', (r) => { if (r.status() >= 400) fail(`HTTP ${r.status()} ${r.url().replace(/token=[^&]+/, 'token=...')}`); });

let n = 0;
const shot = async (name) => { n += 1; await page.screenshot({ path: join(shots, `${String(n).padStart(2, '0')}-${name}.png`) }); };
const card = page.locator('.guided-card');
const visible = (loc) => loc.locator('visible=true').first();
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
async function until(what, test, ms = 120000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await test()) return; await page.waitForTimeout(500); }
  fail(`timed out waiting for ${what}`);
  throw new Error(`timeout: ${what}`);
}
async function setQuantity(label, text) {
  const field = visible(page.getByLabel(label, { exact: true }));
  await field.fill(text);
  await field.press('Tab');
}
const runCard = async () => { await visible(page.locator('[data-ws="operation-card"] button:has-text("Run")')).click(); };
const overlaps = (a, b) => a && b && a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
async function rect(sel) {
  return page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; }, sel);
}

try {
  // 1. A new project from the start screen.
  console.log('1. New project');
  await page.goto(url);
  await visible(page.getByText('New project', { exact: true })).click();
  await page.waitForTimeout(1200);
  await page.evaluate(([name, dir]) => {
    const inputs = Array.from(document.querySelectorAll('input')).filter((x) => x.offsetParent);
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    for (const [i, v] of [[0, name], [1, dir]]) { set.call(inputs[i], v); inputs[i].dispatchEvent(new Event('input', { bubbles: true })); }
  }, ['channel', folder]);
  await shot('new-project');
  await visible(page.getByText('Create', { exact: true })).click();
  await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
  note(`project open: ${(await page.locator('header').first().innerText()).split('\n').slice(0, 3).join(' ')}`);

  // 2. Geometry: box, sphere, subtract.
  console.log('2. Geometry');
  await page.locator('[data-ws="guided-create"]').click();
  await page.locator('[data-ws="build-ribbon"]').waitFor();
  const ribbon = await rect('[data-ws="build-ribbon"]');
  const cube = await rect('[data-ws="gizmo"], .gizmo, .view-island');
  if (overlaps(ribbon, cube)) fail('the build ribbon overlaps the view buttons / gizmo');
  await page.locator('[data-ws="build-shape"] button[aria-label="Box"]').click();
  await page.locator('[data-ws="operation-card"]').waitFor();
  await setQuantity('Size x', '60 [mm]'); await setQuantity('Size y', '20 [mm]'); await setQuantity('Size z', '20 [mm]');
  await runCard();
  await page.locator('[data-ws="operation-card"]').waitFor({ state: 'detached', timeout: 60000 });
  await page.locator('[data-ws="build-shape"] button[aria-label="Sphere"]').click();
  await page.locator('[data-ws="operation-card"]').waitFor();
  await setQuantity('Radius', '4 [mm]');
  await runCard();
  await page.locator('[data-ws="operation-card"]').waitFor({ state: 'detached', timeout: 60000 });
  await page.getByRole('radio', { name: 'Full' }).click();
  await page.waitForTimeout(800);
  for (const group of ['Geometry', 'Bodies']) {
    const open = page.getByRole('button', { name: `Expand ${group}` });
    if (await open.count()) await open.first().click();
    await page.waitForTimeout(300);
  }
  await until('box and sphere in the tree', async () => (await page.locator('[role="treeitem"]').allInnerTexts()).some((t) => t.startsWith('sphere')));
  await page.evaluate(() => {
    const pick = (name, ctrl) => Array.from(document.querySelectorAll('[role=treeitem]')).find((e) => e.innerText.split('\n')[0].trim() === name).dispatchEvent(new MouseEvent('click', { bubbles: true, ctrlKey: ctrl }));
    pick('box', false); pick('sphere', true);
  });
  await page.waitForTimeout(500);
  const many = await page.locator('[data-ws="details-many"]').innerText().catch(() => '');
  if (/n_[0-9a-f]{8}/.test(many)) fail(`the multi-selection lists raw ids: ${many.split('\n').join(' ')}`);
  if (!(await page.locator('[data-ws="build-ribbon"]').count())) await page.getByRole('button', { name: 'Create' }).click();
  await page.locator('[data-ws="build-combine"] button[aria-label="Subtract"]').click();
  await page.locator('[data-ws="operation-card"]').waitFor();
  const stack = await rect('.bottom-stack');
  const rib = await rect('[data-ws="build-ribbon"]');
  if (overlaps(stack, rib) && stack[3] > rib[1] + 1) fail('the hint toast overlaps the build ribbon');
  await shot('subtract-card');
  await runCard();
  await page.locator('[data-ws="operation-card"]').waitFor({ state: 'detached', timeout: 120000 });
  await page.getByRole('radio', { name: 'Guided' }).click();
  await page.waitForTimeout(1000);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await until('difference is the fluid', async () => /difference is the fluid/.test(await cardText()), 60000);
  note(await cardText());
  await shot('geometry');

  // 3. Fluid.
  console.log('3. Fluid');
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await visible(card.getByText('Air', { exact: true })).click();
  await until('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);
  note(await cardText());

  // 4. Boundaries: the two 4 cm² end faces are the inlet and the outlet; the rest stays wall.
  console.log('4. Boundaries');
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  const pickEnd = async () => {
    const row = card.getByText(/flat · 4 cm²/).first();
    await row.locator('xpath=..').getByRole('button', { name: 'Pick' }).click();
  };
  await pickEnd();
  await visible(card.locator('input')).fill('0.05');
  await visible(card.getByRole('button', { name: 'Inlet' })).click();
  await page.waitForTimeout(1200);
  await pickEnd();
  await visible(card.getByRole('button', { name: 'Outlet' })).click();
  await until('2 boundaries', async () => /2 boundaries/.test(await cardText()), 20000);
  note(await cardText());
  await shot('boundaries');

  // 5. Mesh: 2 mm snappyHexMesh.
  console.log('5. Mesh');
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  await visible(card.locator('input')).fill('2');
  await page.keyboard.press('Tab');
  await visible(card.getByRole('button', { name: 'Generate' })).click();
  await until('the mesh', async () => /Next: Run/.test(await cardText()), 240000);
  note(await cardText());
  await shot('mesh');

  // 6. Run the ordinary solve.
  console.log('6. Run');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await visible(card.getByRole('button', { name: 'Run', exact: true })).click();
  await until('the run', async () => /Next: Results/.test(await cardText()), 240000);
  note(await cardText());

  // 7. Results, then Optimise from the Results card.
  console.log('7. Results');
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await page.waitForTimeout(3000);
  await shot('results');
  await page.locator('[data-ws="guided-optimise"]').click();
  const panel = page.locator('[data-ws="optimise-panel"]');
  await panel.waitFor({ timeout: 10000 });
  const title = await panel.locator('.optimise-title').innerText().catch(() => '');
  if (!/Optimise/i.test(title)) fail('the Optimise panel has no title');
  if (!(await panel.getByRole('radio', { name: /Pressure loss/ }).isChecked())) fail('Pressure loss is not the default objective with a flow set up');

  console.log('8. Optimise (adjoint)');
  await page.waitForTimeout(1500);
  const offered = await page.locator('#optimise-names option').evaluateAll((o) => o.map((x) => ({ value: x.value, label: x.label })));
  const radius = offered.find((o) => /sphere Radius$/.test(o.label));
  if (!radius) fail(`the sphere's radius is not offered by name: ${offered.map((o) => o.label).join(', ')}`);
  await panel.getByLabel('Variable 1', { exact: true }).fill(radius?.value ?? '');
  await panel.getByLabel('Variable 1 lower bound').fill('0.003');
  await panel.getByLabel('Variable 1 upper bound').fill('0.005');
  await panel.getByLabel('Maximum cycles').fill('3');
  note(`variable: ${await panel.locator('[data-ws="optimise-variable-label"]').innerText().catch(() => 'no label')}`);
  await shot('optimise-ready');
  const panelBox = await rect('[data-ws="optimise-panel"]');
  const cubeBox = await rect('.view-island, [data-ws="view-island"]');

  if (flag !== '--no-check') {
    await panel.locator('[data-ws="optimise-check"]').click();
    await until('the gradient check', async () => (await panel.locator('[data-ws="optimise-check-result"]').count()) > 0 || (await panel.locator('[role="alert"]').count()) > 0, 480000);
    const ratio = await panel.locator('[data-ws="optimise-check-result"]').innerText().catch(() => '');
    note(`gradient check: ${ratio}`);
    const value = Number((ratio.match(/ratio ([0-9.]+)/) ?? [])[1]);
    if (!(value > 0.5 && value < 2)) fail(`the adjoint / finite-difference ratio is ${ratio || 'missing'}`);
    await shot('gradient-check');
  }

  await panel.locator('[data-ws="optimise-start"]').click();
  await panel.locator('[data-ws="optimise-step"]').waitFor({ timeout: 60000 });
  note(`running: ${await panel.locator('[data-ws="optimise-step"]').innerText()}`);
  await until('the optimisation', async () => (await panel.locator('[data-ws="optimise-apply"]').count()) > 0 && (await panel.locator('[data-ws="optimise-start"]').count()) > 0, 560000);
  const best = (await panel.locator('[data-ws="optimise-best"]').innerText()).split('\n').join(' | ');
  note(`best: ${best}`);
  const chart = await panel.locator('[data-ws="optimise-chart"]').boundingBox();
  if (!chart || chart.height < 100) fail(`the convergence chart is squashed (${chart ? Math.round(chart.height) : 'none'} px high)`);
  if (overlaps(await rect('[data-ws="optimise-panel"]'), cubeBox)) fail('the Optimise panel runs over the view buttons');
  await shot('optimised');
  await panel.locator('[data-ws="optimise-apply"]').click();
  await page.waitForTimeout(3000);
  note(await panel.locator('[data-ws="optimise-status"]').innerText());
  await shot('applied');
  void panelBox;
} catch (e) {
  console.log(`STOPPED: ${e.message}`);
  await shot('stopped');
}
console.log(problems.length ? `\n${problems.length} problem(s):\n${problems.map((p) => `  ${p}`).join('\n')}` : '\nno problems');
await browser.close();
process.exit(problems.length ? 1 : 0);
