// Records the walkthrough "a gyroid counter-flow heat exchanger: two water streams and the aluminium
// wall between them" through the GUI on a NEW project, in Guided mode (ADR-0042): three 24 x 12 x 12
// mm boxes > Lattice (gyroid channel A, channel B, sheet) > Fluid: water, water, aluminium; heat
// transfer on > Boundaries: each stream's end faces picked in the view and made an inlet (350 K hot,
// 300 K cold, at opposite ends) and an outlet > Mesh (three regions, one mesh) > Run (foamMultiRun) >
// Results: temperature through fluids and wall, streamlines coloured by temperature.
//
//   set STOKES_WALK_VIDEO=<folder>   (walk_lib records the page there as an .mp4, with an installed ffmpeg)
//   node record_hx_walkthrough.mjs "<url-with-token>" "<projects folder>" [shots] [--cell=1] [--wall=1.6] [--iterations=300]
//
// A server with NO project must be running. Captions and `marks.json` are as in
// `record_bend_walkthrough.mjs`, so `cut_walkthrough.mjs` runs the waits fast.
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [rawUrl, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!rawUrl || !folder) { console.error('usage: node record_hx_walkthrough.mjs <url-with-token> <projects folder> [shots] [--cell=1] [--wall=1.6] [--iterations=300]'); process.exit(2); }
// `drive=1` gives the page its pick probe (`__stokesScene.idPick`) and store (`__stokes`): the walk
// finds a channel's mouth under the pointer the way a click does, then clicks there.
const url = `${rawUrl}${rawUrl.includes('?') ? '&' : '?'}drive=1`;
const shots = shotsArg ?? join(process.cwd(), '..', '.local', 'walkthrough-hx');
const cell = flag('cell', '1');
const wall = flag('wall', '1.6');
const iterations = flag('iterations', '300');
const reopen = flag('open', '');

const w = await openWalk(url, folder, shots);
const { page, visible } = w;
const t0 = w.recorder?.t0 ?? Date.now();
const marks = [];
const mark = (kind, label) => { marks.push({ t: (Date.now() - t0) / 1000, kind, label }); writeFileSync(join(shots, 'marks.json'), JSON.stringify(marks, null, 1)); };
const card = page.locator('.guided-card');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
const pause = (ms) => page.waitForTimeout(ms);

async function caption(text, hold = 2400) {
  console.log(`\n# ${text}`);
  mark('caption', text);
  await page.evaluate((t) => {
    let el = document.getElementById('walk-caption');
    if (!el) {
      el = document.createElement('div');
      el.id = 'walk-caption';
      el.style.cssText = 'position:fixed;left:50%;bottom:92px;transform:translateX(-50%);z-index:2147483000;max-width:860px;padding:10px 18px;border-radius:12px;background:rgba(16,20,26,.92);color:#fff;font:600 17px/1.35 Inter,system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.35);pointer-events:none;text-align:center;transition:opacity .25s';
      document.body.appendChild(el);
    }
    el.textContent = t;
    el.style.opacity = '1';
  }, text);
  await pause(hold);
}

async function wait(label, test, ms) {
  mark('wait-start', label);
  const started = Date.now();
  let ok = false;
  while (Date.now() - started < ms) { if (await test()) { ok = true; break; } await pause(700); }
  mark('wait-end', label);
  if (!ok) { w.fail(`timed out waiting for ${label}`); throw new Error(`timeout: ${label}`); }
  console.log(`  ${label}: ${Math.round((Date.now() - started) / 1000)} s`);
}

async function type(locator, text, leave = 'Tab') {
  await locator.click();
  await page.keyboard.press('Control+A');
  await page.keyboard.type(String(text), { delay: 45 });
  await page.keyboard.press(leave);
  await pause(350);
}

/** The project's parts by name, from the store's tree. */
async function parts() {
  return page.evaluate(() => {
    const out = {};
    const visit = (n) => { if (!n) return; if (n.type === 'part') out[n.name] = { id: n.id, visible: n.params?.visible !== false }; for (const c of n.children ?? []) visit(c); };
    visit(window.__stokes.getState().tree);
    return out;
  });
}

/** How many boundaries the project has, from the store's tree. */
async function boundaryCount() {
  return page.evaluate(() => {
    let n = 0;
    const visit = (node) => { if (!node) return; if (node.type === 'boundary') n += 1; for (const c of node.children ?? []) visit(c); };
    visit(window.__stokes.getState().tree);
    return n;
  });
}

