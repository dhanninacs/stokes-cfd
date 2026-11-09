// What does the Solve stage actually say? Read the UI rather than guess.
import { chromium } from 'playwright';
const URL = process.argv[2];
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.goto(URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(8000);
const press = async (re) => {
  const b = page.getByRole('button', { name: re }).first();
  if (await b.count()) { await b.click({ timeout: 15000 }); await page.waitForTimeout(1500); return true; }
  return false;
};
await press(/^Solve$/);
await page.waitForTimeout(2500);
await page.screenshot({ path: '/home/chandra/r101/shots/solve-panel.png' });
const text = await page.innerText('body');
const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
const i = lines.findIndex((l) => /SOLVE|Engine|engine/i.test(l));
console.log('--- around the engine mention ---');
console.log(lines.slice(Math.max(0, i - 4), i + 26).join('\n'));
console.log('--- header/top bar ---');
console.log(lines.slice(0, 8).join(' | '));
await browser.close();
