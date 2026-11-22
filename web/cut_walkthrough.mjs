// Turns a recorded walkthrough (the .mp4 from walk_lib's STOKES_WALK_VIDEO) and its marks.json into an
// .mp4 whose waits run fast: each wait the recorder marked (a mesh, a run, the optimisation) is
// shortened to about `--wait` seconds, and everything else plays at the speed it happened.
//
//   node cut_walkthrough.mjs <recording.mp4> <marks.json> <out.mp4> [--wait=6] [--ffmpeg=ffmpeg]
//
// ffmpeg is run as a separate program with an argument list (no shell). The filter graph is one
// argument (a few thousand characters for thirty waits) and is also written beside the output.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const flag = (name, fallback) => (args.find((a) => a.startsWith(`--${name}=`)) ?? `--${name}=${fallback}`).slice(name.length + 3);
const [video, marksFile, out] = args.filter((a) => !a.startsWith('--'));
if (!video || !marksFile || !out) { console.error('usage: node cut_walkthrough.mjs <video.webm> <marks.json> <out.mp4> [--wait=6] [--ffmpeg=ffmpeg]'); process.exit(2); }
const keep = Number(flag('wait', '6')) || 6;
const ffmpeg = flag('ffmpeg', 'ffmpeg');

const marks = JSON.parse(readFileSync(marksFile, 'utf8'));
const end = (marks.find((m) => m.kind === 'end') ?? marks[marks.length - 1]).t;
// The waits, as [from, to]; a wait shorter than what is kept anyway stays as it is.
const waits = [];
let open = null;
for (const m of marks) {
  if (m.kind === 'wait-start') open = m.t;
  else if (m.kind === 'wait-end' && open !== null) { if (m.t - open > keep * 1.5) waits.push([open, m.t]); open = null; }
}
// Segments in order: [from, to, speed].
const segments = [];
let at = 0;
for (const [a, b] of waits) {
  if (a > at) segments.push([at, a, 1]);
  segments.push([a, b, (b - a) / keep]);
  at = b;
}
if (end > at) segments.push([at, end, 1]);

// A fast-forwarded stretch says so: a small badge top right ("x25"), so eight seconds on screen are not read as real time.
const font = flag('font', 'C:/Windows/Fonts/arialbd.ttf').replace(':', '\\:');
const badge = (speed) => (speed > 1.5 && !args.includes('--no-badge')
  ? `,drawtext=fontfile='${font}':text='fast-forward x${Math.round(speed)}':x=w-tw-24:y=60:fontsize=h/26:fontcolor=white:box=1:boxcolor=0x10141acc:boxborderw=10`
  : '');
const parts = segments.map(([a, b, speed], i) => `[0:v]trim=start=${a.toFixed(3)}:end=${b.toFixed(3)},setpts=(PTS-STARTPTS)/${speed.toFixed(4)}${badge(speed)}[v${i}]`);
const graph = `${parts.join(';\n')};\n${segments.map((_, i) => `[v${i}]`).join('')}concat=n=${segments.length}:v=1:a=0,fps=30,format=yuv420p[out]`;
const graphFile = `${out}.filter.txt`;
writeFileSync(graphFile, graph);
const length = segments.reduce((sum, [a, b, speed]) => sum + (b - a) / speed, 0);
console.log(`${segments.length} segments, ${waits.length} waits run fast; ${Math.round(end)} s recorded -> about ${Math.round(length)} s`);
execFileSync(ffmpeg, ['-y', '-i', video, '-filter_complex', graph.replace(/\n/g, ''), '-map', '[out]', '-c:v', 'libx264', '-preset', 'medium', '-crf', '21', '-movflags', '+faststart', out], { stdio: 'inherit' });
console.log(`wrote ${out}`);
