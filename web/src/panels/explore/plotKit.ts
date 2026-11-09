/**
 * What the Explore plots share: the colours they read from the theme tokens, the hook that tells them
 * the theme changed, element sizes, and DPR-correct canvases. Nothing here is a colour of its own:
 * every colour is a token (`--series-1..4`, `--accent`, `--text-muted`, `--panel-border` and the
 * neighbours), read when a plot is drawn, so every style and every custom theme follows.
 */
import { useEffect, useRef, useState, type RefObject } from "react";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface Palette {
  text: string;
  muted: string;
  faint: string;
  /** Hairlines and grid lines. */
  grid: string;
  gridStrong: string;
  accent: string;
  danger: string;
  /** The panel's own fill: the veil that dims what is not selected. */
  surface: string;
  /** `--series-1..4`. */
  series: readonly [string, string, string, string];
  sans: string;
  mono: string;
}

export function readPalette(): Palette {
  return {
    text: token("--text"),
    muted: token("--text-muted"),
    faint: token("--text-faint"),
    grid: token("--panel-border"),
    gridStrong: token("--panel-border-strong"),
    accent: token("--accent"),
    danger: token("--danger"),
    surface: token("--panel-solid") || token("--bg-elev"),
    series: [token("--series-1"), token("--series-2"), token("--series-3"), token("--series-4")],
    sans: token("--font-sans") || "sans-serif",
    mono: token("--font-mono") || "monospace",
  };
}

/**
 * A number that changes whenever the theme does (another style or appearance, or a custom token), for
 * the dependency list of whatever has to be drawn again. The top bar says so by setting `data-theme`.
 */
export function useThemeEpoch(): number {
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => setEpoch((n) => n + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return epoch;
}

export interface Size {
  width: number;
  height: number;
}

/**
 * The content size of an element, kept current by a ResizeObserver; 0 x 0 while it is hidden. The element
 * may appear after the first render (an empty state gives way to a table): it is looked for after every
 * render, and observed from then on.
 */
export function useElementSize(ref: RefObject<HTMLElement | null>): Size {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const observed = useRef<HTMLElement | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el === observed.current) return;
    observer.current?.disconnect();
    observer.current = null;
    observed.current = el;
    if (!el) return;
    const measure = () => {
      const width = Math.floor(el.clientWidth);
      const height = Math.floor(el.clientHeight);
      setSize((s) => (s.width === width && s.height === height ? s : { width, height }));
    };
    measure();
    observer.current = new ResizeObserver(measure);
    observer.current.observe(el);
  });
  useEffect(
    () => () => {
      observer.current?.disconnect();
      observer.current = null;
      observed.current = null;
    },
    [],
  );
  return size;
}

/** Make a canvas `width` x `height` CSS pixels sharp on this display; returns a context in CSS pixels. */
export function prepareCanvas(canvas: HTMLCanvasElement, width: number, height: number): CanvasRenderingContext2D | null {
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.round(width * ratio));
  const h = Math.max(1, Math.round(height * ratio));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

// ---------------------------------------------------------------------------------------------- colours
export type RGB = readonly [number, number, number];

const parsed = new Map<string, RGB>();
let probe: CanvasRenderingContext2D | null = null;

/** Any CSS colour as red, green, blue (0-255); the alpha is dropped. Cached. */
export function toRgb(css: string): RGB {
  const known = parsed.get(css);
  if (known) return known;
  probe ??= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  // A sentinel for a colour the browser cannot parse (never drawn: every colour comes from a token that parses).
  let rgb: RGB = [128, 128, 128];
  if (probe) {
    probe.clearRect(0, 0, 1, 1);
    probe.fillStyle = "gray";
    probe.fillStyle = css; // an unparseable colour leaves the sentinel
    probe.fillRect(0, 0, 1, 1);
    const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
    rgb = [r, g, b];
  }
  parsed.set(css, rgb);
  return rgb;
}

/** A colour along a ramp of stops, `t` from 0 to 1, as a CSS string. */
export function rampColor(stops: readonly RGB[], t: number): string {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const [a, b] = [stops[i], stops[i + 1]];
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)} ${Math.round(a[1] + (b[1] - a[1]) * f)} ${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

/**
 * The colour ramp of the parallel plot: cool to warm through the series tokens (the second series, the
 * third, the fourth), so every style draws it in its own blue, green and amber.
 */
export function rampStops(palette: Palette): RGB[] {
  return [toRgb(palette.series[1]), toRgb(palette.series[2]), toRgb(palette.series[3])];
}

// ---------------------------------------------------------------------------------------------- numbers
/** Fraction of the way from `min` to `max`; the middle when they are the same. */
export function fraction(value: number, min: number, max: number): number {
  return max > min ? (value - min) / (max - min) : 0.5;
}

/** The index of the point nearest to (x, y) among the pairs `xs[i], ys[i]`, within `radius` px; null when none is that close. */
export function nearest(xs: ArrayLike<number>, ys: ArrayLike<number>, x: number, y: number, radius: number): number | null {
  let best = -1;
  let bestDistance = radius * radius;
  for (let i = 0; i < xs.length; i++) {
    const dx = xs[i] - x;
    const dy = ys[i] - y;
    const d = dx * dx + dy * dy;
    if (d <= bestDistance) {
      bestDistance = d;
      best = i;
    }
  }
  return best < 0 ? null : best;
}

/**
 * How big and how opaque a dot is when there are `n` of them in one plot. The first hundred are drawn at
 * full size; a crowd gets smaller and fainter, so that overlapping dots add up to a density instead of
 * a solid patch.
 */
export function dotStyle(n: number, radius: number, alpha: number): { radius: number; alpha: number } {
  if (n <= 100) return { radius, alpha };
  const crowd = Math.pow(100 / n, 0.3);
  return { radius: Math.max(1, radius * Math.pow(crowd, 0.7)), alpha: Math.max(0.25, alpha * crowd) };
}

/** The same for the lines of the parallel plot: fainter and thinner as there are more of them. */
export function lineStyle(n: number): { width: number; alpha: number } {
  if (n <= 12) return { width: 1.6, alpha: 0.9 };
  if (n <= 60) return { width: 1.3, alpha: 0.7 };
  return { width: n > 300 ? 0.9 : 1.2, alpha: Math.max(0.05, 0.7 * Math.sqrt(60 / n)) };
}

/** A CSS colour with an alpha, from any CSS colour. */
export function withAlpha(css: string, alpha: number): string {
  const [r, g, b] = toRgb(css);
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}

/** Plots draw at most this many points; beyond it every n-th point is drawn (the selected ones always). */
export const MAX_DRAWN = 20000;

/** The stride that brings `count` points under MAX_DRAWN. */
export function strideFor(count: number): number {
  return Math.max(1, Math.ceil(count / MAX_DRAWN));
}
