// Every build action through the GUI, each on a NEW project with a NEW sketch, every option of
// every card and Details page tried in turn, a screenshot per action, problems listed at the end.
//
//   node walk_build_gui.mjs "<url-with-token>" "<projects folder>" [shots] [--only=name,name]
//
// A server must be running (`python -m stokes.server --port N`). Scenario groups: sketch tools,
// sketch options (planes, pull, revolve, both directions, typed entry, undo, cancel), shapes,
// transforms, patterns, combines, split, offset and shell.
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const only = (args.find((a) => a.startsWith('--only=')) ?? '').slice(7).split(',').filter(Boolean);
const [url, folder, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!url || !folder) { console.error('usage: node walk_build_gui.mjs <url-with-token> <projects folder> [shots] [--only=a,b]'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'build'));
const { page } = w;
const details = page.locator('[data-ws="details-pane"]');
const editor = page.locator('[data-ws="sketch-editor"]');

/** Open the sketcher with a tool from the ribbon's Sketch group, on a plane. */
async function sketch(toolLabel, plane = 'xy') {
  await w.openRibbon();
  await page.locator('[data-ws="build-sketch"] button').first().click();
  await page.getByRole('menuitem', { name: toolLabel, exact: true }).click();
  await editor.waitFor();
  await page.locator(`[data-ws="sketch-plane-${plane}"]`).click();
  await page.waitForTimeout(700);
}
const status = async () => (await page.locator('[data-ws="sketch-status"]').innerText()).split('\n')[0];
async function expectClosed(what) {
  const s = await status();
  w.note(`${what}: ${s}`);
  if (!/^1 closed loop/.test(s)) w.fail(`${what}: not one closed loop: ${s}`);
}
/** Finish: the sketcher closes and the body is pulled at once (ADR-0039). */
async function finish(name) {
  await w.shot(`${name}-sketch`);
  await page.locator('[data-ws="sketch-finish"]').click();
  await editor.waitFor({ state: 'detached', timeout: 15000 }).catch(() => w.fail(`${name}: the sketcher stayed open after Finish`));
  // The new body is picked once its first pass has drawn: wait for it, then for the fine pass.
  for (let i = 0; i < 120 && !(await w.treeNames()).some((n) => /extrusion|revolve|sketch/i.test(n)); i += 1) await page.waitForTimeout(500);
  await w.settle();
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await page.waitForTimeout(700);
  await w.shot(`${name}-body`);
  await w.checkAlerts(name);
  const names = await w.treeNames();
  if (!names.some((n) => /extrusion|revolve|sketch/i.test(n))) w.fail(`${name}: no body in the tree: ${names.join(', ')}`);
  await partOk(name);
  const adv = details.getByText('Advanced', { exact: true }).first();
  if (await adv.count()) { await adv.click(); await page.waitForTimeout(300); await w.shot(`${name}-advanced`); await adv.click(); }
}
/** The picked part's status chip in Details reads ok. */
async function partOk(what) {
  const text = (await details.innerText().catch(() => '')).replace(/\n+/g, ' | ');
  if (!text) { w.fail(`${what}: no Details page`); return; }
  if (/failed|error|stale/i.test(text.slice(0, 200)) || !/\bok\b/.test(text.slice(0, 200))) w.fail(`${what}: the part is not ok: ${text.slice(0, 200)}`);
}
/** Every visible select of Details with its options. */
async function detailSelects() {
  return details.evaluate((root) => Array.from(root.querySelectorAll('select')).filter((el) => el.offsetParent).map((el) => ({ label: el.getAttribute('aria-label') || (el.labels?.[0]?.innerText ?? ''), value: el.value, options: Array.from(el.options).map((o) => o.value) })));
}
async function setDetail(label, text) {
  const field = details.getByLabel(label, { exact: true }).first();
  await field.fill(String(text));
  await field.press('Enter');
  await w.settle(120000, true);
}
async function chooseDetail(label, value) {
  await details.getByLabel(label, { exact: true }).first().selectOption(value);
  await w.settle(120000, true);
}
/** Try every option of every select on the Details page, restoring the first value after. */
async function cycleDetailSelects(name, skip = []) {
  const selects = await detailSelects();
  w.note(`${name} selects: ${selects.map((s) => `${s.label}[${s.options.join(',')}]`).join('; ')}`);
  for (const s of selects) {
    if (!s.label) { w.fail(`${name}: a select has no label (options ${s.options.join(',')})`); continue; }
    if (skip.includes(s.label)) continue;
    for (const option of s.options) {
      if (option === s.value) continue;
      try {
        await chooseDetail(s.label, option);
        await w.shot(`${name}-${s.label.replace(/\s+/g, '_')}-${option.replace(/[^a-z0-9+-]/gi, '_')}`);
        await w.checkAlerts(`${name} ${s.label}=${option}`);
        await partOk(`${name} ${s.label}=${option}`);
      } catch (e) { w.fail(`${name} ${s.label}=${option}: ${e.message.split('\n')[0]}`); }
    }
    await chooseDetail(s.label, s.value).catch((e) => w.fail(`${name}: could not restore ${s.label}: ${e.message.split('\n')[0]}`));
  }
}

