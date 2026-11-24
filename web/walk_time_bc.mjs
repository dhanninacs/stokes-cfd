// Boundary values that vary in time, end to end through the GUI (ADR-0022 addendum 2026-11-24), on a
// NEW project: a duct sketched and pulled with the Create ribbon, a sine inlet speed set on the Guided
// Boundaries card, the same inlet edited as a table (pasted from a spreadsheet) on its Properties page in
// Full mode, the run switched to transient on the Run card, a coarse mesh and a SHORT transient OpenFOAM
// run on two cores; then the inlet value written in each time folder is checked against the table, the
// Guided history caption is read whole, and Full mode's Results panel steps to another written time.
//
//   node walk_time_bc.mjs "<url-with-token>" "<projects folder>" <shots> --jobs=<agent jobs folder>
//
// STOKES_WALK_VIEWPORT=1600x900 STOKES_WALK_CHANNEL=chrome. Every step is shot in the light and the dark
// appearance (the top bar's appearance button is pressed for the second shot and again after it).
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const jobs = (args.find((a) => a.startsWith('--jobs=')) ?? '').slice(7);
const cell = (args.find((a) => a.startsWith('--cell=')) ?? '--cell=2').slice(7);
const [url, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!url || !folder || !shotsArg) { console.error('usage: node walk_time_bc.mjs <url-with-token> <projects folder> <shots> --jobs=<dir>'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg);
const { page, visible } = w;
const card = page.locator('.guided-card');
const cardText = async () => (await card.first().innerText().catch(() => '')).split('\n').join(' | ');
async function until(what, test, ms = 120000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await test()) return true; await page.waitForTimeout(500); }
  w.fail(`timed out waiting for ${what}`);
  return false;
}

/** The step in both appearances: the top bar's appearance button flips it for the second shot and back. */
async function shots(name) {
  const flip = page.locator('[data-ws-item="appearance"]').first();
  const first = /dark/.test((await flip.getAttribute('aria-label')) ?? '') ? 'light' : 'dark';
  await w.shot(`${name}-${first}`);
  await flip.click();
  await page.waitForTimeout(450);
  await w.shot(`${name}-${first === 'light' ? 'dark' : 'light'}`);
  await flip.click();
  await page.waitForTimeout(300);
}

/** The inlet table pasted into the Full mode editor: s, m/s. */
const TABLE = [
  [0, 1.0], [0.01, 1.5], [0.02, 1.0], [0.03, 0.5], [0.04, 1.0], [0.05, 1.5],
];
const tableAt = (t) => {
  if (t <= TABLE[0][0]) return TABLE[0][1];
  for (let i = 1; i < TABLE.length; i += 1) {
    const [t0, v0] = TABLE[i - 1];
    const [t1, v1] = TABLE[i];
    if (t <= t1) return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  }
  return TABLE[TABLE.length - 1][1];
};

async function boundaryNode(name) {
  const listed = await w.rpc('boundary.list');
  const entry = listed.find((b) => b.name === name);
  if (!entry) throw new Error(`no boundary ${name}`);
  return w.rpc('node.get', { node: entry.id });
}

