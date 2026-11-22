// The whole flow through the GUI on a NEW project with a NEW sketch (a duct: a 20 x 10 mm
// rectangle pulled 60 mm), trying every option each guided card offers on the way: Geometry,
// Fluid, Boundaries, Mesh, Run, Results. A screenshot per action; problems listed at the end.
//
//   node walk_flow_gui.mjs "<url-with-token>" "<projects folder>" [shots] [--cell=1.5]
//
// Every select of a card is cycled through its options, every radio and switch pressed, and the
// card's text read back after each; the mesh and the run are real (a coarse mesh, a short run).
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const mesher = (args.find((a) => a.startsWith('--mesher=')) ?? '').slice(9);
const host = (args.find((a) => a.startsWith('--host=')) ?? '').slice(7);
const cell = (args.find((a) => a.startsWith('--cell=')) ?? '--cell=1.5').slice(7);
const [url, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!url || !folder) { console.error('usage: node walk_flow_gui.mjs <url-with-token> <projects folder> [shots]'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'flow'));
const { page, visible } = w;
const card = page.locator('.guided-card');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
async function until(what, test, ms = 120000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await test()) return true; await page.waitForTimeout(500); }
  w.fail(`timed out waiting for ${what}`);
  return false;
}
const slug = (s) => String(s).replace(/[^a-z0-9]+/gi, '_').slice(0, 40);