const scenarios = {
  // ---- sketch tools: one project and one sketch each ----
  'sketch-line': async () => {
    await sketch('Line');
    for (const [u, v] of [[0, 0], [30, 0], [15, 20], [0, 0]]) await w.at(u, v);
    await expectClosed('triangle');
    await finish('line');
  },
  'sketch-rectangle': async () => {
    await sketch('Rectangle');
    await w.at(-15, -5); await w.at(15, 5);
    await expectClosed('rectangle');
    await finish('rectangle');
  },
  'sketch-rectangle-typed': async () => {
    await sketch('Rectangle');
    await w.at(0, 0);
    await page.keyboard.type('60'); await page.keyboard.press('Tab'); await page.keyboard.type('20'); await page.keyboard.press('Enter');
    await expectClosed('typed rectangle 60 x 20');
    const rows = await page.locator('[data-ws="sketch-table"]').innerText();
    w.note(`table: ${rows.replace(/\n+/g, ' | ').slice(0, 200)}`);
    await finish('rectangle-typed');
  },
  'sketch-circle': async () => {
    await sketch('Circle');
    await w.at(0, 0); await w.at(10, 0);
    await expectClosed('circle');
    await finish('circle');
  },
  'sketch-circle-typed': async () => {
    await sketch('Circle');
    await w.at(5, 5);
    await page.keyboard.type('8'); await page.keyboard.press('Enter');
    await expectClosed('typed circle r 8');
    await finish('circle-typed');
  },
  'sketch-arc': async () => {
    await sketch('Arc');
    await w.at(-10, 0); await w.at(10, 0); await w.at(0, 10);
    await page.locator('[data-ws="sketch-tool-line"]').click();
    await w.at(10, 0); await w.at(-10, 0);
    await page.keyboard.press('Escape');
    await expectClosed('arc and chord');
    await finish('arc');
  },
  'sketch-spline': async () => {
    await sketch('Spline');
    for (const [u, v] of [[-15, 0], [-5, 10], [5, 12], [15, 0]]) await w.at(u, v);
    await page.keyboard.press('Enter');
    await page.locator('[data-ws="sketch-tool-line"]').click();
    await w.at(15, 0); await w.at(-15, 0);
    await page.keyboard.press('Escape');
    await expectClosed('spline and chord');
    await finish('spline');
  },
  'sketch-polygon': async () => {
    await sketch('Polygon');
    const sides = editor.getByLabel(/sides/i).first();
    if (await sides.count()) { await sides.fill('5'); await sides.press('Tab'); } else w.fail('polygon: no sides field');
    await w.at(0, 0); await w.at(12, 0);
    await expectClosed('pentagon');
    await finish('polygon');
  },
  'sketch-two-loops': async () => {
    // A rectangle with a round hole: an outer and an inner loop.
    await sketch('Rectangle');
    await w.at(-20, -10); await w.at(20, 10);
    await page.locator('[data-ws="sketch-tool-circle"]').click();
    await w.at(0, 0); await w.at(5, 0);
    w.note(`plate with hole: ${await status()}`);
    await finish('plate-hole');
  },
  // ---- the sketcher's own controls ----
  'sketch-controls': async () => {
    await sketch('Line');
    await w.at(0, 0); await w.at(20, 0); await w.at(20, 10);
    await page.keyboard.press('Escape');
    const open = await status();
    w.note(`open chain: ${open}`);
    const finishButton = page.locator('[data-ws="sketch-finish"]');
    if ((await finishButton.getAttribute('aria-disabled')) !== 'true') w.fail('Finish is enabled on an open profile');
    await w.shot('open');
    await editor.getByRole('button', { name: 'Undo' }).click();
    w.note(`after Undo: ${await status()}`);
    await editor.getByRole('button', { name: 'Redo' }).click();
    w.note(`after Redo: ${await status()}`);
    await page.keyboard.press('Control+z'); await page.keyboard.press('Control+y');
    await editor.getByRole('button', { name: 'Snap to grid' }).click();
    await page.locator('[data-ws="sketch-tool-line"]').click();
    await w.at(20, 10); await w.at(3.3, 12.7); await w.at(0, 0);
    await page.keyboard.press('Escape');
    w.note(`snap off, closed by clicking the ends: ${await status()}`);
    await editor.getByRole('button', { name: 'Snap to grid' }).click();
    await editor.getByRole('button', { name: 'Fit' }).click();
    await w.shot('fit');
    // Select: pick an entity and delete it; then undo.
    await page.locator('[data-ws="sketch-tool-select"]').click();
    const rowsBefore = await page.locator('[data-ws="sketch-table"] .sketch-row').count();
    await page.locator('[data-ws="sketch-table"] .sketch-row').first().click();
    await page.keyboard.press('Delete');
    const rowsAfter = await page.locator('[data-ws="sketch-table"] .sketch-row').count();
    w.note(`delete: ${rowsBefore} -> ${rowsAfter} entities`);
    if (rowsAfter !== rowsBefore - 1) w.fail(`Delete removed ${rowsBefore - rowsAfter} entities, not 1`);
    await editor.getByRole('button', { name: 'Undo' }).click();
    // Each tool by its key.
    for (const [key, id] of [['v', 'select'], ['l', 'line'], ['r', 'rectangle'], ['c', 'circle'], ['a', 'arc'], ['s', 'spline'], ['p', 'polygon']]) {
      await page.locator('[data-ws="sketch-canvas"]').hover();
      await page.keyboard.press(key);
      const on = await page.locator(`[data-ws="sketch-tool-${id}"]`).getAttribute('aria-pressed');
      if (on !== 'true') w.fail(`key ${key.toUpperCase()} did not choose ${id}`);
    }
    // The plane buttons of the header.
    for (const plane of ['YZ', 'ZX', 'XY']) {
      await editor.getByRole('radio', { name: plane, exact: true }).first().click();
      await page.waitForTimeout(400);
      await w.shot(`plane-${plane}`);
    }
    // Cancel with work drawn asks first; Discard closes with nothing made.
    await page.locator('[data-ws="sketch-cancel"]').click();
    await page.waitForTimeout(400);
    await w.shot('cancel-asked');
    const discard = page.locator('[data-ws="sketch-discard"]');
    if (await discard.count()) await discard.click(); else w.note('Cancel closed without asking');
    await editor.waitFor({ state: 'detached', timeout: 5000 }).catch(() => w.fail('the sketcher stayed open after Cancel'));
    const names = await w.treeNames();
    if (names.some((n) => /extrusion|sketch/i.test(n))) w.fail(`Cancel left a body: ${names.join(', ')}`);
  },
  'sketch-dimensions': async () => {
    await sketch('Rectangle');
    await w.at(-10, -5); await w.at(10, 5);
    const dims = page.locator('[data-ws="sketch-dimensions"]');
    await dims.getByLabel('New dimension name').fill('W');
    await dims.getByLabel('New dimension value').fill('40 [mm]');
    await dims.getByRole('button', { name: 'Add dimension' }).click();
    await page.waitForTimeout(600);
    const inputs = await page.locator('[data-ws="sketch-table"] input').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')));
    w.note(`table fields: ${inputs.join(', ')}`);
    const width = page.locator('[data-ws="sketch-table"]').getByLabel(/width/i).first();
    if (await width.count()) { await width.fill('W'); await width.press('Enter'); await page.waitForTimeout(1000); } else w.fail('no width field for the rectangle in the table');
    await w.shot('bound');
    await expectClosed('dimensioned rectangle');
    await finish('dimensioned');
    // Change the dimension from Details: the body follows.
    const field = details.getByLabel('Dimension W', { exact: true }).first();
    if (await field.count()) { const shown = await field.inputValue(); w.note(`Details shows W as ${shown}`); if (!/mm/.test(shown)) w.fail(`Details shows the dimension W as "${shown}", not in mm`); }
    if (await field.count()) { await field.fill('60 [mm]'); await field.press('Enter'); await w.settle(120000, true); await w.shot('W-60'); await partOk('W = 60 mm'); } else w.note('Details has no field named W');
  },
  // ---- planes, pull, revolve: the body's Details options ----
  ...Object.fromEntries(['xy', 'yz', 'zx'].map((plane) => [`plane-${plane}`, async () => {
    await sketch('Rectangle', plane);
    await w.at(5, -5); await w.at(25, 5);
    await finish(`plane-${plane}`);
    // Pull from the on-view field: type a depth.
    // Pull in the view: the chip opens a field; then drag the arrow.
    const chip = page.locator('[data-ws="pull-chip"]');
    if (await chip.count()) {
      await chip.click();
      const field = page.locator('[data-ws="pull-field"]');
      await field.fill('25'); await field.press('Enter');
      await w.settle(120000, true);
      await w.shot('pull-25'); await partOk('pull 25');
      const shown = await details.getByLabel('Pull distance', { exact: true }).first().inputValue();
      if (shown !== '25') w.fail(`after typing 25 in the view, Details shows a pull distance of ${shown}`);
      const arrow = await page.locator('[data-ws="pull-arrow"] .pull-head').boundingBox();
      const base = await page.locator('[data-ws="pull-arrow"] .pull-base').boundingBox();
      if (arrow && base) {
        const [ax, ay] = [arrow.x + arrow.width / 2, arrow.y + arrow.height / 2];
        const [bx, by] = [base.x + base.width / 2, base.y + base.height / 2];
        await page.mouse.move(ax, ay); await page.mouse.down();
        await page.mouse.move(ax + (ax - bx) * 0.5, ay + (ay - by) * 0.5, { steps: 8 });
        await w.shot('pull-dragging');
        await page.mouse.up();
        await w.settle(120000, true);
        const dragged = await details.getByLabel('Pull distance', { exact: true }).first().inputValue();
        w.note(`dragged the arrow: the pull distance is now ${dragged}`);
        if (dragged === '25') w.fail('dragging the pull arrow did not change the depth');
        await w.shot('pull-dragged'); await partOk('pull dragged');
      } else w.fail('no pull arrow in the view');
    } else w.fail('no pull chip in the view');
    await setDetail('Pull distance', '15 [mm]').catch((e) => w.fail(`Pull distance: ${e.message.split('\n')[0]}`));
    await w.shot('pull-15-details');
    const both = details.getByRole('switch').first();
    if (await both.count()) { await both.click(); await w.settle(120000, true); await w.shot('both-directions'); await partOk('both directions'); await both.click(); await w.settle(120000, true); } else w.fail('no Both directions switch');
    await cycleDetailSelects(`plane-${plane}`);
    for (const [axis, v] of [['x', '5'], ['y', '-3'], ['z', '2']]) await setDetail(`Origin ${axis}`, v).catch((e) => w.fail(`Origin ${axis}: ${e.message.split('\n')[0]}`));
    await w.shot('origin-moved');
    await partOk('origin moved');
  }])),
  'revolve': async () => {
    await sketch('Rectangle');
    await w.at(5, -5); await w.at(15, 5);
    await finish('revolve-start');
    const selects = await detailSelects();
    const method = selects.find((s) => /method/i.test(s.label));
    if (!method) { w.fail('no Method select'); return; }
    const rev = method.options.find((o) => /revol/i.test(o));
    if (!rev) { w.fail(`Method has no revolve: ${method.options.join(',')}`); return; }
    await chooseDetail(method.label, rev);
    await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
    await w.shot('revolved');
    await partOk('revolved');
    const fields = await details.evaluate((root) => Array.from(root.querySelectorAll('input, select, [role="switch"]')).filter((el) => el.offsetParent).map((el) => `${el.getAttribute('aria-label') || el.labels?.[0]?.innerText || '?'}=${el.value ?? ''}`));
    w.note(`revolve fields: ${fields.join('; ')}`);
    const angle = details.getByLabel(/angle/i).first();
    if (await angle.count()) {
      for (const a of ['90 [deg]', '180 [deg]', '270 [deg]']) { await angle.fill(a); await angle.press('Enter'); await w.settle(120000, true); await w.shot(`revolve-${a.split(' ')[0]}`); await partOk(`revolve ${a}`); }
    } else w.fail('revolve: no angle field');
    await cycleDetailSelects('revolve', [method.label]);
  },
};

