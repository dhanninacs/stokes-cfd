// The Lattice tool through the GUI on a NEW project: a 20 mm box filled with each pattern as a sheet,
// then a network, then what the card refuses (a wall too thick for its cell).
//   node walk_lattice_gui.mjs "<url-with-token>" "<projects folder>" [shots]
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';
const [url, folder, shotsArg] = process.argv.slice(2);
if (!url || !folder) { console.error('usage: node walk_lattice_gui.mjs <url-with-token> <projects folder> [shots]'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'lattice'));
const { page } = w;

async function box() {
  await w.tool('shape', 'Box');
  for (const axis of ['x', 'y', 'z']) await w.setField(`Size ${axis}`, '20');
  await w.run('box');
}
async function fit(name) {
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await page.waitForTimeout(900);
  await w.shot(name);
}

for (const [kind, form] of [['gyroid', 'sheet'], ['schwarz_p', 'sheet'], ['diamond', 'sheet'], ['gyroid', 'network']]) {
  await w.newProject(`lattice-${kind}-${form}`);
  await box();
  await w.pick('box');
  await w.tool('modify', 'Lattice');
  w.note(`card: ${JSON.stringify(await w.cardControls())}`);
  await w.choose('Pattern', kind);
  await w.choose('Form', form);
  await w.setField('Cell size', '6');
  if (form === 'sheet') await w.setField('Wall thickness', '0.8');
  await w.shot('card');
  const t0 = Date.now();
  const ok = await w.run(`${kind}-${form}`, 240000);
  w.note(`${kind} ${form}: ${ok ? 'built' : 'not built'} in ${Math.round((Date.now() - t0) / 1000)} s; tree: ${(await w.treeNames()).join(' | ')}`);
  await fit(`${kind}-${form}-fit`);
  await w.checkAlerts(`${kind} ${form}`);
}

// A refusal says why and keeps the card.
await w.newProject('lattice-refused');
await box();
await w.pick('box');
await w.tool('modify', 'Lattice');
await w.setField('Cell size', '3');
await w.setField('Wall thickness', '1.5');
await w.card.getByRole('button', { name: 'Run', exact: true }).click();
await page.waitForTimeout(4000);
const said = (await w.card.innerText().catch(() => '')).replace(/\n+/g, ' | ');
w.note(`refusal: ${said.slice(-220)}`);
if (!/third of the cell/.test(said)) w.fail(`a wall too thick for its cell was not refused in words: ${said.slice(-200)}`);
await w.shot('refused');
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
