// Drives an ADJOINT optimisation from the Optimise panel (ADR-0038): the obstacle project (an
// implicit channel minus a sphere of radius R, inlet/outlet/walls tagged, snappy mesh, laminar
// air) is open on the server; the panel minimises the pressure loss over R in [3, 5] mm. Each cycle
// meshes the flow and runs FoamWin's adjoint, so this takes minutes. Run from `web/`:
//
//   node optimise_adjoint_gui.mjs "<url-with-token>" [shots-folder]

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const url = process.argv[2];
const shots = process.argv[3] ?? join(process.cwd(), '..', '.local', 'optimise-adjoint-shots');
mkdirSync(shots, { recursive: true });
const errors = [];
let step = 0;
const shot = async (page, name) => { step += 1; await page.screenshot({ path: join(shots, `${String(step).padStart(2, '0')}-${name}.png`) }); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (e) => errors.push(`page error: ${e.message}`));
await page.goto(url);
await page.waitForTimeout(4000);
await page.keyboard.press('Control+k');
await page.waitForTimeout(500);
await page.keyboard.type('Optimise');
await page.waitForTimeout(400);
await page.getByText('Open Optimise', { exact: true }).click();
const panel = page.locator('[data-ws="optimise-panel"]');
await panel.waitFor({ timeout: 10000 });
await panel.getByText('Pressure loss (adjoint)').click();
await panel.getByLabel('Variable 1', { exact: true }).fill('R');
await panel.getByLabel('Variable 1 lower bound').fill('0.003');
await panel.getByLabel('Variable 1 upper bound').fill('0.005');
await panel.getByLabel('Maximum cycles').fill('4');
await shot(page, 'ready');
await panel.locator('[data-ws="optimise-start"]').click();
await panel.locator('[data-ws="optimise-step"]').waitFor({ timeout: 60000 });
console.log(`  step shown: ${await panel.locator('[data-ws="optimise-step"]').innerText()}`);
await shot(page, 'running');
await panel.locator('[data-ws="optimise-start"]').waitFor({ timeout: 1800000 });
await page.waitForTimeout(300);
if (await panel.locator('[role="alert"]').count()) errors.push(`alert: ${await panel.locator('[role="alert"]').innerText()}`);
const status = await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '');
const best = await panel.locator('[data-ws="optimise-best"]').innerText().catch(() => '');
console.log(`  status: ${status}`);
console.log(`  best: ${best.replace(/\n/g, ' | ')}`);
const r = Number((best.match(/R = ([0-9.eE+-]+)/) ?? [])[1]);
if (!(r < 0.0035)) errors.push(`R ${r} did not move to the lower bound (smaller obstacle, lower loss)`);
await shot(page, 'done');
if (errors.length) { console.log('\nproblems:'); for (const e of errors) console.log(`  ${e}`); }
await browser.close();
process.exit(errors.length ? 1 : 0);
