// Shared helpers of the GUI walks (`walk_*.mjs`): one browser page on a running server, a NEW
// project per scenario, real presses, a screenshot per action and a list of every problem seen
// (page errors, console errors, failed requests, alerts, timeouts). Run from `web/`.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Records the page as an .mp4 with an installed ffmpeg (a separate program, an argument list): the
 * browser's own screencast frames, each held until the next, written at a fixed rate so a second
 * of video is a second of the walk. Playwright's recordVideo needs a browser download this does not.
 */
async function startRecorder(page, dir) {
  mkdirSync(dir, { recursive: true });
  const file = join(dir, `walk-${Date.now()}.mp4`);
  const fps = Number(process.env.STOKES_WALK_FPS ?? 12) || 12;
  const ffmpeg = spawn(process.env.STOKES_WALK_FFMPEG ?? 'ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', file], { stdio: ['pipe', 'ignore', 'inherit'] });
  const cdp = await page.context().newCDPSession(page);
  let latest = null;
  let written = 0;
  const t0 = Date.now();
  cdp.on('Page.screencastFrame', (frame) => {
    latest = Buffer.from(frame.data, 'base64');
    cdp.send('Page.screencastFrameAck', { sessionId: frame.sessionId }).catch(() => {});
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1 });
  const timer = setInterval(() => {
    if (latest === null) return;
    const due = Math.floor(((Date.now() - t0) / 1000) * fps);
    while (written < due) { ffmpeg.stdin.write(latest); written += 1; }
  }, 1000 / fps);
  const stop = async () => {
    clearInterval(timer);
    await cdp.send('Page.stopScreencast').catch(() => {});
    ffmpeg.stdin.end();
    await new Promise((resolve) => ffmpeg.on('close', resolve));
  };
  return { file, t0, stop };
}