// ---- shapes: each on its own project, every option of its page ----
for (const [label, key] of [['Box', 'box'], ['Rounded box', 'rounded_box'], ['Cylinder', 'cylinder'], ['Cone', 'cone'], ['Sphere', 'sphere'], ['Torus', 'torus'], ['Capsule', 'capsule'], ['Half-space', 'half_space']]) {
  scenarios[`shape-${key}`] = async () => {
    await w.tool('shape', label);
    for (const c of await w.cardControls()) if (!c.label) w.fail(`${label} card: a ${c.kind} has no label`);
    // Non-default numbers, so a field that is ignored shows.
    for (const [field, value] of [['Size x', '20'], ['Size y', '12'], ['Size z', '8'], ['Radius', '6'], ['Length', '18'], ['Corner radius', '2'], ['Top radius', '2'], ['Tube radius', '1.5'], ['Position x', '3']]) {
      if (await w.card.getByLabel(field, { exact: true }).count()) await w.setField(field, value);
    }
    await w.shot('card');
    if (!(await w.run('made'))) return;
    await w.checkAlerts(label);
    await partOk(label);
    await cycleDetailSelects(key, ['Kind']);
  };
}

/** A new sketch pulled into a body named extrusion: the start of every operation scenario. */
async function sketchedBody(shape = 'rectangle') {
  if (shape === 'circle') { await sketch('Circle'); await w.at(0, 0); await w.at(6, 0); } else { await sketch('Rectangle'); await w.at(-10, -5); await w.at(10, 5); }
  await finish('base');
  return 'extrusion';
}
async function operationCard(name, group, label, body, fill) {
  await w.pick(body);
  try { await w.tool(group, label); } catch (e) { w.fail(`${name}: the ${label} card did not open: ${e.message.split('\n')[0]}`); await w.shot('no-card'); return false; }
  for (const c of await w.cardControls()) if (!c.label) w.fail(`${label} card: a ${c.kind} has no label`);
  await fill();
  await w.shot('card');
  const ok = await w.run(name);
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await page.waitForTimeout(500);
  await w.shot(`${name}-fit`);
  await w.checkAlerts(name);
  w.note(`tree: ${(await w.treeNames()).join(' | ')}`);
  return ok;
}

