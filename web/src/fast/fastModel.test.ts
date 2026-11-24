import { describe, expect, it } from "vitest";
import { AUTOMATIC_PLANE, FAST_FIELDS, FIDELITIES, PLANES, STORAGES, TURBULENCE, fieldsFor, resolutionWarning, flowText, heatText, massFlowText, streamRows, zonesLine, frameKey, frameWarning, gridNotes, legendOf, planeOffset, planeShare, pressureText, previewRuns, readFrame, readStatus, statsLines } from "./fastModel";

const FRAME = {
  step: 40000,
  mlups: 2412.6,
  dp: 0.00208,
  dp_pa: 2.08,
  inlet_speed: 0.05,
  flow_in: 2.0e-5,
  flow_out: 1.997e-5,
  imbalance: -0.0015,
  guard: 0,
  unstable: false,
  steady: true,
  restrictive: false,
  geometry_version: 3,
  axis: 1,
  plane: 17,
  vertices: 2944,
};
const GRID = { fidelity: 3, cells: 1_234_000, fluid_cells: 987_000, nx: 100, ny: 36, nz: 36, origin: [-0.03125, -0.01125, -0.01125], dx: 0.000625, coarsened: false };

describe("readFrame and readStatus read what the server sends", () => {
  it("reads a fast.frame event", () => {
    const frame = readFrame(FRAME);
    expect(frame).toMatchObject({ step: 40000, dpPa: 2.08, inletSpeed: 0.05, flowIn: 2.0e-5, flowOut: 1.997e-5, imbalance: -0.0015, steady: true, axis: 1, plane: 17 });
    expect(frameKey(frame!)).toBe("3:1:17");
  });

  it("is null for something that is not a frame", () => {
    expect(readFrame(null)).toBeNull();
    expect(readFrame({ mlups: 3 })).toBeNull();
  });

  it("reads a frame of a server that sends no flow balance as zero", () => {
    const { flow_in: _in, flow_out: _out, imbalance: _im, ...old } = FRAME;
    expect(readFrame(old)).toMatchObject({ flowIn: 0, flowOut: 0, imbalance: 0 });
  });

  it("reads a status with its grid, model and adapter", () => {
    const status = readStatus({
      state: "running",
      fidelity: 2,
      grid: GRID,
      les: true,
      cell_reynolds: 31.25,
      density: 998.2,
      automatic_plane: false,
      adapter: { name: "NVIDIA RTX 4000 Ada Generation" },
      frame: FRAME,
    });
    expect(status).toMatchObject({ state: "running", fidelity: 3, requested: 2, cells: 1_234_000, fluidCells: 987_000, les: true, cellReynolds: 31.25, density: 998.2, automaticPlane: false, coarsened: false });
    expect(status.spans).toEqual([
      { origin: -0.03125, cell: 0.000625, count: 100 },
      { origin: -0.01125, cell: 0.000625, count: 36 },
      { origin: -0.01125, cell: 0.000625, count: 36 },
    ]);
    expect(status.adapter).toContain("RTX");
    expect(status.frame?.step).toBe(40000);
  });

  it("reads off and a failure with its message", () => {
    expect(readStatus({ state: "off" })).toMatchObject({ state: "off", fidelity: null, frame: null, spans: null, density: null, automaticPlane: true });
    expect(readStatus({ state: "failed", error: { code: "fast.unavailable", message: "no GPU" } }).error).toBe("no GPU");
    expect(readStatus({ state: "bogus" }).state).toBe("off");
  });
});

