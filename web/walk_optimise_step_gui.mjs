// The Optimise step of the flow strip on a NEW project: the chip, its card in Guided mode (the two
// blocks, the disabled shape button before a body exists, New study…), and the chip in Full mode.
//   node walk_optimise_step_gui.mjs "<url-with-token>" "<projects folder>" [shots]
import { join } from 'node:path';
import { openWalk } from './walk_lib.mjs';
const [url, folder, shotsArg] = process.argv.slice(2);
if (!url || !folder) { console.error('usage: node walk_optimise_step_gui.mjs <url-with-token> <projects folder> [shots]'); process.exit(2); }
const w = await openWalk(url, folder, shotsArg ?? join(process.cwd(), '..', '.local', 'walk', 'optimise-step'));
const { page } = w;
const card = page.locator('.guided-card');
await w.newProject('optimise-step');
const chips = await page.locator('[data-ws="guided-strip"] .guided-step').evaluateAll((els) => els.map((e) => `${e.getAttribute('data-step')}:${e.getAttribute('data-state')}:${e.getAttribute('title')}`));
w.note(`strip: ${chips.join(' | ')}`);
if (chips.length !== 7 || !chips[6].startsWith('optimise:')) w.fail(`the strip has ${chips.length} steps, the last is ${chips[chips.length - 1]}`);
await w.shot('full-empty');
// Full mode: the chip brings Explore in front.
await page.locator('[data-ws="guided-strip"] [data-step="optimise"]').click();
await page.waitForTimeout(800);
await w.shot('full-optimise-pressed');
// A body, then Guided mode and the card.
await w.tool('shape', 'Box'); await w.run('box');
await page.locator('[data-ws="build-close"]').click().catch(() => {});
await page.getByRole('radio', { name: 'Guided' }).click();
await page.waitForTimeout(1000);
await page.locator('[data-ws="guided-strip"] [data-step="optimise"]').click();
await page.waitForTimeout(800);
w.note(`card: ${(await card.first().innerText()).split('\n').join(' | ')}`);
if ((await card.getAttribute('data-step')) !== 'optimise') w.fail('the Optimise card did not open');
await w.shot('guided-card');
const box = await card.boundingBox();
if (box && box.y + box.height > 900) w.fail(`the card is ${Math.round(box.height)} px tall and runs off a 900 px window`);
await card.locator('[data-ws="guided-optimise-shape"]').click();
await page.waitForTimeout(1200);
if (!(await page.locator('[data-ws="optimise-panel"]').count())) w.fail('Optimise the shape… did not open the Optimise panel');
await w.shot('optimise-panel');
await card.locator('[data-ws="guided-study-new"]').click();
await page.waitForTimeout(1000);
await w.shot('new-study');
w.note(`dialogs: ${(await page.locator('[role="dialog"]').allInnerTexts()).map((t) => t.split('\n').slice(0, 6).join(' | ')).join(' // ')}`);
if (!(await page.locator('[role="dialog"]').count())) w.fail('New study… opened no form');
await page.keyboard.press('Escape');
for (const width of [1280, 1100]) {
  await page.setViewportSize({ width, height: 800 });
  await page.waitForTimeout(600);
  const over = await page.evaluate(() => { const s = document.querySelector('[data-ws="guided-strip"]'); const r = s.getBoundingClientRect(); return { left: r.left, right: r.right, wide: document.scrollingElement.scrollWidth - innerWidth, inner: s.scrollWidth - s.clientWidth }; });
  w.note(`at ${width}px: strip ${Math.round(over.left)}..${Math.round(over.right)}, page overflow ${over.wide}, strip overflow ${over.inner}`);
  if (over.wide > 0 || over.inner > 0 || over.right > width) w.fail(`the strip does not fit at ${width}px`);
  await w.shot(`width-${width}`);
}
await w.checkAlerts('optimise step');
const problems = await w.finish();
process.exit(problems.length ? 1 : 0);
