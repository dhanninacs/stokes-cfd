import { describe, expect, it } from "vitest";
import { colorAtIn, gradientCssFor, tintFor } from "../state/colormap";
import type { RunFields } from "./resultsApi";
import type { ResultsView } from "./fieldsModel";
import {
  DEFAULT_EXPORT,
  EXPORT_KEY,
  EXPORT_TICKS,
  MAX_PIXELS,
  PNG_KEYWORD,
  PNG_SIGNATURE,
  asciiJson,
  buildPngTextChunk,
  buildSidecar,
  crc32,
  exportFileName,
  exportLayout,
  exportLooks,
  exportName,
  exportPixels,
  exportRange,
  exportSettingsOf,
  insertPngText,
  layoutInputOf,
  legendBarColour,
  looksShown,
  readPngText,
  readStoredExport,
  safeNamePart,
  sidecarFileName,
  sidecarText,
  textWidth,
  writeStoredExport,
  type ExportCamera,
  type ExportView,
  type LayoutInput,
} from "./exportModel";

const DATA: RunFields = { run: "r1", time: 0, fields: [{ name: "p", units: "Pa", min: -20, max: 80 }], slice: false, surfaces: "ok" };
const VIEW: ResultsView = { field: "p", lock: null, slice: false, bands: 0 };
const EXPORT_VIEW: ExportView = { project: "pipe 4", run: "run_2", view: VIEW, data: DATA, slices: [], iso: null, streamlines: false, arrows: null };
const CAMERA: ExportCamera = { target: [0, 0.1, 0], dir: [0.6, 0.4, 0.7], span: 0.25, orthographic: false };

