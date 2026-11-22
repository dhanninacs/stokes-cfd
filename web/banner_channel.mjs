// A 2048 x 1152 banner for the whole channel (not one video): the name and the tagline in the middle,
// inside YouTube's safe area (the middle 1546 x 423, which every device shows), and a honeycomb of
// hexagon-cut pictures of the app's own output down both sides: mesh cuts with boundary layers, speed,
// pressure and streamlines, from the pictures `capture_ball_assets.mjs` made.
//
//   node banner_channel.mjs <assets folder> <out.png> [--title=Stokes] [--tagline="CAD in. Results out."]
//        [--chips="Implicit CAD,GPU preview,OpenFOAM"] [--guide] [--ffmpeg=ffmpeg]
//
// `--guide` also writes <out>-guide.png with the safe area outlined. ffmpeg (a separate program, an
// argument list) cuts the pictures to the hexagon's proportions.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [assets, out] = args.filter((a) => !a.startsWith('--'));
if (!assets || !out) { console.error('usage: node banner_channel.mjs <assets folder> <out.png> [--title=] [--tagline=] [--chips=] [--guide] [--ffmpeg=ffmpeg]'); process.exit(2); }
const ffmpeg = flag('ffmpeg', 'ffmpeg');
const title = flag('title', 'STOKES');
const tagline = flag('tagline', 'CAD in. Results out.');
const chips = flag('chips', 'Implicit CAD,GPU preview,OpenFOAM').split(',').map((c) => c.trim()).filter(Boolean);

// Crops with a hexagon's proportions (height = 0.866 x width): [source, crop, name].
const CROPS = {
  m1: ['mesh-zoom.png', 'crop=700:606:560:430'],
  m2: ['mesh-zoom.png', 'crop=700:606:0:315'],
  m3: ['mesh-zoom.png', 'crop=700:606:1100:542'],
  w1: ['mesh-wide.png', 'crop=500:433:132:470'],
  f1: ['flow.png', 'crop=520:450:900:480'],
  f2: ['flow.png', 'crop=520:450:130:520'],
  p1: ['pressure.png', 'crop=700:606:560:430'],
  s1: ['stream.png', 'crop=760:658:420:400'],
  s2: ['stream.png', 'crop=600:520:1100:480'],
};
for (const [name, [src, filter]] of Object.entries(CROPS)) execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-i', join(assets, src), '-vf', filter, join(assets, `ch-${name}.png`)]);
const uri = (name) => `data:image/png;base64,${readFileSync(join(assets, `ch-${name}.png`)).toString('base64')}`;
const W = 320;
const H = W * 0.866;

/** A flat-top hexagon of width W centred at (cx, cy): a rim, then the picture. */
const hex = (cx, cy, pic, rim) => {
  const clip = 'polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%)';
  return `<div style="position:absolute;left:${cx - W / 2}px;top:${cy - H / 2}px;width:${W}px;height:${H}px;filter:drop-shadow(0 12px 26px rgba(0,0,0,.55))">
    <div style="position:absolute;inset:0;clip-path:${clip};background:linear-gradient(135deg,${rim},#2f7bff)"></div>
    <div style="position:absolute;inset:5px;clip-path:${clip};background:url(${uri(pic)}) center/cover"></div></div>`;
};

// Two columns of hexagons down each side, touching (flat-top honeycomb: the next column sits 0.75 W over
// and half a hexagon down); the columns nearer the middle are offset from the outer ones.
const teal = '#4fe3c1';
const blue = '#4aa3ff';
const amber = '#ff9f43';
const LEFT = [
  [100, 130, 's1', teal], [100, 130 + H, 'm1', blue], [100, 130 + 2 * H, 'f1', amber], [100, 130 + 3 * H, 'p1', teal],
  [100 + 0.75 * W, 130 + H / 2, 'f2', blue], [100 + 0.75 * W, 130 + 1.5 * H, 'm3', teal], [100 + 0.75 * W, 130 + 2.5 * H, 's2', amber],
];
const RIGHT = [
  [1948, 130 + H / 2, 's1', amber], [1948, 130 + 1.5 * H, 'w1', teal], [1948, 130 + 2.5 * H, 'p1', blue],
  [1948 - 0.75 * W, 130, 'm2', teal], [1948 - 0.75 * W, 130 + H, 'f1', blue], [1948 - 0.75 * W, 130 + 2 * H, 's2', amber], [1948 - 0.75 * W, 130 + 3 * H, 'm3', teal],
];