describe("the panel's words", () => {
  it("says what runs, how fast, and what it found", () => {
    const lines = statsLines(readStatus({ state: "running", grid: GRID, les: true, frame: FRAME }));
    expect(lines).toEqual([
      "Fidelity 3 · 987 k cells · turbulent preview (LES)",
      "2,413 M cell updates/s · step 40,000",
      "Pressure drop 2.08 Pa",
      "Flow in 20 mL/s · inlet 0.05 m/s",
      "Flow out 20 mL/s (-0.1 %)",
      "Steady",
    ]);
  });

  it("says the lattice is building before the first frame", () => {
    const lines = statsLines(readStatus({ state: "starting", grid: { fidelity: 2, cells: 500_000, fluid_cells: 390_000 } }));
    expect(lines).toEqual(["Fidelity 2 · 390 k cells · laminar", "Building the lattice…"]);
  });

  it("writes a pressure and a flow with the prefix that reads best", () => {
    expect(pressureText(0.00918)).toBe("9.18 mPa");
    expect(pressureText(4.13)).toBe("4.13 Pa");
    expect(pressureText(2500)).toBe("2.5 kPa");
    expect(pressureText(250000)).toBe("2.5 bar");
    expect(pressureText(0)).toBe("0 Pa");
    expect(flowText(2e-5)).toBe("20 mL/s");
    expect(flowText(0.0012)).toBe("1.2 L/s");
    expect(flowText(3.4e-9)).toBe("3.4 µL/s");
  });

  it("warns when the numbers cannot be trusted, worst first", () => {
    expect(frameWarning(readFrame({ ...FRAME, unstable: true, guard: 5 }))).toMatch(/Unstable: these numbers are not valid/);
    expect(frameWarning(readFrame({ ...FRAME, unstable: true, instability: "imbalance" }))).toMatch(/^Unstable \(the flow out and the flow in drifted apart\)/);
    expect(readFrame({ ...FRAME, unstable: true, instability: "guard" }).instability).toBe("guard");
    expect(readFrame(FRAME).instability).toBe("");
    expect(frameWarning(readFrame({ ...FRAME, guard: 12 }))).toMatch(/not reliable/);
    expect(frameWarning(readFrame({ ...FRAME, imbalance: -0.08 }))).toMatch(/flow out differs/);
    // While the flow settles the two flows differ by nature: no warning then.
    expect(frameWarning(readFrame({ ...FRAME, imbalance: -0.5, steady: false }))).toBe("");
    expect(frameWarning(readFrame({ ...FRAME, restrictive: true }))).toMatch(/restrictive/);
    expect(frameWarning(readFrame(FRAME))).toBe("");
    expect(frameWarning(null)).toBe("");
  });

  it("says when the grid is not the one asked for", () => {
    const raised = gridNotes(readStatus({ state: "running", fidelity: 2, grid: { ...GRID, fidelity: 4 } }));
    expect(raised.map((n) => n.text)).toEqual(["Fidelity raised to 4"]);
    expect(raised[0].tip).toMatch(/too fast for the cells of fidelity 2/);
    expect(gridNotes(readStatus({ state: "running", fidelity: 3, grid: { ...GRID, coarsened: true } })).map((n) => n.text)).toEqual(["Coarser cells than asked"]);
    expect(gridNotes(readStatus({ state: "running", fidelity: 3, grid: GRID }))).toEqual([]);
  });

  it("offers five fidelities, the automatic plane and the fields the server serves", () => {
    expect(FIDELITIES.map((f) => f.value)).toEqual([1, 2, 3, 4, 5]);
    expect(FAST_FIELDS.map((f) => f.id)).toEqual(["U", "p", "Ux", "Uy", "Uz", "T"]);
    expect(PLANES.map((p) => p.value)).toEqual([AUTOMATIC_PLANE, 0, 1, 2]);
  });
});

describe("the legend", () => {
  it("names the value and its unit with the range's two ends", () => {
    expect(legendOf({ field: "U", min: 0, max: 0.0734 }, 998)).toEqual({ title: "Velocity, m/s", low: "0", high: "0.0734" });
    expect(legendOf({ field: "Ux", min: -0.012, max: 0.07 }, null)).toEqual({ title: "Velocity x, m/s", low: "-0.012", high: "0.07" });
  });

  it("shows the pressure in pascals with the density, kinematic without", () => {
    expect(legendOf({ field: "p", min: 0, max: 0.0078 }, 1.2)).toEqual({ title: "Pressure, Pa", low: "0", high: "0.00936" });
    expect(legendOf({ field: "p", min: 0, max: 0.0078 }, null)).toEqual({ title: "Pressure, m²/s²", low: "0", high: "0.0078" });
  });
});

