// A 2048 x 1152 channel banner (the size a YouTube channel asks for) from the pictures
// `capture_ball_assets.mjs` made: hexagon-cut pictures of the mesh cut (boundary layers round the ball)
// and the speed in the middle plane, on a honeycomb, with the title kept inside YouTube's safe area
// (the middle 1546 x 423, which every device shows; the rest is cropped on a phone or a TV).
//
//   node banner_ball.mjs <assets folder> <out.png> [--guide] [--ffmpeg=ffmpeg]
//
// `--guide` also writes <out>-guide.png with the safe area outlined, for checking. ffmpeg (a separate
// program, an argument list) cuts the pictures to the hexagon's proportions.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [assets, out] = args.filter((a) => !a.startsWith('--'));
if (!assets || !out) { console.error('usage: node banner_ball.mjs <assets folder> <out.png> [--guide] [--ffmpeg=ffmpeg]'); process.exit(2); }
const ffmpeg = flag('ffmpeg', 'ffmpeg');

// Pictures cut to the proportions of a flat-top hexagon (height = 0.866 x width), around the ball.
const crops = {
  mesh: ['mesh-zoom.png', 'crop=760:658:528:406', 'hex-mesh.png'],
  flow: ['flow.png', 'crop=624:540:640:462', 'hex-flow.png'],
  meshWide: ['mesh-wide.png', 'crop=720:624:560:440', 'hex-mesh-wide.png'],
};
for (const [src, filter, dest] of Object.values(crops)) execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-i', join(assets, src), '-vf', filter, join(assets, dest)]);
const uri = (name) => `data:image/png;base64,${readFileSync(join(assets, name)).toString('base64')}`;

/** A flat-top hexagon of width `w` centred at (cx, cy): a glowing rim, then the picture. */
const hex = (cx, cy, w, picture, { opacity = 1, rim = '#4fe3c1', rimWidth = 5 } = {}) => {
  const h = w * 0.866;
  const clip = 'polygon(25% 0,75% 0,100% 50%,75% 100%,25% 100%,0 50%)';
  return `<div style="position:absolute;left:${cx - w / 2}px;top:${cy - h / 2}px;width:${w}px;height:${h}px;opacity:${opacity};filter:drop-shadow(0 14px 30px rgba(0,0,0,.55))">
    <div style="position:absolute;inset:0;clip-path:${clip};background:linear-gradient(135deg,${rim},#2f7bff)"></div>
    <div style="position:absolute;inset:${rimWidth}px;clip-path:${clip};background:url(${picture}) center/cover"></div></div>`;
};

// The honeycomb behind: hexagon outlines, r = 44.
const r = 44;
const tile = `<svg xmlns='http://www.w3.org/2000/svg' width='${3 * r}' height='${Math.sqrt(3) * r}'><g fill='none' stroke='rgba(120,170,220,0.10)' stroke-width='1.5'>${
  [[0, 0], [3 * r, 0], [1.5 * r, Math.sqrt(3) * r / 2], [0, Math.sqrt(3) * r], [3 * r, Math.sqrt(3) * r]].map(([x, y]) => `<polygon points='${[0, 1, 2, 3, 4, 5].map((i) => `${(x + r * Math.cos(Math.PI / 3 * i)).toFixed(1)},${(y + r * Math.sin(Math.PI / 3 * i)).toFixed(1)}`).join(' ')}'/>`).join('')
}</g></svg>`;
const honey = `url("data:image/svg+xml;utf8,${encodeURIComponent(tile)}")`;

// Safe area: x 251..1797, y 364..788. The title block sits left in it, two hexagons right in it; the
// rest of the hexagons are decoration that may be cropped.
const html = `<!doctype html><meta charset="utf-8"><style>
  html,body{margin:0;width:2048px;height:1152px;overflow:hidden;background:#0a0f16;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:#eef2f6}
  .bg{position:absolute;inset:0;background:radial-gradient(1300px 800px at 12% 15%,#12395a 0%,rgba(10,15,22,0) 65%),radial-gradient(1100px 700px at 95% 95%,#0f4a3d 0%,rgba(10,15,22,0) 65%),#0a0f16}
  .honey{position:absolute;inset:0;background:${honey};opacity:1}
  .text{position:absolute;left:251px;top:446px;width:760px}
  .eyebrow{font-size:28px;letter-spacing:8px;color:#4fe3c1;font-weight:700}
  h1{margin:4px 0 0;font-size:84px;line-height:1.05;font-weight:800;letter-spacing:.5px;white-space:nowrap}
  .tag{margin-top:12px;font-size:33px;color:#b6c6d6;line-height:1.25;white-space:nowrap}
  .chips{margin-top:24px;display:flex;gap:12px;font-size:26px;font-weight:600}
  .chips span{padding:8px 20px;border-radius:999px;background:rgba(79,227,193,.14);border:1px solid rgba(79,227,193,.55);color:#d8fff5}
  .guide{position:absolute;left:251px;top:364px;width:1546px;height:423px;border:3px dashed #ff4d6d;box-sizing:border-box}
</style><div class="bg"></div><div class="honey"></div>
${hex(1000, 150, 520, uri('hex-flow.png'), { opacity: 0.55, rim: '#2f7bff' })}
${hex(1960, 120, 380, uri('hex-mesh-wide.png'), { opacity: 0.5, rim: '#2f7bff' })}
${hex(1830, 1080, 560, uri('hex-mesh.png'), { opacity: 0.6, rim: '#4fe3c1' })}
${hex(140, 1010, 420, uri('hex-flow.png'), { opacity: 0.5, rim: '#4fe3c1' })}
${hex(1195, 576, 370, uri('hex-mesh.png'))}
${hex(1595, 576, 370, uri('hex-flow.png'), { rim: '#ff9f43' })}
<div class="text"><div class="eyebrow">STOKES</div><h1>Flow past a ball</h1>
<div class="tag">From a CAD sketch to a solved flow in one app</div>
<div class="chips"><span>Implicit CAD</span><span>Fast GPU preview</span><span>OpenFOAM</span></div></div>
<!--GUIDE-->`;

const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage({ viewport: { width: 2048, height: 1152 } });
await page.setContent(html);
await page.waitForTimeout(1200);
await page.screenshot({ path: out });
console.log(`wrote ${out}`);
if (args.includes('--guide')) {
  await page.setContent(html.replace('<!--GUIDE-->', '<div class="guide"></div>'));
  await page.waitForTimeout(1200);
  await page.screenshot({ path: out.replace(/\.png$/i, '-guide.png') });
  console.log('wrote the guide copy');
}
await browser.close();
writeFileSync(join(assets, 'banner.html'), html);
