// Fast mode through the GUI, end to end (ADR-0047), with real presses, against a running server.
// Run from `web/`, where Playwright is installed:
//
//   node walk_fast.mjs "<url-with-token>" <projects-folder> [shots-folder]
//
// New project > a 60 x 20 x 20 mm box less a 4 mm sphere (Create ribbon) > Fluid (Air) > Boundaries
// (inlet 0.05 m/s on one end, outlet on the other) > the Fast mode panel (the top bar's Fast
// button): Start, the plane and its legend, the panel's numbers, another value, another plane moved
// along its axis and the automatic one again, a higher fidelity, a faster inlet while it runs (the
// turbulent preview), Stop and Start again, Start with no inlet, and round 4's options (WALE, 16-bit
// storage, a warm start at fidelity 4). Every step waits on the UI's own state; a screenshot per step; problems (page errors,
// console errors, failed requests, timeouts) are printed at the end and make the exit code 1.
//
// STOKES_WALK_CHANNEL=chrome drives an installed Chrome where Playwright's own browser is absent.

import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const [url, folder, shots = join(process.cwd(), '..', '.local', 'fast-walk')] = process.argv.slice(2);
if (!url || !folder) { console.error('usage: node walk_fast.mjs <url> <projects folder> [shots]'); process.exit(2); }
const walk = await openWalk(url.includes('drive=1') ? url : `${url}&drive=1`, folder, shots);
const { page, visible, shot, fail, note, problems } = walk;

const card = page.locator('.guided-card');
const panel = page.locator('[data-ws="fast-panel"]');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
const panelText = async () => (await panel.first().innerText().catch(() => '')).split('\n').join(' | ');
const stats = async () => (await panel.locator('[data-ws="fast-stats"]').innerText().catch(() => '')).split('\n').join(' | ');
const alerts = async () => (await panel.locator('[role="alert"]').allInnerTexts()).join(' ');
async function until(what, test, ms = 120000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await test()) return Date.now() - t0; await page.waitForTimeout(250); }
  fail(`timed out waiting for ${what}`);
  throw new Error(`timeout: ${what}`);
}
/** Whether the panel's status lines are wholly inside the panel and the window: read without scrolling. */
const statusInView = () => page.evaluate(() => {
  const lines = document.querySelector('[data-ws="fast-stats"]');
  const card = document.querySelector('[data-ws="fast-panel"]');
  if (!lines || !card) return false;
  const a = lines.getBoundingClientRect();
  const b = card.getBoundingClientRect();
  return a.height > 0 && a.top >= b.top - 1 && a.bottom <= Math.min(b.bottom, window.innerHeight) + 1;
});
/** A command over the page's own client (what the panel calls). */
const rpc = (method, params = {}) => page.evaluate(([m, p]) => window.__stokes.getState().execute(m, p), [method, params]);
/** Stop from the panel. The plane's last fetch may lose the race with the stop: the routes answer
 * 204 then, so nothing reaches the console (a 404 did, before). */
async function stop() {
  await panel.locator('[data-ws="fast-stop"]').click();
  await panel.locator('[data-ws="fast-start"]').waitFor({ timeout: 10000 });
  await page.waitForTimeout(800);
}

