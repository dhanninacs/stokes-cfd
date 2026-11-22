// Pictures of a solved project for a report: the mesh, a temperature contour on the mid-plane, streamlines
// from every inlet coloured by temperature, and the Streams table (flow, pressure drop, heat per stream).
//   node capture_results.mjs "<url-with-token>" "<project.stokes>" "<out folder>"
// A server must be running; the project is opened on it and left open.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const [rawUrl, project, out] = process.argv.slice(2);
if (!rawUrl || !project || !out) { console.error('usage: node capture_results.mjs <url-with-token> <project.stokes> <out folder>'); process.exit(2); }
const url = `${rawUrl}${rawUrl.includes('?') ? '&' : '?'}drive=1`;
mkdirSync(out, { recursive: true });
const w = await openWalk(url, out, join(out, 'walk'));
const { page } = w;
const pause = (ms) => page.waitForTimeout(ms);
const card = page.locator('.guided-card');
const shot = async (name) => { const file = join(out, `${name}.png`); await page.screenshot({ path: file }); console.log(`wrote ${file}`); };

async function frame(view) {
  await page.keyboard.press(view);
  await pause(800);
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await pause(1500);
}

try {
  await w.openProject(project, 'capture');
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  // The bodies' display meshes are rebuilt after an open: wait for the model to be drawn.
  for (let i = 0; i < 90; i += 1) {
    const drawn = await page.evaluate(() => (window.__stokesScene?.perf?.().triangles ?? 0) > 0).catch(() => false);
    if (drawn) break;
    await pause(2000);
  }
  await pause(3000);

  // 1. The mesh.
  await page.locator('nav').getByText('Mesh', { exact: true }).first().click();
  await pause(1500);
  await card.getByRole('switch').last().click();
  await pause(6000);
  await frame('Alt+0');
  await shot('1-mesh');
  await card.getByRole('switch').last().click();
  await pause(1000);

  // 2. Temperature on the mid-plane, seen square-on.
  await page.locator('nav').getByText('Results', { exact: true }).first().click();
  await pause(8000);
  await page.locator('[data-ws="guided-looks"] [data-look="slice"]').click().catch(() => {});
  await pause(1500);
  await page.locator('[data-ws="guided-field-picker"]').first().click();
  await pause(500);
  await page.locator('[data-ws="guided-field-picker-search"]').fill('temp');
  await pause(600);
  await page.getByRole('option', { name: /^Temperature/ }).first().click();
  await pause(5000);
  await frame('Alt+1');
  await shot('2-temperature-contour');

  // 3. Streamlines from every inlet, coloured by temperature, in the iso view.
  await page.locator('[data-ws="guided-looks"] [data-look="stream"]').click();
  for (let i = 0; i < 60 && /Not drawn yet|Tracing|Drawing/.test(await card.innerText()); i += 1) await pause(2000);
  await pause(4000);
  await frame('Alt+0');
  await shot('3-streamlines');

  // 4. The Streams table: each stream's flow, pressure drop, temperatures and heat.
  const streams = page.locator('[data-ws="results-streams"]');
  await streams.scrollIntoViewIfNeeded();
  await pause(800);
  await streams.screenshot({ path: join(out, '4-streams.png') });
  console.log(`streams: ${(await streams.innerText()).replace(/\n+/g, ' | ')}`);

  // 5. 3-D contours: five temperature levels through both streams and the plate, see-through.
  await page.locator('[data-ws="guided-looks"] [data-look="iso"]').click();
  await pause(1500);
  await card.getByRole('group', { name: 'Count' }).locator('[data-choice="5"]').click();
  const status = page.locator('[data-testid="viewport-iso-status"]');
  for (let i = 0; i < 150; i += 1) {
    const line = await status.innerText().catch(() => '');
    if (/levels?/.test(line) && !/cutting/.test(line)) break;
    await pause(2000);
  }
  await pause(2000);
  await frame('Alt+0');
  await shot('5-contours');
  console.log(`contours: ${await status.innerText().catch(() => '(none)')}`);
} catch (err) {
  w.fail(`stopped: ${String(err?.message ?? err).slice(0, 300)}`);
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
