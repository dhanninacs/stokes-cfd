/**
 * The cut routes of the results: the `?plane=` query the three slice routes take (the contract of the
 * slices), and what `fetchCut` makes of the server's answers (a plane that misses the model is not a missing
 * slice).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCut, fieldRoute, sliceEdgesRoute, sliceFieldRoute, sliceRoute } from "./resultsApi";
import { axisPlane } from "../state/slicePlane";

describe("the plane query on the cut routes", () => {
  it("adds plane=ox,oy,oz,nx,ny,nz to the slice and the slice-edges routes, commas as they are", () => {
    expect(sliceRoute("r_1", axisPlane("z", 0.01))).toBe("/api/results/slice/r_1?plane=0,0,0.01,0,0,1");
    expect(sliceEdgesRoute("r_1", axisPlane("x", 0.25))).toBe("/api/results/slice-edges/r_1?plane=0.25,0,0,1,0,0");
  });

  it("keeps the run's own mid-plane routes exactly as they were when there is no plane", () => {
    expect(sliceRoute("r_1")).toBe("/api/results/slice/r_1");
    expect(sliceRoute("r_1", null)).toBe("/api/results/slice/r_1");
    expect(sliceEdgesRoute("r_1")).toBe("/api/results/slice-edges/r_1");
    expect(sliceFieldRoute("r_1", null, "U_mag")).toBe(fieldRoute("r_1", "slice", "U_mag"));
    expect(fieldRoute("r_1", "slice", "U_mag")).toBe("/api/results/field/r_1/slice?field=U_mag");
  });

  it("puts the plane before the field on the field route", () => {
    expect(sliceFieldRoute("r_1", axisPlane("y", 0.005), "p")).toBe("/api/results/field/r_1/slice?plane=0,0.005,0,0,1,0&field=p");
  });

  it("rounds the plane to six significant digits, so the same plane is the same URL", () => {
    expect(sliceRoute("r_1", axisPlane("z", 0.0100000004))).toBe(sliceRoute("r_1", axisPlane("z", 0.01)));
    expect(sliceRoute("r_1", { origin: [0.123456789, 0, 0], normal: [1, 0, 0] })).toBe("/api/results/slice/r_1?plane=0.123457,0,0,1,0,0");
  });

  it("encodes the run id and the field, which are data", () => {
    expect(sliceRoute("r/1", axisPlane("z", 0))).toBe("/api/results/slice/r%2F1?plane=0,0,0,0,0,1");
    expect(sliceFieldRoute("r_1", axisPlane("z", 0), "a b")).toContain("field=a%20b");
  });

  it("is null for a plane the server would refuse", () => {
    expect(sliceRoute("r_1", axisPlane("z", Number.NaN))).toBeNull();
    expect(sliceEdgesRoute("r_1", { origin: [0, 0, 0], normal: [0, 0, 0] })).toBeNull();
    expect(sliceFieldRoute("r_1", { origin: [0, Number.POSITIVE_INFINITY, 0], normal: [0, 0, 1] }, "p")).toBeNull();
  });
});

function answer(status: number, body: string | ArrayBuffer): unknown {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => (typeof body === "string" ? body : ""),
    arrayBuffer: async () => (typeof body === "string" ? new ArrayBuffer(0) : body),
  };
}

describe("fetchCut", () => {
  afterEach(() => vi.unstubAllGlobals());

  const client = { apiBase: "http://h:1/", token: "tok" };

  it("sends the token and gives the bytes of a 200", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: { headers: Record<string, string> }) => {
      seen.push(url, init.headers["X-Stokes-Token"]);
      return answer(200, new ArrayBuffer(8));
    });
    const out = await fetchCut(client, "/api/results/slice/r_1?plane=0,0,0,0,0,1");
    expect(out.kind).toBe("ok");
    expect(seen).toEqual(["http://h:1/api/results/slice/r_1?plane=0,0,0,0,0,1", "tok"]);
  });

  it("tells a plane that misses the model from any other 404, wherever the server puts the code", async () => {
    for (const body of ['{"detail":"results.slice_empty"}', '{"detail":{"code":"results.slice_empty","message":"x"}}', '{"code":"results.slice_empty"}']) {
      vi.stubGlobal("fetch", async () => answer(404, body));
      expect((await fetchCut(client, "/r")).kind).toBe("empty");
    }
    vi.stubGlobal("fetch", async () => answer(404, '{"detail":"unknown run"}'));
    expect((await fetchCut(client, "/r")).kind).toBe("missing");
  });

  it("knows a run whose host is gone", async () => {
    vi.stubGlobal("fetch", async () => answer(404, '{"detail":{"code":"results.host_unavailable"}}'));
    expect((await fetchCut(client, "/r")).kind).toBe("host");
  });

  it("reads a 400 as missing, a body it cannot read as missing, and throws on a server error", async () => {
    vi.stubGlobal("fetch", async () => answer(400, '{"detail":"results.plane"}'));
    expect((await fetchCut(client, "/r")).kind).toBe("missing");
    vi.stubGlobal("fetch", async () => ({ status: 404, ok: false, arrayBuffer: async () => new ArrayBuffer(0) }));
    expect((await fetchCut(client, "/r")).kind).toBe("missing");
    vi.stubGlobal("fetch", async () => answer(500, ""));
    await expect(fetchCut(client, "/r")).rejects.toThrow(/HTTP 500/);
  });
});
