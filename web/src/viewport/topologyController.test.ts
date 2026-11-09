/**
 * The lazy topology lifecycle (ADR-0022 §15.2, T-058).
 *
 * These test the rules the ADR states explicitly, because each is easy to get wrong in a way no
 * screenshot would show: fetching eagerly, fetching for invisible parts, unbounded concurrency,
 * ignoring the hovered-part priority, or failing to release on leaving the mode.
 */

import { describe, expect, it, vi } from "vitest";

import { MAX_IN_FLIGHT, MAX_QUEUED, TopologyController, type TopologyState } from "./topologyController";
import { TOPOLOGY_VERSION } from "./topology";

/** A payload the parser accepts, so a "ready" state is a real parse rather than a stub. */
function goodBuffer(): ArrayBuffer {
  const header = {
    version: TOPOLOGY_VERSION,
    part: "n_0000000a",
    basis: "abc",
    units: "m",
    scale: 0.001,
    faces: 0,
    // v3: the sizes of things, present (empty) so the record is a valid v3 payload.
    faceSizes: [],
    solids: [],
    edges: [],
    vertices: [],
  };
  const body = new TextEncoder().encode(JSON.stringify(header));
  const padding = (4 - (body.length % 4)) % 4;
  const buffer = new ArrayBuffer(4 + body.length + padding);
  new DataView(buffer).setUint32(0, body.length + padding, true);
  new Uint8Array(buffer, 4, body.length).set(body);
  return buffer;
}

/** A fetch that resolves when the test says so, so concurrency can be observed. */
function deferredFetcher() {
  const pending: { part: string; resolve: (v: ArrayBuffer | null) => void; reject: (e: Error) => void; signal: AbortSignal }[] = [];
  const fetch = (part: string, signal: AbortSignal) =>
    new Promise<ArrayBuffer | null>((resolve, reject) => {
      pending.push({ part, resolve, reject, signal });
    });
  return { fetch, pending };
}

describe("TopologyController", () => {
  it("fetches nothing until the mode is active", () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.want(["p1", "p2"]);
    expect(pending).toHaveLength(0);
    expect(controller.queued).toBe(0);
  });

  it("fetches the wanted parts once the mode is on", () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    expect(pending.map((p) => p.part)).toEqual(["p1"]);
  });

  it("caps the requests in flight", () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1", "p2", "p3", "p4", "p5"]);
    expect(controller.inFlight).toBe(MAX_IN_FLIGHT);
    expect(pending).toHaveLength(MAX_IN_FLIGHT);
    expect(controller.queued).toBe(2);
  });

  it("takes the hovered part first", () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1", "p2", "p3"], "p3");
    expect(pending[0].part).toBe("p3");
  });

  it("does not ask twice for one part", () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    controller.want(["p1"]);
    expect(pending).toHaveLength(1);
  });

  it("reports ready with a parsed record", async () => {
    const { fetch, pending } = deferredFetcher();
    const seen: [string, TopologyState | null][] = [];
    const controller = new TopologyController({ fetch, onChange: (p, s) => seen.push([p, s]) });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));
    const state = controller.state("p1");
    expect(state?.status === "ready" && state.topology.part).toBe("n_0000000a");
    expect(seen.map(([p, s]) => `${p}:${s.status}`)).toEqual(["p1:loading", "p1:ready"]);
  });

  it("treats a 404 (null) as a known 'no topology', not an error or a retry loop", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["mesh_only"]);
    pending[0].resolve(null);
    await vi.waitFor(() => expect(controller.state("mesh_only")?.status).toBe("none"));
    // Asking again must not re-fetch: "none" is an answer.
    controller.want(["mesh_only"]);
    expect(pending).toHaveLength(1);
  });

  it("remembers a real failure instead of retrying forever", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].reject(new Error("boom"));
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("error"));
    controller.want(["p1"]);
    expect(pending).toHaveLength(1);
  });

  it("does not start queued work when the mode was left", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1", "p2", "p3", "p4", "p5"]);
    expect(pending).toHaveLength(MAX_IN_FLIGHT);
    controller.setActive(false);
    // A slot frees up (the abort resolves the in-flight fetches), but nothing new may start.
    pending[0].reject(new Error("aborted"));
    await vi.waitFor(() => expect(controller.inFlight).toBeLessThan(MAX_IN_FLIGHT));
    expect(pending).toHaveLength(MAX_IN_FLIGHT);
  });

  it("aborts in-flight requests and forgets everything on leaving", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1", "p2"]);
    const signals = pending.map((p) => p.signal);
    controller.setActive(false);
    expect(signals.every((s) => s.aborted)).toBe(true);
    expect(controller.state("p1")).toBeUndefined();
    expect(controller.queued).toBe(0);
  });

  it("starts clean when the mode is entered again", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));
    controller.setActive(false);
    controller.setActive(true);
    controller.want(["p1"]);
    expect(pending).toHaveLength(2);
  });

  it("can forget one part, e.g. after its mesh changes", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));
    controller.forget("p1");
    expect(controller.state("p1")).toBeUndefined();
    controller.want(["p1"]);
    expect(pending).toHaveLength(2);
  });

  it("an abort is not recorded as an error", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["p1"]);
    controller.forget("p1");
    pending[0].reject(new Error("aborted"));
    await vi.waitFor(() => expect(controller.inFlight).toBe(0));
    expect(controller.state("p1")).toBeUndefined();
  });

  it("surfaces a malformed record as an error rather than a partial parse", async () => {
    const controller = new TopologyController({ fetch: async () => new ArrayBuffer(2) });
    controller.setActive(true);
    controller.want(["p1"]);
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("error"));
  });
});


