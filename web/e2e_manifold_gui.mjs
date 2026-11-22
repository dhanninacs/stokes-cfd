// The flow-split manifold through the GUI on a NEW project, ending in the optimised geometry:
// start screen > New project > the manifold (one inlet, four outlets, a header whose last three segments
// have the heights h2, h3, h4) > Fluid (Air) > Boundaries (the inlet 0.2 m/s, the four outlet caps 0 Pa,
// the rest wall) > Mesh > Run > Results > Optimise (Equal flow split, finite differences, h2..h4) >
// Apply best point (the part rebuilt, its boundaries found again) > the final geometry, meshed again.
//
// **The geometry is built through the API over the server's own WebSocket**, with the commands of
// `tools/manifold_demo.py` (parameter.define, then implicit operations: boxes whose heights are h2..h4,
// round pipes, a linear pattern, a blended union and a sharp clip). Typing eleven operations into the
// build ribbon is `build_elbow_gui.mjs`'s job; this walk is about the flow split. Everything after the
// geometry is pressed in the GUI, except the solver's iteration limit and residual target (a flow split is
// only measured on a converged run), set through the same socket before the first run.
//
// A server with NO project must be running (`python -m stokes.server --port N`); the folder is given.
//
//   node e2e_manifold_gui.mjs "<url-with-token>" "<empty folder for projects>" [shots-folder] [--until-start] [--base=2]
//
// `--until-start` stops once the first optimisation cycle is under way (about ten minutes: the setup run);
// without it the whole optimisation runs (each cycle is N + 1 = 4 flow solves; an hour or more).
// `--base` is the mesh's base cell in mm (default 2; the measured demonstration used 1).
// Prints what each step shows and every problem seen: page errors, failed requests, alerts.

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const args = process.argv.slice(2);
const flags = args.filter((a) => a.startsWith('--'));
const [url, folder, shots = join(process.cwd(), '..', '.local', 'e2e-manifold-shots')] = args.filter((a) => !a.startsWith('--'));
if (!url || !folder) { console.error('usage: node e2e_manifold_gui.mjs <url> <projects folder> [shots] [--until-start] [--base=2]'); process.exit(2); }
const untilStart = flags.includes('--until-start');
const base = Number((flags.find((f) => f.startsWith('--base=')) ?? '--base=2').slice(7)) || 2;
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

// ------------------------------------------------------------------ the API, over the page's server
/** One command over a WebSocket of our own to the page's server (the GUI's socket is the app's). */
async function rpc(method, params = {}) {
  return page.evaluate(async ([method, params]) => {
    const q = new URLSearchParams(location.search);
    const api = (q.get('api') ?? location.origin).replace(/\/$/, '');
    if (!window.__e2eSocket || window.__e2eSocket.readyState > 1) {
      const ws = new WebSocket(`${api.replace(/^http/, 'ws')}/ws?token=${encodeURIComponent(q.get('token') ?? '')}`);
      window.__e2eSocket = ws;
      window.__e2ePending = new Map();
      window.__e2eId = 1;
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(String(ev.data)); } catch { return; }
        const p = msg.id !== undefined ? window.__e2ePending.get(msg.id) : undefined;
        if (!p) return;
        window.__e2ePending.delete(msg.id);
        if (msg.error) p.reject(new Error(`${msg.error.code}: ${msg.error.message}`)); else p.resolve(msg.result);
      };
      await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cannot reach the server')); });
    }
    const id = window.__e2eId++;
    return new Promise((resolve, reject) => {
      window.__e2ePending.set(id, { resolve, reject });
      window.__e2eSocket.send(JSON.stringify({ id, method, params }));
    });
  }, [method, params]);
}