describe("the plane's position", () => {
  const spans = readStatus({ state: "running", grid: GRID }).spans;

  it("is the frame's cell as a share of the lattice's box", () => {
    expect(planeShare(readFrame(FRAME), spans)).toBeCloseTo(17.5 / 36);
    expect(planeShare(null, spans)).toBe(0.5);
    expect(planeShare(readFrame(FRAME), null)).toBe(0.5);
  });

  it("turns a share back into the offset fast.configure takes, in metres", () => {
    expect(planeOffset(0.5, 1, spans)).toBeCloseTo(0);
    expect(planeOffset(0, 0, spans)).toBeCloseTo(-0.03125);
    expect(planeOffset(2, 0, spans)).toBeCloseTo(0.03125); // held to the box
    expect(planeOffset(0.5, 1, null)).toBeNull();
  });
});

describe("the top bar's button", () => {
  it("is lit while a preview starts or steps", () => {
    expect(previewRuns("running")).toBe(true);
    expect(previewRuns("starting")).toBe(true);
    expect(previewRuns("stopped")).toBe(false);
    expect(previewRuns(undefined)).toBe(false);
  });
});

describe("a preview of several bodies", () => {
  const STREAMS = [
    { name: "hot", flow: 3.84e-6, flow_out: 3.8e-6, mass_flow: 0.00382, dp: 2826, t_in: 350, t_out: 342.6, heat: -117.5 },
    { name: "cold", flow: 3.84e-6, flow_out: 3.8e-6, mass_flow: 0.00383, dp: 3004, t_in: 300, t_out: 306.7, heat: 107.1 },
  ];
  const status = readStatus({
    state: "running",
    grid: GRID,
    thermal: true,
    turbulence: "auto",
    closure: "shear_improved",
    zones: [
      { name: "hot", kind: "fluid" },
      { name: "cold", kind: "fluid" },
      { name: "plate", kind: "solid" },
    ],
    frame: { ...FRAME, streams: STREAMS, heat_balance: -10.4, holding: true },
  });

  it("reads the zones, the closure and each stream", () => {
    expect(status).toMatchObject({ thermal: true, turbulence: "auto", closure: "shear_improved" });
    expect(status.zones.map((z) => z.name)).toEqual(["hot", "cold", "plate"]);
    expect(status.frame?.streams[0]).toEqual({ name: "hot", massFlow: 0.00382, dp: 2826, tIn: 350, tOut: 342.6, heat: -117.5 });
    expect(status.frame?.heatBalance).toBe(-10.4);
    expect(status.frame?.holding).toBe(true);
  });

  it("says what it is previewing, and nothing for one fluid", () => {
    expect(zonesLine(status)).toBe("hot, cold (fluids) · plate (solid)");
    expect(zonesLine(readStatus({ state: "running", zones: [{ name: "fluid", kind: "fluid" }] }))).toBe("");
  });

  it("lists the streams as the Results table does: flow, drop, temperatures, heat", () => {
    expect(streamRows(status.frame)).toEqual([
      ["hot", "3.82 g/s", "2.83 kPa", "350.0 → 342.6 K", "-118 W"],
      ["cold", "3.83 g/s", "3 kPa", "300.0 → 306.7 K", "+107 W"],
    ]);
    expect(streamRows(null)).toEqual([]);
    // A stream of a case with no temperature has its flow and its drop alone.
    expect(streamRows(readFrame({ ...FRAME, streams: [{ name: "a", mass_flow: 0.02, dp: 1.5 }] }))).toEqual([["a", "20 g/s", "1.5 Pa", "–", "–"]]);
  });

  it("writes a mass flow and a heat with their prefixes", () => {
    expect(massFlowText(1.2)).toBe("1.2 kg/s");
    expect(massFlowText(2.5e-7)).toBe("250 µg/s");
    expect(heatText(1530)).toBe("+1.53 kW");
    expect(heatText(-0.25)).toBe("-250 mW");
  });

  it("offers the temperature only when it is solved, to a tenth of a kelvin", () => {
    expect(fieldsFor(true).map((f) => f.id)).toContain("T");
    expect(fieldsFor(false).map((f) => f.id)).not.toContain("T");
    expect(legendOf({ field: "T", min: 300, max: 342.63 }, 998)).toEqual({ title: "Temperature, K", low: "300.0", high: "342.6" });
  });

  it("says a steady preview is holding, and offers the closures", () => {
    expect(statsLines(status).at(-1)).toBe("Steady · holding (edit to resume)");
    expect(TURBULENCE.map((t) => t.value)).toEqual(["auto", "shear_improved", "smagorinsky", "wale", "vreman"]);
  });
});

