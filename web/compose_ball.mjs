// Finishes the ball walkthrough: a title card, the recording (the waits run fast, with a badge; the
// seconds before the project exists trimmed), and the side-by-side ending made from the two crops the
// recording saved (Fast mode and OpenFOAM, same view, colour map and range), joined into one 1920x1080
// .mp4 in the broadcast colour range.
//
//   node compose_ball.mjs <recording.mp4> <shots folder> <out.mp4> [--wait=8] [--hold=10] [--trim=<seconds>] [--ffmpeg=ffmpeg]
//
// The recording is trimmed to its `start` mark (the project exists): the New project dialog shows the
// computer's default folder and the start screen the recent projects, and neither belongs in a post. The
// mark is read from marks.json, so a slower or faster run cannot move the cut onto the dialog; with no
// mark the script stops. The title and the ending are pages the script draws (not the app). ffmpeg runs
// as a separate program with an argument list (no shell).
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [video, shots, out] = args.filter((a) => !a.startsWith('--'));
if (!video || !shots || !out) { console.error('usage: node compose_ball.mjs <recording.mp4> <shots folder> <out.mp4> [--wait=8] [--hold=10] [--trim=<seconds>] [--ffmpeg=ffmpeg]'); process.exit(2); }
const ffmpeg = flag('ffmpeg', 'ffmpeg');
const keep = flag('wait', '8');
const hold = Number(flag('hold', '10'));