// The honeycomb behind: hexagon outlines, r = 44.
const r = 44;
const tile = `<svg xmlns='http://www.w3.org/2000/svg' width='${3 * r}' height='${Math.sqrt(3) * r}'><g fill='none' stroke='rgba(120,170,220,0.10)' stroke-width='1.5'>${
  [[0, 0], [3 * r, 0], [1.5 * r, Math.sqrt(3) * r / 2], [0, Math.sqrt(3) * r], [3 * r, Math.sqrt(3) * r]].map(([x, y]) => `<polygon points='${[0, 1, 2, 3, 4, 5].map((i) => `${(x + r * Math.cos(Math.PI / 3 * i)).toFixed(1)},${(y + r * Math.sin(Math.PI / 3 * i)).toFixed(1)}`).join(' ')}'/>`).join('')
}</g></svg>`;
const honey = `url("data:image/svg+xml;utf8,${encodeURIComponent(tile)}")`;

const html = `<!doctype html><meta charset="utf-8"><style>
  html,body{margin:0;width:2048px;height:1152px;overflow:hidden;background:#0a0f16;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#eef2f6}
  .bg{position:absolute;inset:0;background:radial-gradient(1300px 800px at 50% 0%,#12395a 0%,rgba(10,15,22,0) 65%),radial-gradient(1100px 700px at 50% 110%,#0f4a3d 0%,rgba(10,15,22,0) 65%),#0a0f16}
  .honey{position:absolute;inset:0;background:${honey}}
  .veil{position:absolute;inset:0;background:radial-gradient(ellipse 820px 420px at 50% 50%,rgba(10,15,22,.96) 0%,rgba(10,15,22,.9) 55%,rgba(10,15,22,0) 100%)}
  .mid{position:absolute;left:0;right:0;top:404px;text-align:center}
  h1{margin:0;font-size:190px;line-height:1;font-weight:800;letter-spacing:22px;padding-left:22px;background:linear-gradient(90deg,#4fe3c1,#4aa3ff);-webkit-background-clip:text;background-clip:text;color:transparent}
  .tag{margin-top:8px;font-size:60px;font-weight:600;color:#eef2f6}
  .chips{margin-top:22px;display:flex;gap:14px;justify-content:center;font-size:28px;font-weight:600}
  .chips span{padding:8px 22px;border-radius:999px;background:rgba(79,227,193,.14);border:1px solid rgba(79,227,193,.55);color:#d8fff5}
  .guide{position:absolute;left:251px;top:364px;width:1546px;height:423px;border:3px dashed #ff4d6d;box-sizing:border-box}
</style><div class="bg"></div><div class="honey"></div>
${[...LEFT, ...RIGHT].map(([x, y, pic, rim]) => hex(x, y, pic, rim)).join('\n')}
<div class="veil"></div>
<div class="mid"><h1>${title}</h1><div class="tag">${tagline}</div><div class="chips">${chips.map((c) => `<span>${c}</span>`).join('')}</div></div>
<!--GUIDE-->`;

const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage({ viewport: { width: 2048, height: 1152 } });
await page.setContent(html);
await page.waitForTimeout(1500);
await page.screenshot({ path: out });
console.log(`wrote ${out}`);
if (args.includes('--guide')) {
  await page.setContent(html.replace('<!--GUIDE-->', '<div class="guide"></div>'));
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out.replace(/\.png$/i, '-guide.png') });
  console.log('wrote the guide copy');
}
await browser.close();
