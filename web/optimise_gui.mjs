// Drives the Optimise panel (ADR-0038) in a browser: a sphere from the build ribbon, the panel
// from the Commands palette, its radius as the variable, a target volume, Start, the convergence
// chart and Apply. Run from `web/` against a server on an empty project:
//
//   node optimise_gui.mjs "<url-with-token>" [shots-folder]

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const url = process.argv[2];
const shots = process.argv[3] ?? join(process.cwd(), '..', '.local', 'optimise-gui-shots');
if (!url) { console.error('usage: node optimise_gui.mjs <url-with-token> [shots-folder]'); process.exit(2); }
mkdirSync(shots, { recursive: true });
const errors = [];
let step = 0;
const shot = async (page, name) => { step += 1; await page.screenshot({ path: join(shots, `${String(step).padStart(2, '0')}-${name}.png`) }); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });

await page.goto(url);
await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
await page.locator('[data-ws="guided-create"]').click();
await page.locator('[data-ws="build-ribbon"]').waitFor();
await page.locator('[data-ws="build-shape"] button[aria-label="Sphere"]').click();
const card = page.locator('[data-ws="operation-card"]');
await card.waitFor();
await card.getByLabel('Radius', { exact: true }).fill('5 [mm]');
await card.getByLabel('Radius', { exact: true }).press('Tab');
await card.getByRole('button', { name: 'Run', exact: true }).click();
await card.waitFor({ state: 'detached', timeout: 120000 });
await page.waitForTimeout(800);

// The panel, from the Commands palette.
await page.keyboard.press('Control+k');
await page.waitForTimeout(500);
await page.keyboard.type('Optimise');
await page.waitForTimeout(400);
await shot(page, 'palette');
await page.getByText('Open Optimise', { exact: true }).click();
const panel = page.locator('[data-ws="optimise-panel"]');
await panel.waitFor({ timeout: 10000 });

// The sphere's radius, from the quantities the panel offers for the part (shape.variables); choosing
// one fills its bounds, which are then widened to reach the target.
await page.waitForTimeout(800);
const offered = await page.locator('#optimise-names option').evaluateAll((o) => o.map((x) => ({ value: x.value, label: x.label })));
console.log(`  offered: ${offered.map((o) => o.label || o.value).join(', ')}`);
const radius0 = offered.find((o) => /Radius$/.test(o.label));
if (!radius0) errors.push('the radius is not offered');
await panel.getByLabel('Variable 1', { exact: true }).fill(radius0?.value ?? '');
const filled = await panel.getByLabel('Variable 1 lower bound').inputValue();
if (filled === '') errors.push('choosing the radius did not fill its bounds');
await panel.getByLabel('Variable 1 lower bound').fill('0.003');
await panel.getByLabel('Variable 1 upper bound').fill('0.02');
await panel.getByLabel('Target volume').fill(String((4 / 3) * Math.PI * 0.008 ** 3));
await shot(page, 'panel');
await panel.locator('[data-ws="optimise-start"]').click();
await panel.locator('[data-ws="optimise-best"], [role="alert"]').first().waitFor({ timeout: 180000 });
if (await panel.locator('[role="alert"]').count()) {
  console.log(`  alert: ${await panel.locator('[role="alert"]').innerText()}`);
  await shot(page, 'alert');
  await browser.close();
  process.exit(1);
}
// The run streams its cycles and reports its end as an event: Start comes back when it is over.
await panel.locator('[data-ws="optimise-start"]').waitFor({ timeout: 180000 });
await page.waitForTimeout(300);
const status = await panel.locator('[data-ws="optimise-status"]').innerText();
const best = await panel.locator('[data-ws="optimise-best"]').innerText();
console.log(`  status: ${status}`);
console.log(`  best: ${best.replace(/\n/g, ' | ')}`);
const radius = Number((best.match(/radius = ([0-9.eE+-]+)/) ?? [])[1]);
if (!(Math.abs(radius - 0.008) / 0.008 < 0.03)) errors.push(`the best radius is ${radius}, not 0.008`);
await shot(page, 'converged');
await panel.locator('[data-ws="optimise-apply"]').click();
await page.waitForTimeout(3000);
await shot(page, 'applied');
const after = await panel.locator('[data-ws="optimise-status"]').innerText();
if (!/applied/.test(after)) errors.push(`apply said: ${after}`);

if (errors.length) { console.log('\nproblems:'); for (const e of errors) console.log(`  ${e}`); }
await browser.close();
process.exit(errors.length ? 1 : 0);
