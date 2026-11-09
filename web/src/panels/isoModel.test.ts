import { describe, expect, it } from "vitest";
import {
  ISO_CODES,
  NO_ISO,
  hideIso,
  isoFields,
  isoKey,
  isoRefusalText,
  isoRoute,
  isoStateText,
  isoValueParam,
  isoViewportText,
  middleValue,
  readIsoEvent,
  readIsoReply,
  sameSurface,
  setIsoVisible,
  showIso,
  type IsoSummary,
} from "./isoModel";
import type { RunFields } from "./resultsApi";

const ENERGY: RunFields = {
  run: "r_e",
  time: 300,
  slice: true,
  surfaces: "ok",
  fields: [
    { name: "U_mag", units: "m/s", min: 0, max: 1.2 },
    { name: "p", units: "Pa", min: 100000, max: 100020 },
    { name: "T", units: "K", min: 293.15, max: 353.15 },
  ],
};

describe("the contourable fields", () => {
  it("offers speed, T then p with their units and ranges, and nothing else", () => {
    expect(isoFields(ENERGY).map((f) => `${f.name} ${f.units}`)).toEqual(["U_mag m/s", "T K", "p Pa"]);
    expect(isoFields(null)).toEqual([]);
    expect(isoFields({ ...ENERGY, surfaces: "missing" })).toEqual([]);
  });

  it("starts the value at the middle of the range", () => {
    expect(middleValue({ name: "T", units: "K", min: 293.15, max: 353.15 })).toBe("323.1");
  });
});

describe("what a typed value is sent as", () => {
  it("sends a plain number as a number (SI, the unit shown) and anything else as quantity text", () => {
    expect(isoValueParam(" 300 ")).toBe(300);
    expect(isoValueParam("1e5")).toBe(100000);
    expect(isoValueParam("27 degC")).toBe("27 degC");
    expect(isoValueParam("300 [K]")).toBe("300 [K]");
    expect(isoValueParam("")).toBeNull();
  });
});

describe("the reply and the events", () => {
  it("reads a ready reply with its triangle count, and a cutting one", () => {
    const ready = readIsoReply({ run: "r_e", field: "T", value: 300, units: "K", state: "ready", triangles: 12345, points: 7000, range: { min: 293, max: 353 }, key: "k", result: "/x" });
    expect(ready).toEqual({ run: "r_e", field: "T", value: 300, units: "K", state: "ready", triangles: 12345 });
    expect(isoStateText(ready!)).toBe("Ready: T = 300 K, 12,345 triangles");
    const cutting = readIsoReply({ run: "r_e", field: "T", value: 300, units: "K", state: "cutting" });
    expect(cutting?.state).toBe("cutting");
    expect(isoStateText(cutting!)).toBe("Cutting T = 300 K on the run's host…");
    expect(readIsoReply({ run: "r_e", state: "odd", field: "T", value: 1 })).toBeNull();
    expect(readIsoReply(null)).toBeNull();
  });

  it("reads run.iso_ready and run.iso_failed, and ignores other events", () => {
    const ready = readIsoEvent("run.iso_ready", { run_id: "r_e", field: "T", value: 300, units: "K", state: "ready", triangles: 9 });
    expect(ready).toEqual({ kind: "ready", summary: { run: "r_e", field: "T", value: 300, units: "K", state: "ready", triangles: 9 } });
    const failed = readIsoEvent("run.iso_failed", { run_id: "r_e", field: "T", value: 300, code: ISO_CODES.host, message: "gone" });
    expect(failed).toEqual({ kind: "failed", run: "r_e", field: "T", value: 300, code: ISO_CODES.host, message: "gone" });
    expect(readIsoEvent("run.residual", { run_id: "r_e" })).toBeNull();
  });

  it("matches a surface by run, field and value", () => {
    const a = { run: "r", field: "T", value: 300 };
    expect(sameSurface(a, { ...a, value: 300 + 1e-10 })).toBe(true);
    expect(sameSurface(a, { ...a, value: 301 })).toBe(false);
    expect(sameSurface(a, { ...a, field: "p" })).toBe(false);
  });
});

describe("the surface in the viewport", () => {
  const ready = (value: number, triangles = 10): IsoSummary => ({ run: "r_e", field: "T", value, units: "K", state: "ready", triangles });

  it("shows a ready surface, replaces it with a new one, and hides it", () => {
    const first = showIso(NO_ISO, ready(300));
    expect(first).toEqual({ surface: ready(300), visible: true });
    // The same surface again is the same view: nothing is fetched twice.
    expect(showIso(first, ready(300))).toBe(first);
    const second = showIso(first, ready(310, 20));
    expect(second.surface?.value).toBe(310);
    expect(hideIso(second)).toEqual(NO_ISO);
    expect(hideIso(NO_ISO)).toBe(NO_ISO);
  });

  it("leaves a cutting answer alone and clears for an empty one", () => {
    const first = showIso(NO_ISO, ready(300));
    expect(showIso(first, { ...ready(310), state: "cutting" })).toBe(first);
    expect(showIso(first, { ...ready(310), state: "empty" })).toEqual(NO_ISO);
  });

  it("switches visibility without dropping the surface, and Show makes it visible again", () => {
    const hidden = setIsoVisible(showIso(NO_ISO, ready(300)), false);
    expect(hidden).toEqual({ surface: ready(300), visible: false });
    expect(setIsoVisible(hidden, false)).toBe(hidden);
    expect(showIso(hidden, ready(300)).visible).toBe(true);
  });

  it("names the route, the key and the viewport line", () => {
    expect(isoRoute(ready(300))).toBe("/api/results/iso/r_e?field=T&value=300");
    expect(isoKey(ready(300))).toBe("r_e|T|300");
    expect(isoViewportText(ready(300), 12345)).toBe("iso T = 300 K, 12,345 triangles");
  });
});

describe("the refusals", () => {
  it("says each refused case in words", () => {
    expect(isoRefusalText(ISO_CODES.outOfRange, "500 K is outside the range of T, 293.15 to 353.15 K")).toBe("Out of range: 500 K is outside the range of T, 293.15 to 353.15 K.");
    expect(isoRefusalText(ISO_CODES.tooLarge, "")).toMatch(/^Too large to show/);
    expect(isoRefusalText(ISO_CODES.host, "")).toBe("Host unavailable: the surface is not cached and the run's host cannot cut it.");
    expect(isoRefusalText("results.iso_invalid", "this run has no field 'T'")).toBe("this run has no field 'T'");
  });
});
