// Lists every control of every build-ribbon tool's card on a new project: the inventory the
// option walks are written from.  node walk_inventory_gui.mjs "<url-with-token>" "<projects folder>" [shots]
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const [url, folder, shotsArg] = process.argv.slice(2);
if (!url || !folder) { console.error('usage: node walk_inventory_gui.mjs <url-with-token> <projects folder> [shots]'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'inventory'));

await w.newProject('inventory');
await w.shot('empty');
const groups = await w.page.locator('[data-ws="build-ribbon"] [role="group"]').evaluateAll((els) => els.map((g) => ({
  id: g.getAttribute('data-ws'), label: g.getAttribute('aria-label'),
  tools: Array.from(g.querySelectorAll('button')).map((b) => ({ label: b.getAttribute('aria-label'), disabled: b.disabled || b.getAttribute('aria-disabled') === 'true' })),
})));
console.log(JSON.stringify(groups, null, 1));

// Two boxes, so every tool has a pick that fits.
await w.tool('shape', 'Box');
console.log('Box', JSON.stringify(await w.cardControls()));
await w.run('box-a');
await w.tool('shape', 'Sphere');
console.log('Sphere', JSON.stringify(await w.cardControls()));
await w.run('sphere-b');
const names = await w.treeNames();
console.log('tree', JSON.stringify(names));

for (const kind of ['Rounded', 'Cylinder', 'Cone', 'Torus', 'Capsule', 'Half-space']) {
  await w.tool('shape', kind);
  console.log(kind, JSON.stringify(await w.cardControls()));
  await w.shot(`card-${kind}`);
  await w.page.keyboard.press('Escape');
  await w.card.waitFor({ state: 'detached', timeout: 5000 }).catch(() => w.fail(`${kind}: Esc did not close the card`));
}
const body = names.find((n) => /box/i.test(n));
for (const [group, label] of [['transform', 'Move'], ['transform', 'Rotate'], ['transform', 'Mirror'], ['transform', 'Scale'], ['pattern', 'Linear'], ['pattern', 'Polar'], ['combine', 'Split'], ['modify', 'Offset'], ['modify', 'Shell']]) {
  await w.pick(body);
  try {
    await w.tool(group, label);
    console.log(label, JSON.stringify(await w.cardControls()));
    await w.shot(`card-${label}`);
    await w.page.keyboard.press('Escape');
    await w.card.waitFor({ state: 'detached', timeout: 5000 }).catch(() => w.fail(`${label}: Esc did not close the card`));
  } catch (e) { w.fail(`${label}: ${e.message.split('\n')[0]}`); await w.shot(`fail-${label}`); await w.page.keyboard.press('Escape'); }
}
const other = names.find((n) => /sphere/i.test(n));
for (const label of ['Union', 'Subtract', 'Intersect']) {
  await w.pick(body);
  await w.pick(other, true);
  try {
    await w.tool('combine', label);
    console.log(label, JSON.stringify(await w.cardControls()));
    await w.shot(`card-${label}`);
    await w.page.keyboard.press('Escape');
    await w.card.waitFor({ state: 'detached', timeout: 5000 }).catch(() => w.fail(`${label}: Esc did not close the card`));
  } catch (e) { w.fail(`${label}: ${e.message.split('\n')[0]}`); await w.shot(`fail-${label}`); await w.page.keyboard.press('Escape'); }
}
// The sketcher's own controls.
await w.tool('sketch', 'Sketch', false);
await w.page.locator('[data-ws="sketch-editor"]').waitFor();
const sk = await w.page.locator('[data-ws="sketch-editor"]').evaluate((root) => Array.from(root.querySelectorAll('button, input, select')).filter((el) => el.offsetParent).map((el) => `${el.tagName.toLowerCase()}:${el.getAttribute('aria-label') || el.innerText || el.getAttribute('data-ws') || ''}${el.tagName === 'SELECT' ? ` [${Array.from(el.options).map((o) => o.value).join(',')}]` : ''}`));
console.log('sketcher', JSON.stringify(sk));
await w.shot('sketcher');
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
