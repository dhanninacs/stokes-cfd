import { describe, expect, it } from "vitest";
import { addSlice, NO_SLICES, updateSlice } from "../panels/sliceModel";
import {
  contourLinesOn,
  defaultSurfaces,
  fieldChoices,
  formatValue,
  functionNameProblem,
  histogramChart,
  linesFor,
  monitorLabel,
  monitorSeries,
  reportCsv,
  reportTable,
  settled,
  toggleSurface,
  withContourLines,
  xScaleFor,
  xyChart,
  type IntegralsReply,
  type SurfaceInfo,
} from "./analyseModel";

const QUANTITIES = ["area", "area_avg", "mass_avg", "flow_rate", "mass_flow", "uniformity_area"];

function reply(rows: { surface: string; area: number; flow: number }[], massFlow: number | null = null): IntegralsReply {
  return {
    run: "r_1",
    field: "p",
    field_units: "m2/s2",
    density: massFlow === null ? null : 1000,
    quantities: QUANTITIES,
    units: { area: "m2", area_avg: "m2/s2", mass_avg: "m2/s2", flow_rate: "m3/s", mass_flow: "kg/s", uniformity_area: "" },
    rows: rows.map((r) => ({ surface: r.surface, id: r.surface, area: r.area, area_avg: 0.15, mass_avg: 0.15, flow_rate: r.flow, mass_flow: massFlow, uniformity_area: 0.98 })),
    net: { area: rows.reduce((a, r) => a + r.area, 0), area_avg: 0.15, mass_avg: 0.15, flow_rate: rows.reduce((a, r) => a + r.flow, 0), mass_flow: null, uniformity_area: null },
  };
}

describe("the report table", () => {
  it("has a column per surface and a Net column only for several", () => {
    expect(reportTable(reply([{ surface: "inlet", area: 0.0028, flow: -0.00564 }])).columns).toEqual(["inlet"]);
    const two = reportTable(reply([
      { surface: "inlet", area: 0.0028, flow: -0.00564 },
      { surface: "outlet", area: 0.0028, flow: 0.00564 },
    ]));
    expect(two.columns).toEqual(["inlet", "outlet", "Net"]);
    const flow = two.rows.find((r) => r.key === "flow_rate")!;
    expect(flow.label).toBe("Volume flow rate");
    expect(flow.units).toBe("m3/s");
    expect(flow.cells).toEqual(["-0.00564", "0.00564", "0"]);
  });

  it("leaves out a row with no value anywhere (mass flow without a density)", () => {
    const table = reportTable(reply([{ surface: "inlet", area: 1, flow: 1 }]));
    expect(table.rows.map((r) => r.key)).not.toContain("mass_flow");
    expect(reportTable(reply([{ surface: "inlet", area: 1, flow: 1 }], 1000)).rows.map((r) => r.key)).toContain("mass_flow");
  });

  it("writes CSV at full precision with units and the field", () => {
    const text = reportCsv(reply([{ surface: "in,let", area: 0.00282236787, flow: -0.0056447 }]));
    const lines = text.trim().split("\n");
    expect(lines[0]).toBe("# field: p");
    expect(lines[1]).toBe('quantity,units,"in,let"');
    expect(lines).toContain("area,m2,0.00282236787");
    expect(lines).toContain("mass_flow,kg/s,");
  });
});