for (const axis of ['x', 'y', 'z']) {
  scenarios[`move-${axis}`] = async () => {
    const body = await sketchedBody();
    await operationCard('moved', 'transform', 'Move', body, async () => w.setField(`Offset ${axis}`, '15'));
    await partOk('moved');
  };
  scenarios[`rotate-${axis}`] = async () => {
    const body = await sketchedBody();
    await operationCard('rotated', 'transform', 'Rotate', body, async () => { await w.choose('Axis', axis); await w.setField('Angle', '30'); await w.setField('Centre x', '5'); });
    await partOk('rotated');
  };
  scenarios[`mirror-${axis}`] = async () => {
    const body = await sketchedBody();
    await operationCard('mirrored', 'transform', 'Mirror', body, async () => { await w.choose('Axis', axis); await w.setField(`Centre ${axis}`, '12'); });
    await partOk('mirrored');
  };
  scenarios[`linear-${axis}`] = async () => {
    const body = await sketchedBody();
    await operationCard('linear', 'pattern', 'Linear', body, async () => { await w.choose('Axis', axis); await w.setField('Count', '4'); await w.setField('Pitch', '25'); });
    await partOk('linear pattern');
    await cycleDetailSelects(`linear-${axis}`, ['Kind']);
  };
  scenarios[`polar-${axis}`] = async () => {
    const body = await sketchedBody();
    await operationCard('polar', 'pattern', 'Polar', body, async () => { await w.choose('Axis', axis); await w.setField('Count', '5'); await w.setField(`Centre ${axis === 'x' ? 'y' : 'x'}`, '30'); await w.setField('Angle', '180'); });
    await partOk('polar pattern');
  };
}
scenarios['scale'] = async () => {
  const body = await sketchedBody();
  await operationCard('scaled', 'transform', 'Scale', body, async () => { await w.setField('Factor', '2'); await w.setField('Centre x', '10'); });
  await partOk('scaled');
};
for (const normal of ['+x', '-x', '+y', '-y', '+z', '-z']) {
  scenarios[`split${normal}`] = async () => {
    const body = await sketchedBody();
    await operationCard('split', 'combine', 'Split', body, async () => { await w.choose('Plane normal', normal); await w.setField(`Point on plane ${normal[1]}`, '2'); });
    const names = await w.treeNames();
    if (names.filter((n) => /above|below/.test(n)).length !== 2) w.fail(`split ${normal} did not leave two halves: ${names.join(', ')}`);
  };
}
scenarios['offset-out'] = async () => {
  const body = await sketchedBody();
  await operationCard('offset', 'modify', 'Offset', body, async () => w.setField('Distance', '2'));
  await partOk('offset 2');
};
scenarios['offset-in'] = async () => {
  const body = await sketchedBody();
  await operationCard('inset', 'modify', 'Offset', body, async () => w.setField('Distance', '-2'));
  await partOk('offset -2');
};
for (const outside of [false, true]) {
  scenarios[`shell-${outside ? 'outside' : 'inside'}`] = async () => {
    const body = await sketchedBody();
    await operationCard('shell', 'modify', 'Shell', body, async () => { await w.setField('Distance', '1.5'); if (outside) await w.card.getByRole('switch').first().click(); });
    await partOk('shell');
  };
}
for (const [label, op] of [['Union', 'union'], ['Subtract', 'difference'], ['Intersect', 'intersection']]) {
  for (const blend of ['0', '2']) {
    scenarios[`${op}-blend${blend}`] = async () => {
      await sketchedBody();
      // A second body through the ribbon: a sphere overlapping the plate's end.
      await w.tool('shape', 'Sphere');
      await w.setField('Radius', '6'); await w.setField('Position x', '10'); await w.setField('Position z', '5');
      await w.run('sphere');
      const names = await w.treeNames();
      const sphere = names.find((n) => /sphere/i.test(n));
      await w.pick('extrusion');
      await w.pick(sphere, true);
      try { await w.tool('combine', label); } catch (e) { w.fail(`${label}: the card did not open: ${e.message.split('\n')[0]}`); await w.shot('no-card'); return; }
      await w.setField('Blend radius', blend);
      await w.shot('card');
      await w.run(op);
      await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
      await page.waitForTimeout(500);
      await w.shot(`${op}-fit`);
      await w.checkAlerts(op);
      w.note(`tree: ${(await w.treeNames()).join(' | ')}`);
      await partOk(op);
      await page.waitForTimeout(20000);
      await w.shot(`${op}-after-20s`);
    };
  }
}

for (const [name, body] of Object.entries(scenarios)) {
  if (only.length && !only.some((o) => name.startsWith(o))) continue;
  try {
    await w.newProject(name);
    await body();
  } catch (e) {
    w.fail(`stopped: ${e.message.split('\n')[0]}`);
    await w.shot('stopped').catch(() => {});
    await page.keyboard.press('Escape').catch(() => {});
  }
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
