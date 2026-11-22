// Every option of the Results stage, pressed in the running app on a SOLVED project, with a
// screenshot per action and a list of problems at the end: the Guided Results card (every field,
// look and look control, range, map, bands, lines, time, probe, line, views, export, report and
// every Analyse tab), then Full mode's Results tree (every Surfaces, Graphics, Plots and Reports
// entry and the controls of the panel it opens).
//
//   node walk_results_gui.mjs "<url-with-token>" "<solved project folder (.stokes)>" [shots]
//
// Work on a COPY of the project: the walk changes the view and may save view nodes. Besides the
// page errors, console errors, failed requests and alerts every walk reports, it checks that the
// legend follows each field and that the viewport draws something after each press.
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';

const args = process.argv.slice(2);
const [url, project, shotsArg] = args.filter((a) => !a.startsWith('--'));
if (!url || !project) {
  console.error('usage: node walk_results_gui.mjs <url-with-token> <solved project folder> [shots]');
  process.exit(2);
}
const shots = shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'results');
const w = await openWalk(url, join(shots, 'projects'), shots);
const { page, visible } = w;
const card = page.locator('.guided-card');
const slug = (s) => String(s).replace(/[^a-z0-9]+/gi, '_').slice(0, 40);
const text = async (loc) => (await loc.first().innerText().catch(() => '')).split('\n').join(' | ');

/** The viewport: the largest canvas on the page (the view cube and the plots have their own). */
async function viewportBox() {
  const boxes = await page.locator('canvas').evaluateAll((list) => list.map((c) => { const r = c.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }));
  return boxes.sort((a, b) => b.width * b.height - a.width * a.height)[0] ?? null;
}

/**
 * Whether the viewport shows a coloured model: a screenshot of its middle, decoded in the page, has
 * enough saturated pixels. (A WebGL canvas cannot be read back directly, and the grid alone is grey.)
 */
async function drawn() {
  const canvas = await viewportBox();
  if (!canvas) return false;
  const clip = { x: canvas.x + canvas.width * 0.3, y: canvas.y + canvas.height * 0.15, width: canvas.width * 0.6, height: canvas.height * 0.7 };
  const png = (await page.screenshot({ clip })).toString('base64');
  return page.evaluate(async (data) => {
    const img = new Image();
    img.src = `data:image/png;base64,${data}`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = 96;
    c.height = 96;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0, 96, 96);
    const d = ctx.getImageData(0, 0, 96, 96).data;
    let coloured = 0;
    for (let i = 0; i < d.length; i += 4) {
      const max = Math.max(d[i], d[i + 1], d[i + 2]);
      const min = Math.min(d[i], d[i + 1], d[i + 2]);
      if (max - min > 40) coloured += 1;
    }
    return coloured > 96 * 96 * 0.01;
  }, png);
}

async function after(what, { legend = true, drawn: check = true } = {}) {
  await page.waitForTimeout(1200);
  await w.checkAlerts(what);
  if (check && !(await drawn())) w.fail(`${what}: the viewport looks empty`);
  if (legend) {
    const has = await page.locator('[data-ws="field-legend"]').count();
    if (!has) w.fail(`${what}: no colour legend`);
  }
}

/** Every visible control of a region, as the inventory walk lists them. */
async function controls(region) {
  return region.first().evaluate((root) => Array.from(root.querySelectorAll('button, input, select, [role="radio"], [role="switch"], [role="tab"], [role="checkbox"], a[href]')).filter((el) => el.offsetParent).map((el) => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || el.getAttribute('type') || '',
    name: (el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') ? document.getElementById(el.getAttribute('aria-labelledby'))?.innerText : '') || el.labels?.[0]?.innerText || el.innerText || el.getAttribute('title') || '').trim().split('\n')[0],
    ws: el.getAttribute('data-ws') || '',
    value: el.tagName === 'SELECT' || el.tagName === 'INPUT' ? el.value : (el.getAttribute('aria-checked') ?? el.getAttribute('aria-pressed') ?? ''),
    options: el.tagName === 'SELECT' ? Array.from(el.options).filter((o) => !o.disabled).map((o) => o.value) : undefined,
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
  })));
}

