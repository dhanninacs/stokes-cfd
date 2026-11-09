import { chromium } from 'playwright';
const URL = process.argv[2];
const OUT = '/home/chandra/r102/view';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto(URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(9000);
const press = async (re, opt = false) => {
  const b = page.getByRole('button', { name: re }).first();
  if (await b.count()) { await b.click({ timeout: 20000 }); await page.waitForTimeout(1500); return true; }
  if (!opt) throw new Error('no button ' + re);
  return false;
};
await press(/^Mesh$/);
await page.screenshot({ path: `${OUT}/a-mesh-stage.png` });
await press(/^Generate mesh/);
await page.waitForFunction(() => /Mesh · \d+/.test(document.body.innerText), { timeout: 300000 }).catch(() => {});
await page.waitForTimeout(5000);
await page.screenshot({ path: `${OUT}/b-after-mesh.png` });
// Expand the tree under mesh/results so the patch nodes are on screen.
for (const name of [/^mesh$/, /^results$/, /^blockmesh$/]) {
  const row = page.getByText(name).first();
  if (await row.count()) { await row.click({ timeout: 8000 }).catch(() => {}); await page.waitForTimeout(900); }
}
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/c-tree-open.png` });
console.log('status:', ((await page.innerText('body')).match(/Mesh · [^\n]*/) || ['?'])[0]);
console.log('body mentions of patch names:', ['unassigned', 'inlet', 'outlet', 'walls'].filter((n) => new RegExp(`\\b${n}\\b`).test('')));
const text = await page.innerText('body');
for (const n of ['unassigned', 'inlet', 'outlet', 'walls']) console.log(`  tree shows ${n}:`, text.includes(n));
await browser.close();