/** Build one box and fill it with a lattice form; the new body is renamed to `name`. */
async function latticeBody(name, form, thickness) {
  const before = new Set(Object.keys(await parts()));
  await w.tool('shape', 'Box');
  for (const axis of ['x', 'y', 'z']) await w.setField(`Size ${axis}`, axis === 'x' ? '24' : '12');
  await w.run(`${name}-box`);
  const box = Object.keys(await parts()).find((n) => !before.has(n));
  await w.pick(box);
  await w.tool('modify', 'Lattice');
  await w.choose('Pattern', 'gyroid');
  await w.choose('Form', form);
  await w.setField('Cell size', '6');
  await w.setField('Wall thickness', thickness);
  await pause(500);
  await w.run(`${name}-lattice`, 300000);
  const now = await parts();
  const made = Object.keys(now).find((n) => !before.has(n) && n !== box && now[n].visible);
  if (made === undefined) { w.fail(`no new body for ${name}`); return null; }
  await w.rpc('node.rename', { node: now[made].id, name });
  await pause(800);
  return now[made].id;
}

/**
 * Pick the end faces of `part` that lie on the plane x = ±12 mm, as a user does: click one mouth in the
 * view (found under the pointer with the same id pick a click uses), then Find related > Coplanar.
 */
async function pickEnd(part, view) {
  await page.keyboard.press(view === 'Left' ? 'Alt+3' : 'Alt+4');
  await pause(1600);
  // Every face of the part under a grid of points: a mouth's cap, or the channel's inside seen
  // through a mouth. The cap is the one whose coplanar set is the largest: every mouth on that end.
  const hits = await page.evaluate((target) => {
    const probe = window.__stokesScene;
    const rect = document.querySelector('canvas')?.getBoundingClientRect();
    if (!probe || !rect) return [];
    const seen = new Map();
    for (let gy = 0.15; gy <= 0.85; gy += 0.02) {
      for (let gx = 0.15; gx <= 0.85; gx += 0.02) {
        const x = rect.left + rect.width * gx;
        const y = rect.top + rect.height * gy;
        const found = probe.idPick(x, y);
        if (found && found.part === target && found.face >= 0 && !seen.has(found.face)) seen.set(found.face, { x, y, face: found.face });
      }
    }
    return Array.from(seen.values()).slice(0, 16);
  }, part);
  if (hits.length === 0) { w.fail(`no face of ${part} under the pointer in the ${view} view`); return 0; }
  let best = null;
  for (const hit of hits) {
    const answer = await w.rpc('tag.related', { members: [{ part, faces: [hit.face] }], relation: 'coplanar', parts: [part] }).catch(() => ({ members: [] }));
    const items = [];
    for (const m of answer.members ?? []) {
      if (m.faces === 'all') continue;
      const faces = [];
      for (const [first, last] of m.faces) for (let id = first; id <= last; id += 1) faces.push(id);
      items.push({ part: m.part, faces });
    }
    const count = items.reduce((n, it) => n + it.faces.length, 0);
    if (best === null || count > best.count) best = { hit, items, count };
  }
  await page.mouse.click(best.hit.x, best.hit.y);
  await pause(900);
  const items = best.items;
  await page.evaluate((items) => window.__stokes.getState().pickMany(items, 'add'), items);
  await pause(1200);
  return items.reduce((n, it) => n + it.faces.length, 0);
}