/** Create an implicit operation and wait until its run has made its outputs; the first output's id. */
async function operation(type, name, params, inputs = []) {
  const made = await rpc('operation.create', { type, name, params, inputs });
  const op = made.id ?? made.node_id;
  const t0 = Date.now();
  while (Date.now() - t0 < 180000) {
    const info = await rpc('operation.info', { op });
    if (info.status === 'failed') throw new Error(`${name}: ${info.error ?? 'failed'}`);
    if (info.status === 'ok' && info.outputs.length > 0 && info.state?.state === 'idle') return info.outputs[0];
    await page.waitForTimeout(500);
  }
  throw new Error(`${name} did not finish`);
}

const mm = (v) => `${v} [mm]`;
/** A box between two corners (mm); a top given as a parameter name grows with it from y = 0. */
async function box(name, low, high) {
  const size = [];
  const position = [];
  low.forEach((lo, i) => {
    const hi = high[i];
    if (typeof hi === 'string') { size.push(hi); position.push(`${hi} / 2`); } else { size.push(mm(hi - lo)); position.push(mm((hi + lo) / 2)); }
  });
  return operation('implicit_primitive', name, { kind: 'box', size, position });
}

/** The manifold of tools/manifold_demo.py (mm): its fluid part's id. */
async function buildManifold() {
  for (const h of ['h2', 'h3', 'h4']) await rpc('parameter.define', { name: h, dimension: 'length', value: mm(16) });
  const first = await box('S1', [10, 0, -4], [45, 16, 4]);
  const segments = [];
  for (const [i, [x0, x1, h]] of [[45, 75, 'h2'], [75, 105, 'h3'], [105, 130, 'h4']].entries()) segments.push(await box(`S${i + 2}`, [x0, 0, -4], [x1, h, 4]));
  const inlet = await operation('implicit_primitive', 'inlet_pipe', { kind: 'cylinder', radius: mm(4), length: mm(27), axis: 'x', position: [mm(-1.5), mm(8), mm(0)] });
  const stub = await operation('implicit_primitive', 'outlet_pipe', { kind: 'cylinder', radius: mm(3.5), length: mm(32), axis: 'y', position: [mm(30), mm(-14), mm(0)] });
  const outlets = await operation('implicit_pattern', 'outlets', { kind: 'linear', axis: 'x', count: 4, pitch: mm(30) }, [stub]);
  const union = await operation('implicit_boolean', 'header', { op: 'union', tools: [...segments, inlet, outlets], blend_radius: mm(2) }, [first]);
  const clip = await box('clip', [0, -20, -10], [135, 30, 10]);
  return operation('implicit_boolean', 'manifold', { op: 'intersection', tools: [clip] }, [union]);
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
  }, ['manifold', folder]);
  await visible(page.getByText('Create', { exact: true })).click();
  await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });

  // 2. Geometry (through the API; see the header).
  console.log('2. Geometry');
  const t0 = Date.now();
  const part = await buildManifold();
  note(`manifold built in ${Math.round((Date.now() - t0) / 1000)} s: part ${part}`);
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  await page.waitForTimeout(1000);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await until('the fluid body', async () => /is the fluid/.test(await cardText()), 60000);
  note(await cardText());
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await shot('geometry');

  // 3. Fluid.
  console.log('3. Fluid');
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await visible(card.getByText('Air', { exact: true })).click();
  await until('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);

  // 4. Boundaries: the inlet disk faces -X; the four outlet caps face -Y. The rest stays wall.
  console.log('4. Boundaries');
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  // The card lists the first eight unassigned faces: a face it lists is picked there and assigned with the
  // card's chip; one further down the list (the manifold has about thirty) is found with the card's own query
  // (`boundary.uncovered`, described) and assigned through the API, as clicking it in the view would.
  let viaApi = 0;
  const assignFace = async (pattern, axis, sign, chip, kind, values) => {
    const row = card.locator('[data-ws="guided-unassigned-face"]').filter({ hasText: pattern }).first();
    if (await row.count()) {
      await row.getByRole('button', { name: 'Pick' }).click();
      await visible(card.locator(`[data-ws="${chip}"]`)).click();
      return;
    }
    viaApi += 1;
    const answer = await rpc('boundary.uncovered', { part, describe: true });
    const faces = answer.parts.find((p) => p.part === part)?.described ?? [];
    const face = faces.find((f) => f.flat && Array.isArray(f.normal) && sign * f.normal[axis] > 0.99);
    if (!face) throw new Error(`no unassigned flat face facing ${sign > 0 ? '+' : '-'}${'XYZ'[axis]}`);
    await rpc('boundary.assign', { target: [{ part, faces: [face.face] }], kind, values });
  };
  const bounds = async () => card.locator('[data-ws="guided-boundary"]').count();
  await visible(card.getByLabel('Inlet speed in m/s')).fill('0.2');
  for (let k = 1; k <= 4; k += 1) {
    await assignFace(/facing −Y/, 1, -1, 'guided-outlet', 'pressure_outlet', { pressure: '0 [Pa]' });
    await until(`outlet ${k}`, async () => (await bounds()) === k, 20000);
  }
  await assignFace(/facing −X/, 0, -1, 'guided-inlet', 'velocity_inlet', { velocity: '0.2 [m/s]' });
  await until('the inlet', async () => (await bounds()) === 5, 20000);
  if (viaApi) note(`${viaApi} of 5 boundaries assigned through the API: the card listed only the first faces`);
  note(await cardText());
  await shot('boundaries');

  // The flow split is only measured on a converged run: a long iteration limit and a tight residual.
  await rpc('solve.configure', { values: { turbulence: 'laminar', iterations: 2000, residual_target: 1e-5, write_interval: 2000 } });

  // 5. Mesh.
  console.log('5. Mesh');
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  const size = visible(card.getByLabel('Cell size in millimetres'));
  await size.fill(String(base));
  await size.press('Tab');
  await visible(card.getByRole('button', { name: 'Generate' })).click();
  await until('the mesh', async () => /Next: Run/.test(await cardText()), 600000);
  note(await cardText());
  await shot('mesh');

  // 6. Run the ordinary solve.
  console.log('6. Run');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await visible(card.getByRole('button', { name: 'Run', exact: true })).click();
  await until('the run', async () => /Next: Results/.test(await cardText()), 900000);
  note(await cardText());

  // 7. Results, then Optimise from the Results card.
  console.log('7. Results');
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await page.waitForTimeout(3000);
  await shot('results');
  await page.locator('[data-ws="guided-optimise"]').click();
  const panel = page.locator('[data-ws="optimise-panel"]');
  await panel.waitFor({ timeout: 10000 });

  // 8. The flow split: every outlet, a one-point target, finite differences, h2..h4 from 6 to 26 mm.
  console.log('8. Optimise (flow split)');
  await panel.locator('[data-ws="optimise-objective-split"]').check();
  const outlets = await panel.locator('[data-ws="optimise-outlet"]').allInnerTexts();
  note(`outlets offered: ${outlets.join(', ')}`);
  if (outlets.length !== 4) fail(`the panel offers ${outlets.length} outlets, not 4`);
  if (!(await panel.locator('[data-ws="optimise-gradient-adjoint"]').isDisabled())) fail('the adjoint is offered for the flow split');
  if (!(await panel.locator('[data-ws="optimise-gradient-fd"]').isChecked())) fail('finite differences are not chosen for the flow split');
  await panel.locator('[data-ws="optimise-target"]').fill('1');
  await panel.locator('[data-ws="optimise-fd-step"]').fill('5');
  await until('the part\'s variables', async () => (await panel.getByLabel('Variable 1', { exact: true }).locator('option').count()) > 2, 30000);
  for (const [i, h] of ['h2', 'h3', 'h4'].entries()) {
    if (i > 0) await panel.getByRole('button', { name: 'Add variable' }).click();
    await panel.getByLabel(`Variable ${i + 1}`, { exact: true }).selectOption(h);
    await panel.getByLabel(`Variable ${i + 1} lower bound (mm)`).fill('6');
    await panel.getByLabel(`Variable ${i + 1} upper bound (mm)`).fill('26');
  }
  await panel.getByLabel('Maximum cycles').fill(untilStart ? '1' : '8');
  note(`gradient: ${(await panel.locator('.optimise-choice small').allInnerTexts()).join(' / ')}`);
  await shot('optimise-ready');

  await panel.locator('[data-ws="optimise-start"]').click();
  const alert = panel.locator('[role="alert"]');
  await until('a cycle under way', async () => (await panel.locator('[data-ws="optimise-step"]').count()) > 0 || (await alert.count()) > 0, 60000);
  if (await alert.count()) throw new Error(`the optimisation was refused: ${await alert.innerText()}`);
  await until('the first step of a cycle', async () => /Cycle 1 .* · /.test(await panel.locator('[data-ws="optimise-step"]').innerText().catch(() => '')) && !/starting/.test(await panel.locator('[data-ws="optimise-step"]').innerText().catch(() => 'starting')), 120000);
  note(`running: ${(await panel.locator('[data-ws="optimise-step"]').innerText()).split('\n').join(' | ')}`);
  await shot('cycle-under-way');

  if (untilStart) {
    // Stop the cycle under way: the walk has shown the panel start one.
    await panel.locator('[data-ws="optimise-cancel-now"]').click();
    await until('the run to stop', async () => (await panel.locator('[data-ws="optimise-start"]').isEnabled()), 300000);
    note(`stopped: ${await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '(no status)')}`);
  } else {
    await until('the optimisation', async () => (await panel.locator('[data-ws="optimise-apply"]').count()) > 0 && (await panel.locator('[data-ws="optimise-start"]').isEnabled()), 6 * 3600000);
    note(`status: ${await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '')}`);
    note(`best: ${(await panel.locator('[data-ws="optimise-best"]').innerText()).split('\n').join(' | ')}`);
    const shares = await panel.locator('[data-ws="optimise-split-table"]').innerText().catch(() => '');
    if (!shares) fail('no per-outlet share table');
    note(`shares:\n${shares}`);
    const ticks = await panel.locator('[data-ws="optimise-chart"] .optimise-tick').allInnerTexts();
    if (!ticks.some((t) => /\d/.test(t))) fail('the history chart has no numeric axis labels');
    if (!(await panel.locator('[data-ws="optimise-deviation-chart-target"]').count())) fail('the deviation chart has no target line');
    await shot('optimised');

    // 9. Apply: the part is rebuilt with the best heights and its boundaries found again.
    console.log('9. Apply and the final geometry');
    await panel.locator('[data-ws="optimise-apply"]').click();
    await until('the apply', async () => /applied/.test(await panel.locator('[data-ws="optimise-status"]').innerText().catch(() => '')) || (await alert.count()) > 0, 300000);
    if (await alert.count()) fail(`apply: ${await alert.innerText()}`);
    note(await panel.locator('[data-ws="optimise-status"]').innerText());
    await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
    await shot('final-geometry');
    // The tags are current: the final design meshes (a stale tag would refuse here).
    await visible(page.getByText('Mesh', { exact: true })).click().catch(() => {});
    await visible(card.getByRole('button', { name: 'Generate' })).click();
    await until('the final mesh', async () => /Next: Run/.test(await cardText()) || /older version/.test(await cardText()), 600000);
    if (/older version/.test(await cardText())) fail('a boundary tag is stale after Apply');
    note(await cardText());
    await shot('final-mesh');
  }
} catch (e) {
  fail(`STOPPED: ${e.message.split(String.fromCharCode(10))[0]}`);
  await shot('stopped');
}
console.log(problems.length ? `\n${problems.length} problem(s):\n${problems.map((p) => `  ${p}`).join('\n')}` : '\nno problems');
await browser.close();
process.exit(problems.length ? 1 : 0);