export async function openWalk(url, folder, shots) {
  mkdirSync(shots, { recursive: true });
  mkdirSync(folder, { recursive: true });
  // STOKES_WALK_CHANNEL=chrome (or msedge) drives an installed browser where Playwright's own
  // download is absent; STOKES_WALK_VIDEO=<dir> records the page as an .mp4 there (startRecorder).
  const channel = process.env.STOKES_WALK_CHANNEL;
  const video = process.env.STOKES_WALK_VIDEO;
  const browser = await chromium.launch(channel ? { channel } : {});
  // STOKES_WALK_SCHEME=dark asks the app for its dark appearance (a new profile follows the system's).
  const scheme = process.env.STOKES_WALK_SCHEME;
  // STOKES_WALK_VIEWPORT=1600x900 sets the page size (a 16:9 recording for social posts).
  const [vw, vh] = (process.env.STOKES_WALK_VIEWPORT ?? '1440x900').split('x').map(Number);
  const page = await browser.newPage({ viewport: { width: vw || 1440, height: vh || 900 }, ...(scheme ? { colorScheme: scheme } : {}) });
  const recorder = video ? await startRecorder(page, video) : null;
  const problems = [];
  let scenario = 'start';
  let step = 0;
  const fail = (text) => { problems.push(`[${scenario}] ${text}`); console.log(`  PROBLEM: ${text}`); };
  page.on('pageerror', (e) => fail(`page error: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') fail(`console: ${m.text().slice(0, 300)}`); });
  page.on('response', (r) => { if (r.status() >= 400) fail(`${r.status()} ${r.url().replace(/token=[^&]+/, 'token=…')}`); });

  const card = page.locator('[data-ws="operation-card"]');
  const visible = (loc) => loc.locator('visible=true').first();

  async function shot(name) {
    step += 1;
    const file = join(shots, `${scenario}-${String(step).padStart(2, '0')}-${name}.png`);
    await page.screenshot({ path: file });
    return file;
  }

  /** A command over a socket of the page's own (the same API the app uses). */
  function rpc(method, params = {}) {
    return page.evaluate(async ([method, params]) => {
      const q = new URLSearchParams(location.search);
      const api = (q.get('api') ?? location.origin).replace(/\/$/, '');
      if (!window.__walkSocket || window.__walkSocket.readyState > 1) {
        const ws = new WebSocket(`${api.replace(/^http/, 'ws')}/ws?token=${encodeURIComponent(q.get('token') ?? '')}`);
        window.__walkSocket = ws;
        window.__walkPending = new Map();
        window.__walkId = 1;
        ws.onmessage = (ev) => {
          let msg;
          try { msg = JSON.parse(String(ev.data)); } catch { return; }
          const p = msg.id !== undefined ? window.__walkPending.get(msg.id) : undefined;
          if (!p) return;
          window.__walkPending.delete(msg.id);
          if (msg.error) p.reject(new Error(`${msg.error.code}: ${msg.error.message}`)); else p.resolve(msg.result);
        };
        await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cannot reach the server')); });
      }
      const id = window.__walkId++;
      return new Promise((resolve, reject) => {
        window.__walkPending.set(id, { resolve, reject });
        window.__walkSocket.send(JSON.stringify({ id, method, params }));
      });
    }, [method, params]);
  }

  /** Close whatever is open and make a new project from the start screen; the ribbon is opened. */
  async function newProject(name, { exact = false } = {}) {
    scenario = name;
    step = 0;
    console.log(`\n== ${name}`);
    await page.goto(url);
    await page.waitForTimeout(800);
    const info = await rpc('project.info').catch(() => null);
    if (info && (info.path || info.name)) {
      await rpc('project.close', { discard: true }).catch(async () => rpc('project.close').catch((e) => fail(`project.close: ${e.message}`)));
      await page.goto(url);
    }
    await visible(page.getByText('New project', { exact: true })).click();
    await page.waitForTimeout(800);
    await page.evaluate(([n, dir]) => {
      const inputs = Array.from(document.querySelectorAll('input')).filter((x) => x.offsetParent);
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      for (const [i, v] of [[0, n], [1, dir]]) { set.call(inputs[i], v); inputs[i].dispatchEvent(new Event('input', { bubbles: true })); }
    }, [exact ? name : `${name}-${Date.now() % 100000}`, folder]);
    await visible(page.getByText('Create', { exact: true })).click();
    await page.locator('[data-ws="guided-create"], [role="tree"]').first().waitFor({ timeout: 30000 });
    await page.getByRole('radio', { name: 'Full' }).click();
    await page.waitForTimeout(600);
    await page.keyboard.press('Escape');
    await openRibbon();
  }

  /** Open a project that already exists (a solved case for the results walks) and show it. */
  async function openProject(path, name) {
    scenario = name;
    step = 0;
    console.log(`
== ${name}`);
    await page.goto(url);
    await page.waitForTimeout(800);
    const info = await rpc('project.info').catch(() => null);
    if (info && (info.path || info.name)) await rpc('project.close', { discard: true }).catch(() => rpc('project.close').catch(() => {}));
    await rpc('project.open', { path });
    await page.goto(url);
    await page.locator('[role="tree"], [data-ws="guided-card"]').first().waitFor({ timeout: 30000 });
  }

  /** Name the scenario the next screenshots and problems belong to. */
  function section(name) {
    scenario = name;
    step = 0;
    console.log(`
== ${name}`);
  }

  async function openRibbon() {
    if (!(await page.locator('[data-ws="build-ribbon"]').count())) await page.getByRole('button', { name: 'Create', exact: true }).first().click();
    await page.locator('[data-ws="build-ribbon"]').waitFor();
  }

  /** Press a ribbon tool; a folded group is opened first. */
  async function tool(group, label, waitCard = true) {
    await openRibbon();
    const direct = page.locator(`[data-ws="build-${group}"] button[aria-label^="${label}"]`).first();
    if (await direct.count()) {
      await direct.click();
      // A folded group's own button starts with the same word ("Sketch tools" for "Sketch"): it
      // opened the group's menu, and the tool is the item in it.
      const item = page.getByRole('menuitem', { name: new RegExp(`^${label}`) }).first();
      if (await item.count()) await item.click();
    } else {
      await page.locator(`[data-ws="build-${group}"] button`).first().click();
      await page.getByRole('menuitem', { name: label }).first().click();
    }
    if (waitCard) await card.waitFor({ timeout: 10000 });
  }

  /** Every control of the open card: its label, kind, value and options. */
  async function cardControls() {
    return card.evaluate((root) => Array.from(root.querySelectorAll('input, select, textarea, [role="radio"], [role="switch"], [role="checkbox"]')).filter((el) => el.offsetParent).map((el) => {
      const label = el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') ? document.getElementById(el.getAttribute('aria-labelledby'))?.innerText : '') || (el.labels && el.labels[0] ? el.labels[0].innerText : '') || el.getAttribute('name') || '';
      return {
        label: label.trim(),
        kind: el.tagName === 'SELECT' ? 'select' : (el.getAttribute('role') || el.getAttribute('type') || 'text'),
        value: el.value ?? el.getAttribute('aria-checked') ?? '',
        options: el.tagName === 'SELECT' ? Array.from(el.options).map((o) => o.value) : undefined,
        disabled: el.disabled || undefined,
      };
    }));
  }

  async function setField(label, text) {
    const field = card.getByLabel(label, { exact: true });
    await field.fill(String(text));
    await field.press('Tab');
  }

  async function choose(label, value) {
    await card.getByLabel(label, { exact: true }).selectOption(value);
  }

  /** Press Run and wait for the card to go; false (with the card's text as a problem) when it stays. */
  async function run(name, ms = 60000) {
    await card.getByRole('button', { name: 'Run', exact: true }).click();
    try {
      await card.waitFor({ state: 'detached', timeout: ms });
    } catch {
      await shot(`${name}-stuck`);
      fail(`${name}: the card stayed: ${(await card.innerText()).replace(/\n+/g, ' | ').slice(0, 400)}`);
      await card.getByRole('button', { name: /Cancel|Close/ }).first().click().catch(() => {});
      await page.keyboard.press('Escape');
      return false;
    }
    await settle();
    await shot(name);
    return true;
  }

  /** The picked part is building: a build status is up, or its Details chip is not ok. */
  async function busy() {
    const head = ((await page.locator('[data-ws="details-pane"]').innerText().catch(() => '')) || '').slice(0, 160);
    return (await page.locator('[data-ws="build-status"]').count()) > 0 || /(stale|running|new|queued)/.test(head);
  }
  /**
   * Wait for a rebuild to finish. An edit is committed a moment after the key, so `expectChange`
   * first waits (up to 3 s) for the rebuild to START; without it only a running build is awaited.
   */
  async function settle(ms = 120000, expectChange = false) {
    await page.waitForTimeout(400);
    if (expectChange) for (let i = 0; i < 12 && !(await busy()); i += 1) await page.waitForTimeout(250);
    const t0 = Date.now();
    // Idle means idle for 2.5 s: the chip can read ok between the coarse pass and the fine one.
    let quiet = 0;
    while (Date.now() - t0 < ms && quiet < 6) { quiet = (await busy()) ? 0 : quiet + 1; await page.waitForTimeout(400); }
    if (quiet < 6) fail('the rebuild never finished (the part is still stale or building)');
    await page.waitForTimeout(500);
  }

  const treeNames = async () => (await page.locator('[role="tree"] [role="treeitem"]').allInnerTexts()).map((t) => t.trim().split('\n')[0]);
  async function pick(name, add = false) {
    const row = page.locator('[role="tree"] [role="treeitem"]').filter({ has: page.getByText(name, { exact: true }) }).first();
    await row.click(add ? { modifiers: ['Control'] } : {});
  }

  /** Click the sketch canvas at sketch coordinates (mm): the view opens at 6 px/mm, centred on 0. */
  async function at(u, v) {
    const box = await page.locator('[data-ws="sketch-canvas"]').boundingBox();
    await page.mouse.click(box.x + box.width / 2 + u * 6, box.y + box.height / 2 - v * 6);
  }

  /** What is on screen that reads as trouble: alerts and error toasts. */
  async function alerts() {
    const texts = await page.locator('[role="alert"]:visible, .toast-error:visible, [data-kind="error"]:visible').allInnerTexts().catch(() => []);
    return texts.map((t) => t.replace(/\n+/g, ' | ').trim()).filter(Boolean);
  }
  async function checkAlerts(where) {
    for (const a of await alerts()) fail(`${where}: alert: ${a.slice(0, 300)}`);
  }

  /** The parts the server holds, with their bounds, through geometry.info-like queries. */
  async function operations() {
    const tree = await rpc('model.tree').catch(() => null);
    return tree;
  }

  async function finish() {
    console.log(`\n${problems.length} problem(s)`);
    for (const p of problems) console.log(`  ${p}`);
    if (recorder) await recorder.stop();
    await browser.close();
    return problems;
  }

  return { recorder, page, card, visible, shot, rpc, newProject, openProject, section, openRibbon, tool, cardControls, setField, choose, run, settle, treeNames, pick, at, alerts, checkAlerts, operations, fail, finish, problems, note: (t) => console.log(`  ${t}`) };
}