/** Name every control, then cycle each select through its options and press each chip of a group. */
async function explore(region, name, { skip = [] } = {}) {
  const list = await controls(region);
  w.note(`${name}: ${list.map((c) => `${c.role || c.tag}:${c.name || '(no name)'}${c.options ? `[${c.options.join(',')}]` : ''}${c.disabled ? ' (off)' : ''}`).join('; ')}`);
  for (const c of list) if (!c.name && !c.ws) w.fail(`${name}: a ${c.role || c.tag} has no name`);
  for (const c of list.filter((x) => x.tag === 'select' && !x.disabled && !skip.includes(x.name))) {
    const select = region.locator(c.ws ? `select[data-ws="${c.ws}"]` : `select[aria-label="${c.name}"]`).first();
    for (const option of c.options) {
      if (option === c.value || option === '') continue;
      try {
        await select.selectOption(option);
        await after(`${name} ${c.name}=${option}`, { legend: false });
        await w.shot(`${name}-${slug(c.name)}-${slug(option)}`);
      } catch (e) {
        w.fail(`${name} ${c.name}=${option}: ${e.message.split('\n')[0]}`);
      }
    }
    if (c.value) await select.selectOption(c.value).catch(() => {});
  }
  // Switches: toggled and back.
  for (const c of list.filter((x) => x.role === 'switch' && !x.disabled && !skip.includes(x.name))) {
    const sw = region.getByRole('switch', { name: c.name, exact: true }).first();
    for (let k = 0; k < 2; k++) {
      try {
        await sw.click({ timeout: 4000 });
        await after(`${name} switch ${c.name} ${k === 0 ? 'toggled' : 'back'}`, { legend: false });
        if (k === 0) await w.shot(`${name}-switch-${slug(c.name)}`);
      } catch (e) {
        w.fail(`${name} switch ${c.name}: ${e.message.split('\n')[0]}`);
        break;
      }
    }
  }
  // Radios: each in turn, then the one that was on.
  for (const c of list.filter((x) => x.role === 'radio' && !x.disabled && !skip.includes(x.name))) {
    try {
      await region.getByRole('radio', { name: c.name, exact: true }).first().click({ timeout: 4000 });
      await after(`${name} radio ${c.name}`, { legend: false });
    } catch (e) {
      w.fail(`${name} radio ${c.name}: ${e.message.split('\n')[0]}`);
    }
  }
  for (const c of list.filter((x) => x.role === 'radio' && x.value === 'true')) await region.getByRole('radio', { name: c.name, exact: true }).first().click().catch(() => {});
  // Chip groups: each chip of a group in turn, then the one that was on.
  const groups = region.locator('[role="group"]');
  for (let g = 0; g < (await groups.count()); g++) {
    const group = groups.nth(g);
    const label = (await group.getAttribute('aria-label')) ?? `group ${g}`;
    if (skip.includes(label)) continue;
    const toggles = label === 'Surfaces shown';
    const chips = group.locator(':scope > button.guided-chip');
    const n = await chips.count();
    if (n === 0) continue;
    let on = -1;
    for (let i = 0; i < n; i++) if ((await chips.nth(i).getAttribute('aria-pressed')) === 'true') on = i;
    for (let i = 0; i < n; i++) {
      const chip = chips.nth(i);
      const chipName = (await chip.innerText()).trim();
      if ((await chip.getAttribute('aria-disabled')) === 'true') continue;
      try {
        await chip.click({ timeout: 4000 });
        await after(`${name} ${label}=${chipName}`, { legend: false, drawn: !toggles });
        await w.shot(`${name}-${slug(label)}-${slug(chipName)}`);
        // A group of toggles (each chip on or off by itself) is put back at once.
        if (toggles) await chip.click({ timeout: 4000 });
      } catch (e) {
        w.fail(`${name} ${label}=${chipName}: ${e.message.split('\n')[0]}`);
      }
    }
    if (on >= 0 && !toggles) await chips.nth(on).click().catch(() => {});
  }
  return list;
}

