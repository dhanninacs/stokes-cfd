/**
 * A transient run's results (ADR-0022 addendum 2026-11-24): the History plot's robust range, start and captions,
 * the flows' sign, the Run card's words and time rules, the residuals against time, the openings at a time and the
 * colour range over every written time.
 */
import { describe, expect, it } from "vitest";
import { historyFrom, monitorLabel, monitorSeries, robustRange, transientCaption, transientStats, xyChart } from "./analyseModel";
import { frameCount, readWriteSuggestion, saveEveryNote, saveEveryProblem, stepProblem } from "./steps/runTimeModel";
import { noticeToast, runReadout, simSeconds, stateLine, stepLimit, transientEnd, type RunNotice } from "../panels/runPanelModel";
import type { RunSummary } from "../panels/runApi";
import { readRunRecord } from "../panels/runApi";
import { appendResidual, EMPTY_SERIES, seriesTimes } from "../state/residualSeries";
import { buildChart } from "../state/residualChart";
import { atTimeText, pressureDrop, readMonitorsAt } from "../panels/monitorsModel";
import { readStreams } from "../panels/streamsModel";
import { autoRange, effectiveRange, hasSeveralTimes, rangeScopeOf, setTimeScope, spansAllTimes, type ResultsView } from "../panels/fieldsModel";
import { readRunFields, type RunFields } from "../panels/resultsApi";
import type { NodeSummary } from "../api/types";

/** A 5 Hz pressure swing of +-2 after an impulsive start of 200 at the second sample, 400 samples over 0.4 s. */
function startUp(): { x: number[]; y: number[] } {
  const x: number[] = [];
  const y: number[] = [];
  for (let i = 1; i <= 400; i++) {
    const t = i * 0.001;
    x.push(t);
    y.push(i === 2 ? 200 : i === 1 ? 50 : 2 * Math.sin(2 * Math.PI * 5 * t));
  }
  return { x, y };
}