describe("values and choices", () => {
  it("formats a value as a person reads it", () => {
    expect(formatValue(null)).toBe("–");
    expect(formatValue(0)).toBe("0");
    expect(formatValue(2.0556974)).toBe("2.0557");
    expect(formatValue(0.000123456)).toBe("1.2346e-4");
    expect(formatValue(Number.NaN)).toBe("–");
  });

  it("starts a report on the inlets and outlets", () => {
    const surfaces: SurfaceInfo[] = [
      { id: "n_1", name: "walls", kind: "patch", points: 1, triangles: 1, area: 1 },
      { id: "n_2", name: "inlet", kind: "patch", points: 1, triangles: 1, area: 1 },
      { id: "n_3", name: "outlet_main", kind: "patch", points: 1, triangles: 1, area: 1 },
      { id: "slice", name: "slice", kind: "slice", points: 1, triangles: 1, area: 1 },
    ];
    expect(defaultSurfaces(surfaces)).toEqual(["n_2", "n_3"]);
    expect(defaultSurfaces(surfaces.slice(0, 1))).toEqual(["n_1"]);
    expect(defaultSurfaces([])).toEqual([]);
  });

  it("toggles a surface but never the last one off", () => {
    expect(toggleSurface(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleSurface(["a", "b"], "a")).toEqual(["b"]);
    expect(toggleSurface(["a"], "a")).toEqual(["a"]);
  });

  it("lists scalars, a vector's components, then functions", () => {
    const choices = fieldChoices(
      [
        { name: "U", units: "m/s", components: 3 },
        { name: "U_mag", units: "m/s", components: 1 },
        { name: "p", units: "m2/s2", components: 1 },
      ],
      [{ name: "p_total", definition: "p + 0.5*U_mag^2" }],
    );
    expect(choices.map((c) => c.value)).toEqual(["U_x", "U_y", "U_z", "U_mag", "p", "p_total"]);
    expect(choices[3]!.label).toBe("U_mag (m/s)");
  });

  it("checks a function name as the server will", () => {
    expect(functionNameProblem("p_total")).toBeNull();
    expect(functionNameProblem(" ")).toBe("Give the function a name");
    expect(functionNameProblem("2p")).not.toBeNull();
    expect(functionNameProblem("rho")).toBe("rho is already given");
  });
});

describe("charts", () => {
  it("draws several series on one pair of axes, metres in mm", () => {
    const chart = xyChart(
      [
        { x: [0, 0.01, 0.02, 0.03], y: [2.2, 2.1, 1.9, 0] },
        { x: [0, 0.03], y: [2, 0.5] },
      ],
      300,
      150,
      "m",
      "dots",
    );
    expect(chart.xUnit).toBe("mm");
    expect(chart.paths).toHaveLength(2);
    expect(chart.paths[0]!.match(/M/g)).toHaveLength(4);
    expect(chart.xTicks.length).toBeGreaterThan(1);
    expect(chart.yTicks.every((t) => t.at >= chart.y0 - 1 && t.at <= chart.y1 + 1)).toBe(true);
  });

  it("joins a history as one line and draws nothing for no data", () => {
    const chart = xyChart([{ x: [1, 2, 3], y: [3, 2, 1] }], 300, 150, "", "line");
    expect(chart.paths[0]!.startsWith("M")).toBe(true);
    expect(chart.paths[0]!.match(/L/g)).toHaveLength(2);
    expect(xyChart([{ x: [], y: [] }], 300, 150, "", "line").paths).toEqual([""]);
  });

  it("scales metres to mm only below a metre", () => {
    expect(xScaleFor("m", 0.03)).toEqual({ unit: "mm", scale: 1e-3 });
    expect(xScaleFor("m", 4)).toEqual({ unit: "m", scale: 1 });
    expect(xScaleFor("", 4)).toEqual({ unit: "", scale: 1 });
  });

  it("draws a bar per bin, its height the share of the area", () => {
    const chart = histogramChart({ field: "U_mag", units: "m/s", edges: [0, 1, 2], area: [1, 3], fraction: [0.25, 0.75], min: 0, max: 2, total: 4 }, 300, 150);
    expect(chart.bars).toHaveLength(2);
    expect(chart.bars[1]!.h).toBeCloseTo(chart.bars[0]!.h * 3, 5);
    expect(chart.bars[1]!.label).toBe("1 to 2: 75.0 %");
    expect(histogramChart({ field: "", units: "", edges: [1], area: [], fraction: [], min: 0, max: 0, total: 0 }, 300, 150).bars).toEqual([]);
  });
});

describe("history", () => {
  it("names the solver's report files", () => {
    expect(monitorLabel("patchFlowRate(patch=inlet)/0/surfaceFieldValue.dat")).toEqual({ label: "Flow rate, inlet", units: "m3/s" });
    expect(monitorLabel("patchAverage(patch=outlet,fields=(p))/0/surfaceFieldValue.dat")).toEqual({ label: "Mean p, outlet", units: "m2/s2" });
    expect(monitorLabel("forces/0/force.dat").label).toBe("forces");
  });

  it("reads a table's first value column against its first column", () => {
    expect(monitorSeries({ columns: ["Time", "sum(phi)"], values: { Time: [1, 2], "sum(phi)": [-1, -2] } })).toEqual({ x: [1, 2], y: [-1, -2], column: "sum(phi)" });
    expect(monitorSeries({ columns: ["Time"], values: { Time: [1] } })).toBeNull();
  });

  it("says whether a report has settled over its last tenth", () => {
    const flat = Array.from({ length: 100 }, () => 2);
    expect(settled(flat)).toEqual({ last: 2, change: 0 });
    const moving = Array.from({ length: 100 }, (_, i) => i + 1);
    expect(settled(moving)!.change).toBeGreaterThan(5);
    expect(settled([1, 2])!.change).toBeNull();
    expect(settled([])).toBeNull();
  });
});

describe("contour lines on the slices", () => {
  const plane = { origin: [0, 0, 0] as [number, number, number], normal: [1, 0, 0] as [number, number, number] };

  it("is null with no slice on screen, and turns every visible slice's lines on at the band edges", () => {
    expect(contourLinesOn(NO_SLICES)).toBeNull();
    const one = addSlice(NO_SLICES, plane);
    expect(contourLinesOn(one)).toBe(false);
    const on = withContourLines(one, true, 8);
    expect(contourLinesOn(on)).toBe(true);
    expect(on.list[0]).toMatchObject({ lines: true, levels: 8, lineColour: "neutral", bands: true });
    expect(withContourLines(one, true, 0).list[0]!.levels).toBe(10);
    expect(contourLinesOn(withContourLines(on, false, 8))).toBe(false);
  });

  it("leaves a hidden slice alone", () => {
    const one = addSlice(NO_SLICES, plane);
    const hidden = updateSlice(one, one.list[0]!.id, { visible: false });
    expect(withContourLines(hidden, true, 8)).toBe(hidden);
  });
});

describe("lines over the run's own mid-plane", () => {
  it("adds an axis slice at the mid-plane with lines and turns the mid-plane off", () => {
    const out = linesFor(NO_SLICES, true, { axis: "x", offset: 0.0005 }, true, 8);
    expect(out.midOff).toBe(true);
    expect(out.slices.list).toHaveLength(1);
    expect(out.slices.list[0]).toMatchObject({ lines: true, levels: 8, visible: true });
    expect(out.slices.list[0]!.plane.normal).toEqual([1, 0, 0]);
  });

  it("does nothing new with no slice at all, and only toggles a listed one", () => {
    expect(linesFor(NO_SLICES, false, null, true, 8)).toEqual({ slices: NO_SLICES, midOff: false });
    const plane = { origin: [0, 0, 0] as [number, number, number], normal: [0, 1, 0] as [number, number, number] };
    const listed = addSlice(NO_SLICES, plane);
    const out = linesFor(listed, true, { axis: "x", offset: 0 }, true, 16);
    expect(out.midOff).toBe(false);
    expect(out.slices.list).toHaveLength(1);
  });
});
