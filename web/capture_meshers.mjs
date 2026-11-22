// Pictures of every mesher's mesh in one project, for a side-by-side comparison: for each kept mesh result,
// the boundary with its cell edges, a square-on cut through the cells, and a close-up of the cut at a wall.
//   node capture_meshers.mjs "<url-with-token>" "<project.stokes>" "<out folder>" [axis] [offset metres]
// A server must be running. Each result is made the one in use (`mesh.use`) in turn.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const [rawUrl, project, out, axis = 'z', offset = '0'] = process.argv.slice(2);
if (!rawUrl || !project || !out) { console.error('usage: node capture_meshers.mjs <url> <project> <out> [axis] [offset]'); process.exit(2); }
const url = `${rawUrl}${rawUrl.includes('?') ? '&' : '?'}drive=1`;
mkdirSync(out, { recursive: true });
const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const pause = (ms) => page.waitForTimeout(ms);
const run = (name, params) => page.evaluate(([n, p]) => window.__stokes.getState().execute(n, p), [name, params]);
const press = async (name) => { await page.getByRole('button', { name }).click().catch(() => {}); await pause(1200); };
try {
  await page.goto(url);
  await page.waitForFunction(() => window.__stokes !== undefined, null, { timeout: 60000 });
  await pause(3000);
  await run('project.open', { path: project });
  await pause(8000);
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  const listed = await run('mesh.list', {});
  const results = (listed.results ?? []).filter((r) => r.state === 'done');
  console.log(`results: ${results.map((r) => `${r.mesher}:${r.cells}`).join(', ')}`);
  for (const result of results) {
    const tag = String(result.mesher);
    await run('mesh.use', { result: result.id });
    await page.evaluate(() => { const s = window.__stokes.getState(); s.setSection(false); s.setStage('Mesh'); s.setMeshDisplay('cells'); });
    await pause(6000);
    await page.keyboard.press('Alt+0').catch(() => {});
    await press('Iso view');
    await press('Fit to view');
    await pause(4000);
    await page.screenshot({ path: join(out, `${tag}-1-surface.png`) });
    // The cut through the cells, square-on.
    await page.evaluate(([a, o]) => { const s = window.__stokes.getState(); s.setMeshDisplay('surface'); s.putSectionAt(a, Number(o)); s.setSectionInvert(true); }, [axis, offset]);
    for (let i = 0; i < 180; i += 1) {
      if (await page.evaluate(() => window.__stokesScene.meshCutInfo() !== null)) break;
      await pause(1000);
    }
    await press('Front view');
    await press('Fit to view');
    await pause(2000);
    const info = await page.evaluate(() => window.__stokesScene.meshCutInfo());
    await page.screenshot({ path: join(out, `${tag}-2-cut.png`) });
    // A close-up at the middle of the view: the cells against the lattice walls, where the layers are.
    await page.mouse.move(980, 520);
    for (let i = 0; i < 12; i += 1) { await page.mouse.wheel(0, -240); await pause(120); }
    await pause(2500);
    await page.screenshot({ path: join(out, `${tag}-3-cut-closeup.png`) });
    console.log(`${tag}: ${result.cells} cells, cut ${JSON.stringify(info)}`);
  }
} catch (err) {
  console.error(`stopped: ${String(err?.message ?? err).slice(0, 300)}`);
  process.exitCode = 1;
}
await browser.close();
