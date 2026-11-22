// Pictures of the section views on a meshed (and solved) project: the volume mesh at the cut on the Mesh stage,
// and the solid cut of the bodies on the Geometry stage.
//   node capture_section.mjs "<url-with-token>" "<project.stokes>" "<out folder>" [axis] [offset metres] [view]
// `view` is the view button to press ("Back view", "Front view", ...). A server must be running.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const [rawUrl, project, out, axis = 'y', offset = '0', view = 'Back view'] = process.argv.slice(2);
if (!rawUrl || !project || !out) { console.error('usage: node capture_section.mjs <url> <project> <out> [axis] [offset] [view]'); process.exit(2); }
const url = `${rawUrl}${rawUrl.includes('?') ? '&' : '?'}drive=1`;
mkdirSync(out, { recursive: true });
const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const pause = (ms) => page.waitForTimeout(ms);
try {
  await page.goto(url);
  await page.waitForFunction(() => window.__stokes !== undefined, null, { timeout: 60000 });
  await pause(3000);
  await page.evaluate((p) => window.__stokes.getState().execute('project.open', { path: p }), project);
  for (let i = 0; i < 90; i += 1) {
    if (await page.evaluate(() => (window.__stokesScene?.perf?.().triangles ?? 0) > 0).catch(() => false)) break;
    await pause(2000);
  }
  await pause(3000);
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  const press = async (name) => { await page.getByRole('button', { name }).click().catch(() => {}); await pause(1200); };
  const section = async () => {
    await page.evaluate(([a, o]) => { const s = window.__stokes.getState(); s.putSectionAt(a, Number(o)); s.setSectionInvert(true); }, [axis, offset]);
    await pause(1500);
  };

  // 1. Geometry: the bodies cut by the plane, each cut filled with its body's colour.
  await page.locator('nav').getByText('Geometry', { exact: true }).first().click();
  await pause(3000);
  await section();
  // The bodies are drawn after an open, and each body made with the modeller is filled from its field.
  for (let i = 0; i < 90; i += 1) {
    if (await page.evaluate(() => window.__stokesScene.solidCutInfo().parts > 0 || window.__stokesScene.capsInfo().groups > 0)) break;
    await pause(1000);
    if (i % 10 === 9) await section();
  }
  await press(view);
  await press('Fit to view');
  await pause(1500);
  await page.screenshot({ path: join(out, 'section-geometry.png') });
  console.log(`caps: ${JSON.stringify(await page.evaluate(() => window.__stokesScene.capsInfo()))} solid: ${JSON.stringify(await page.evaluate(() => window.__stokesScene.solidCutInfo()))}`);

  // 2. Mesh: the cells the plane cuts, outlined and filled by region.
  await page.locator('nav').getByText('Mesh', { exact: true }).first().click();
  await pause(2000);
  await section();
  for (let i = 0; i < 120; i += 1) {
    if (await page.evaluate(() => window.__stokesScene.meshCutInfo() !== null)) break;
    await pause(1000);
  }
  await press(view);
  await press('Fit to view');
  await pause(1500);
  console.log(`mesh cut: ${JSON.stringify(await page.evaluate(() => window.__stokesScene.meshCutInfo()))}`);
  await page.screenshot({ path: join(out, 'section-mesh.png') });
  console.log(`wrote ${out}`);
} catch (err) {
  console.error(`stopped: ${String(err?.message ?? err).slice(0, 300)}`);
  process.exitCode = 1;
}
await browser.close();