const numbers = JSON.parse(readFileSync(join(shots, 'numbers.json'), 'utf8'));
const marks = JSON.parse(readFileSync(join(shots, 'marks.json'), 'utf8'));
const startMark = marks.find((m) => m.kind === 'start');
const trim = flag('trim', startMark ? (startMark.t + 0.3).toFixed(2) : '');
if (!trim) { console.error('no `start` mark in marks.json and no --trim: refusing to cut (the first seconds can show the New project dialog)'); process.exit(1); }
const seconds = (label) => {
  const a = marks.find((m) => m.kind === 'wait-start' && m.label === label);
  const b = marks.find((m) => m.kind === 'wait-end' && m.label === label);
  return a && b ? Math.round(b.t - a.t) : null;
};
const fmtTime = (s) => (s === null ? '' : s >= 90 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s` : `${s} s`);
const png = (name) => `data:image/png;base64,${readFileSync(join(shots, name)).toString('base64')}`;
// The crops the recording saved carry a sliver of a card at each side at some viewport sizes: cut to the plane.
const crop = flag('crop', '630:250:42:20');
for (const name of ['fast-view', 'foam-view']) execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-i', join(shots, `${name}.png`), '-vf', `crop=${crop}`, join(shots, `${name}-crop.png`)]);
const need = (value, what) => { if (!Number.isFinite(value)) { console.error(`could not read ${what} from numbers.json: refusing to draw a card with it missing`); process.exit(1); } return value; };

// What each side measured, read from what the app showed.
const density = need(numbers.fastStatus?.density, 'the density Fast mode used');
const fastDp = need(numbers.fastStatus?.frame?.dp_pa, 'the Fast mode pressure drop') * 1000; // mPa
const foamKinematic = need(Number(/(\d+(?:\.\d+)?(?:e-?\d+)?)\s*m(?:²|2)\/s(?:²|2)/.exec(numbers.foamCard ?? '')?.[1]), 'the OpenFOAM pressure drop (dp/rho)');
const foamDp = foamKinematic * density * 1000; // mPa
const apart = Math.abs(fastDp - foamDp) / foamDp * 100;
const fastSeconds = need(seconds(numbers.fastLabel), 'the Fast mode settling time');
const foamMesh = need(seconds('the mesh'), 'the mesh time');
const foamRun = need(seconds('the run'), 'the run time');
const foamSeconds = foamMesh + foamRun;
const faster = Math.round(foamSeconds / fastSeconds);
const meshCells = /([\d,]+) cells/.exec(numbers.meshDone ?? '')?.[1] ?? '';
const fastCells = /([\d.]+ [kM]) cells/.exec(numbers.fastStats ?? '')?.[1] ?? '';
console.log(`fast ${fastDp.toFixed(2)} mPa in ${fastSeconds} s; foam ${foamDp.toFixed(2)} mPa in ${foamSeconds} s; ${apart.toFixed(1)} % apart; ${faster}x; trim ${trim} s`);

const base = `html,body{margin:0;width:1920px;height:1080px;background:#0e1218;color:#eef2f6;font-family:Inter,"Segoe UI",system-ui,sans-serif}`;
const titleHtml = `<!doctype html><meta charset="utf-8"><style>${base}
  .wrap{display:flex;flex-direction:column;align-items:center;justify-content:center;height:1080px;text-align:center}
  h1{margin:0;font-size:120px;font-weight:700;letter-spacing:.5px}
  .sub{margin-top:30px;font-size:48px;color:#9fb0c2}
  .steps{margin-top:70px;display:flex;gap:30px;font-size:42px}
  .steps span{padding:16px 34px;border-radius:16px;background:#151b24;border:1px solid #243041}
</style><div class="wrap"><h1>Flow past a ball</h1><div class="sub">A duct, a 4 mm ball and air at 0.05 m/s</div>
<div class="steps"><span>Implicit CAD</span><span>Fast GPU preview</span><span>OpenFOAM</span></div></div>`;

const compareHtml = `<!doctype html><meta charset="utf-8"><style>${base}
  h1{margin:0;padding:34px 0 4px;text-align:center;font-size:64px;font-weight:700}
  .sub{text-align:center;color:#9fb0c2;font-size:32px;margin-bottom:22px}
  .row{display:flex;gap:36px;padding:0 50px;justify-content:center}
  .col{flex:1;max-width:890px;background:#151b24;border-radius:18px;padding:20px 22px 22px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
  .col h2{margin:0 0 2px;font-size:46px;font-weight:650}
  .col .how{color:#9fb0c2;font-size:28px;margin-bottom:12px}
  .pic{width:100%;display:block;border-radius:10px;background:#fff}
  .note{text-align:center;color:#7f91a4;font-size:24px;margin-top:12px}
  .strip{display:flex;gap:24px;padding:24px 50px 0;justify-content:center}
  .card{flex:1;background:#151b24;border-radius:16px;padding:18px 24px;box-shadow:0 8px 30px rgba(0,0,0,.35)}
  .card .k{color:#9fb0c2;font-size:28px;margin-bottom:6px}
  .card .v{font-size:44px;font-weight:700;line-height:1.2}
  .card .d{color:#9fb0c2;font-size:26px;margin-top:6px}
  .vs{color:#9fb0c2;font-weight:500}
  .good{color:#5ad6a0}
  .legend{display:block;width:100%;border-radius:8px;margin-top:4px}
  .foot{position:absolute;bottom:20px;left:0;right:0;text-align:center;color:#7f91a4;font-size:24px}
</style>
<h1>Same case, two solvers</h1>
<div class="sub">Speed in the middle plane · same view, colour map and range</div>
<div class="row">
  <div class="col"><h2>Fast mode</h2><div class="how">GPU lattice-Boltzmann${fastCells ? ' · ' + fastCells + ' cells' : ''}</div><img class="pic" src="${png('fast-view-crop.png')}"></div>
  <div class="col"><h2>OpenFOAM</h2><div class="how">snappyHexMesh, boundary layers${meshCells ? ' · ' + meshCells + ' cells' : ''}</div><img class="pic" src="${png('foam-view-crop.png')}"></div>
</div>
<div class="note">The disc is the ball: each viewer draws the solid in its own way.</div>
<div class="strip">
  <div class="card"><div class="k">Time to a picture</div><div class="v">${fmtTime(fastSeconds)} <span class="vs">vs</span> ${fmtTime(foamSeconds)}</div><div class="d">GPU vs one CPU core, about ${faster}× · mesh ${fmtTime(foamMesh)} + solve ${fmtTime(foamRun)}</div></div>
  <div class="card"><div class="k">Pressure drop</div><div class="v">${fastDp.toFixed(2)} <span class="vs">vs</span> ${foamDp.toFixed(2)} mPa</div><div class="d"><span class="good">${apart.toFixed(0)} % apart</span> · OpenFOAM to a residual of ${numbers.residual ?? flag('residual', '1e-5')}</div></div>
  <div class="card" style="max-width:360px"><div class="k">Speed, m/s</div><img class="legend" src="${png('fast-legend.png')}"></div>
</div>
<div class="foot">Made in Stokes · implicit CAD, a GPU preview and OpenFOAM in one app</div>`;

// The banner for posts: 2048 x 1152, the two pictures and the three figures that matter.
const bannerPath = flag('banner', out.replace(/\.mp4$/i, '') + '-banner-2048x1152.png');
const bannerHtml = `<!doctype html><meta charset="utf-8"><style>
  html,body{margin:0;width:2048px;height:1152px;background:#0e1218;color:#eef2f6;font-family:Inter,"Segoe UI",system-ui,sans-serif;overflow:hidden}
  .bg{position:absolute;inset:0;background:radial-gradient(1200px 700px at 15% 0%,#16324a 0%,rgba(14,18,24,0) 70%),radial-gradient(1000px 600px at 100% 100%,#14382f 0%,rgba(14,18,24,0) 70%)}
  .wrap{position:relative;padding:64px 80px 0}
  h1{margin:0;font-size:104px;font-weight:800;letter-spacing:.5px}
  .sub{margin-top:10px;font-size:44px;color:#a9bacb}
  .steps{margin-top:26px;display:flex;gap:18px;font-size:34px}
  .steps span{padding:10px 24px;border-radius:14px;background:rgba(21,27,36,.9);border:1px solid #2a3a4d}
  .row{display:flex;gap:44px;margin-top:40px}
  .col{flex:1;background:rgba(21,27,36,.92);border-radius:22px;padding:22px 24px 24px;box-shadow:0 14px 50px rgba(0,0,0,.45)}
  .col h2{margin:0;font-size:46px;font-weight:700;display:flex;justify-content:space-between;align-items:baseline}
  .col h2 small{font-size:34px;color:#5ad6a0;font-weight:700}
  .how{color:#a9bacb;font-size:28px;margin:2px 0 14px}
  .pic{width:100%;display:block;border-radius:12px;background:#fff}
  .foot{position:absolute;left:0;right:0;bottom:30px;text-align:center;font-size:34px;color:#a9bacb}
  .foot b{color:#eef2f6}
</style><div class="bg"></div><div class="wrap">
<h1>Flow past a ball</h1>
<div class="sub">From a CAD sketch to a solved flow, in one app</div>
<div class="steps"><span>Implicit CAD</span><span>Fast GPU preview</span><span>OpenFOAM with boundary layers</span></div>
<div class="row">
  <div class="col"><h2>Fast mode <small>${fmtTime(fastSeconds)}</small></h2><div class="how">GPU lattice-Boltzmann${fastCells ? ' · ' + fastCells + ' cells' : ''}</div><img class="pic" src="${png('fast-view-crop.png')}"></div>
  <div class="col"><h2>OpenFOAM <small>${fmtTime(foamSeconds)}</small></h2><div class="how">snappyHexMesh${meshCells ? ' · ' + meshCells + ' cells' : ''}, one CPU core</div><img class="pic" src="${png('foam-view-crop.png')}"></div>
</div>
</div>
<div class="foot">Pressure drop <b>${fastDp.toFixed(2)}</b> vs <b>${foamDp.toFixed(2)} mPa</b>, ${apart.toFixed(0)} % apart · speed in the middle plane</div>`;

const channel = process.env.STOKES_WALK_CHANNEL;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const titlePng = join(shots, 'title.png');
const comparePng = join(shots, 'compare.png');
await page.setContent(titleHtml); await page.waitForTimeout(500); await page.screenshot({ path: titlePng });
await page.setContent(compareHtml); await page.waitForTimeout(800); await page.screenshot({ path: comparePng });
writeFileSync(join(shots, 'compare.html'), compareHtml);
const bannerPage = await browser.newPage({ viewport: { width: 2048, height: 1152 } });
await bannerPage.setContent(bannerHtml); await bannerPage.waitForTimeout(800); await bannerPage.screenshot({ path: bannerPath });
console.log(`wrote ${bannerPath}`);
await browser.close();

// The recording, its waits run fast (with the badge).
const cut = join(shots, 'cut.mp4');
execFileSync(process.execPath, [join(import.meta.dirname, 'cut_walkthrough.mjs'), video, join(shots, 'marks.json'), cut, `--wait=${keep}`, `--ffmpeg=${ffmpeg}`], { stdio: 'inherit' });

// Join: the title (3 s), the cut from `trim`, then the comparison held for `hold` seconds; short fades on the cards.
// tv range, BT.709, as the platforms expect.
const scale = 'scale=1920:1080:flags=lanczos:out_color_matrix=bt709:out_range=tv,setsar=1,fps=30,format=yuv420p';
execFileSync(ffmpeg, ['-y', '-loglevel', 'error',
  '-loop', '1', '-t', '3', '-i', titlePng,
  '-ss', trim, '-i', cut,
  '-loop', '1', '-t', String(hold), '-i', comparePng,
  '-filter_complex', `[0:v]${scale},fade=t=in:st=0:d=0.5,fade=t=out:st=2.5:d=0.5[a];[1:v]${scale}[b];[2:v]${scale},fade=t=in:st=0:d=0.6[c];[a][b][c]concat=n=3:v=1:a=0[out]`,
  '-map', '[out]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
  '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv', out], { stdio: 'inherit' });
console.log(`wrote ${out}`);
