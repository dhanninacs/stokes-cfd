/**
 * The picture on a project card that has no saved thumbnail: streamlines of potential flow around a cylinder,
 * the card's monogram sitting where the cylinder is. The free stream's angle and the cylinder's circulation
 * come from a hash of the seed (the project's path or the example's id), so every card has its own pattern
 * and the same project always draws the same one. Pure and deterministic; the start screen renders the paths.
 *
 * The field is the textbook one: w(z) = U (z e^{-iα} + R² e^{iα} / z) + iΓ/(2π) log z, and the velocity is
 * u − iv = dw/dz. |Γ| stays under 4πUR so both stagnation points sit on the surface and the picture reads as
 * flow past a body with lift rather than a vortex.
 */

export interface FlowArt {
  /** SVG path data, one per streamline, in the viewBox `0 0 width height`. */
  lines: string[];
  width: number;
  height: number;
  /** The cylinder, in the same units: the monogram tile is drawn over it. */
  cx: number;
  cy: number;
  radius: number;
}

export const FLOW_ART_WIDTH = 160;
export const FLOW_ART_HEIGHT = 90;
/** The cylinder's radius in viewBox units: 2R / height is the share of the thumbnail the tile takes. */
export const FLOW_ART_RADIUS = 18;

/** FNV-1a, 32 bit: a stable seed from any string. */
function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** mulberry32: small, fast and good enough for picking three numbers. */
function random(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function flowArt(seed: string, lineCount = 15): FlowArt {
  const width = FLOW_ART_WIDTH;
  const height = FLOW_ART_HEIGHT;
  const radius = FLOW_ART_RADIUS;
  const cx = width / 2;
  const cy = height / 2;
  const rand = random(hash(seed));
  const alpha = (rand() - 0.5) * 0.55; // ±16° angle of attack
  const speed = 1;
  const gamma = (rand() * 2 - 1) * 0.7 * 4 * Math.PI * speed * radius; // up to 70 % of the separation limit
  const phase = rand(); // where the seeds start across the inflow
  const ca = Math.cos(alpha);
  const sa = Math.sin(alpha);
  const r2 = radius * radius;

  // u − iv = U (e^{-iα} − R² e^{iα} / z²) + iΓ / (2π z), evaluated at z = x + iy relative to the centre.
  const velocity = (px: number, py: number): [number, number] => {
    const x = px - cx;
    const y = -(py - cy); // SVG y runs down; the field is in maths orientation
    const d2 = x * x + y * y;
    const d4 = d2 * d2;
    // 1 / z² = (x² − y² − 2ixy) / |z|⁴
    const izr = (x * x - y * y) / d4;
    const izi = (-2 * x * y) / d4;
    // e^{iα} / z²
    const er = ca * izr - sa * izi;
    const ei = ca * izi + sa * izr;
    // iΓ / (2π z) = iΓ (x − iy) / (2π |z|²) = Γ (y + ix) / (2π |z|²)
    const k = gamma / (2 * Math.PI * d2);
    const wr = speed * (ca - r2 * er) + k * y;
    const wi = speed * (-sa - r2 * ei) + k * x;
    // dw/dz = u − iv, so u = Re, v = −Im; back to SVG orientation flips v
    return [wr, wi];
  };

  const lines: string[] = [];
  const step = 1.6;
  const limit = 260;
  const margin = 12;
  const span = height + 2 * margin + Math.abs(Math.tan(alpha)) * width;
  for (let i = 0; i < lineCount; i++) {
    // Seeds on a line across the inflow, upstream of the box, spread past the top and bottom edges so a
    // tilted stream still fills the picture.
    let x = -margin;
    let y = cy - span / 2 + ((i + 0.5 + (phase - 0.5) * 0.8) / lineCount) * span + Math.tan(alpha) * (cx + margin);
    const points: string[] = [`M${x.toFixed(1)} ${y.toFixed(1)}`];
    let inside = 0;
    for (let n = 0; n < limit; n++) {
      // RK2 on the unit direction: even spacing along the line, and it walks through the slow region near a
      // stagnation point instead of stalling there.
      const [u1, v1] = velocity(x, y);
      const m1 = Math.hypot(u1, v1) || 1;
      const mx = x + (u1 / m1) * step * 0.5;
      const my = y + (v1 / m1) * step * 0.5;
      const [u2, v2] = velocity(mx, my);
      const m2 = Math.hypot(u2, v2) || 1;
      x += (u2 / m2) * step;
      y += (v2 / m2) * step;
      if (Math.hypot(x - cx, y - cy) < radius * 1.02) break;
      if (x > width + margin || y < -margin * 2 || y > height + margin * 2) {
        points.push(`L${x.toFixed(1)} ${y.toFixed(1)}`);
        break;
      }
      if (x >= -1 && x <= width + 1 && y >= -1 && y <= height + 1) inside++;
      points.push(`L${x.toFixed(1)} ${y.toFixed(1)}`);
    }
    if (inside > 4) lines.push(points.join(""));
  }
  return { lines, width, height, cx, cy, radius };
}
