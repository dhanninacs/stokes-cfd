// Records the walkthrough "flow past a ball: implicit CAD, a Fast preview, then OpenFOAM" through the GUI
// on a NEW project. The case is the simplest one that shows all three: a 60 x 20 x 20 mm box less a 4 mm
// sphere (Create ribbon: Box, Sphere, Subtract), air at 0.05 m/s from one end to the other, so the
// duct has a ball-shaped hole in the middle and the flow goes round it.
//
//   set STOKES_WALK_VIDEO=<folder>        (walk_lib records the page there as an .mp4, with an installed ffmpeg)
//   set STOKES_WALK_VIEWPORT=1280x720     (16:9; the app's text is then large when the post is scaled up)
//   set STOKES_WALK_FPS=24
//   node record_ball_walkthrough.mjs "<url-with-token>" "<projects folder>" [shots] [--cell=0.6] [--iterations=400] [--residual=1e-5] [--fidelity=3]
//
// Geometry (Full mode, the ribbon; a section shows the ball's hole) > Guided: Fluid (Air) > Boundaries >
// Fast mode (Front view, Turbo, live plane until steady, an edit while it runs, a finer lattice) > Mesh
// (snappyHexMesh with boundary layers, the mesh cut through the ball) > Run > Results (same view, map and
// range as Fast mode). The two pictures to compare are saved as crops (`fast-view.png`, `foam-view.png`),
// with `numbers.json`; `compose_ball.mjs` makes the side-by-side ending. A server with NO project open
// must be running. Every step is captioned on the page (a pill the script adds; it is not part of the
// app). `marks.json` lists the captions and the waits (`cut_walkthrough.mjs` runs the waits fast) and a
// `start` mark: the moment the project exists. Everything before it (the New project dialog shows the
// computer's default folder, and the start screen the recent projects) is trimmed away by the compose
// step, so the video never shows either.
//
// Nothing that names the person or the machine goes on screen: the projects folder is a fresh
// sub-folder of the one given, so a re-run never meets an earlier project of the same name.
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [rawUrl, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!rawUrl || !folder) { console.error('usage: node record_ball_walkthrough.mjs <url-with-token> <projects folder> [shots] [--cell=0.6] [--iterations=400] [--residual=1e-5] [--fidelity=3]'); process.exit(2); }
const url = rawUrl.includes('drive=1') ? rawUrl : `${rawUrl}&drive=1`;
const shots = shotsArg ?? join(process.cwd(), '..', '.local', 'walkthrough-ball');
const cell = flag('cell', '0.6');
const iterations = flag('iterations', '400');
const residual = flag('residual', '1e-5');
const fidelity = flag('fidelity', '3');
const MAP = flag('map', 'turbo');

const w = await openWalk(url, join(folder, `run-${Date.now() % 1000000}`), shots);
const { page, visible } = w;
const t0 = w.recorder?.t0 ?? Date.now();
const marks = [];
const numbers = { residual };
const mark = (kind, label) => { marks.push({ t: (Date.now() - t0) / 1000, kind, label }); writeFileSync(join(shots, 'marks.json'), JSON.stringify(marks, null, 1)); };
const card = page.locator('.guided-card');
const panel = page.locator('[data-ws="fast-panel"]');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
const panelText = async () => (await panel.first().innerText().catch(() => '')).split('\n').join(' | ');
const stats = async () => (await panel.locator('[data-ws="fast-stats"]').innerText().catch(() => '')).split('\n').join(' | ');
const pause = (ms) => page.waitForTimeout(ms);

/** The caption pill: what the viewer is looking at, in a sentence (top of the view, clear of the toolbars). */
async function caption(text, hold = 2400) {
  console.log(`\n# ${text}`);
  mark('caption', text);
  await page.evaluate((t) => {
    let el = document.getElementById('walk-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'walk-caption';
      el.style.cssText = 'position:fixed;left:50%;top:60px;transform:translateX(-50%);z-index:2147483000;max-width:640px;padding:10px 18px;border-radius:12px;background:rgba(16,20,26,.92);color:#fff;font:600 24px/1.3 Inter,system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.35);pointer-events:none;text-align:center;transition:opacity .25s';
      document.body.appendChild(el);
    }
    el.textContent = t;
    el.style.opacity = '1';
  }, text);
  await pause(hold);
}
/** Take the caption down (one that no longer describes what is on screen). */
async function uncaption() {
  await page.evaluate(() => { const el = document.getElementById('walk-caption'); if (el) el.style.opacity = '0'; });
  await pause(300);
}

