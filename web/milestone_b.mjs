// MILESTONE (b): the owner's whole workflow in the browser, with real presses.
//
//   cd web && node milestone_b.mjs "<url-with-token>" <path-to-stl>
//
// upload a CAD file through the REAL file input -> define boundaries -> Mesh -> Run -> Results,
// with `p/rho`, `|U|` and the mid-plane slice. Every step presses the control a user would press and
// waits on the app's own text, never on a timer alone.
//
// **Every workaround is recorded.** Claude asked for "every place the browser flow needed a
// workaround you would not ask a user to make", so a step that only works because the script does
// something unusual prints WORKAROUND with the reason. That list is the useful output: it is the
// difference between "the flow works" and "the flow works if you know where to click".
import { chromium } from 'playwright';
import { mkdirSync, copyFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const url = process.argv[2];
const stl = process.argv[3];
if (!url || !stl) { console.error('usage: node milestone_b.mjs <url> <stl>'); process.exit(2); }

const SHOTS = join(homedir(), 'stokes-shots', 'milestone-b');
const MIRROR = join(process.cwd(), '..', '.coordination', 'shots');
mkdirSync(SHOTS, { recursive: true });
mkdirSync(MIRROR, { recursive: true });

const errors = [];
const failed = [];
const workarounds = [];
let step = 20;

async function shot(page, name) {
  const f = join(SHOTS, `${name}.png`);
  await page.screenshot({ path: f });
  copyFileSync(f, join(MIRROR, `${name}.png`));
  console.log(`  shot: ${name}.png`);
}
const work = (what, why) => { workarounds.push(`${what}: ${why}`); console.log(`  WORKAROUND ${what}: ${why}`); };

// Press a button by visible text, and say when nothing matched rather than failing silently.
async function press(page, pattern, { optional = false } = {}) {
  const button = page.getByRole('button', { name: pattern }).first();
  if ((await button.count()) === 0) {
    if (!optional) console.log(`  (no button matching ${pattern})`);
    return false;
  }
  await button.click({ timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
  return true;
}

const body = (page) => page.innerText('body');

async function main() {
  const browser = await chromium.launch({
    args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 300)}`));
  page.on('response', (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url().replace(/token=[^&]*/, 'token=…')}`);
  });

  console.log('1. open the app');
  await page.goto(url, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(6000);
  console.log('   title:', await page.title());
  await shot(page, `${step++}-start`);

  console.log('2. upload the STL through the real file input');
  const input = page.locator('input[type=file]').first();
  if ((await input.count()) === 0) { console.log('   NO FILE INPUT'); }
  else {
    // **The true user path: the project menu, then Import geometry…, then the filechooser.**
    //
    // There is no "File" menu - the menu is the PROJECT NAME button (`duct`), which is what my first
    // probe got wrong: it looked for a `File` button, found none, and set the hidden input directly,
    // reporting that as a workaround. **The workaround was mine, not the product's.** Claude's recipe
    // is the correct one, and it works:
    //
    //     const [fc] = await Promise.all([page.waitForEvent('filechooser'), click(Import geometry…)])
    //
    // A hidden input is never "visible" to Playwright, so `setInputFiles` on it would ALSO have
    // looked like it worked - which is why the old driver could not tell the two apart.
    const chooser = page.waitForEvent('filechooser', { timeout: 20000 }).catch(() => null);
    await page.locator('button.project-btn').first().click({ timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await page.getByText(/Import geometry/i).first().click({ timeout: 10000 }).catch(() => {});
    const fc = await chooser;
    if (fc) {
      await fc.setFiles(stl);
      console.log(`   the filechooser fired and took ${stl} (the real user path)`);
    } else {
      work('set a hidden file input', 'the filechooser event did NOT fire from the project menu, so the script set the hidden input directly - that IS a product bug, reported to Claude');
      await input.setInputFiles(stl);
      console.log(`   set ${stl} (fallback)`);
    }
    await page.waitForTimeout(3000);
    // **STL declares no unit, so the app asks for one - a real modal, and a user must answer it.**
    // Missing this left the dialog open, which blocks EVERY later click: the Results tab never
    // opened and the field select stayed disabled. That is not a workaround, it is the flow.
    const units = page.getByRole('button', { name: /^Import$/ }).last();
    if (await units.count()) {
      console.log('   the units dialog is up (STL declares no unit): pressing Import');
      await units.click({ timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(3000);
    }
    await page.waitForFunction(() => /imported|part|geometry/i.test(document.body.innerText), { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(6000);
    await shot(page, `${step++}-uploaded`);
  }

  console.log('3. set up the flow (the Conditions card)');
  const before = await body(page);
  if (/Create fluid region and default boundaries/i.test(before)) {
    await press(page, /Create fluid region and default boundaries/i);
    await page.waitForTimeout(4000);
    await shot(page, `${step++}-setup`);
  } else {
    console.log('   (no Set up flow card offered; the project already has a region)');
  }

  console.log('4. Mesh');
  await press(page, /^Mesh$/);
  await press(page, /^Generate( again)?$/);
  await page.waitForFunction(() => /Mesh · \d+/.test(document.body.innerText), { timeout: 300000 })
    .catch(() => console.log('   (no mesh status within 300 s)'));
  await page.waitForTimeout(3000);
  await shot(page, `${step++}-meshed`);
  console.log('   status:', ((await body(page)).match(/Mesh · [^\n]*/) || ['?'])[0]);

  console.log('5. Run');
  await press(page, /^Solve$/);
  // **The Solve panel's Run, not the top bar's.** Both are labelled `Run`; the panel's is the one
  // the flow uses. The first version matched `/^Run$/` and found neither, because `getByRole` with an
  // exact name also matches the top-bar button whose accessible name differs. Taking the LAST match
  // and printing how many there are is what makes this checkable.
  const runs = page.locator('button').filter({ hasText: /^Run$/ });
  console.log(`   Run buttons on screen: ${await runs.count()}`);
  if (await runs.count()) await runs.last().click({ timeout: 15000 }).catch(() => {});
  await page.waitForFunction(() => /Run · it \d+ · converged/.test(document.body.innerText), { timeout: 300000 })
    .catch(() => console.log('   (no converged status within 300 s)'));
  await page.waitForTimeout(3000);
  await shot(page, `${step++}-converged`);
  console.log('   status:', ((await body(page)).match(/Run · [^\n]*/) || ['?'])[0]);

  console.log('6. Results: p/rho, |U|, slice');
  await press(page, /^Results$/);
  await press(page, /^Results$/, { optional: true });   // the right-slot tab, when it is separate
  await page.waitForTimeout(2500);
  const field = page.locator('[data-ws="results-select"]');
  console.log('   field select found:', await field.count());
  if (await field.count()) {
    for (const value of ['p', 'U_mag']) {
      await field.selectOption(value);
      await page.waitForTimeout(3500);
      const legend = (await page.locator('.field-legend').first().innerText()).replace(/\s+/g, ' ').trim();
      console.log(`   ${value} -> ${legend}`);
      await shot(page, `${step++}-field-${value}`);
    }
    const slice = page.locator('[data-ws*="slic"] [role=switch], [data-ws*="slic"] input[type=checkbox]').first();
    if (await slice.count()) {
      await slice.click({ force: true }).catch(() => {});
      await page.waitForTimeout(5000);
      await shot(page, `${step++}-slice-on`);
      console.log('   slice toggled');
    }
  }
  await shot(page, `${step++}-end`);

  await browser.close();
  console.log(`\nconsole errors: ${errors.length}`);
  for (const e of errors.slice(0, 10)) console.log('  !', e);
  console.log(`failed requests: ${failed.length}`);
  for (const f of failed.slice(0, 10)) console.log('  !', f);
  console.log(`workarounds: ${workarounds.length}`);
  for (const w of workarounds) console.log('  -', w);
}

main().catch((e) => { console.error('DRIVER FAILED:', e.message); process.exit(2); });
