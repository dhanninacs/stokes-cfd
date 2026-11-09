/**
 * The drawer strip's Mesh readout (T-217): "Mesh · 25 k cells · ok", "Mesh · meshing 60 %", "Mesh ·
 * stale", "Mesh · failed". A pure function over what the Mesh panel already reads (the `/mesh/results`
 * node and the live step), so the strip needs no state of its own and no command.
 *
 * Import rule: no React, no store, no DOM.
 */
import { isStale, phaseNow, progressOf, type MeshLive, type MeshResultView } from "./meshPanelModel";

export type MeshReadoutState = "ok" | "warn" | "running" | "stale" | "failed" | "idle";

export interface MeshReadout {
  text: string;
  state: MeshReadoutState;
  detail: string;
}

/** The colour each state's dot takes: tokens every theme defines, never a literal colour. */
export const MESH_DOT: Record<MeshReadoutState, string> = {
  ok: "var(--ok)",
  warn: "var(--warn)",
  running: "var(--accent)",
  stale: "var(--warn)",
  failed: "var(--danger)",
  idle: "var(--text-muted)",
};

/** A cell count as the strip says it: 25000 is "25 k", 24500 "24.5 k", 1200000 "1.2 M", 800 "800". */
export function kCells(cells: number): string {
  const n = Math.max(0, Math.round(cells));
  const short = (v: number) => (v >= 100 || Number.isInteger(v) ? String(Math.round(v)) : v.toFixed(1).replace(/\.0$/, ""));
  if (n >= 1_000_000) return `${short(n / 1_000_000)} M`;
  if (n >= 1000) return `${short(n / 1000)} k`;
  return String(n);
}

/** The readout for the mesh result, or null while there is no result (the strip then shows nothing). */
export function meshReadout(view: MeshResultView | null, live: MeshLive): MeshReadout | null {
  if (view === null) return null;
  const phase = phaseNow(view, live);
  switch (phase) {
    case "none":
      return null;
    case "queued":
      return { text: "Mesh · starting", state: "running", detail: "The mesh job is waiting to start." };
    case "running": {
      const f = progressOf(view, live).fraction;
      return { text: f !== null && f > 0 ? `Mesh · meshing ${Math.round(f * 100)} %` : "Mesh · meshing", state: "running", detail: "The mesh is being built." };
    }
    case "cancelling":
      return { text: "Mesh · stopping", state: "running", detail: "The mesh job was asked to stop." };
    case "done": {
      if (isStale(view)) return { text: "Mesh · stale", state: "stale", detail: "The geometry, the tags or the settings changed since this mesh was made." };
      const cells = `${kCells(view.cells)} cells`;
      return view.ok ? { text: `Mesh · ${cells} · ok`, state: "ok", detail: "Every mesh check passed." } : { text: `Mesh · ${cells} · quality not met`, state: "warn", detail: "The mesh was built but a quality check missed its limit." };
    }
    case "failed":
      return { text: "Mesh · failed", state: "failed", detail: view.error !== "" ? view.error : "The mesh job failed." };
    case "cancelled":
      return { text: "Mesh · cancelled", state: "idle", detail: "The mesh job was cancelled." };
    case "interrupted":
      return { text: "Mesh · interrupted", state: "stale", detail: "The agent stopped reporting this job; its outcome is unknown." };
    case "skipped":
      return { text: "Mesh · skipped", state: "stale", detail: view.error !== "" ? view.error.replace(/^[a-z_.]+:\s*/, "") : "No host could run it." };
  }
}