/** A wait the cut runs fast: from now until `test` holds. Soft: false instead of a failure. */
async function wait(label, test, ms, soft = false) {
  mark('wait-start', label);
  const started = Date.now();
  let ok = false;
  while (Date.now() - started < ms) { if (await test()) { ok = true; break; } await pause(500); }
  mark('wait-end', label);
  if (!ok) {
    if (soft) { console.log(`  ${label}: not within ${Math.round(ms / 1000)} s`); return false; }
    w.fail(`timed out waiting for ${label}`);
    throw new Error(`timeout: ${label}`);
  }
  console.log(`  ${label}: ${Math.round((Date.now() - started) / 1000)} s`);
  return true;
}
const fastStatus = () => page.evaluate(() => window.__stokes.getState().execute('fast.status', {}));
/** Steady at a fidelity, by the preview's own flag (the status text can still show the last run's word). */
const steadyAt = (fid) => async () => { const s = await fastStatus().catch(() => null); return !!s && s.fidelity === Number(fid) && s.frame?.steady === true && s.frame.step > 2000; };
/** The lattice restarted at a fidelity and has not settled yet (so the next steady is its own). */
const restartedAt = (fid) => async () => { const s = await fastStatus().catch(() => null); return !!s && s.fidelity === Number(fid) && s.frame?.steady === false; };

/** One of the view buttons (Front, Iso ...) and Fit, by their labels. */
async function view(name) {
  await page.locator('[data-ws="view-buttons"]').getByText(name, { exact: true }).click().catch(() => w.fail(`no ${name} view button`));
  await pause(700);
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await pause(1200);
}
const setMap = (id) => page.evaluate((m) => window.__stokes.setState((s) => ({ results: { ...s.results, view: { ...s.results.view, map: m } } })), id);
const setSection = (on) => page.evaluate((o) => { const s = window.__stokes.getState(); if (o) { s.putSectionAt('z', 0); s.setSectionInvert(true); } else s.setSection(false); }, on);

