// Pictures of the solved ball project at twice the resolution, for a banner or a thumbnail: the mesh cut
// (boundary layers around the ball), wide and zoomed, and the speed in the middle plane (Turbo).
//   set STOKES_WALK_CHANNEL=chrome
//   node capture_ball_assets.mjs "<url-with-token>" "<project.stokes>" "<out folder>"
// A server with no project open must be running. The crops leave out the guided card and the toolbars.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const [rawUrl, project, out] = process.argv.slice(2);
if (!rawUrl || !project || !out) { console.error('usage: node capture_ball_assets.mjs <url> <project> <out>'); process.exit(2); }
const url = `${rawUrl}${rawUrl.includes('?') ? '&' : '?'}drive=1`;
mkdirSync(out, { recursive: true });
const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 })).newPage();
const pause = (ms) => page.waitForTimeout(ms);
const CLIP = { x: 330, y: 90, width: 1170, height: 640 };
const until = async (what, test, ms) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await test().catch(() => false)) return; await pause(1000); }
  throw new Error(`timed out waiting for ${what}`);
};
const view = async (name) => {
  await page.locator('[data-ws="view-buttons"]').getByText(name, { exact: true }).click();
  await pause(700);
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await pause(1500);
};
try {
  await page.goto(url);
  await page.waitForFunction(() => window.__stokes !== undefined, null, { timeout: 60000 });
  await pause(3000);
  await page.evaluate((p) => window.__stokes.getState().execute('project.open', { path: p }), project);
  // A project whose server was stopped with edits unsaved offers them back: they are the case.
  await pause(2500);
  const restore = page.getByRole('button', { name: 'Restore', exact: true });
  if (await restore.count()) { await restore.first().click(); await pause(3000); }
  await until('the geometry', () => page.evaluate(() => (window.__stokesScene?.perf?.().triangles ?? 0) > 0), 180000);
  await pause(3000);
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});

  // The mesh cut: wide, then zoomed on the ball.
  await page.locator('nav').getByText('Mesh', { exact: true }).first().click();
  await pause(2500);
  await page.evaluate(() => { const s = window.__stokes.getState(); s.putSectionAt('z', 0); s.setSectionInvert(true); });
  await until('the mesh cut', () => page.evaluate(() => window.__stokesScene.meshCutInfo() !== null), 180000);
  await view('Front');
  await page.screenshot({ path: join(out, 'mesh-wide.png'), clip: CLIP });
  const box = await page.locator('canvas').first().boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < 6; i += 1) { await page.mouse.wheel(0, -300); await pause(350); }
  await pause(1500);
  await page.screenshot({ path: join(out, 'mesh-zoom.png'), clip: CLIP });
  await page.evaluate(() => window.__stokes.getState().setSection(false));

  // The speed in the middle plane.
  await page.locator('nav').getByText('Results', { exact: true }).first().click();
  await until('the results', () => page.locator('[data-ws="guided-looks"], .guided-card').first().innerText().then((t) => /Velocity/.test(t)), 120000);
  await pause(6000);
  await page.evaluate(() => window.__stokes.setState((s) => ({ results: { ...s.results, view: { ...s.results.view, map: 'turbo' } } })));
  await view('Front');
  await page.addStyleTag({ content: '.field-legend{visibility:hidden !important}' });
  await pause(1500);
  await page.screenshot({ path: join(out, 'flow.png'), clip: CLIP });
  // The pressure and the streamlines, if the card offers them (a banner takes what it gets).
  const card = page.locator('.guided-card').first();
  try {
    await card.getByText('Pressure', { exact: true }).first().click();
    await pause(5000);
    await page.screenshot({ path: join(out, 'pressure.png'), clip: CLIP });
    await card.getByText('Velocity', { exact: true }).first().click();
    await pause(3000);
    await card.getByText('Stream', { exact: true }).first().click();
    await pause(8000);
    await page.screenshot({ path: join(out, 'stream.png'), clip: CLIP });
  } catch (err) { console.log(`pressure/streamlines skipped: ${String(err?.message ?? err).split('\n')[0]}`); }
  console.log(`wrote ${out}`);
} catch (err) {
  console.error(`stopped: ${String(err?.message ?? err).slice(0, 300)}`);
  await page.screenshot({ path: join(out, 'stopped.png') }).catch(() => {});
  process.exitCode = 1;
}
await browser.close();