try {
  if (reopen === '') {
  await page.goto(url);
  await pause(1200);
  const open = await w.rpc('project.info').catch(() => null);
  if (open && (open.path || open.name)) {
    await w.rpc('project.close', { discard: true }).catch(() => w.rpc('project.close').catch(() => {}));
    await page.goto(url);
    await pause(1200);
  }
  await caption('Stokes: a gyroid counter-flow heat exchanger, two water streams and the wall between them', 3600);
  await caption('1 · A new project');
  await visible(page.getByText('New project', { exact: true })).click();
  await pause(800);
  await type(page.locator('input:visible').nth(0), `gyroid-hx-${Date.now() % 100000}`);
  await page.evaluate((dir) => {
    const input = Array.from(document.querySelectorAll('input')).filter((x) => x.offsetParent)[1];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, dir);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, folder);
  await pause(700);
  await visible(page.getByText('Create', { exact: true })).click();
  await page.locator('[data-ws="guided-create"]').waitFor({ timeout: 30000 });
  await page.getByRole('radio', { name: 'Full' }).click();
  await pause(800);

  // ------------------------------------------------------------------ 2. geometry
  await caption('2 · Geometry: a 24 × 12 × 12 mm core, filled three times with a gyroid of 6 mm cells');
  await caption('Channel A is the hot water, Channel B the cold water, the sheet between them the wall');
  const hot = await latticeBody('hot', 'channel_a', wall);
  const cold = await latticeBody('cold', 'channel_b', wall);
  const solid = await latticeBody('wall', 'sheet', wall);
  if (!hot || !cold || !solid) throw new Error('the three bodies were not built');
  await page.locator('[data-ws="build-close"]').click().catch(() => {});
  await page.getByRole('radio', { name: 'Guided' }).click();
  await pause(900);
  if (await page.locator('[role="tree"]').count()) { await page.locator('[data-ws="tree-toggle"]').click().catch(() => {}); await pause(600); }
  await page.keyboard.press('Alt+0').catch(() => {});
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await pause(1500);
  await w.shot('bodies');
  await caption('Say which body is the first stream: the hot channel is a fluid');
  await card.getByRole('group', { name: 'What hot is' }).locator('[data-choice="fluid"]').click();
  await wait('hot is the fluid', async () => /hot is the fluid/.test(await page.locator('[data-ws="flow-strip"], nav').first().innerText().catch(() => '')) || (await card.getByRole('group', { name: 'What hot is' }).locator('[data-choice="fluid"][aria-pressed="true"]').count()) > 0, 30000);
  await pause(800);

  // ------------------------------------------------------------------ 3. fluid
  await caption('3 · Materials: water in both channels, aluminium for the wall; heat transfer on');
  await visible(page.getByText('Fluid', { exact: true })).click();
  await pause(1000);
  await visible(card.locator('[data-choice="water_liquid"]')).click();
  await wait('the first water', async () => /Water/.test(await cardText()), 60000);
  await pause(800);
  const other = (name) => card.locator(`[data-ws="guided-other-body"][data-body="${name}"]`);
  await wait('the other bodies listed', async () => (await other('cold').count()) > 0 && (await other('wall').count()) > 0, 30000);
  await other('cold').locator('[data-choice="water_liquid"]').click();
  await wait('cold is water', async () => /Fluid · Water/.test(await other('cold').innerText()), 60000);
  await other('wall').locator('[data-choice="aluminium"]').click();
  await wait('the wall is aluminium', async () => /Solid · Aluminium/.test(await other('wall').innerText()), 60000);
  await pause(600);
  await card.getByRole('group', { name: 'Heat transfer' }).getByRole('button', { name: /^On/ }).click();
  await pause(1500);
  await w.shot('materials');

  // ------------------------------------------------------------------ 4. boundaries
  await caption('4 · Boundaries: the hot water enters at the left end at 350 K, the cold at the right at 300 K');
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  await pause(1200);
  const setInlet = async (speed, temperature) => {
    await type(card.locator('.guided-assign input[aria-label="Inlet speed in m/s"]').first(), speed);
    await type(card.locator('[data-ws="guided-inlet-temperature"]').first(), temperature);
  };
  await caption('Click one mouth of the hot channel, then Find related › Coplanar picks every mouth on that end');
  let n = await pickEnd(hot, 'Left');
  w.note(`hot inlet faces: ${n}`);
  await setInlet('0.05', '350');
  await card.locator('[data-ws="guided-inlet"]').click();
  await wait('the hot inlet', async () => (await boundaryCount()) >= 1, 30000);
  n = await pickEnd(hot, 'Right');
  w.note(`hot outlet faces: ${n}`);
  await card.locator('[data-ws="guided-outlet"]').click();
  await wait('the hot outlet', async () => (await boundaryCount()) >= 2, 30000);
  await caption('The cold water runs the other way: in at the right end, out at the left (counter-flow)');
  n = await pickEnd(cold, 'Right');
  w.note(`cold inlet faces: ${n}`);
  await setInlet('0.05', '300');
  await card.locator('[data-ws="guided-inlet"]').click();
  await wait('the cold inlet', async () => (await boundaryCount()) >= 3, 30000);
  n = await pickEnd(cold, 'Left');
  w.note(`cold outlet faces: ${n}`);
  await card.locator('[data-ws="guided-outlet"]').click();
  await wait('the cold outlet', async () => (await boundaryCount()) >= 4, 30000);
  await page.keyboard.press('Alt+0').catch(() => {});
  await pause(1500);
  await w.shot('boundaries');

  // ------------------------------------------------------------------ 5. mesh
  await caption(`5 · Mesh: one snappyHexMesh mesh of all three bodies at ${cell} mm, split into three regions`);
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  await pause(1200);
  await type(card.getByLabel('Cell size in millimetres'), cell);
  await type(card.locator('[data-ws="guided-mesh-cores"]'), '8');
  await pause(900);
  await card.locator('[data-ws="guided-generate"]').click();
  await wait('the mesh', async () => /Next: Run/.test(await cardText()) || /failed|could not/i.test(await cardText()), 1800000);
  await pause(1500);
  await w.shot('mesh');
  const meshText = await cardText();
  w.note(`mesh: ${meshText.slice(0, 300)}`);

  // ------------------------------------------------------------------ 6. run
  await caption('6 · Run: foamMultiRun solves both streams and the conduction in the wall together');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await pause(1200);
  await type(card.getByLabel('Number of iterations'), iterations);
  await pause(600);
  await card.locator('[data-ws="guided-run"]').click();
  await wait('the run', async () => /Next: Results|converged|stopped|failed/i.test(await cardText()) && !/running|queued/i.test(await cardText()), 3600000);
  await pause(2000);
  await w.shot('run');
  await caption('Residuals per region: hot, cold and the wall');
  await pause(1500);

  } else {
    await w.openProject(reopen, 'results');
    await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
    await pause(4000);
    await page.keyboard.press('Alt+0').catch(() => {});
    await pause(1500);
  }
  // ------------------------------------------------------------------ 7. results
  await caption('7 · Results: temperature on a slice through both streams and the wall');
  await visible(page.getByText('Results', { exact: true })).first().click();
  await pause(3000);
  await page.locator('[data-ws="guided-looks"] [data-look="slice"]').first().click().catch(() => {});
  await pause(1200);
  await page.locator('[data-ws="guided-field-picker"]').first().click();
  await pause(600);
  await page.locator('[data-ws="guided-field-picker-search"]').fill('');
  await page.keyboard.type('temp', { delay: 70 });
  await pause(700);
  await page.getByRole('option', { name: /^Temperature/ }).first().click();
  await pause(3500);
  await w.shot('temperature');
  await caption('The hot stream cools from 350 K and the cold one warms from 300 K, through the aluminium wall', 3600);
  const runs = await w.rpc('run.list').catch(() => []);
  const last = Array.isArray(runs) ? runs.find((r) => r.kind !== 'mesh') : null;
  if (last) {
    const status = await w.rpc('run.status', { run: last.id }).catch(() => null);
    const m = status?.monitors ?? {};
    const q = (s) => {
      const i = m[`${s}_in`] ?? m.inlet, o = m[`${s}_out`] ?? m.outlet;
      return i && o ? o.flow_rate * 4182 * (o.T_bulk - i.T_bulk) : null;
    };
    w.note(`monitors: ${JSON.stringify(m).slice(0, 500)}`);
    w.note(`heat: hot ${q('hot')} W, cold ${q('cold')} W`);
  }
  await caption('Streamlines from both inlets: the two streams run past each other through the wall');
  await page.locator('[data-ws="guided-looks"] [data-look="stream"]').first().click().catch(() => {});
  await pause(1200);
  // In Guided mode the Stream look draws the lines itself, from every inlet.
  await wait('the streamlines', async () => !/Not drawn yet|Tracing|Drawing/.test(await cardText()), 120000).catch(() => {});
  await page.keyboard.press('Alt+0').catch(() => {});
  await pause(3000);
  await w.shot('streamlines');
  await caption('Two fluids and a solid, meshed, solved and shown as one case', 3600);
  await w.rpc('project.save').catch(() => {});
  mark('end', 'end');
} catch (err) {
  w.fail(`stopped: ${String(err?.message ?? err).slice(0, 300)}`);
  await w.shot('stopped').catch(() => {});
  mark('end', 'end');
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