try {
  await w.newProject('time-bc');
  // 1. Geometry: a 20 x 10 mm rectangle pulled 60 mm with the Create ribbon.
  await page.locator('[data-ws="build-sketch"] button').first().click();
  await page.waitForTimeout(600);
  // The ribbon either offers the sketch's first tool in a menu or opens the sketcher at once.
  const rectangleItem = page.getByRole('menuitem', { name: 'Rectangle', exact: true });
  if (await rectangleItem.count()) await rectangleItem.click();
  await page.locator('[data-ws="sketch-editor"]').waitFor();
  await page.locator('[data-ws="sketch-plane-xy"]').click();
  await page.waitForTimeout(700);
  const rectangleTool = page.locator('[data-ws="sketch-editor"]').getByRole('button', { name: /^Rectangle/ }).first();
  if (await rectangleTool.count()) await rectangleTool.click();
  await page.waitForTimeout(300);
  await w.at(-10, -5); await w.at(10, 5);
  await page.locator('[data-ws="sketch-finish"]').click();
  await page.locator('[data-ws="sketch-editor"]').waitFor({ state: 'detached' });
  await w.settle();
  const pull = page.locator('[data-ws="details-pane"]').getByLabel('Pull distance', { exact: true }).first();
  await pull.fill('60'); await pull.press('Enter');
  await w.settle();
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await shots('duct');

  // 2. Guided: the body is the fluid, air.
  await page.locator('[data-ws="build-close"]').click().catch(() => {});
  await page.getByRole('radio', { name: 'Guided' }).click();
  await page.waitForTimeout(1200);
  await visible(page.getByText('Geometry', { exact: true })).click();
  await page.waitForTimeout(800);
  const fluidChip = card.locator('[data-ws="guided-fluid-body"]').first();
  if (await fluidChip.count()) await fluidChip.click().catch(() => {});
  if (!/is the fluid/.test(await cardText())) {
    const body = card.getByRole('button', { name: /Fluid/ }).first();
    if (await body.count()) await body.click();
  }
  await until('the fluid body', async () => /is the fluid/.test(await cardText()), 30000);
  await visible(page.getByText('Next: Fluid', { exact: true })).click();
  await page.waitForTimeout(800);
  await visible(card.getByText('Air', { exact: true })).click();
  await until('Next: Boundaries', async () => /Next: Boundaries/.test(await cardText()), 20000);

  // 3. Boundaries: inlet on -Z at 1 m/s, outlet on +Z; then the inlet's speed varies as a sine.
  await visible(card.getByText('Next: Boundaries', { exact: true })).click();
  await page.waitForTimeout(800);
  await visible(card.getByLabel('Inlet speed in m/s')).fill('1');
  const assign = async (pattern, chip, what) => {
    const row = card.locator('[data-ws="guided-unassigned-face"]').filter({ hasText: pattern }).first();
    if (!(await row.count())) { w.fail(`${what}: no unassigned face matching ${pattern}`); return; }
    await row.getByRole('button', { name: 'Pick' }).click();
    await page.waitForTimeout(500);
    await visible(card.locator(`[data-ws="${chip}"]`)).click();
    await page.waitForTimeout(900);
  };
  await assign(/−Z/, 'guided-inlet', 'inlet');
  await assign(/\+Z/, 'guided-outlet', 'outlet');
  await shots('boundaries-assigned');
  const inletRow = card.locator('[data-ws="guided-boundary"][data-kind="velocity_inlet"]').first();
  await inletRow.locator('[data-ws="varies-toggle"]').click();
  const editor = card.locator('[data-ws="guided-profile-editor"] [data-ws="time-profile"]');
  await editor.waitFor({ timeout: 10000 });
  const field = (name) => editor.locator(`[data-field="${name}"]`);
  await field('mean').fill('1');
  await field('amplitude').fill('0.5');
  await field('frequency').fill('40');
  await page.waitForTimeout(900);
  await editor.locator('[data-ws="profile-plot"]').waitFor({ timeout: 10000 });
  await shots('guided-sine-editor');
  // An inline refusal: a frequency of zero is named under its field.
  await field('frequency').fill('0');
  await page.waitForTimeout(900);
  const refusal = await editor.locator('[role="alert"]').allInnerTexts();
  if (!refusal.some((t) => /frequency/.test(t))) w.fail(`a zero frequency was not refused under its field: ${refusal.join(' / ')}`);
  await shots('guided-sine-refused');
  await field('frequency').fill('40');
  await page.waitForTimeout(700);
  await editor.locator('[data-ws="profile-apply"]').click();
  await until('the sine to be stored', async () => (await boundaryNode('inlet')).params?.profiles?.velocity?.form === 'sine', 15000);
  await page.waitForTimeout(600);
  await shots('guided-sine-applied');
  const summary = await card.locator('[data-ws="guided-profile-summary"]').first().innerText().catch(() => '');
  w.note(`guided row says: ${summary}`);
  if (!/sine/.test(summary)) w.fail(`the inlet row does not say it varies: ${summary}`);
  const check = await w.rpc('setup.check');
  const steady = check.findings.find((f) => f.code === 'setup.profile_steady');
  w.note(`setup.check: ${steady ? steady.message : '(no setup.profile_steady)'}`);
  if (!steady) w.fail('no setup.profile_steady finding on a steady run with a sine inlet');

  // 4. Full mode: the inlet's Properties page, the same editor; the sine becomes a pasted table.
  await page.getByRole('radio', { name: 'Full' }).click();
  await page.waitForTimeout(1200);
  // The tree's filter brings the inlet's row into view under Physics > Boundary conditions.
  const filter = page.getByPlaceholder('Filter the tree').first();
  await filter.fill('inlet');
  await page.waitForTimeout(900);
  await page.locator('[role="tree"] [data-ws="tree-varies"]').first().waitFor({ timeout: 10000 }).catch(() => w.fail('the tree shows no marker on the varying inlet'));
  await page.locator('[role="tree"] [role="treeitem"]').filter({ has: page.locator('[data-ws="tree-varies"]') }).first().click();
  await page.waitForTimeout(1200);
  const section = page.locator('[data-ws="boundary-time-profiles"]').first();
  await section.waitFor({ timeout: 10000 });
  await shots('full-inlet-properties');
  await filter.fill('');
  await section.locator('[data-parameter="velocity"] [data-ws="varies-toggle"]').first().click();
  const full = section.locator('[data-ws="time-profile"]').first();
  await full.waitFor({ timeout: 10000 });
  if ((await full.getAttribute('data-form')) !== 'sine') w.fail(`the Full editor opened on ${await full.getAttribute('data-form')}, not the stored sine`);
  await shots('full-editor-sine');
  // Keyboard: the form picker is a radio group the arrow keys move along (Sine -> Table is one step left).
  await full.locator('[role="radio"][data-form="sine"]').focus();
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(400);
  if ((await full.getAttribute('data-form')) !== 'table') w.fail('the arrow key did not move the form picker to Table');
  // Thousands written with commas on every line: nothing is a row, but the paste is still the editor's and
  // says why, rather than the raw text landing in the cell (review: the cell became '00,101,325 1,101,400').
  const firstTime = full.locator('[data-cell="rows.0.time"]');
  const before = await firstTime.inputValue();
  await firstTime.evaluate((input, text) => {
    const data = new DataTransfer();
    data.setData('text', text);
    input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, '0,101,325\n1,101,400');
  await page.waitForTimeout(500);
  const crowdedSaid = await full.locator('[data-ws="profile-paste-note"]').innerText().catch(() => '');
  w.note(`after a thousands-comma paste: ${crowdedSaid}`);
  if (!/more than two comma-separated cells/.test(crowdedSaid)) w.fail(`a paste of crowded comma lines said nothing: ${crowdedSaid}`);
  if ((await firstTime.inputValue()) !== before) w.fail(`a paste of crowded comma lines changed the cell to ${await firstTime.inputValue()}`);
  await shots('full-editor-paste-thousands');
  const sheet = ['Time\tSpeed', ...TABLE.map(([t, v]) => `${t}\t${v}`)].join('\n');
  await full.locator('[data-cell="rows.0.time"]').evaluate((input, text) => {
    const data = new DataTransfer();
    data.setData('text', text);
    input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, sheet);
  await page.waitForTimeout(900);
  const rows = await full.locator('[data-ws="profile-table"] tbody tr').count();
  if (rows !== TABLE.length) w.fail(`the pasted table has ${rows} rows, not ${TABLE.length}`);
  const pasteSaid = await full.locator('[data-ws="profile-paste-note"]').innerText().catch(() => '');
  w.note(`after the paste: ${pasteSaid}`);
  if (!new RegExp(`^${TABLE.length} rows pasted`).test(pasteSaid)) w.fail(`the paste did not say how many rows came in: ${pasteSaid}`);
  await full.locator('[data-ws="profile-add-row"]').click();
  await page.waitForTimeout(300);
  await full.locator('[data-ws="profile-table"] tbody tr').last().getByRole('button', { name: /Remove row/ }).click();
  await page.waitForTimeout(900);
  await shots('full-editor-table');
  await full.locator('[data-ws="profile-apply"]').click();
  await until('the table to be stored', async () => (await boundaryNode('inlet')).params?.profiles?.velocity?.form === 'table', 15000);
  await page.waitForTimeout(600);
  await shots('full-table-applied');
  const stored = (await boundaryNode('inlet')).params.profiles.velocity.rows.map((r) => [r.time.value, r.value.value]);
  w.note(`stored table: ${JSON.stringify(stored)}`);
  // The Conditions panel (palette: Move Conditions to the left side) is Full mode's Fix button for the
  // steady-run finding. It blanked the app (a `fix: null` finding); the Fix switches the run to transient.
  await page.keyboard.press('Control+k');
  const search = page.getByLabel('Search commands');
  await search.waitFor({ timeout: 10000 });
  await search.fill('Move Conditions to the left side');
  await page.waitForTimeout(400);
  await page.keyboard.press('Enter');
  const conditions = page.locator('[data-ws="conditions-panel"]').first();
  await conditions.waitFor({ timeout: 10000 }).catch(() => w.fail('the Conditions panel did not open'));
  await page.waitForTimeout(900);
  await shots('full-conditions-panel');
  // The steady-run finding is the only one here with a command behind it, so the only Fix button.
  const said = await conditions.innerText().catch(() => '');
  if (!/varies in time, but the run is steady/.test(said)) w.fail('the Conditions panel does not list the steady-run finding');
  const fixButton = conditions.getByRole('button', { name: 'Fix', exact: true }).first();
  if ((await fixButton.count()) === 0) w.fail('no Fix button on the steady-run finding');
  // A REAL pointer click, as a user makes it: a third panel in the left slot once drew without its card and
  // the viewport's canvas took the pointer over it. No fallback that presses the button from script: if the
  // pointer cannot reach it, the walk fails.
  const fixBox = await fixButton.boundingBox();
  const atFix = fixBox === null ? 'none' : await page.evaluate(([x, y]) => {
    const hit = document.elementFromPoint(x, y);
    return hit?.closest('button')?.textContent?.trim() ?? hit?.tagName ?? 'none';
  }, [fixBox.x + fixBox.width / 2, fixBox.y + fixBox.height / 2]);
  if (atFix !== 'Fix') w.fail(`the Fix button does not take the pointer: ${atFix} is on top of it`);
  await fixButton.click({ timeout: 5000 }).catch((e) => w.fail(`a real click on Fix failed: ${String(e.message).split('\n')[0]}`));
  await until('the Fix to switch the run to transient', async () => {
    const tree = await w.rpc('node.get', { node: '/solver/settings' }).catch(() => null);
    return tree?.params?.transient === true;
  }, 15000);
  await page.waitForTimeout(700);
  await shots('full-conditions-fixed');
  // Back to steady, so the Run card offers the switch as a new user would meet it.
  await w.rpc('solve.configure', { values: { transient: false } });
  await page.keyboard.press('Control+k');
  await search.waitFor({ timeout: 10000 });
  await search.fill('Move Conditions to hidden');
  await page.waitForTimeout(400);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(600);
  // Narrow: a smaller window keeps the Properties pane and the editor in it usable.
  await page.setViewportSize({ width: 1100, height: 760 });
  await page.waitForTimeout(800);
  await section.locator('[data-parameter="velocity"] [data-ws="varies-toggle"]').first().click();
  await section.locator('[data-ws="time-profile"]').first().waitFor({ timeout: 10000 });
  await page.waitForTimeout(900);
  await shots('full-table-narrow');
  await section.locator('[data-parameter="velocity"] [data-ws="varies-toggle"]').first().click();
  const [vw, vh] = (process.env.STOKES_WALK_VIEWPORT ?? '1600x900').split('x').map(Number);
  await page.setViewportSize({ width: vw, height: vh });
  await page.waitForTimeout(800);

  // 5. Guided again: the mesh, coarse, then the Run card offers the switch to transient.
  await page.getByRole('radio', { name: 'Guided' }).click();
  await page.waitForTimeout(1200);
  await visible(page.getByText('Mesh', { exact: true })).click().catch(() => {});
  await page.waitForTimeout(800);
  const size = visible(card.getByLabel('Cell size in millimetres'));
  await size.fill(cell); await size.press('Tab');
  const meshCores = card.getByLabel('Number of processor cores for meshing');
  if (await meshCores.count()) { await meshCores.fill('2'); await meshCores.press('Tab'); }
  await visible(card.getByRole('button', { name: 'Generate' })).click();
  await until('the mesh', async () => /Next: Run/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 900000);
  await shots('mesh-done');
  await visible(card.getByText('Next: Run', { exact: true })).click();
  await page.waitForTimeout(1200);
  await card.locator('[data-ws="guided-needs-transient"]').waitFor({ timeout: 10000 }).catch(() => w.fail('the Run card does not say the run should be transient'));
  await shots('run-needs-transient');
  await card.locator('[data-ws="guided-transient"]').click();
  await card.locator('[data-ws="guided-end-time"]').waitFor({ timeout: 10000 });
  const end = card.locator('[data-ws="guided-end-time"]');
  // A zero end time is refused under the field; quantity text is taken (50 ms is 0.05 s).
  await end.fill('0'); await end.press('Tab');
  await page.waitForTimeout(400);
  const endError = await card.locator('[data-ws="guided-end-time-error"]').innerText().catch(() => '');
  if (!/above 0/.test(endError)) w.fail(`a zero end time was not refused under the field: ${endError}`);
  await shots('run-end-time-refused');
  await end.fill('50 [ms]'); await end.press('Tab');
  await until('the end time to be 0.05 s', async () => {
    const settings = await w.rpc('node.get', { node: '/solver/settings' }).catch(() => null);
    const value = settings?.params?.end_time;
    return Math.abs((typeof value === 'object' && value !== null ? value.value : value) - 0.05) < 1e-12;
  }, 10000);
  await page.waitForTimeout(500);
  const step = card.locator('[data-ws="guided-time-step"]');
  await step.fill('0.0005'); await step.press('Tab');
  await page.waitForTimeout(500);
  // Fields every 0.005 s: the Guided card has no write control, so it is set through the API.
  await w.rpc('solve.configure', { values: { write_time: 0.005 } });
  const runCores = card.getByLabel('Number of processor cores', { exact: true });
  await runCores.fill('2'); await runCores.press('Tab');
  await page.waitForTimeout(800);
  await shots('run-transient');
  const t1 = Date.now();
  await visible(card.getByRole('button', { name: 'Run', exact: true })).click();
  await page.waitForTimeout(4000);
  await shots('run-running');
  await until('the run', async () => /Next: Results/.test(await cardText()) || (await card.locator('[data-ws="guided-error"]').count()) > 0, 900000);
  w.note(`run after ${Math.round((Date.now() - t1) / 1000)} s: ${await cardText()}`);
  await shots('run-done');
  await w.checkAlerts('run');

  // 6. The written time folders: the inlet's value at each time against the table.
  const status = await w.rpc('run.status');
  w.note(`run ${status.id}: ${status.state}, job ${status.job}`);
  const caseDir = (() => {
    if (!jobs || !status.job) return null;
    const root = join(jobs, status.job);
    for (const dir of [root, join(root, 'case')]) if (existsSync(join(dir, '0'))) return dir;
    return null;
  })();
  if (caseDir === null) w.fail(`no case folder for job ${status.job} under ${jobs}`);
  else {
    const times = readdirSync(caseDir).filter((d) => /^\d+(\.\d+)?(e-?\d+)?$/.test(d) && d !== '0' && statSync(join(caseDir, d)).isDirectory()).sort((a, b) => Number(a) - Number(b));
    let worst = 0;
    const lines = [];
    for (const t of times) {
      const text = readFileSync(join(caseDir, t, 'U'), 'utf8');
      // The inlet's entry runs from its name to the brace that closes it at the entry's own indent.
      const start = text.search(/\n\s{4}inlet\s*\n/);
      const block = start < 0 ? '' : text.slice(start, text.indexOf('\n    }', start + 1));
      const values = [...block.matchAll(/value\s+uniform\s+\(([^)]*)\)/g)];
      const last = values.length ? values[values.length - 1][1].trim().split(/\s+/).map(Number) : null;
      if (last === null) { w.fail(`${t}/U has no inlet value`); continue; }
      const speed = Math.hypot(...last);
      const want = tableAt(Number(t));
      worst = Math.max(worst, Math.abs(speed - want));
      lines.push(`t=${t} s: ${speed.toFixed(4)} m/s (table ${want.toFixed(4)})`);
    }
    for (const line of lines) w.note(line);
    w.note(`${times.length} time folders; largest difference from the table ${worst.toExponential(2)} m/s`);
    if (times.length < 3) w.fail(`only ${times.length} time folders were written`);
    if (worst > 1e-3) w.fail(`the inlet value differs from the table by ${worst} m/s`);
  }

  // 7. Results: the history says which report follows the inlet.
  await visible(card.getByText('Next: Results', { exact: true })).click();
  await page.waitForTimeout(4000);
  await shots('results-card');
  const analyse = page.locator('[data-ws="guided-analyse"]').first();
  if (await analyse.count()) {
    await analyse.click();
    await page.waitForTimeout(1200);
    const tab = page.getByRole('tab', { name: 'History' }).first();
    if (await tab.count()) await tab.click(); else await page.getByText('History', { exact: true }).first().click().catch(() => {});
    await page.locator('[data-ws="analyse-history"]').waitFor({ timeout: 30000 }).catch(() => w.fail('no history plot'));
    await page.locator('[data-ws="analyse-history"]').scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(800);
    const notes = await page.locator('[data-ws="analyse-history-varies"]').allInnerTexts();
    w.note(`history notes: ${notes.join(' / ')}`);
    if (!notes.some((n) => /inlet/.test(n))) w.fail('the history does not say the inlet varies');
    // The caption wraps rather than cutting off the max and the last period's mean.
    const caption = page.locator('[data-ws="analyse-history-caption"]').first();
    const captionText = await caption.innerText().catch(() => '');
    w.note(`history caption: ${captionText}`);
    const cut = await caption.evaluate((el) => el.scrollWidth > el.clientWidth + 1).catch(() => true);
    if (cut) w.fail('the history caption is cut off');
    if (!/max/.test(captionText)) w.fail(`the history caption has no max: ${captionText}`);
    await shots('results-history');
  } else w.fail('no Analyse on the Results card');

  // 8. Full mode's Results panel steps through the written times too (it had no time control).
  await page.getByRole('radio', { name: 'Full' }).click();
  await page.waitForTimeout(1500);
  // A Results tree entry opens the Results panel on the Details tab.
  await page.getByRole('tab', { name: 'Simulation tree' }).first().click().catch(() => {});
  for (const group of ['Results', 'Graphics']) {
    const expand = page.getByRole('button', { name: `Expand ${group}` });
    if (await expand.count()) await expand.first().click().catch(() => {});
    await page.waitForTimeout(400);
  }
  await page.locator('[role="tree"]').first().getByText('Contours', { exact: true }).first().click().catch(() => {});
  await page.waitForTimeout(800);
  await page.getByRole('tab', { name: 'Details' }).first().click().catch(() => {});
  await page.locator('[data-ws="results-panel"]').first().waitFor({ timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(800);
  const slider = page.locator('[data-ws="results-time-slider"]').first();
  if (await slider.count()) {
    await slider.scrollIntoViewIfNeeded().catch(() => {});
    const stateText = page.locator('[data-ws="results-state-text"]').first();
    const was = await stateText.innerText().catch(() => '');
    const back = page.locator('[data-ws="results-time-back"]').first();
    const forward = page.locator('[data-ws="results-time-on"]').first();
    const move = (await back.getAttribute('aria-disabled')) === 'true' ? forward : back;
    await move.click();
    await until('the Full Results panel to show another time', async () => (await stateText.innerText().catch(() => was)) !== was, 60000);
    w.note(`Full results: ${was} -> ${await stateText.innerText().catch(() => '')}`);
    await shots('full-results-time');
  } else w.fail('the Full mode Results panel has no time slider');
} catch (e) {
  w.fail(`stopped: ${e.message.split('\n')[0]}`);
  await w.shot('stopped').catch(() => {});
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