describe("the History plot of a transient run", () => {
  it("draws a robust range that leaves out the start-up spike and marks it", () => {
    const { x, y } = startUp();
    const robust = robustRange(x, y);
    expect(robust).not.toBeNull();
    expect(robust!.range[0]).toBeCloseTo(-2, 1);
    expect(robust!.range[1]).toBeCloseTo(2, 1);
    expect(robust!.full[1]).toBe(200);
    // The start-up is the first 5 % of the span: the range is measured from there.
    expect(robust!.after).toBeCloseTo(0.021, 6);
    const chart = xyChart([{ x, y }], 300, 150, "", "line", { yRange: robust!.range });
    // The ticks are the range's, not the spike's: every tick sits inside the plot and reads the swing.
    expect(chart.yTicks.length).toBeGreaterThan(2);
    for (const t of chart.yTicks) {
      expect(t.at).toBeGreaterThanOrEqual(chart.y0 - 0.01);
      expect(t.at).toBeLessThanOrEqual(chart.y1 + 0.01);
      expect(Math.abs(t.value)).toBeLessThanOrEqual(2.5);
    }
    // One excursion off the top, at its furthest point (200 at t = 0.002).
    expect(chart.clipped).toHaveLength(1);
    expect(chart.clipped[0]).toMatchObject({ edge: "top", value: 200, y: chart.y0 });
  });

  it("draws the full range when nothing stands out, and on request", () => {
    const x = [1, 2, 3, 4, 5, 6, 7, 8];
    const y = [1, 1.2, 1.1, 1.3, 1.2, 1.25, 1.22, 1.24];
    expect(robustRange(x, y)).toBeNull();
    const { x: sx, y: sy } = startUp();
    const full = xyChart([{ x: sx, y: sy }], 300, 150, "", "line");
    expect(full.clipped).toHaveLength(0);
    expect(Math.max(...full.yTicks.map((t) => t.value))).toBeGreaterThanOrEqual(200);
  });

  it("starts where the user says, and refuses a start past the end", () => {
    const { x, y } = startUp();
    const chart = xyChart([{ x, y }], 300, 150, "", "line", { xFrom: 0.1 });
    expect(chart.xTicks[0]!.value).toBeGreaterThanOrEqual(0.1);
    expect(Math.max(...chart.yTicks.map((t) => t.value))).toBeLessThan(3);
    expect(historyFrom("0.1", x)).toEqual({ from: 0.1 });
    expect(historyFrom("", x)).toEqual({ from: undefined });
    expect(historyFrom("abc", x)).toEqual({ error: "Type a number on the x axis" });
    expect(historyFrom("0.5", x)).toEqual({ error: "The history ends at 0.4" });
  });

  it("says min, mean and max, and the last period's mean, instead of 'settled'", () => {
    const x = Array.from({ length: 401 }, (_, i) => i * 0.001);
    const y = x.map((t) => 1 + 0.3 * Math.sin(2 * Math.PI * 5 * t));
    const stats = transientStats(x, y, undefined, 0.2)!;
    expect(stats.min).toBeCloseTo(0.7, 3);
    expect(stats.max).toBeCloseTo(1.3, 3);
    expect(stats.mean).toBeCloseTo(1, 3);
    expect(stats.periodMean).toBeCloseTo(1, 3);
    expect(transientCaption(stats)).toMatch(/^min 0\.7\d* · mean 1(\.0+\d*)? · max 1\.3\d* · last period's mean 1/);
    // Too short for a whole period: no period mean.
    expect(transientStats(x.slice(0, 50), y.slice(0, 50), undefined, 0.2)!.periodMean).toBeNull();
  });

  it("shows an inlet's flow as the flow in, positive", () => {
    expect(monitorLabel("patchFlowRate(patch=inlet)/0/surfaceFieldValue.dat", "inlet").label).toBe("Flow in, inlet");
    expect(monitorLabel("patchFlowRate(patch=outlet)/0/surfaceFieldValue.dat", "outlet").label).toBe("Flow out, outlet");
    expect(monitorLabel("fluid/patchFlowRate(patch=a,region=fluid)/0/surfaceFieldValue.dat").label).toBe("Flow rate, a");
    const table = { columns: ["Time", "sum(phi)"], values: { Time: [0.1, 0.2], "sum(phi)": [-4e-4, -5e-4] } };
    expect(monitorSeries(table, -1)!.y).toEqual([4e-4, 5e-4]);
    expect(monitorSeries(table)!.y).toEqual([-4e-4, -5e-4]);
  });
});

const transientRun = (over: Partial<RunSummary> = {}): RunSummary => ({
  id: "r1",
  name: "duct",
  state: "done",
  stale: false,
  iterations: 400,
  converged: false,
  seconds: 12,
  step: "",
  fraction: 1,
  error: "",
  host: "local",
  last: [],
  monitors: [],
  transient: true,
  endTime: 0.4,
  simTime: 0.4,
  ...over,
});

describe("the Run card's words for a transient run", () => {
  it("says how much time it simulated, not that it did not converge", () => {
    expect(transientEnd(transientRun())).toBe("0.4 s simulated in 400 steps");
    expect(stateLine(transientRun(), 400).text).toBe("Finished: 0.4 s simulated in 400 steps in 12 s");
    expect(stateLine(transientRun(), 400).tone).toBe("ok");
    expect(runReadout(transientRun(), null)?.text).toBe("Run · 0.4 s · finished");
    const early = transientRun({ state: "done_unconverged", simTime: 0.1, iterations: 100 });
    expect(stateLine(early, 400).text).toBe("Ended before its end time (0.4 s): 0.1 s simulated in 100 steps in 12 s");
    expect(stateLine(transientRun({ state: "running", iterations: 50 }), 400).text).toBe("Running: step 50 of 400");
    const note: RunNotice = { seq: 1, runId: "r1", state: "done", iterations: 400, seconds: 12, converged: false, message: "", transient: true, endTime: 0.4, simTime: 0 };
    expect(noticeToast(note)?.text).toBe("Run finished: 0.4 s simulated in 400 steps · 12 s");
    // A steady run keeps its words.
    expect(stateLine({ ...transientRun(), transient: false, state: "done_unconverged" }, 400).text).toMatch(/^Finished, not converged/);
    expect(simSeconds(0.0002)).toBe("2e-4 s");
  });

  it("reads the record's transient fields, and counts a transient run's steps from its times", () => {
    const run = readRunRecord({ id: "r1", state: "done", transient: true, end_time: 0.4, sim_time: 0.4 })!;
    expect(run.transient).toBe(true);
    expect(run.endTime).toBe(0.4);
    const settings = { params: { transient: true, end_time: { value: 0.4 }, delta_t: 0.001, iterations: 500 } } as unknown as NodeSummary;
    expect(stepLimit(settings)).toBe(400);
    expect(stepLimit({ params: { iterations: 500 } } as unknown as NodeSummary)).toBe(500);
  });

  it("refuses a time step longer than the run and a save interval out of range", () => {
    expect(stepProblem(1, 0.05)).toBe("is longer than the end time (0.05 s): shorten it or run for longer");
    expect(stepProblem(0.001, 0.4)).toBeNull();
    expect(saveEveryProblem(0.0001, 0.001, 0.4)).toBe("is shorter than one time step (0.001 s)");
    expect(saveEveryProblem(1, 0.001, 0.4)).toBe("is longer than the run (0.4 s)");
    expect(saveEveryProblem(0.01, 0.001, 0.4)).toBeNull();
    expect(frameCount(0.4, 0.008)).toBe(50);
  });

  it("offers a finer save interval when the run writes too few frames", () => {
    const s = readWriteSuggestion({ write_time: 0.008, frames: 50, period: 0.2, current: { write_time: 0.05, frames: 8, steps: true } })!;
    expect(saveEveryNote(s)).toEqual({ text: "8 frames, 4 a period", offer: 0.008 });
    const fine = readWriteSuggestion({ write_time: 0.008, frames: 50, period: 0.2, current: { write_time: 0.008, frames: 50, steps: false } })!;
    expect(saveEveryNote(fine)).toEqual({ text: "50 frames, 25 a period", offer: null });
    expect(readWriteSuggestion({})).toBeNull();
  });
});

describe("the residuals of a transient run", () => {
  it("keep each block's time and are drawn against it", () => {
    const series = appendResidual(EMPTY_SERIES, [
      { iteration: 1, time: 0.001, values: [{ name: "p", value: 1e-2 }] },
      { iteration: 2, time: 0.002, values: [{ name: "p", value: 1e-3 }] },
      { iteration: 3, time: 0.003, values: [{ name: "p", value: 1e-4 }] },
    ]);
    expect(seriesTimes(series)).toEqual([0.001, 0.002, 0.003]);
    const byTime = buildChart(series, new Set(), null, 320, 150, undefined, true);
    expect(byTime.xLabel).toBe("time (s)");
    expect(byTime.target).toBeNull();
    expect(byTime.xTicks.every((t) => t.value >= 0.001 - 1e-12 && t.value <= 0.003 + 1e-12)).toBe(true);
    expect(buildChart(series, new Set(), 1e-3, 320, 150).xLabel).toBe("iteration");
  });
});

describe("the openings at the time on screen", () => {
  it("reads run.monitors_at with the flows in their own direction and says the time", () => {
    const at = readMonitorsAt({ sampled: 0.25, monitors: { outlet: { flow_rate: 4e-4, flow: 4e-4, p_mean: 0 }, inlet: { flow_rate: -4e-4, flow: 4e-4, p_mean: 1.5 } } })!;
    expect(at.time).toBe(0.25);
    expect(at.monitors.map((m) => [m.patch, m.flowRate])).toEqual([
      ["inlet", 4e-4],
      ["outlet", 4e-4],
    ]);
    expect(pressureDrop(at.monitors)?.value).toBe(1.5);
    expect(atTimeText(true, 0.25, false)).toBe("at t = 0.25 s");
    expect(atTimeText(true, 0.4, true)).toBe("at t = 0.4 s (last)");
    expect(atTimeText(true, null, true, 0.4)).toBe("at t = 0.4 s (last)");
    expect(atTimeText(false, 0.25, false)).toBe("");
    expect(readStreams({ time: 0.25, streams: [] }).time).toBe(0.25);
    expect(readStreams({ streams: [] }).time).toBeNull();
  });
});

describe("the colour range over every written time", () => {
  const data: RunFields = readRunFields({
    run: "r1",
    time: 0.1,
    times: [0, 0.05, 0.1],
    time_ranges: "ready",
    fields: [{ name: "p", units: "m2/s2", min: -1, max: 1, p01: -0.9, p99: 0.9, all_times: { min: -5, max: 4, p01: -4, p99: 3, times: 2 } }],
  })!;
  const view: ResultsView = { field: "p", lock: null, slice: false, bands: 0 };

  it("spans all times by default and the shown time on request", () => {
    expect(data.fields[0]!.allTimes).toEqual({ min: -5, max: 4, p01: -4, p99: 3, times: 2 });
    expect(hasSeveralTimes(data)).toBe(true);
    expect(effectiveRange(view, data)).toEqual({ min: -4, max: 3 });
    expect(autoRange({ fullRange: true }, data, "p")).toEqual({ min: -5, max: 4 });
    const thisTime = setTimeScope(view, "time");
    expect(effectiveRange(thisTime, data)).toEqual({ min: -0.9, max: 0.9 });
    expect(spansAllTimes(view, data, "p")).toBe(true);
    expect(spansAllTimes(thisTime, data, "p")).toBe(false);
    expect(rangeScopeOf("p", false, true, "all")).toBe("range over all times");
    expect(rangeScopeOf("p", false, true, "time")).toBe("range at this time");
    expect(setTimeScope(view, "all")).toBe(view);
  });

  it("uses the shown time's range until the server has the all-times one", () => {
    const pending = readRunFields({ run: "r1", time: 0.1, times: [0.05, 0.1], time_ranges: "computing", fields: [{ name: "p", units: "", min: -1, max: 1 }] })!;
    expect(pending.timeRanges).toBe("computing");
    expect(effectiveRange(view, pending)).toEqual({ min: -1, max: 1 });
  });
});
