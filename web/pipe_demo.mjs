// The pipe tutorial AS THE APP OPENS IT: mesh -> run -> plot, through the browser.
// No upload and no conditions step: the demo project already has the geometry, the region and the
// typed boundaries, which is the point of the check.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const URL = process.argv[2];
const OUT = process.argv[3] || '/home/chandra/r101/shots';
mkdirSync(OUT, { recursive: true });

const body = (page) => page.innerText('body');
const consoleErrors = [];
const failedRequests = [];
let step = 1;

async function shot(page, name) {
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: false });
  console.log(`  shot: ${name}.png`);
}
async function press(page, pattern, { optional = false } = {}) {
  const button = page.getByRole('button', { name: pattern }).first();
  const count = await button.count();
  if (!count) {
    if (optional) return false;
    throw new Error(`no button matching ${pattern}`);
  }
  await button.click({ timeout: 20000 });
  await page.waitForTimeout(1200);
  return true;
}

async function main() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('requestfailed', (r) => failedRequests.push(`${r.method()} ${r.url()}`));

  console.log('1. open the app (the demo pipe loads by default)');
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000);
  const text = await body(page);
  console.log('   the tree names the demo:', /demo_pipe|pipe/i.test(text));
  await shot(page, `${step++}-opened`);

  console.log('2. Mesh  (no configuration: the tutorial must be meshable as it loads)');
  await press(page, /^Mesh$/);
  // **The action is the TOP BAR's `Generate mesh ->`**, not a button inside the panel. The earlier
  // driver looked for `/^Generate( again)?$/` and found nothing, because the Mesh stage replaces the
  // top bar with the next action rather than offering one in the panel.
  await press(page, /^Generate mesh/);
  await page.waitForFunction(() => /Mesh · \d+/.test(document.body.innerText), { timeout: 300000 })
    .catch(() => console.log('   (no mesh status within 300 s)'));
  await page.waitForTimeout(3000);
  console.log('   status:', ((await body(page)).match(/Mesh · [^\n]*/) || ['?'])[0]);
  await shot(page, `${step++}-meshed`);

  console.log('3. Run');
  const runs = page.locator('button').filter({ hasText: /^Run$/ });
  console.log('   Run buttons on screen:', await runs.count());
  await press(page, /^Run$/, { optional: true });
  await page.waitForFunction(() => /Run · (it \d+|converged|done)/i.test(document.body.innerText), { timeout: 300000 })
    .catch(() => console.log('   (no run status within 300 s)'));
  await page.waitForTimeout(4000);
  console.log('   status:', ((await body(page)).match(/Run · [^\n]*/) || ['?'])[0]);
  await shot(page, `${step++}-ran`);

  console.log('4. Results: is there anything to plot?');
  await press(page, /^Results$/);
  await press(page, /^Results$/, { optional: true });
  await page.waitForTimeout(4000);
  const sel = page.locator('[data-ws="results-select"]');
  console.log('   field select found:', await sel.count());
  const options = await sel.locator('option').allTextContents().catch(() => []);
  console.log('   fields offered:', options.slice(0, 6));
  for (const field of ['p', 'U_mag']) {
    const opt = sel.locator('option').filter({ hasText: new RegExp(`^${field}\\b|\\b${field}\\b`) }).first();
    if (await opt.count()) {
      await sel.selectOption(await opt.getAttribute('value'));
      await page.waitForTimeout(3500);
      console.log(`   ${field}:`, ((await body(page)).match(/Δp\/ρ [^\n]*|Legend[^\n]*/) || ['(no legend text)'])[0]);
      await shot(page, `${step++}-field-${field}`);
    }
  }
  await shot(page, `${step++}-end`);

  console.log('');
  console.log('console errors:', consoleErrors.length, consoleErrors.slice(0, 3));
  console.log('failed requests:', failedRequests.length, failedRequests.slice(0, 3));
  await browser.close();
}

main().catch((e) => { console.error('DRIVER FAILED:', e.message); process.exit(1); });