describe("A3: releasing a record must reach the caller", () => {
  it("reports a removal through onChange when the mode is left", async () => {
    const { fetch, pending } = deferredFetcher();
    const seen: [string, TopologyState | null][] = [];
    const controller = new TopologyController({ fetch, onChange: (p, s) => seen.push([p, s]) });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));

    controller.setActive(false);
    // Without this the scene's own map only grew, and a re-import kept serving the old edges.
    expect(seen[seen.length - 1]).toEqual(["p1", null]);
  });

  it("reports a removal for forget", async () => {
    const { fetch, pending } = deferredFetcher();
    const seen: [string, TopologyState | null][] = [];
    const controller = new TopologyController({ fetch, onChange: (p, s) => seen.push([p, s]) });
    controller.setActive(true);
    controller.want(["p1"]);
    pending[0].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));

    controller.forget("p1");
    expect(seen[seen.length - 1]).toEqual(["p1", null]);
  });

  it("does not report a removal for a part that had no record", () => {
    const { fetch } = deferredFetcher();
    const seen: [string, TopologyState | null][] = [];
    const controller = new TopologyController({ fetch, onChange: (p, s) => seen.push([p, s]) });
    controller.setActive(true);
    controller.forget("never-asked");
    expect(seen).toEqual([]);
  });

  it("reports every released record when cleared", async () => {
    const { fetch, pending } = deferredFetcher();
    const seen: [string, TopologyState | null][] = [];
    const controller = new TopologyController({ fetch, onChange: (p, s) => seen.push([p, s]) });
    controller.setActive(true);
    controller.want(["p1", "p2"]);
    pending[0].resolve(goodBuffer());
    pending[1].resolve(goodBuffer());
    await vi.waitFor(() => expect(controller.state("p1")?.status).toBe("ready"));
    await vi.waitFor(() => expect(controller.state("p2")?.status).toBe("ready"));

    controller.clear();
    const removed = seen.filter(([, s]) => s === null).map(([p]) => p).sort();
    expect(removed).toEqual(["p1", "p2"]);
  });
});