try {
  await w.openProject(project, 'guided');
  await page.getByRole('radio', { name: 'Guided' }).click().catch(() => {});
  await page.getByRole('button', { name: /^Results,/ }).first().click();
  await card.first().waitFor();
  await page.waitForTimeout(6000);
  await w.shot('results-card');
  w.note(`card: ${await text(card)}`);
  await after('first picture');

  // Every field the run offers: the chips, then the "more" menu.
  const fieldChips = card.locator('[aria-label="What colours the model"] button');
  for (let i = 0; i < (await fieldChips.count()); i++) {
    const name = (await fieldChips.nth(i).innerText()).trim();
    await fieldChips.nth(i).click();
    await after(`field ${name}`);
    w.note(`field ${name}: legend ${await text(page.locator('[data-ws="field-legend"]'))}`);
    await w.shot(`field-${slug(name)}`);
  }
  // Every variable of the field picker, one after another.
  const picker = card.locator('[data-ws="guided-field-picker"]');
  if (!(await picker.count())) w.fail('no field picker on the Results card');
  else {
    await picker.click();
    await page.waitForTimeout(1500);
    const rows = await page.locator('.field-picker-row').evaluateAll((list) => list.map((r) => ({ name: r.getAttribute('data-name'), off: r.getAttribute('aria-disabled') === 'true', title: r.getAttribute('title') })));
    await w.shot('field-picker');
    await page.keyboard.press('Escape');
    w.note(`variables: ${rows.map((r) => `${r.name}${r.off ? ` (off: ${r.title})` : ''}`).join(', ')}`);
    for (const row of rows.filter((r) => !r.off)) {
      await picker.click();
      await page.waitForTimeout(600);
      await page.locator(`.field-picker-row[data-name="${row.name}"]`).click();
      await page.waitForTimeout(3000);
      await after(`variable ${row.name}`);
      w.note(`variable ${row.name}: legend ${await text(page.locator('[data-ws="field-legend"]'))}`);
      await w.shot(`variable-${slug(row.name)}`);
    }
  }
  await fieldChips.first().click();

  // Every look and the controls it brings.
  const looks = card.locator('[data-ws="guided-looks"] button');
  const lookNames = await looks.allInnerTexts();
  for (const [i, look] of lookNames.entries()) {
    await looks.nth(i).click();
    await page.waitForTimeout(3500);
    await after(`look ${look}`);
    await w.shot(`look-${slug(look)}`);
    const inner = card.locator('[data-ws="guided-look-controls"]');
    if (await inner.count()) await explore(inner, `look-${slug(look)}`);
  }
  await looks.nth(Math.max(0, lookNames.findIndex((l) => /slice/i.test(l)))).click();
  await page.waitForTimeout(2500);

  // The legend rows: range, map, bands, lines; the time row.
  await explore(card.locator('[data-ws="guided-legend"]'), 'legend');
  const shown = card.locator('[data-ws="guided-shown"]');
  if (await shown.count()) await explore(shown, 'shown');
  else w.fail('no Show row on the Results card');
  const time = card.locator('[data-ws="guided-time"]');
  if (await time.count()) await explore(time, 'time');

  // Probe: clicks on a grid of points until one lands on the model; those points serve the Line too.
  const box = await viewportBox();
  const grid = [];
  for (const fy of [0.5, 0.65, 0.8, 0.35, 0.2]) for (const fx of [0.5, 0.65, 0.8, 0.35, 0.2]) grid.push([fx, fy]);
  const landed = [];
  await card.locator('[data-ws="guided-probe"]').click();
  for (const [fx, fy] of grid) {
    if (!box) break;
    if (!(await page.evaluate(() => document.querySelector('[data-ws="guided-probe"]')?.getAttribute('aria-pressed') === 'true'))) await card.locator('[data-ws="guided-probe"]').click();
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    await page.waitForTimeout(1500);
    if (await card.locator('[data-ws="guided-probe-value"]').count()) {
      landed.push([fx, fy]);
      if (landed.length === 1) {
        w.note(`probe at ${fx},${fy}: ${await text(card.locator('[data-ws="guided-probe-value"]'))}`);
        await w.shot('probe');
      }
      if (landed.length >= 2) break;
    }
  }
  if (landed.length === 0) w.fail('probe: no value after clicks all over the viewport');
  if (await page.evaluate(() => document.querySelector('[data-ws="guided-probe"]')?.getAttribute('aria-pressed') === 'true')) await card.locator('[data-ws="guided-probe"]').click();

  // The hover readout: the pointer over a point that probed.
  if (box && landed.length) {
    await page.mouse.move(box.x + box.width * landed[0][0], box.y + box.height * landed[0][1]);
    await page.waitForTimeout(800);
    const tag = page.locator('[data-ws="hover-readout"]');
    if (!(await tag.count())) w.fail('hover: no readout over the model');
    else w.note(`hover: ${await text(tag)}`);
    await w.shot('hover-readout');
  }

  // Line: between the two points that probed.
  await card.locator('[data-ws="guided-line"]').click();
  if (box && landed.length >= 2) {
    await page.mouse.click(box.x + box.width * landed[0][0], box.y + box.height * landed[0][1]);
    // Apart, or the two clicks read as a double-click.
    await page.waitForTimeout(900);
    await page.mouse.click(box.x + box.width * landed[1][0], box.y + box.height * landed[1][1]);
  }
  await page.waitForTimeout(3000);
  if (!(await page.locator('[data-ws="guided-line-plot"]').count())) w.fail('line: no plot after two clicks on the model');
  await w.shot('line');
  await page.locator('[data-ws="guided-line-clear"]').click().catch(() => {});

  // Views: open the menu, save one, close.
  await card.locator('[data-ws="guided-views"]').click();
  await page.waitForTimeout(800);
  await w.shot('views-menu');
  await w.checkAlerts('views');
  await page.keyboard.press('Escape');

  // Export: the dialog opens and closes.
  await card.locator('[data-ws="guided-export"]').click();
  await page.waitForTimeout(1000);
  await w.shot('export-dialog');
  await w.checkAlerts('export');
  await page.keyboard.press('Escape');

  // Analyse: every tab computes with its defaults.
  for (const tab of ['report', 'xy', 'histogram', 'history', 'functions']) {
    const button = card.locator(`[data-ws="analyse-tab-${tab}"]`);
    if (!(await button.count())) { w.fail(`analyse ${tab}: no tab`); continue; }
    await button.click();
    await page.waitForTimeout(4000);
    await w.checkAlerts(`analyse ${tab}`);
    const err = card.locator('[data-ws="analyse-error"]');
    if (await err.count()) w.fail(`analyse ${tab}: ${await text(err)}`);
    await w.shot(`analyse-${tab}`);
    w.note(`analyse ${tab}: ${(await text(card.locator('[data-ws="guided-analyse"]'))).slice(0, 400)}`);
    if (tab !== 'functions') {
      const block = card.locator('[data-ws="guided-analyse"]');
      await explore(block, `analyse-${tab}`, { skip: ['Analyse'] });
    }
  }

  // Full mode: every Results tree entry and the panel it opens.
  w.section('full');
  await page.getByRole('radio', { name: 'Full' }).click();
  await page.waitForTimeout(1500);
  const tree = page.locator('[role="tree"]').first();
  const results = tree.getByText('Results', { exact: true }).first();
  await results.click();
  for (const group of ['Surfaces', 'Graphics', 'Plots', 'Reports']) {
    const expand = page.getByRole('button', { name: `Expand ${group}` });
    if (await expand.count()) await expand.first().click().catch(() => {});
  }
  await page.waitForTimeout(800);
  const rows = await tree.evaluate((root) => {
    const all = Array.from(root.querySelectorAll('[role="treeitem"]'));
    const start = all.findIndex((r) => r.innerText.split('\n')[0].trim() === 'Results');
    const out = [];
    for (const r of all.slice(start + 1)) {
      const label = r.innerText.split('\n')[0].trim();
      if (label === 'Explore') break;
      out.push(label);
    }
    return out;
  });
  w.note(`results tree: ${rows.join(', ')}`);
  for (const row of rows) {
    if (['Surfaces', 'Graphics', 'Plots', 'Reports'].includes(row)) continue;
    try {
      await tree.getByText(row, { exact: true }).first().click({ timeout: 4000 });
      await page.waitForTimeout(2000);
      await w.checkAlerts(`tree ${row}`);
      await w.shot(`tree-${slug(row)}`);
      const pane = page.locator('[data-ws="details-pane"], [data-ws="results-panel"]').first();
      if (await pane.count()) await explore(pane, `panel-${slug(row)}`);
    } catch (e) {
      w.fail(`tree ${row}: ${e.message.split('\n')[0]}`);
    }
  }
} catch (e) {
  w.fail(`stopped: ${e.message.split('\n')[0]}`);
  await w.shot('stopped').catch(() => {});
}
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