/** Every control of a region: tag, role, name, value, options. */
async function controls(region) {
  return region.first().evaluate((root) => Array.from(root.querySelectorAll('button, input, select, [role="radio"], [role="switch"], [role="tab"], [role="checkbox"]')).filter((el) => el.offsetParent).map((el, i) => ({
    i,
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || el.getAttribute('type') || '',
    name: (el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') ? document.getElementById(el.getAttribute('aria-labelledby'))?.innerText : '') || el.labels?.[0]?.innerText || el.innerText || el.getAttribute('title') || '').trim().split('\n')[0],
    ws: el.getAttribute('data-ws') || '',
    value: el.tagName === 'SELECT' || el.tagName === 'INPUT' ? el.value : (el.getAttribute('aria-checked') ?? el.getAttribute('aria-pressed') ?? ''),
    options: el.tagName === 'SELECT' ? Array.from(el.options).map((o) => o.value) : undefined,
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
  })));
}
/** Say what a region offers, flag unnamed controls, and cycle its selects and radios. */
async function explore(region, name, { skip = [] } = {}) {
  const list = await controls(region);
  w.note(`${name}: ${list.map((c) => `${c.role || c.tag}:${c.name || '(no name)'}${c.options ? `[${c.options.join(',')}]` : ''}${c.disabled ? ' (off)' : ''}`).join('; ')}`);
  for (const c of list) if (!c.name && !c.ws) w.fail(`${name}: a ${c.role || c.tag} has no name`);
  for (const c of list.filter((x) => x.tag === 'select' && !x.disabled && !skip.includes(x.name))) {
    const select = region.locator('select:visible').filter({ has: page.locator(`option[value="${c.options[0]}"]`) }).first();
    for (const option of c.options) {
      if (option === c.value) continue;
      try {
        await select.selectOption(option);
        await page.waitForTimeout(900);
        await w.shot(`${name}-${slug(c.name)}-${slug(option)}`);
        await w.checkAlerts(`${name} ${c.name}=${option}`);
      } catch (e) { w.fail(`${name} ${c.name}=${option}: ${e.message.split('\n')[0]}`); }
    }
    await select.selectOption(c.value).catch((e) => w.fail(`${name}: could not restore ${c.name}: ${e.message.split('\n')[0]}`));
    await page.waitForTimeout(500);
  }
  for (const c of list.filter((x) => x.role === 'radio' && !x.disabled && !skip.includes(x.name))) {
    try {
      await region.getByRole('radio', { name: c.name, exact: true }).first().click();
      await page.waitForTimeout(900);
      await w.shot(`${name}-radio-${slug(c.name)}`);
      await w.checkAlerts(`${name} ${c.name}`);
    } catch (e) { w.fail(`${name} radio ${c.name}: ${e.message.split('\n')[0]}`); }
  }
  // Back to the radio that was on.
  for (const c of list.filter((x) => x.role === 'radio' && x.value === 'true')) await region.getByRole('radio', { name: c.name, exact: true }).first().click().catch(() => {});
  return list;
}

try {
  await w.newProject('flow');
  // 1. Geometry: a new sketch, pulled 60 mm.
  await page.locator('[data-ws="build-sketch"] button').first().click();
  await page.getByRole('menuitem', { name: 'Rectangle', exact: true }).click();
  await page.locator('[data-ws="sketch-editor"]').waitFor();
  await page.locator('[data-ws="sketch-plane-xy"]').click();
  await page.waitForTimeout(700);
  await w.at(-10, -5); await w.at(10, 5);
  await page.locator('[data-ws="sketch-finish"]').click();
  await page.locator('[data-ws="sketch-editor"]').waitFor({ state: 'detached' });
  await w.settle();
  const pull = page.locator('[data-ws="details-pane"]').getByLabel('Pull distance', { exact: true }).first();
  await pull.fill('60'); await pull.press('Enter');
  await w.settle();
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await w.shot('duct');

  // Guided mode for the rest: the cards.
  await page.locator('[data-ws="build-close"]').click().catch(() => {});
  await page.getByRole('radio', { name: 'Guided' }).click();
  await page.waitForTimeout(1200);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await page.waitForTimeout(800);
  await w.shot('geometry-card');
  w.note(`geometry: ${await cardText()}`);
  await explore(card, 'geometry');
  // The body must be the fluid.
  const fluidChip = card.locator('[data-ws="guided-fluid-body"]').first();
  if (await fluidChip.count()) await fluidChip.click().catch(() => {});
  if (!/is the fluid/.test(await cardText())) {
    const body = card.getByRole('button', { name: /Fluid/ }).first();
    if (await body.count()) await body.click();
  }
  await until('the fluid body', async () => /is the fluid/.test(await cardText()), 30000);
  w.note(`geometry: ${await cardText()}`);
  await w.shot('geometry-fluid');

  // 2. Fluid: every material offered, then Air.
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await page.waitForTimeout(800);
  await w.shot('fluid-card');
  w.note(`fluid: ${await cardText()}`);
  const materials = await card.locator('[data-ws="guided-material"]').allInnerTexts();
  w.note(`materials: ${materials.map((m) => m.split('\n')[0]).join(', ')}`);
  for (const [i, m] of materials.entries()) {
    await card.locator('[data-ws="guided-material"]').nth(i).click();
    await page.waitForTimeout(700);
    await w.shot(`fluid-${slug(m.split('\n')[0])}`);
    await w.checkAlerts(`fluid ${m.split('\n')[0]}`);
  }
  await visible(card.getByText('Air', { exact: true })).click();
  await explore(card, 'fluid');
  const flowOptions = card.locator('[data-ws="guided-flow-options"]');
  if (await flowOptions.count()) { await w.shot('flow-options'); w.note(`flow options: ${(await flowOptions.innerText()).split('\n').join(' | ')}`); }
  await until('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);

  // 3. Boundaries: the faces listed; an inlet on -Z (the sketch plane), an outlet on +Z.
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  await page.waitForTimeout(800);
  await w.shot('boundaries-card');
  w.note(`boundaries: ${await cardText()}`);
  const faces = await card.locator('[data-ws="guided-unassigned-face"]').allInnerTexts();
  w.note(`unassigned faces: ${faces.map((f) => f.split('\n').join(' ')).join(' / ')}`);
  if (faces.length !== 6) w.fail(`a pulled rectangle lists ${faces.length} unassigned faces, not 6`);
  for (const f of faces) if (/curved/i.test(f)) w.fail(`a flat face of the duct is called curved: ${f.split('\n').join(' ')}`);
  await visible(card.getByLabel('Inlet speed in m/s')).fill('0.05');
  const assign = async (pattern, chip, what) => {
    const row = card.locator('[data-ws="guided-unassigned-face"]').filter({ hasText: pattern }).first();
    if (!(await row.count())) { w.fail(`${what}: no unassigned face matching ${pattern}`); return; }
    await row.getByRole('button', { name: 'Pick' }).click();
    await page.waitForTimeout(500);
    await w.shot(`pick-${what}`);
    await visible(card.locator(`[data-ws="${chip}"]`)).click();
    await page.waitForTimeout(900);
  };
  await assign(/−Z/, 'guided-inlet', 'inlet');
  await assign(/\+Z/, 'guided-outlet', 'outlet');
  await assign(/\+Y/, 'guided-symmetry', 'symmetry');
  await assign(/−Y/, 'guided-wall', 'wall');
  w.note(`boundaries: ${await cardText()}`);
  await w.shot('boundaries-assigned');
  const named = await card.locator('[data-ws="guided-boundary"]').allInnerTexts();
  w.note(`boundaries so far: ${named.map((n) => n.split('\n').join(' ')).join(' / ')}`);
  if (named.length !== 4) w.fail(`${named.length} boundaries after assigning 4`);
  await explore(card, 'boundaries', { skip: [] });
  await until('Next: Mesh', async () => /Next: Mesh/.test(await cardText()), 20000);

  // 4. Mesh: every mesher and fineness offered, then a coarse real mesh.
  await visible(card.getByText('Next: Mesh', { exact: true })).click();
  await page.waitForTimeout(800);
  await w.shot('mesh-card');
  w.note(`mesh: ${await cardText()}`);
  if (mesher === '') await explore(card, 'mesh');
  else {
    await card.getByLabel('Mesher', { exact: true }).selectOption(mesher);
    await page.waitForTimeout(800);
    w.note(`mesher ${mesher}: ${await cardText()}`);
    await w.shot('mesher-chosen');
  }
  const slider = card.locator('[data-ws="fineness-slider"]');
  if (await slider.count()) {
    for (const key of ['ArrowRight', 'ArrowRight', 'ArrowLeft', 'ArrowLeft', 'ArrowLeft']) { await slider.focus(); await page.keyboard.press(key); await page.waitForTimeout(300); }
    await w.shot('fineness-moved');
    w.note(`after the fineness slider: ${await cardText()}`);
  }
  if (host !== '') {
    const tree = await w.rpc('node.get', { node: '/mesh' });
    const settings = (tree.children ?? []).find((c) => c.type === 'mesh_settings');
    const target = settings ? settings.path : (await w.rpc('node.get', { node: '/mesh/settings' })).path;
    await w.rpc('parameter.set', { node: target, name: 'host', value: host });
    w.note(`mesh host set to ${host} on ${target}`);
  }
  const size = visible(card.getByLabel('Cell size in millimetres'));
  await size.fill(cell); await size.press('Tab');
  const cores = card.getByLabel('Number of processor cores for meshing');
  if (await cores.count()) { await cores.fill('2'); await cores.press('Tab'); }
  await w.shot('mesh-ready');
  const t0 = Date.now();
  await visible(card.getByRole('button', { name: 'Generate' })).click();
  await page.waitForTimeout(2500);
  await w.shot('mesh-running');
  w.note(`meshing: ${await cardText()}`);
  await until('the mesh', async () => /Next: Run/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 900000);
  w.note(`mesh after ${Math.round((Date.now() - t0) / 1000)} s: ${await cardText()}`);
  await w.shot('mesh-done');
  if (mesher !== '') {
    const seen = await page.evaluate(() => document.body.innerText.split('\n').join(' | ').slice(-1800));
    w.note(`screen: ${seen}`);
    throw new Error('stopped after the mesh, as asked');
  }
  await w.checkAlerts('mesh');
  for (const sel of ['guided-mesh-warning', 'guided-mesh-issue', 'guided-error']) {
    const el = card.locator(`[data-ws="${sel}"]`);
    if (await el.count()) w.fail(`mesh: ${sel}: ${(await el.first().innerText()).split('\n').join(' | ').slice(0, 300)}`);
  }

  // 5. Run: the options, then a short run.
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await page.waitForTimeout(800);
  await w.shot('run-card');
  w.note(`run: ${await cardText()}`);
  await explore(card, 'run');
  const iterations = card.getByLabel('Number of iterations');
  if (await iterations.count()) { await iterations.fill('150'); await iterations.press('Tab'); }
  const runCores = card.getByLabel('Number of processor cores', { exact: true });
  if (await runCores.count()) { await runCores.fill('2'); await runCores.press('Tab'); }
  const t1 = Date.now();
  await visible(card.getByRole('button', { name: 'Run', exact: true })).click();
  await page.waitForTimeout(4000);
  await w.shot('run-running');
  w.note(`running: ${await cardText()}`);
  await until('the run', async () => /Next: Results/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 900000);
  w.note(`run after ${Math.round((Date.now() - t1) / 1000)} s: ${await cardText()}`);
  await w.shot('run-done');
  await w.checkAlerts('run');

  // 6. Results: every look, colour map, field and surface; the analyse blocks; the line plot.
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await page.waitForTimeout(4000);
  await w.shot('results-card');
  w.note(`results: ${await cardText()}`);
  const looks = card.locator('[data-ws="guided-looks"] button, [data-ws="guided-looks"] [role="radio"]');
  const lookNames = await looks.allInnerTexts();
  w.note(`looks: ${lookNames.join(', ')}`);
  for (const [i, look] of lookNames.entries()) {
    await looks.nth(i).click();
    await page.waitForTimeout(3500);
    await w.shot(`look-${slug(look)}`);
    await w.checkAlerts(`look ${look}`);
    const legend = await page.locator('[data-ws="guided-legend"]').innerText().catch(() => '');
    w.note(`look ${look}: legend ${legend.split('\n').join(' ')}`);
    const inner = card.locator('[data-ws="guided-look-controls"]');
    if (await inner.count()) await explore(inner, `look-${slug(look)}`);
  }
  await explore(card, 'results', { skip: lookNames });
  for (const ws of ['guided-more', 'guided-analyse', 'guided-line', 'guided-views', 'guided-report', 'guided-residuals', 'guided-probe', 'guided-time-on']) {
    const el = page.locator(`[data-ws="${ws}"]`).first();
    if (!(await el.count())) { w.note(`${ws}: not on screen`); continue; }
    try {
      await el.click({ timeout: 5000 });
      await page.waitForTimeout(1500);
      await w.shot(`results-${ws}`);
      await w.checkAlerts(ws);
      w.note(`${ws}: ${(await cardText()).slice(0, 300)}`);
    } catch (e) { w.fail(`${ws}: ${e.message.split('\n')[0]}`); }
  }
} catch (e) {
  w.fail(`stopped: ${e.message.split('\n')[0]}`);
  await w.shot('stopped').catch(() => {});
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