try {
  console.log('1. New project; 2. Geometry: a box less a sphere');
  await walk.newProject('fast');
  await walk.tool('shape', 'Box');
  await walk.setField('Size x', '60 [mm]'); await walk.setField('Size y', '20 [mm]'); await walk.setField('Size z', '20 [mm]');
  await walk.run('box');
  await walk.tool('shape', 'Sphere');
  await walk.setField('Radius', '4 [mm]');
  await walk.run('sphere');
  for (const group of ['Geometry', 'Bodies']) {
    const open = page.getByRole('button', { name: `Expand ${group}` });
    if (await open.count()) await open.first().click();
    await page.waitForTimeout(300);
  }
  await walk.pick('box');
  await walk.pick('sphere', true);
  await walk.tool('combine', 'Subtract');
  await walk.run('subtract', 120000);
  await page.getByRole('radio', { name: 'Guided' }).click();
  await page.waitForTimeout(1000);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await until('the body is the fluid', async () => /is the fluid/.test(await cardText()), 60000);

  console.log('3. Fluid and boundaries');
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await visible(card.getByText('Air', { exact: true })).click();
  await until('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  const pickEnd = async () => {
    const row = card.getByText(/flat.* · 4 cm²/).first();
    await row.locator('xpath=..').getByRole('button', { name: 'Pick' }).click();
  };
  await pickEnd();
  await visible(card.locator('input')).fill('0.05');
  await visible(card.getByRole('button', { name: 'Inlet' })).click();
  await page.waitForTimeout(1200);
  await pickEnd();
  await visible(card.getByRole('button', { name: 'Outlet' })).click();
  await until('2 boundaries', async () => /2 boundaries/.test(await cardText()), 20000);
  await shot('boundaries');

  console.log('4. The Fast mode panel');
  await page.locator('[data-ws="fast-toggle"]').click();
  await panel.waitFor({ timeout: 10000 });
  await until('the GPU check', async () => /GPU:|not available|needs/.test(await panelText()), 20000);
  note(await panelText());
  await shot('panel-idle');

  console.log('5. Start');
  await panel.locator('[data-ws="fast-start"]').click();
  const first = await until('the first numbers', async () => /updates\/s/.test(await stats()), 30000);
  note(`first numbers after ${first} ms: ${await stats()}`);
  await shot('running-first');
  const steady = await until('steady', async () => /Steady/.test(await stats()), 120000);
  note(`steady after ${steady} ms: ${await stats()}`);
  if (!(await statusInView())) fail('the status lines are not in view without scrolling');
  await shot('running-steady');
  const legend = await page.locator('[data-ws="fast-legend"]').innerText().catch(() => '');
  if (!legend) fail('no legend for the plane: its colours have no numbers');
  else note(`legend: ${legend.split('\n').join(' ')}`);
  // A steady preview holds (it steps slowly, so the GPU is the viewport's again) and says so; the
  // viewport's own frame time is measured while it steps flat out and while it holds.
  const frameMs = async () => {
    const t = await page.evaluate(() => new Promise((resolve) => {
      const marks = [];
      const tick = (now) => { marks.push(now); if (marks.length < 90) requestAnimationFrame(tick); else resolve(marks); };
      requestAnimationFrame(tick);
    }));
    const gaps = t.slice(1).map((v, i) => v - t[i]).sort((a, b) => a - b);
    return { median: gaps[Math.floor(gaps.length / 2)], p95: gaps[Math.floor(gaps.length * 0.95)] };
  };
  const holding = await until('the hold', async () => /holding/.test(await stats()), 60000);
  const held = await frameMs();
  note(`holding after ${holding} ms more; frame time while holding: median ${held.median.toFixed(1)} ms, p95 ${held.p95.toFixed(1)} ms`);
  await shot('holding');
  // A closure chosen by hand ends the hold (any request does) and is what the status then says.
  await panel.locator('[data-ws="fast-turbulence"]').selectOption('smagorinsky');
  await until('the closure', async () => (await page.evaluate(() => window.__stokes.getState().execute('fast.status', {}))).turbulence === 'smagorinsky', 10000);
  const busy = await frameMs();
  note(`frame time while stepping flat out: median ${busy.median.toFixed(1)} ms, p95 ${busy.p95.toFixed(1)} ms`);
  await panel.locator('[data-ws="fast-turbulence"]').selectOption('auto');

  console.log('6. Another value, another plane');
  await panel.locator('[data-ws="fast-field"]').selectOption('p');
  await page.waitForTimeout(1200);
  await shot('pressure');
  await panel.locator('[data-ws="fast-field"]').selectOption('U');
  await panel.locator('[data-ws="fast-plane"]').selectOption('0');
  await until('the plane normal to x', async () => (await page.evaluate(() => window.__stokes.getState().execute('fast.status', {}))).frame.axis === 0, 10000);
  await page.waitForTimeout(800);
  await shot('plane-x');
  // The plane moved along its axis with the slider, a quarter of the way down the duct.
  await panel.locator('[data-ws="fast-position"]').focus();
  await page.keyboard.press('Home');
  for (let i = 0; i < 25; i += 1) await page.keyboard.press('ArrowRight');
  await until('the plane a quarter along', async () => {
    const s = await page.evaluate(() => window.__stokes.getState().execute('fast.status', {}));
    return Math.abs((s.frame.plane + 0.5) / s.grid.nx - 0.25) < 0.02;
  }, 10000);
  await page.waitForTimeout(800);
  await shot('plane-x-moved');
  await panel.locator('[data-ws="fast-plane"]').selectOption('-1');
  await until('the automatic plane again', async () => (await page.evaluate(() => window.__stokes.getState().execute('fast.status', {}))).automatic_plane === true, 10000);
  await page.waitForTimeout(1200);

  console.log('7. A higher fidelity while it runs');
  await panel.locator('[data-ws="fast-fidelity"]').selectOption('3');
  // The finer lattice takes the running one's flow, and the status says so until it is steady.
  await until('the warm start from fidelity 2 is said', async () => /Warm start from fidelity 2/.test(await stats()), 20000);
  await shot('warm-from-fidelity-2');
  await until('fidelity 3', async () => /Fidelity 3/.test(await stats()) && /updates\/s/.test(await stats()), 30000);
  note(await stats());
  await shot('fidelity-3');
  await panel.locator('[data-ws="fast-fidelity"]').selectOption('2');
  await until('fidelity 2', async () => /Fidelity 2/.test(await stats()), 30000);

  console.log('8. A faster inlet while it runs (the turbulent preview)');
  const tree = await page.evaluate(() => window.__stokes.getState().tree);
  const find = (node, test) => (test(node) ? node : (node.children ?? []).map((c) => find(c, test)).find(Boolean));
  const inlet = find(tree, (x) => x.type === 'boundary' && x.params?.kind === 'velocity_inlet');
  if (!inlet) fail('no velocity inlet in the tree');
  else {
    await rpc('parameter.set', { node: inlet.id, name: 'velocity', value: '2 [m/s]' });
    await until('the turbulent preview', async () => /LES/.test(await stats()), 30000);
    await page.waitForTimeout(4000);
    note(await stats());
    await shot('turbulent');
    await rpc('parameter.set', { node: inlet.id, name: 'velocity', value: '0.05 [m/s]' });
    await until('laminar again', async () => /laminar/i.test(await stats()), 30000);
  }

  console.log('9. Stop and start again');
  await stop();
  note(`after stop: ${await panelText()}`);
  await shot('stopped');
  if (await page.locator('[data-ws="fast-legend"]').count()) fail('the legend stayed after Stop');
  await panel.locator('[data-ws="fast-start"]').click();
  await until('numbers again', async () => /updates\/s/.test(await stats()), 30000);
  await shot('restarted');

  console.log('10. No inlet');
  await stop();
  if (inlet) {
    await rpc('parameter.set', { node: inlet.id, name: 'kind', value: 'wall' });
    await panel.locator('[data-ws="fast-start"]').click();
    await until('the refusal', async () => /inlet/i.test(await alerts()), 10000);
    note(`refused: ${await alerts()}`);
    await shot('no-inlet');
    // Undo brings the inlet back (the kind is model state), and the preview starts again.
    await rpc('history.undo');
    await panel.locator('[data-ws="fast-start"]').click();
    await until('numbers after undo', async () => /updates\/s/.test(await stats()), 30000);
    await stop();
  }

  // Round 4 (ADR-0047 addendum 4): the closures chosen by hand, 16-bit storage, and a fine lattice
  // that settles a coarse one first and starts from its flow.
  console.log('10b. WALE, 16-bit storage and a warm start at fidelity 4');
  const closures = await panel.locator('[data-ws="fast-turbulence"] option').allInnerTexts();
  if (!closures.some((c) => /WALE/.test(c)) || !closures.some((c) => /Vreman/.test(c))) fail(`the closures offered are ${closures.join(', ')}`);
  await panel.locator('[data-ws="fast-turbulence"]').selectOption('wale');
  await panel.locator('[data-ws="fast-storage"]').selectOption('f16');
  if (!(await panel.locator('[data-ws="fast-warm"]').isChecked())) fail('the warm start is not on by default');
  await panel.locator('[data-ws="fast-fidelity"]').selectOption('4');
  await shot('round4-options');
  await panel.locator('[data-ws="fast-start"]').click();
  const warm = await until('the coarse pass of the warm start', async () => /Warm start/.test(await stats()), 30000);
  note(`warm start said after ${warm} ms: ${await stats()}`);
  if (!(await statusInView())) fail('the warm start line is not in view without scrolling');
  await shot('round4-warming');
  const fine = await until('fidelity 4 in 16 bits', async () => /Fidelity 4/.test(await stats()) && /16-bit/.test(await stats()) && !/Warm start/.test(await stats()), 120000);
  note(`fine lattice after ${fine} ms more: ${await stats()}`);
  const settled = await until('steady at fidelity 4', async () => /Steady/.test(await stats()), 240000);
  note(`steady after ${settled} ms more: ${await stats()}`);
  if (!(await statusInView())) fail('the status lines are not in view without scrolling at fidelity 4');
  await shot('round4-fidelity-4-16-bit');
  const round4 = await rpc('fast.status');
  if (round4.storage !== 'f16' || round4.turbulence !== 'wale' || round4.warm_start !== true) fail(`the status says storage ${round4.storage}, turbulence ${round4.turbulence}, warm start ${round4.warm_start}`);
  if (await panel.locator('[data-ws="fast-storage"]').isEnabled()) fail('the storage can be changed while a preview runs');
  await panel.locator('[data-ws="fast-turbulence"]').selectOption('vreman');
  await until('the Vreman closure', async () => (await rpc('fast.status')).turbulence === 'vreman', 10000);
  await stop();
  await panel.locator('[data-ws="fast-turbulence"]').selectOption('auto');
  await panel.locator('[data-ws="fast-storage"]').selectOption('f32');
  await panel.locator('[data-ws="fast-fidelity"]').selectOption('2');

  // 11. A conjugate project (optional fourth argument: a saved project with two fluids and a
  // solid, such as the plate exchanger): the panel names the bodies, offers the temperature, and
  // lists each stream as the Results stage's Streams table does.
  const conjugate = process.argv[5];
  if (conjugate) {
    console.log('11. A conjugate project');
    await walk.openProject(conjugate, 'conjugate');
    await page.waitForTimeout(4000);
    await page.locator('[data-ws="fast-toggle"]').click();
    await panel.waitFor({ timeout: 10000 });
    await until('the GPU check', async () => /GPU:/.test(await panelText()), 20000);
    await until('Start can be pressed', async () => !(await panel.locator('[data-ws="fast-start"]').isDisabled()), 120000);
    // STOKES_WALK_FIDELITY (1 to 5) is the fidelity the conjugate project is previewed at, and
    // STOKES_WALK_SETTLE the seconds it is given before its figures are read (25 without).
    if (process.env.STOKES_WALK_FIDELITY) await panel.locator('[data-ws="fast-fidelity"]').selectOption(process.env.STOKES_WALK_FIDELITY);
    await panel.locator('[data-ws="fast-start"]').click();
    await until('the bodies named', async () => (await panel.locator('[data-ws="fast-zones"]').count()) > 0, 60000);
    note(`previewing: ${await panel.locator('[data-ws="fast-zones"]').innerText()}`);
    await panel.locator('[data-ws="fast-field"]').selectOption('T');
    await until('the streams', async () => /K/.test(await panel.locator('[data-ws="fast-streams"]').innerText().catch(() => '')), 60000);
    await page.waitForTimeout(1000 * Number(process.env.STOKES_WALK_SETTLE ?? 25));
    note(`streams: ${(await panel.locator('[data-ws="fast-streams"]').innerText()).split('\n').join(' | ')}`);
    note(`legend: ${(await page.locator('[data-ws="fast-legend"]').innerText()).split('\n').join(' ')}`);
    // Passages a few cells across are said to be so: the picture is a guide, the numbers are not.
    const coarse = await panel.locator('[data-ws="fast-coarse"]').innerText().catch(() => '');
    note(coarse ? `said: ${coarse}` : 'resolved: no under-resolved note');
    await shot('temperature');
    await panel.locator('[data-ws="fast-plane"]').selectOption('2');
    await page.waitForTimeout(2500);
    await shot('temperature-plane-z');
    await panel.locator('[data-ws="fast-field"]').selectOption('U');
    await page.waitForTimeout(2500);
    await shot('velocity-plane-z');
    await stop();
  }
} catch (e) {
  fail(`the walk stopped: ${e.message.split('\n')[0]}`);
  await shot('stopped-here').catch(() => {});
}
await walk.finish();
process.exit(problems.length ? 1 : 0);