describe("A4: the queue is capped", () => {
  it("does not hand thousands of parts to the worker when the mode opens", () => {
    // Reproduces the reported failure: entering Edge mode on a 4,500-part assembly queued all of
    // them on the single geometry worker, blocking imports and operations (§15.2).
    const { fetch } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(Array.from({ length: 4500 }, (_, i) => `p${i}`));
    expect(controller.pending).toBeLessThanOrEqual(MAX_QUEUED);
  });

  it("remembers the parts it did not queue, so progress continues without re-asking", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    const many = Array.from({ length: MAX_QUEUED * 3 }, (_, i) => `p${i}`);
    controller.want(many);

    // Resolve what is in flight; the freed slots are refilled from the remembered set.
    for (let round = 0; round < 30; round++) {
      const batch = pending.splice(0, pending.length);
      for (const request of batch) request.resolve(goodBuffer());
      await vi.waitFor(() => expect(controller.pending).toBeLessThanOrEqual(MAX_QUEUED));
    }
    // More parts got their turn than could have been queued at once.
    const done = many.filter((part) => controller.state(part)?.status === "ready").length;
    expect(done).toBeGreaterThan(MAX_QUEUED);
  });

  it("always admits the hovered part, evicting the tail of the queue if the cap is full", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(Array.from({ length: MAX_QUEUED * 2 }, (_, i) => `p${i}`));
    expect(controller.pending).toBe(MAX_QUEUED);

    // A part named with priority must be fetched even though the queue was full.
    controller.want(["hovered"], "hovered");
    // The three in-flight slots are occupied, so the priority part is next in line, not fetched
    // instantly: free one slot and it must be the very next request.
    pending.splice(0, pending.length).forEach((request) => request.resolve(goodBuffer()));
    await vi.waitFor(() => expect(pending.some((r) => r.part === "hovered")).toBe(true));
  });

  it("still reports the priority part first when it was already known", async () => {
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    controller.want(["a", "b"]);
    controller.want(["a", "b"], "b");
    // b was already queued, so no duplicate request: the priority must not re-add it.
    expect(pending.filter((r) => r.part === "b").length).toBeLessThanOrEqual(MAX_IN_FLIGHT);
  });
});


describe("A4: only what is visible is fetched, not everything ever named", () => {
  it("stops fetching parts that have left the visible set", async () => {
    // The defect the cap alone did not fix: `_wanted` accumulated the union of every `want()` call.
    // Naming a large assembly once (as entering Edge mode does) left 4,500 parts remembered, and
    // `_pump` kept handing them to the worker as slots freed — 4,500 fetches, three at a time, long
    // after the view had moved on. The queue was bounded; the set of parts TO FETCH was not.
    const { fetch, pending } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);

    // Name 500 parts, as a first call for a large visible set would.
    controller.want(Array.from({ length: 500 }, (_, i) => `p${i}`));
    // Let one batch through, then narrow the view to a single part.
    pending.splice(0, pending.length).forEach((request) => request.resolve(null));
    controller.want(["only"]);
    pending.splice(0, pending.length).forEach((request) => request.resolve(null));

    // From here on, NOTHING except `only` may still be requested: the rest are not visible.
    const later: string[] = [];
    for (let round = 0; round < 10; round++) {
      const batch = pending.splice(0, pending.length);
      later.push(...batch.map((r) => r.part));
      for (const request of batch) request.resolve(null);
      await vi.waitFor(() => expect(controller.pending).toBeLessThanOrEqual(MAX_QUEUED));
      if (batch.length === 0) break;
    }
    const stale = later.filter((part) => part !== "only");
    expect(stale).toEqual([]);
  });

  it("drops a queued part from the queue when it leaves the view", async () => {
    const { fetch } = deferredFetcher();
    const controller = new TopologyController({ fetch });
    controller.setActive(true);
    // More than the cap, so some wait in the queue rather than in flight.
    controller.want(Array.from({ length: MAX_QUEUED + 20 }, (_, i) => `q${i}`));
    expect(controller.pending).toBe(MAX_QUEUED);
    // Then only one is visible.
    controller.want(["q0"]);
    expect(controller.pending).toBeLessThanOrEqual(MAX_IN_FLIGHT + 1);
  });
});