describe("round 4: storage and the warm start", () => {
  it("offers 32-bit and 16-bit storage and reads what the run uses", () => {
    expect(STORAGES.map((s) => s.value)).toEqual(["f32", "f16"]);
    expect(readStatus({ state: "running" })).toMatchObject({ storage: "f32", warmStart: true, warming: false });
    const half = readStatus({ state: "running", grid: GRID, storage: "f16", warm_start: false, frame: FRAME });
    expect(half).toMatchObject({ storage: "f16", warmStart: false });
    expect(statsLines(half)[0]).toContain("16-bit");
  });

  it("says a warm start is on its coarse lattice, and never calls that steady", () => {
    const coarse = readStatus({ state: "running", grid: GRID, fidelity: 5, warming: true, frame: { ...FRAME, warming: true, steady: false } });
    expect(coarse.warming).toBe(true);
    expect(statsLines(coarse).at(-1)).toBe("Warm start · settling a coarse lattice first (then fidelity 5)…");
  });

  it("says a lattice took its flow from another until it is steady", () => {
    const raised = readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: false, warmed_from: 2 } });
    expect(raised.frame?.warmedFrom).toBe(2);
    expect(statsLines(raised).at(-1)).toBe("Warm start from fidelity 2 · settling…");
    expect(statsLines(readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: false } })).at(-1)).toBe("Settling…");
    expect(statsLines(readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: true, warmed_from: null } })).at(-1)).toBe("Steady");
  });

  it("heads the lines with the frame's grid, not a status a second old", () => {
    // A fidelity raised to 4: the status still has grid 3 (987 k cells), the frame is the new grid's.
    const uncounted = readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: false, warmed_from: 3, fidelity: 4, fluid_cells: 0 } });
    expect(statsLines(uncounted)[0]).toBe("Fidelity 4 · laminar");
    const counted = readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: false, warmed_from: 3, fidelity: 4, fluid_cells: 2_340_000 } });
    expect(statsLines(counted)[0]).toBe("Fidelity 4 · 2.3 M cells · laminar");
    expect(statsLines(counted).at(-1)).toBe("Warm start from fidelity 3 · settling…");
  });

  it("counts the fluid's cells, and says none before the lattice has counted them", () => {
    expect(statsLines(readStatus({ state: "running", grid: GRID, frame: FRAME }))[0]).toContain("987 k cells");
    const uncounted = readStatus({ state: "running", grid: { ...GRID, fluid_cells: 0 }, frame: FRAME });
    expect(statsLines(uncounted)[0]).toBe("Fidelity 3 · laminar");
  });
});

describe("an under-resolved preview says so", () => {
  const withResolution = (resolution: unknown) => readStatus({ state: "running", grid: GRID, resolution });

  it("names the fluid whose passages are fewest cells across", () => {
    const status = withResolution([
      { name: "hot", cells: 1000, cells_across: 5.6 },
      { name: "cold", cells: 1000, cells_across: 4.2 },
    ]);
    expect(status.resolution).toEqual([
      { name: "hot", cellsAcross: 5.6 },
      { name: "cold", cellsAcross: 4.2 },
    ]);
    expect(resolutionWarning(status)).toBe("Under-resolved: cold's passages are about 4 cells across. Read the picture, not the numbers; raise the fidelity.");
  });

  it("says nothing for a resolved fluid, and speaks of the passages for one fluid", () => {
    expect(resolutionWarning(withResolution([{ name: "fluid", cells: 9, cells_across: 32 }]))).toBe("");
    expect(resolutionWarning(withResolution([{ name: "fluid", cells: 9, cells_across: 7.4 }]))).toMatch(/^Under-resolved: The passages are about 7 cells across/);
    expect(resolutionWarning(withResolution(undefined))).toBe("");
  });

  it("says the temperature is catching up once the flow is steady", () => {
    const status = readStatus({ state: "running", grid: GRID, frame: { ...FRAME, steady: false, catching_up: true } });
    expect(statsLines(status).at(-1)).toBe("Flow steady · temperature settling…");
  });
});
