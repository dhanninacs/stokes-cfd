/**
 * The ghost on screen (ADR-0039), as transient view state: what it shows, and which operation's
 * run it stands in for. Not model state: it is never sent anywhere, and it goes when the body it
 * stands for arrives (`GhostLayer` decides when). One ghost at a time.
 */
import type { GhostSpec } from "./ghost";

export interface GhostState {
  spec: GhostSpec;
  /** The operation whose rebuild it covers; null while dragging, or before `operation.create` answered. */
  opId: string | null;
  /** "drag": follows the handle; "build": waits for the operation's new body. */
  phase: "drag" | "build";
  /** `performance.now()` when the build phase began, or the operation became known (`ghostFor`). */
  since: number;
  /** `performance.now()` when the build was asked for (Finish, a drag's release): the status line's clock. */
  started: number;
}

let current: GhostState | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export function subscribeGhost(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function currentGhost(): GhostState | null {
  return current;
}

/** Shows `spec` while a handle is dragged. */
export function dragGhost(spec: GhostSpec): void {
  const now = performance.now();
  current = { spec, opId: null, phase: "drag", since: now, started: now };
  emit();
}

/** Shows `spec` until operation `opId` (null: not known yet) has rebuilt its body. */
export function buildGhost(spec: GhostSpec, opId: string | null): void {
  const now = performance.now();
  current = { spec, opId, phase: "build", since: now, started: now };
  emit();
}

/** Names the operation a build ghost waits for, once `operation.create` answered. */
export function ghostFor(opId: string): void {
  if (current === null || current.phase !== "build") return;
  current = { ...current, opId, since: performance.now() };
  emit();
}

/**
 * The operation whose Pull field should open, once its handle is on screen (after Finish: the depth Finish chose,
 * ready to type over). Taken once by the handle; a request for another operation replaces it.
 */
let pullFocus: string | null = null;

export function requestPullFocus(opId: string): void {
  pullFocus = opId;
  emit();
}

/** True, once, when the handle of `opId` should open its field. */
export function takePullFocus(opId: string): boolean {
  if (pullFocus !== opId) return false;
  pullFocus = null;
  return true;
}

export function clearGhost(): void {
  if (current === null) return;
  current = null;
  emit();
}
