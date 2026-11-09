/**
 * Paths into the workspace document, spelled as the server spells them (stokes.workspace.paths):
 * plain keys of letters, digits, `_` and `-` (not all digits) joined with dots, list indices as `.N`,
 * every other key in brackets as a JSON string. `workspace.get` reports `sources` under these
 * canonical paths, so a lookup must build its key the same way: `shortcuts["view.fit"]`,
 * `layout.left.panels.0.size`, `theme.tokens.--accent`.
 *
 * Also the small immutable helpers the optimistic updates use. Pure functions.
 */

export type Segment = string | number;

const PLAIN_KEY = /^[A-Za-z0-9_-]+$/;
const DIGITS = /^\d+$/;

export function formatPath(segments: readonly Segment[]): string {
  let out = "";
  for (const segment of segments) {
    if (typeof segment === "number") out += out ? `.${segment}` : String(segment);
    else if (PLAIN_KEY.test(segment) && !DIGITS.test(segment)) out += out ? `.${segment}` : segment;
    else out += `[${JSON.stringify(segment)}]`;
  }
  return out;
}

/** The value at a path, or undefined. */
export function getIn(root: unknown, segments: readonly Segment[]): unknown {
  let node = root;
  for (const segment of segments) {
    if (Array.isArray(node) && typeof segment === "number") node = node[segment];
    else if (node !== null && typeof node === "object" && typeof segment === "string") node = (node as Record<string, unknown>)[segment];
    else return undefined;
  }
  return node;
}

/** A copy of `root` with `value` at the path; the objects and lists on the way are copied, the rest shared. */
export function setIn<T>(root: T, segments: readonly Segment[], value: unknown): T {
  if (segments.length === 0) return value as T;
  const [head, ...rest] = segments;
  if (Array.isArray(root) && typeof head === "number") {
    const copy = root.slice();
    copy[head] = setIn(copy[head], rest, value);
    return copy as T;
  }
  const base = root !== null && typeof root === "object" && !Array.isArray(root) ? (root as Record<string, unknown>) : {};
  return { ...base, [String(head)]: setIn(base[String(head)], rest, value) } as T;
}

/** A copy of `root` without the key at the path (the layer below shows through). */
export function removeIn<T>(root: T, segments: readonly Segment[]): T {
  if (segments.length === 0) return root;
  const [head, ...rest] = segments;
  if (root === null || typeof root !== "object" || Array.isArray(root)) return root;
  const base = root as Record<string, unknown>;
  const key = String(head);
  if (!(key in base)) return root;
  if (rest.length === 0) {
    const { [key]: _gone, ...others } = base;
    void _gone;
    return others as T;
  }
  return { ...base, [key]: removeIn(base[key], rest) } as T;
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    const bb = b as unknown[];
    return a.length === bb.length && a.every((v, i) => deepEqual(v, bb[i]));
  }
  const ka = Object.keys(a as object);
  const kb = Object.keys(b as object);
  return ka.length === kb.length && ka.every((k) => deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

export function clone<T>(value: T): T {
  return structuredClone(value);
}