/** A 1x1 PNG, the smallest valid file there is (RGBA, one transparent pixel). */
const PNG_1X1 = Uint8Array.from(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64"));

function ascii(text: string): Uint8Array {
  return Uint8Array.from(Array.from(text, (c) => c.charCodeAt(0)));
}

describe("settings", () => {
  it("fills every missing or nonsensical field with its default", () => {
    expect(exportSettingsOf(undefined)).toEqual(DEFAULT_EXPORT);
    expect(exportSettingsOf({ scale: 3, background: "pink", legend: "yes", title: 5, scaleBar: null, size: { w: "a", h: 3 } })).toEqual(DEFAULT_EXPORT);
  });

  it("keeps what is valid and clamps a size into 64..16384", () => {
    const s = exportSettingsOf({ scale: 4, background: "transparent", legend: false, title: "Fig 1", scaleBar: false, size: { w: 20, h: 99999 } });
    expect(s).toEqual({ scale: 4, background: "transparent", legend: false, title: "Fig 1", scaleBar: false, size: { w: 64, h: 16384 } });
  });
});

describe("remembering", () => {
  function store(initial: Record<string, string> = {}) {
    const data = { ...initial };
    return {
      data,
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => void (data[k] = v),
      removeItem: (k: string) => void delete data[k],
    };
  }

  it("opens on the defaults with no storage, an empty one, one that throws, or text that is not ours", () => {
    const fresh = { remember: false, settings: DEFAULT_EXPORT };
    expect(readStoredExport(null)).toEqual(fresh);
    expect(readStoredExport(store())).toEqual(fresh);
    expect(readStoredExport({ getItem: () => { throw new Error("blocked"); } })).toEqual(fresh);
    expect(readStoredExport(store({ [EXPORT_KEY]: "not json" }))).toEqual(fresh);
    expect(readStoredExport(store({ [EXPORT_KEY]: JSON.stringify({ remember: false, settings: { scale: 4 } }) }))).toEqual(fresh);
  });

  it("keeps the settings but never the title, and forgets them when Remember is off", () => {
    const s = store();
    writeStoredExport(s, { remember: true, settings: { ...DEFAULT_EXPORT, scale: 4, title: "Figure 3" } });
    expect(readStoredExport(s)).toEqual({ remember: true, settings: { ...DEFAULT_EXPORT, scale: 4, title: "" } });
    writeStoredExport(s, { remember: false, settings: DEFAULT_EXPORT });
    expect(s.data[EXPORT_KEY]).toBeUndefined();
  });

  it("swallows a storage that refuses", () => {
    expect(() => writeStoredExport({ setItem: () => { throw new Error("full"); }, removeItem: () => undefined }, { remember: true, settings: DEFAULT_EXPORT })).not.toThrow();
    expect(() => writeStoredExport(null, { remember: true, settings: DEFAULT_EXPORT })).not.toThrow();
  });
});

describe("pixels", () => {
  const live = { width: 1500, height: 900 };

  it("multiplies the window by the scale, and unit is the scale", () => {
    expect(exportPixels({ scale: 1 }, live)).toEqual({ w: 1500, h: 900, unit: 1, clamped: false });
    expect(exportPixels({ scale: 4 }, live)).toEqual({ w: 6000, h: 3600, unit: 4, clamped: false });
  });

  it("takes an exact size over the scale; the unit is that size over the window's", () => {
    expect(exportPixels({ scale: 4, size: { w: 3000, h: 1800 } }, live)).toEqual({ w: 3000, h: 1800, unit: 2, clamped: false });
  });

  it("scales a frame the driver cannot draw down, keeping the aspect, and says so", () => {
    const p = exportPixels({ scale: 4 }, live, 4096);
    expect(p.clamped).toBe(true);
    expect(p.w).toBe(4096);
    expect(p.h).toBe(Math.floor(3600 * (4096 / 6000)));
    expect(p.unit).toBeCloseTo(4096 / 1500, 6);
  });

  it("limits the pixel count as well as the side", () => {
    const p = exportPixels({ scale: 1, size: { w: 16384, h: 16384 } }, live);
    expect(p.clamped).toBe(true);
    expect(p.w * p.h).toBeLessThanOrEqual(MAX_PIXELS);
  });

  it("keeps a 4x frame of an ordinary window whole (the cap of the GPU-memory review)", () => {
    expect(exportPixels({ scale: 4 }, live).clamped).toBe(false);
    expect(MAX_PIXELS).toBeLessThan(80_000_000);
  });
});

describe("file name", () => {
  it("keeps letters, digits, dot, underscore and hyphen, and trims what is left over", () => {
    expect(safeNamePart("pipe 4 / v2")).toBe("pipe-4-v2");
    expect(safeNamePart("  ..x..  ")).toBe("x");
    expect(safeNamePart("Δp résumé")).toBe("p-resume");
    expect(safeNamePart("a".repeat(80)).length).toBe(40);
    expect(safeNamePart("///")).toBe("");
  });

  it("is project-run-field-look.png, with the looks on screen joined and `view` for none", () => {
    expect(exportFileName({ project: "pipe 4", run: "run_2", field: "U_mag", looks: ["surface", "slice"] })).toBe("pipe-4-run_2-U_mag-surface_slice.png");
    expect(exportFileName({ project: "x", run: "", field: "", looks: [] })).toBe("x-view.png");
    expect(exportFileName({ project: "", run: "r", field: "p", looks: ["iso"] }, "json")).toBe("stokes-r-p-iso.json");
  });

  it("names the looks in the order of the panel", () => {
    expect(looksShown({ surface: true, slices: 2, iso: true, streamlines: true, arrows: true })).toEqual(["surface", "slice", "iso", "streamlines", "vectors"]);
    expect(looksShown({ surface: false, slices: 0, iso: false, streamlines: false, arrows: false })).toEqual([]);
  });

  it("names the picture from the view, and the sidecar after it", () => {
    expect(exportLooks(EXPORT_VIEW)).toEqual(["surface"]);
    expect(exportName(EXPORT_VIEW)).toBe("pipe-4-run_2-p-surface.png");
    expect(exportName({ ...EXPORT_VIEW, view: { ...VIEW, field: "none" }, data: null })).toBe("pipe-4-run_2-view.png");
    expect(sidecarFileName("a-b.png")).toBe("a-b.json");
  });
});

describe("sidecar", () => {
  const px = { w: 3000, h: 1800 };

  it("carries what is needed to make the figure again", () => {
    const side = buildSidecar({ ...EXPORT_VIEW, view: { ...VIEW, map: "coolwarm", reverse: true, bands: 8, format: "fixed", digits: 2, placement: "bottom", lock: { field: "p", min: -5, max: 50 } } }, CAMERA, { ...DEFAULT_EXPORT, scale: 2 }, px);
    expect(side.version).toBe(1);
    const s = side.stokes;
    expect(s.camera).toEqual(CAMERA);
    expect(s.field).toEqual({ name: "p", title: "p (Pa)", units: "Pa" });
    expect(s.range).toEqual({ min: -5, max: 50, locked: true });
    expect([s.map, s.reverse, s.bands]).toEqual(["coolwarm", true, 8]);
    expect(s.legend).toMatchObject({ format: "fixed", digits: 2, placement: "bottom", shown: true, title: "p (Pa)" });
    expect(s.image).toEqual({ width: 3000, height: 1800, scale: 2, background: "white", scaleBar: true });
  });

  it("records the range mode in force, not the stored one", () => {
    const stale = { ...VIEW, rangeMode: "manual" as const };
    expect(buildSidecar({ ...EXPORT_VIEW, view: stale }, CAMERA, DEFAULT_EXPORT, px).stokes.legend.rangeMode).toBe("auto");
    const typed = { ...stale, lock: { field: "p", min: -5, max: 50 } };
    expect(buildSidecar({ ...EXPORT_VIEW, view: typed }, CAMERA, DEFAULT_EXPORT, px).stokes.legend.rangeMode).toBe("manual");
    expect(buildSidecar({ ...EXPORT_VIEW, view: { ...typed, rangeMode: "locked" } }, CAMERA, DEFAULT_EXPORT, px).stokes.legend.rangeMode).toBe("locked");
  });

  it("is the default look for a view that predates the legend settings", () => {
    const s = buildSidecar(EXPORT_VIEW, CAMERA, DEFAULT_EXPORT, px).stokes;
    expect([s.map, s.reverse, s.bands, s.legend.format, s.legend.placement]).toEqual(["viridis", false, 0, "auto", "right"]);
    expect(s.range).toEqual({ min: -20, max: 80, locked: false });
  });

  it("has no field and no range with nothing coloured, and a size has no scale", () => {
    const s = buildSidecar({ ...EXPORT_VIEW, view: { ...VIEW, field: "none" }, data: null }, CAMERA, { ...DEFAULT_EXPORT, size: { w: 800, h: 600 } }, { w: 800, h: 600 }).stokes;
    expect(s.range).toBeNull();
    expect(s.field.name).toBe("none");
    expect(s.legend.shown).toBe(false);
    expect(s.image.scale).toBeNull();
  });

  it("writes non-finite numbers as null and is plain JSON", () => {
    const side = buildSidecar(EXPORT_VIEW, { ...CAMERA, span: NaN, target: [Infinity, 0, 0] }, DEFAULT_EXPORT, px);
    expect(side.stokes.camera.span).toBe(0);
    expect(JSON.parse(sidecarText(side))).toEqual(JSON.parse(JSON.stringify(side)));
    expect(sidecarText(side).endsWith("}\n")).toBe(true);
  });

  it("lists the slices that are on, field by field, and the iso value", () => {
    const side = buildSidecar(
      {
        ...EXPORT_VIEW,
        slices: [{ id: "s1", origin: "axis", plane: { origin: [0, 0, 0], normal: [0, 0, 1] } as never, visible: true, field: "follow", fill: true, lines: false, bands: false, levels: 8, lineColour: "map" }],
        iso: { field: "p", value: 12.5, units: "Pa" },
      },
      CAMERA,
      DEFAULT_EXPORT,
      px,
    );
    expect(side.stokes.looks.slices).toHaveLength(1);
    expect(side.stokes.looks.slices[0]).toMatchObject({ id: "s1", fill: true, lineColour: "map" });
    expect(side.stokes.looks.iso).toEqual({ field: "p", value: 12.5, units: "Pa" });
  });
});

describe("PNG text chunk", () => {
  it("has the CRC-32 PNG defines: the check value, and the real IEND and IHDR chunks", () => {
    expect(crc32(ascii("123456789"))).toBe(0xcbf43926);
    expect(crc32(ascii("IEND"))).toBe(0xae426082);
    // IHDR of PNG_1X1: type + 13 data bytes, CRC at bytes 29..32.
    const view = new DataView(PNG_1X1.buffer, PNG_1X1.byteOffset);
    expect(crc32(PNG_1X1.subarray(12, 29))).toBe(view.getUint32(29));
  });

  it("is length, `tEXt`, keyword, NUL, text, CRC", () => {
    const chunk = buildPngTextChunk("stokes:view", '{"a":1}');
    const view = new DataView(chunk.buffer);
    expect(view.getUint32(0)).toBe("stokes:view".length + 1 + 7);
    expect(String.fromCharCode(...chunk.subarray(4, 8))).toBe("tEXt");
    expect(String.fromCharCode(...chunk.subarray(8, 8 + 11))).toBe("stokes:view");
    expect(chunk[8 + 11]).toBe(0);
    expect(String.fromCharCode(...chunk.subarray(8 + 12, chunk.length - 4))).toBe('{"a":1}');
    expect(view.getUint32(chunk.length - 4)).toBe(crc32(chunk.subarray(4, chunk.length - 4)));
    expect(chunk.length).toBe(12 + view.getUint32(0));
  });

  it("refuses a keyword or a text the format cannot hold", () => {
    expect(() => buildPngTextChunk("", "x")).toThrow(RangeError);
    expect(() => buildPngTextChunk("k".repeat(80), "x")).toThrow(RangeError);
    expect(() => buildPngTextChunk(" lead", "x")).toThrow(RangeError);
    expect(() => buildPngTextChunk("k", "Δ")).toThrow(RangeError);
    expect(() => buildPngTextChunk("k", "a\0b")).toThrow(RangeError);
  });

  it("is inserted straight after IHDR, and the file is still a PNG that ends in IEND", () => {
    const out = insertPngText(PNG_1X1, buildPngTextChunk(PNG_KEYWORD, "{}"));
    expect(Array.from(out.subarray(0, 8))).toEqual([...PNG_SIGNATURE]);
    expect(String.fromCharCode(...out.subarray(12, 16))).toBe("IHDR");
    expect(String.fromCharCode(...out.subarray(37, 41))).toBe("tEXt");
    expect(out.length).toBe(PNG_1X1.length + buildPngTextChunk(PNG_KEYWORD, "{}").length);
    expect(Array.from(out.subarray(out.length - 12))).toEqual(Array.from(PNG_1X1.subarray(PNG_1X1.length - 12)));
    expect(Array.from(out.subarray(0, 33))).toEqual(Array.from(PNG_1X1.subarray(0, 33)));
  });

  it("refuses bytes that are not a PNG", () => {
    expect(() => insertPngText(ascii("GIF89a..........................................."), buildPngTextChunk("k", "v"))).toThrow(RangeError);
    expect(() => insertPngText(new Uint8Array(3), buildPngTextChunk("k", "v"))).toThrow(RangeError);
  });

  it("carries the sidecar through a read-back, including the characters Latin-1 cannot hold", () => {
    const side = buildSidecar({ ...EXPORT_VIEW, data: { ...DATA, fields: [{ name: "p", units: "µPa Δ", min: -20, max: 80 }] } }, CAMERA, DEFAULT_EXPORT, { w: 10, h: 10 });
    const out = insertPngText(PNG_1X1, buildPngTextChunk(PNG_KEYWORD, asciiJson(side)));
    const text = readPngText(out, PNG_KEYWORD);
    expect(text).not.toBeNull();
    expect(/^[\x20-\x7e]*$/.test(text as string)).toBe(true);
    expect(JSON.parse(text as string)).toEqual(JSON.parse(JSON.stringify(side)));
    expect(readPngText(out, "other")).toBeNull();
    expect(readPngText(PNG_1X1, PNG_KEYWORD)).toBeNull();
  });

  it("escapes only above ASCII", () => {
    expect(asciiJson({ a: "Δp µ ~", b: 'q"' })).toBe('{"a":"\\u0394p \\u00b5 ~","b":"q\\""}');
    expect(JSON.parse(asciiJson({ a: "Δp" }))).toEqual({ a: "Δp" });
  });
});

describe("legend bar colours", () => {
  const range = { min: -20, max: 80 };

  it("agree with the surface's tint at the corners of the bar", () => {
    for (const map of ["viridis", "magma", "coolwarm"] as const) {
      for (const reverse of [false, true]) {
        const look = { map, reverse, bands: 0 };
        const tint = tintFor([range.min, range.max], range.min, range.max, 0, map, reverse);
        expect(legendBarColour(0, look, range)).toEqual([tint[0], tint[1], tint[2]]);
        expect(legendBarColour(1, look, range)).toEqual([tint[4], tint[5], tint[6]]);
      }
    }
  });

  it("agree with the surface's tint all the way along, diverging map included", () => {
    const look = { map: "coolwarm" as const, reverse: false, bands: 0 };
    for (let i = 0; i <= 20; i++) {
      const p = i / 20;
      const v = range.min + p * (range.max - range.min);
      const tint = tintFor([v], range.min, range.max, 0, "coolwarm", false);
      expect(legendBarColour(p, look, range)).toEqual([tint[0], tint[1], tint[2]]);
    }
  });

  it("puts the neutral colour of a diverging map where zero is, as the CSS gradient does", () => {
    const look = { map: "coolwarm" as const, reverse: false, bands: 0 };
    const css = gradientCssFor("coolwarm", false, "to top", range);
    const at = /rgb\((\d+) (\d+) (\d+)\) 20\.00%/.exec(css);
    expect(at).not.toBeNull();
    expect(legendBarColour(0.2, look, range)).toEqual([Number(at?.[1]), Number(at?.[2]), Number(at?.[3])]);
    const middle = colorAtIn("coolwarm", 0.5).map(Math.round);
    expect(legendBarColour(0.2, look, range)).toEqual(middle);
  });

  it("is flat within a band and steps between bands, as the surface is", () => {
    const look = { map: "viridis" as const, reverse: false, bands: 4 };
    const run = [0.01, 0.2, 0.24].map((p) => legendBarColour(p, look, { min: 0, max: 1 }));
    expect(run[1]).toEqual(run[0]);
    expect(run[2]).toEqual(run[0]);
    expect(legendBarColour(0.26, look, { min: 0, max: 1 })).not.toEqual(run[0]);
    const tint = tintFor([0.1, 0.9], 0, 1, 4, "viridis", false);
    expect(legendBarColour(0.1, look, { min: 0, max: 1 })).toEqual([tint[0], tint[1], tint[2]]);
    expect(legendBarColour(0.9, look, { min: 0, max: 1 })).toEqual([tint[4], tint[5], tint[6]]);
  });

  it("clamps p and treats a missing one as the low end", () => {
    const look = { map: "viridis" as const, reverse: false, bands: 0 };
    expect(legendBarColour(-3, look, range)).toEqual(legendBarColour(0, look, range));
    expect(legendBarColour(9, look, range)).toEqual(legendBarColour(1, look, range));
    expect(legendBarColour(NaN, look, range)).toEqual(legendBarColour(0, look, range));
  });
});

describe("layout", () => {
  const base: LayoutInput = {
    width: 1500,
    height: 900,
    unit: 1,
    placement: "right",
    legend: true,
    legendTitle: "p (Pa)",
    labels: ["80", "55", "30", "5", "-20"],
    title: "",
    scaleBar: true,
  };
  const inside = (r: { x: number; y: number; w: number; h: number }, w: number, h: number) =>
    r.x >= 0 && r.y >= 0 && r.x + r.w <= w + 1e-9 && r.y + r.h <= h + 1e-9;

  it("puts the legend in a strip at the right and draws the model in the rest", () => {
    const l = exportLayout(base);
    expect(l.legend).not.toBeNull();
    const box = l.legend!.box;
    expect(l.render).toEqual({ x: 0, y: 0, w: 1500 - box.w, h: 900 });
    expect(box.x).toBe(l.render.w);
    expect(box.x + box.w).toBe(1500);
    expect(l.legend!.panel).toBe(false);
    expect(l.legend!.vertical).toBe(true);
    // The strip holds the widest tick and the bar, and it is not most of the picture.
    expect(box.w).toBeLessThanOrEqual(1500 * 0.35);
    expect(box.w).toBeGreaterThanOrEqual(l.pad * 2 + l.barThickness + l.gap + textWidth("-20", l.fonts.label));
  });

  it("lists the ticks from the top of the bar to the bottom, evenly, beside the bar", () => {
    const l = exportLayout(base).legend!;
    expect(l.ticks.map((t) => t.text)).toEqual(base.labels);
    const ys = l.ticks.map((t) => t.y);
    for (let i = 1; i < ys.length; i++) expect(ys[i]).toBeGreaterThan(ys[i - 1]);
    expect(ys[0]).toBe(l.bar.y);
    expect(ys[4]).toBeCloseTo(l.bar.y + l.bar.h, 6);
    expect(ys[2] - ys[1]).toBeCloseTo(ys[1] - ys[0], 6);
    for (const t of l.ticks) expect(t.x).toBe(l.bar.x + l.bar.w + exportLayout(base).gap);
    expect(inside(l.bar, 1500, 900)).toBe(true);
  });

  it("puts a bottom legend in a strip under the model with the bar reading low to high", () => {
    const l = exportLayout({ ...base, placement: "bottom" });
    const lg = l.legend!;
    expect(lg.vertical).toBe(false);
    expect(l.render).toEqual({ x: 0, y: 0, w: 1500, h: 900 - lg.box.h });
    expect(lg.box.y).toBe(l.render.h);
    expect(lg.ticks.map((t) => t.text)).toEqual([...base.labels].reverse());
    expect(lg.ticks[0].x).toBe(lg.bar.x);
    expect(lg.ticks[4].x).toBeCloseTo(lg.bar.x + lg.bar.w, 6);
    expect(lg.bar.x * 2 + lg.bar.w).toBe(1500);
    expect(inside(lg.box, 1500, 900) && inside(lg.bar, 1500, 900)).toBe(true);
  });

  it("floats the legend over the model's top left, on a panel, and leaves the whole picture to the model", () => {
    const l = exportLayout({ ...base, placement: "floating" });
    expect(l.render).toEqual({ x: 0, y: 0, w: 1500, h: 900 });
    const lg = l.legend!;
    expect(lg.panel).toBe(true);
    expect(lg.box.x).toBe(l.pad);
    expect(lg.box.y).toBe(l.pad);
    expect(inside(lg.box, 1500, 900)).toBe(true);
    expect(lg.bar.y + lg.bar.h).toBeLessThanOrEqual(lg.box.y + lg.box.h);
  });

  it("takes the title's strip from the top and centres the text in it", () => {
    const l = exportLayout({ ...base, title: "  Pressure on the mid-plane  " });
    expect(l.title).not.toBeNull();
    expect(l.title!.text).toBe("Pressure on the mid-plane");
    expect(l.title!.box).toEqual({ x: 0, y: 0, w: 1500, h: l.render.y });
    expect(l.render.y).toBeGreaterThan(0);
    expect(l.render.y + l.render.h).toBe(900);
    expect(l.title!.x).toBe(750);
    expect(l.title!.y).toBe(l.render.y / 2);
    // The legend's strip starts below the title, so the two never overlap.
    expect(l.legend!.box.y).toBe(l.render.y);
  });

  it("has no title strip for an empty or blank title", () => {
    expect(exportLayout({ ...base, title: "   " }).title).toBeNull();
    expect(exportLayout(base).render.y).toBe(0);
  });

  it("draws no legend when asked not to, with fewer than two ticks, or with none", () => {
    for (const input of [{ ...base, legend: false }, { ...base, labels: ["1"] }, { ...base, labels: [] }]) {
      const l = exportLayout(input);
      expect(l.legend).toBeNull();
      expect(l.render).toEqual({ x: 0, y: 0, w: 1500, h: 900 });
    }
  });

  it("anchors the scale bar at the bottom left of the model's rectangle, inside it", () => {
    const l = exportLayout({ ...base, placement: "bottom", title: "T" });
    expect(l.scaleBar).toEqual({ x: l.render.x + l.pad, y: l.render.y + l.render.h - l.pad });
    expect(exportLayout({ ...base, scaleBar: false }).scaleBar).toBeNull();
  });

  it("grows fonts, the bar and the padding with the unit, and keeps the print-size ratios", () => {
    const one = exportLayout(base);
    const four = exportLayout({ ...base, width: 6000, height: 3600, unit: 4 });
    expect(one.fonts.label).toBe(Math.round(11 * 1.5));
    expect(one.barThickness).toBe(Math.round(14 * 1.6));
    expect(four.fonts.label).toBe(Math.round(11 * 1.5 * 4));
    expect(four.barThickness).toBe(Math.round(14 * 4 * 1.6));
    expect(four.pad).toBe(one.pad * 4);
  });

  it("truncates a long heading to the strip rather than widening it", () => {
    const l = exportLayout({ ...base, legendTitle: "A very long field title (with units and more words) ".repeat(4) });
    expect(l.legend!.box.w).toBeLessThanOrEqual(1500 * 0.35);
    expect(l.legend!.title.maxWidth).toBe(l.legend!.box.w - 2 * l.pad);
  });

  it("is the same picture for every placement in its parts: every rectangle inside it", () => {
    for (const placement of ["right", "bottom", "floating"] as const) {
      for (const [w, h] of [[1500, 900], [800, 800], [600, 1200]]) {
        const l = exportLayout({ ...base, placement, width: w, height: h, title: "T" });
        expect(inside(l.render, w, h)).toBe(true);
        expect(inside(l.legend!.box, w, h)).toBe(true);
        expect(l.render.w).toBeGreaterThan(0);
        expect(l.render.h).toBeGreaterThan(0);
      }
    }
  });
});

describe("layout input from a view", () => {
  const px = { w: 3000, h: 1800, unit: 2, clamped: false };

  it("reads the placement, the heading and the six ticks from the view", () => {
    const input = layoutInputOf({ ...EXPORT_VIEW, view: { ...VIEW, placement: "bottom", format: "fixed", digits: 1 } }, DEFAULT_EXPORT, px);
    expect(input.placement).toBe("bottom");
    expect(input.legendTitle).toBe("p (Pa)");
    expect(input.labels).toEqual(["80.0", "60.0", "40.0", "20.0", "0.0", "-20.0"]);
    expect(input.labels).toHaveLength(EXPORT_TICKS);
    expect(input.unit).toBe(2);
    expect(input.legend).toBe(true);
  });

  it("has no legend with the switch off or nothing coloured", () => {
    expect(layoutInputOf(EXPORT_VIEW, { ...DEFAULT_EXPORT, legend: false }, px).legend).toBe(false);
    const none = layoutInputOf({ ...EXPORT_VIEW, view: { ...VIEW, field: "none" }, data: null }, DEFAULT_EXPORT, px);
    expect(none.legend).toBe(false);
    expect(none.labels).toEqual([]);
    expect(exportRange({ ...EXPORT_VIEW, view: { ...VIEW, field: "none" } })).toBeNull();
  });
});