try {
  await page.goto(url);
  await pause(1200);

  // ------------------------------------------------------------------ 1. the project and the geometry
  await w.newProject('ball', { exact: true });
  mark('start', 'the project exists');
  await caption('1 · Geometry: a box and a ball', 1500);
  mark('wait-start', 'Building the geometry');
  await w.tool('shape', 'Box');
  await w.setField('Size x', '60 [mm]'); await w.setField('Size y', '20 [mm]'); await w.setField('Size z', '20 [mm]');
  await w.run('box');
  await w.tool('shape', 'Sphere');
  await w.setField('Radius', '4 [mm]');
  await w.run('sphere');
  for (const group of ['Geometry', 'Bodies']) {
    const open = page.getByRole('button', { name: `Expand ${group}` });
    if (await open.count()) await open.first().click();
    await pause(300);
  }
  await w.pick('box');
  await w.pick('sphere', true);
  await w.tool('combine', 'Subtract');
  await w.run('subtract', 120000);
  await pause(800);
  mark('wait-end', 'Building the geometry');
  await caption('Subtract the ball from the box: a ball-shaped hole', 800);
  // The build ribbon is put away so the view is clear; a section from a corner shows the hole.
  if (await page.locator('[data-ws="build-ribbon"]').count()) await page.getByRole('button', { name: 'Create', exact: true }).first().click().catch(() => {});
  await pause(600);
  await setSection(true);
  await pause(1500);
  await view('Iso');
  await w.shot('cad-section');
  await pause(3500);
  await setSection(false);
  await uncaption();

  // ------------------------------------------------------------------ 2. fluid and boundaries
  await page.getByRole('radio', { name: 'Guided' }).click();
  await pause(1000);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await wait('the body is the fluid', async () => /is the fluid/.test(await cardText()), 60000);
  await caption('2 · Fluid: air');
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await visible(card.getByText('Air', { exact: true })).click();
  await wait('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);
  await caption('3 · Boundaries: air in at one end, out at the other');
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  const pickEnd = async () => {
    const row = card.getByText(/flat.* · 4 cm²/).first();
    await row.locator('xpath=..').getByRole('button', { name: 'Pick' }).click();
  };
  await pickEnd();
  await visible(card.locator('input')).fill('0.05');
  await visible(card.getByRole('button', { name: 'Inlet' })).click();
  await pause(1200);
  await pickEnd();
  await visible(card.getByRole('button', { name: 'Outlet' })).click();
  await wait('2 boundaries', async () => /2 boundaries/.test(await cardText()), 20000);
  await pause(1200);
  await uncaption();

  // ------------------------------------------------------------------ 3. Fast mode
  await caption('4 · Fast mode: the same case on the GPU, drawn live');
  await setMap(MAP);
  await page.locator('[data-ws="fast-toggle"]').click();
  await panel.waitFor({ timeout: 10000 });
  await wait('the GPU check', async () => /GPU:|not available|needs/.test(await panelText()), 20000);
  await panel.locator('[data-ws="fast-fidelity"]').selectOption(fidelity);
  // The plane normal to z, as the OpenFOAM slice will be, seen face-on.
  await panel.locator('[data-ws="fast-plane"]').selectOption('2');
  await view('Front');
  await panel.locator('[data-ws="fast-start"]').click();
  await wait('the first numbers', async () => /updates\/s/.test(await stats()), 60000);
  await caption('Speed in the middle plane, as it settles: the wake forms behind the ball', 3500);
  await wait('Fast mode settling', steadyAt(fidelity), 400000);
  await pause(1500);
  // A value edited while it runs: the flow follows within seconds.
  const tree = await page.evaluate(() => window.__stokes.getState().tree);
  const find = (node, test) => (test(node) ? node : (node.children ?? []).map((c) => find(c, test)).find(Boolean));
  const inlet = find(tree, (x) => x.type === 'boundary' && x.params?.kind === 'velocity_inlet');
  const setInlet = (v) => page.evaluate(([id, value]) => window.__stokes.getState().execute('parameter.set', { node: id, name: 'velocity', value }), [inlet.id, v]);
  const settled = async (label) => {
    await wait(`${label} (edit)`, async () => !(await steadyAt(fidelity)()), 8000, true);
    await wait(label, steadyAt(fidelity), 400000);
  };
  if (!inlet) w.fail('no velocity inlet in the tree');
  else {
    await caption('Change the inlet speed while it runs: the flow follows, live', 1500);
    await setInlet('0.1 [m/s]');
    await settled('Fast mode, faster inlet');
    await pause(2500);
    await setInlet('0.05 [m/s]');
    await settled('Fast mode, back');
    await pause(1000);
  }
  await caption('More resolution, live: raise the fidelity while it runs', 1500);
  // The finest takes a few minutes to settle; if it has not within 15 minutes, the one below is used.
  await panel.locator('[data-ws="fast-fidelity"]').selectOption('5');
  await wait('Fast mode finest (restart)', restartedAt(5), 20000, true);
  numbers.fastLabel = 'Fast mode finest';
  if (!(await wait('Fast mode finest', steadyAt(5), 900000, true))) {
    await panel.locator('[data-ws="fast-fidelity"]').selectOption('4');
    await wait('Fast mode finer (restart)', restartedAt(4), 20000, true);
    await wait('Fast mode finer', steadyAt(4), 300000);
    numbers.fastLabel = 'Fast mode finer';
  }
  await pause(1500);
  numbers.fastStats = await stats();
  numbers.fastStatus = await fastStatus();
  const fastLegendText = await panel.locator('[data-ws="fast-legend"]').innerText().catch(() => '');
  numbers.fastMax = Number((fastLegendText.match(/[\d.]+/g) ?? []).map(Number).filter((n) => n > 0).pop());
  await w.shot('fast-steady');
  await page.screenshot({ path: join(shots, 'fast-view.png'), clip: CLIP() });
  await panel.locator('[data-ws="fast-legend"]').screenshot({ path: join(shots, 'fast-legend.png') }).catch(() => {});
  await panel.locator('[data-ws="fast-field"]').selectOption('p');
  await caption('Pressure: high in front of the ball, low behind it', 3500);
  await panel.locator('[data-ws="fast-field"]').selectOption('U');
  await pause(800);
  await panel.locator('[data-ws="fast-stop"]').click();
  await panel.locator('[data-ws="fast-start"]').waitFor({ timeout: 10000 });
  // The panel has no close button: it is put away here so it does not cover the next views.
  await page.addStyleTag({ content: '[data-ws="fast-panel"]{display:none !important}' });
  await uncaption();

  // ------------------------------------------------------------------ 4. OpenFOAM
  await caption('5 · Mesh: snappyHexMesh, with boundary layers on every wall');
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  await pause(800);
  const layers = card.getByRole('switch', { name: /Boundary layers/ });
  if (await layers.count()) {
    if ((await layers.first().getAttribute('aria-checked')) !== 'true') { await layers.first().click(); await pause(800); }
  } else w.fail('no Boundary layers switch on the Mesh card');
  numbers.meshCard = await cardText();
  const size = visible(card.getByLabel('Cell size in millimetres'));
  await size.fill(cell); await size.press('Tab');
  await pause(600);
  await w.shot('mesh-card');
  await visible(card.getByRole('button', { name: 'Generate' })).click();
  await wait('the mesh', async () => /Next: Run/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 1500000);
  numbers.meshDone = await cardText();
  await w.shot('mesh-done');
  await caption('A cut through the ball: the cells, and the thin layers hugging each wall', 600);
  await setSection(true);
  await pause(2000);
  await view('Front');
  await w.shot('mesh-cut');
  await pause(2500);
  // Zoom to the ball so the layers show.
  const box = await page.locator('canvas').first().boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    for (let i = 0; i < 6; i += 1) { await page.mouse.wheel(0, -300); await pause(350); }
    await pause(1200);
    await w.shot('mesh-cut-zoom');
    await pause(3500);
  }
  await setSection(false);
  await view('Front');
  await pause(800);
  await caption('6 · Run: OpenFOAM solves the same case');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await pause(800);
  const its = card.getByLabel('Number of iterations');
  if (await its.count()) { await its.fill(iterations); await its.press('Tab'); } else w.fail('no iterations field');
  const target = card.getByLabel('Residual target');
  if (await target.count()) { await target.fill(residual); await target.press('Tab'); } else w.fail('no residual target field');
  await pause(500);
  await w.shot('run-card');
  await visible(card.getByRole('button', { name: 'Run', exact: true })).click();
  await wait('the run', async () => /Next: Results/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 1500000);
  await w.shot('run-done');
  await caption('7 · Results: the solved flow', 1800);
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await pause(5000);
  await setMap(MAP);
  await view('Front');
  // The same range as Fast mode's legend (its own is fixed by its data): Manual, 0 to its top.
  const manual = card.getByRole('radio', { name: 'Manual', exact: true });
  await ((await manual.count()) ? manual : card.getByRole('button', { name: 'Manual', exact: true })).first().click().catch(() => w.fail('no Manual range'));
  await pause(500);
  const max = card.getByLabel('Max', { exact: true });
  if (await max.count() && Number.isFinite(numbers.fastMax)) { await max.fill(String(numbers.fastMax)); await max.press('Tab'); } else w.fail('could not set the range');
  // The wall surfaces off, so the ball is drawn as the part itself, as Fast mode draws it.
  await card.getByText('unassigned', { exact: true }).first().click().catch(() => w.note('no unassigned chip'));
  await pause(2500);
  numbers.foamCard = await cardText();
  await w.shot('results');
  // The legend card sits over a corner of the view: hidden for the crop.
  await page.addStyleTag({ content: '.field-legend{visibility:hidden !important}' });
  await pause(500);
  await page.screenshot({ path: join(shots, 'foam-view.png'), clip: CLIP() });
  await pause(500);
  writeFileSync(join(shots, 'numbers.json'), JSON.stringify(numbers, null, 1));
  mark('end', 'done');
} catch (e) {
  w.fail(`stopped: ${e.message.split('\n')[0]}`);
  await w.shot('stopped').catch(() => {});
  writeFileSync(join(shots, 'numbers.json'), JSON.stringify(numbers, null, 1));
}
await w.finish();
process.exit(w.problems.length ? 1 : 0);

/** The part of the page both pictures are cut from: the middle of the view, clear of the cards. */
function CLIP() {
  const [vw, vh] = (process.env.STOKES_WALK_VIEWPORT ?? '1440x900').split('x').map(Number);
  const W = vw || 1440;
  const H = vh || 900;
  return { x: Math.round(W * 0.215), y: Math.round(H * 0.3), width: Math.round(W * 0.57), height: Math.round(H * 0.42) };
}
