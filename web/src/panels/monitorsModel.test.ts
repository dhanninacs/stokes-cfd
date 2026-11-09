/** The monitors table, its two exports and the pressure drop (T-219 D). */
import { describe, expect, it } from "vitest";
import { csvFileName, csvText, dropSymbol, formatMonitor, monitorRows, monitorsOf, pressureDrop, runForMonitors, tableText } from "./monitorsModel";
import { readMonitors, readRunRecord } from "./runApi";

const DUCT = [
  { patch: "inlet", flowRate: -3.2e-5, pMean: 0.0851 },
  { patch: "outlet", flowRate: 3.2e-5, pMean: 0 },
  { patch: "walls", flowRate: null, pMean: null },
];

describe("reading the record", () => {
  it("reads the monitors object, sorted by patch, with null where nothing was reported", () => {
    const monitors = readMonitors({ outlet: { flow_rate: 3.2e-5, p_mean: 0 }, inlet: { flow_rate: -3.2e-5, p_mean: 0.0851 }, walls: { flow_rate: null } });
    expect(monitors.map((m) => m.patch)).toEqual(["inlet", "outlet", "walls"]);
    expect(monitors[2]).toEqual({ patch: "walls", flowRate: null, pMean: null });
  });
  it("reads anything else as none", () => {
    expect(readMonitors(undefined)).toEqual([]);
    expect(readMonitors([1])).toEqual([]);
    expect(readMonitors({ inlet: 5 })).toEqual([]);
  });
  it("is part of the run record", () => {
    const run = readRunRecord({ id: "r_1", state: "done", monitors: { inlet: { flow_rate: 1, p_mean: 2 } } });
    expect(run?.monitors).toEqual([{ patch: "inlet", flowRate: 1, pMean: 2 }]);
    expect(readRunRecord({ id: "r_2", state: "done" })?.monitors).toEqual([]);
  });
});

describe("numbers", () => {
  it("prints four significant digits and an exponent for the very small", () => {
    expect(formatMonitor(0.0851234)).toBe("0.08512");
    expect(formatMonitor(-3.2e-5)).toBe("-3.2e-5");
    expect(formatMonitor(0)).toBe("0");
    expect(formatMonitor(null)).toBe("-");
    expect(formatMonitor(Number.NaN)).toBe("-");
    expect(formatMonitor(123456)).toBe("1.235e5");
  });
  it("makes a row per patch", () => {
    expect(monitorRows(DUCT)[0]).toEqual({ patch: "inlet", flowRate: "-3.2e-5", pMean: "0.0851" });
    expect(monitorRows(DUCT)[2]).toEqual({ patch: "walls", flowRate: "-", pMean: "-" });
  });
});

describe("the exports", () => {
  it("copies a tab-separated table with a header and units", () => {
    const text = tableText(DUCT);
    expect(text.split("\n")[0]).toBe("patch\tflow rate (m3/s)\tmean p/rho (m2/s2)");
    // An energy run's pressure is in Pa, and is p rather than p/rho.
    expect(tableText(DUCT, "Pa").split("\n")[0]).toBe("patch\tflow rate (m3/s)\tmean p (Pa)");
    expect(dropSymbol("Pa")).toBe("Δp");
    expect(dropSymbol("m2/s2")).toBe("Δp/ρ");
    expect(text.split("\n")[1]).toBe("inlet\t-3.2e-5\t0.0851");
    expect(text.endsWith("\n")).toBe(true);
  });
  it("writes CSV with the full numbers, an empty cell for a missing one, and quotes where needed", () => {
    const csv = csvText([...DUCT, { patch: 'odd, "name"', flowRate: 1, pMean: null }]);
    const lines = csv.split("\n");
    expect(lines[0]).toBe("patch,flow_rate_m3/s,p_mean_m2/s2");
    expect(lines[1]).toBe("inlet,-0.000032,0.0851");
    expect(lines[3]).toBe("walls,,");
    expect(lines[4]).toBe('"odd, ""name""",1,');
    expect(csv).not.toContain("\r");
  });
  it("names the file after the run, safely", () => {
    expect(csvFileName({ id: "r_1", name: "duct run" })).toBe("duct_run-monitors.csv");
    expect(csvFileName({ id: "r_9", name: "" })).toBe("r_9-monitors.csv");
    expect(csvFileName(null)).toBe("run-monitors.csv");
    expect(csvFileName({ id: "r", name: "../x" })).toBe(".._x-monitors.csv");
  });
});

describe("the pressure drop", () => {
  it("is the inlet's mean pressure minus the outlet's", () => {
    const drop = pressureDrop(DUCT);
    expect(drop?.value).toBeCloseTo(0.0851, 12);
    expect(drop?.text).toBe("0.0851 m2/s2");
    expect(drop).toMatchObject({ inlet: "inlet", outlet: "outlet" });
  });
  it("needs both patches and both pressures", () => {
    expect(pressureDrop([DUCT[0]])).toBeNull();
    expect(pressureDrop([{ patch: "inlet", flowRate: 1, pMean: null }, DUCT[1]])).toBeNull();
    expect(pressureDrop([])).toBeNull();
  });
  it("finds a patch that merely contains the word, and refuses a guess between two", () => {
    expect(pressureDrop([{ patch: "inlet_main", flowRate: 0, pMean: 3 }, { patch: "outlet", flowRate: 0, pMean: 1 }])?.value).toBe(2);
    expect(pressureDrop([{ patch: "inlet_a", flowRate: 0, pMean: 3 }, { patch: "inlet_b", flowRate: 0, pMean: 3 }, { patch: "outlet", flowRate: 0, pMean: 1 }])).toBeNull();
    // An exact name wins over a longer one.
    expect(pressureDrop([{ patch: "inlet", flowRate: 0, pMean: 5 }, { patch: "inlet_extra", flowRate: 0, pMean: 9 }, { patch: "outlet", flowRate: 0, pMean: 1 }])?.value).toBe(4);
  });
});

describe("monitorsOf", () => {
  it("is none for no run and for a record that has no monitors field", () => {
    expect(monitorsOf(null)).toEqual([]);
    expect(monitorsOf(undefined)).toEqual([]);
    expect(monitorsOf({})).toEqual([]);
    expect(monitorsOf({ monitors: DUCT })).toBe(DUCT);
  });
});

describe("which run's monitors", () => {
  const runs = [
    { id: "r_3", state: "running" as const },
    { id: "r_2", state: "done" as const },
    { id: "r_1", state: "done_unconverged" as const },
  ];
  it("follows the results' run, else the newest finished one", () => {
    expect(runForMonitors(runs, "r_1")?.id).toBe("r_1");
    expect(runForMonitors(runs, null)?.id).toBe("r_2");
    expect(runForMonitors(runs, "r_3")?.id).toBe("r_2");
    expect(runForMonitors([{ id: "r_9", state: "failed" as const }], null)).toBeNull();
  });
});
