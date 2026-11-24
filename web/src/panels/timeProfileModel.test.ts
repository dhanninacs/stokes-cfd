import { describe, expect, it } from "vitest";
import {
  addRow,
  constantKeptNote,
  defaultDraft,
  defaultSwing,
  draftFromStored,
  errorKey,
  followingReport,
  historyNotes,
  isBlockPaste,
  keptAfter,
  parsePasted,
  pasteNote,
  pasteRows,
  pasteTaken,
  plotShape,
  previewNotes,
  previewRefusals,
  profileSummary,
  quantityText,
  removeRow,
  setCell,
  switchForm,
  toProfile,
  varyingInTree,
  varyingOf,
} from "./timeProfileModel";

describe("the draft", () => {
  it("builds each form around the constant", () => {
    expect(defaultDraft("sine", "velocity", 2).fields).toEqual({ mean: "2", amplitude: "0.5", frequency: "10", phase: "0" });
    expect(defaultDraft("square", "pressure", 0).fields).toEqual({ low: "-1000", high: "1000", period: "0.1", duty: "0.5" });
    const table = defaultDraft("table", "velocity", 2);
    expect(table.rows).toHaveLength(3);
    expect(table.rows[1]).toEqual({ time: "0.05", value: "2.5" });
  });

  it("opens a temperature 10 K around its constant, never a quarter of the kelvin value", () => {
    expect(defaultDraft("sine", "temperature", 293.15).fields).toMatchObject({ mean: "293.15", amplitude: "10" });
    expect(defaultSwing("temperature", 400)).toEqual({ centre: 400, swing: 10 });
    expect(defaultDraft("square", "temperature", 300).fields).toMatchObject({ low: "290", high: "310" });
    // No constant (or 0 K) opens at room temperature.
    expect(defaultSwing("temperature", null).centre).toBe(293.15);
    expect(defaultSwing("temperature", 0).centre).toBe(293.15);
  });

  it("opens an inflow at 0 above zero, so the default never reverses the flow", () => {
    const flow = defaultDraft("sine", "mass_flow", 0).fields;
    expect(Number(flow.mean)).toBeGreaterThan(Number(flow.amplitude));
    expect(Number(flow.amplitude)).toBeGreaterThan(0);
    const speed = defaultDraft("square", "velocity", 0).fields;
    expect(Number(speed.low)).toBeGreaterThan(0);
    expect(Number(speed.high)).toBeGreaterThan(Number(speed.low));
    const table = defaultDraft("table", "mass_flow", 0).rows.map((r) => Number(r.value));
    expect(Math.min(...table)).toBeGreaterThan(0);
    // An outlet at 0 Pa gauge may swing either side of zero.
    expect(defaultDraft("sine", "pressure", 0).fields).toMatchObject({ mean: "0", amplitude: "1000" });
  });

  it("names the constant field a profile leaves kept but unused", () => {
    expect(constantKeptNote("velocity")).toBe('Constant in "velocity" below: kept, not used');
  });

  it("carries the mean and the swing between a sine and a pulse", () => {
    const sine = defaultDraft("sine", "velocity", 2);
    const pulse = switchForm(sine, "square", "velocity", 2);
    expect(pulse.fields.low).toBe("1.5");
    expect(pulse.fields.high).toBe("2.5");
    const back = switchForm(pulse, "sine", "velocity", 2);
    expect(back.fields.mean).toBe("2");
    expect(back.fields.amplitude).toBe("0.5");
  });

  it("sends bare numbers as quantity text in the editor's unit and anything else as typed", () => {
    expect(quantityText("2", "m/s")).toBe("2 [m/s]");
    expect(quantityText(" 0.3 ", "")).toBe(0.3);
    expect(quantityText("U_in * 1.5", "m/s")).toBe("U_in * 1.5");
    expect(quantityText("30 [km/h]", "m/s")).toBe("30 [km/h]");
    const sine = toProfile(defaultDraft("sine", "velocity", 2), "velocity");
    expect(sine).toEqual({ form: "sine", mean: "2 [m/s]", amplitude: "0.5 [m/s]", frequency: "10 [Hz]", phase: "0 [deg]" });
    const pulse = toProfile(defaultDraft("square", "pressure", 100), "pressure");
    expect(pulse).toMatchObject({ period: "0.1 [s]", duty: 0.5 });
    const table = toProfile(defaultDraft("table", "mass_flow", 0.01), "mass_flow");
    expect(table).toMatchObject({ form: "table", rows: [{ time: "0 [s]", value: "0.01 [kg/s]" }, expect.anything(), expect.anything()] });
    expect(toProfile(defaultDraft("constant", "velocity", 2), "velocity")).toBeNull();
  });

  it("reads a stored profile back, expressions as typed and the phase in degrees", () => {
    const stored = {
      form: "sine",
      mean: { value: 3, unit: "m/s", expr: "U_in" },
      amplitude: { value: 1, unit: "m/s" },
      frequency: { value: 10, unit: "Hz" },
      phase: { value: Math.PI / 2, unit: "rad" },
    };
    expect(draftFromStored(stored, "velocity", 0).fields).toEqual({ mean: "U_in", amplitude: "1", frequency: "10", phase: "90" });
    const table = { form: "table", rows: [{ time: { value: 0, unit: "s" }, value: { value: 1, unit: "Pa" } }] };
    expect(draftFromStored(table, "pressure", 0).rows).toEqual([{ time: "0", value: "1" }]);
    expect(draftFromStored(null, "velocity", 2).form).toBe("constant");
    expect(profileSummary(stored, "velocity")).toBe("sine U_in ± 1 m/s, 10 Hz");
  });
});

describe("the table", () => {
  it("adds a row a step after the last and removes one", () => {
    const table = defaultDraft("table", "velocity", 1);
    const more = addRow(table);
    expect(more.rows[3]).toEqual({ time: "0.15", value: "1" });
    expect(removeRow(more, 0).rows[0].time).toBe("0.05");
    expect(setCell(table, 1, "value", "4").rows[1].value).toBe("4");
  });

  it("reads a block pasted from a spreadsheet, skipping the header", () => {
    const sheet = "Time\tSpeed\n0\t1.5\n0,1\t2,5\n0.2\t3\n";
    expect(isBlockPaste(sheet)).toBe(true);
    expect(parsePasted(sheet).rows).toEqual([
      { time: "0", value: "1.5" },
      { time: "0.1", value: "2.5" },
      { time: "0.2", value: "3" },
    ]);
    expect(parsePasted("0, 1\n1, 2").rows).toEqual([
      { time: "0", value: "1" },
      { time: "1", value: "2" },
    ]);
    expect(parsePasted("0;1,5\n1;2").rows).toEqual([
      { time: "0", value: "1.5" },
      { time: "1", value: "2" },
    ]);
    expect(isBlockPaste("2.5")).toBe(false);
  });

  it("keeps cells with units and blanks for the server to check, and reports lines it leaves out", () => {
    // The review's paste: one cell with a unit, a decimal comma, a cell in the wrong unit. All three are rows.
    const block = parsePasted("0 s\t1 m/s\n0,01\t1,5\n0.02\t2 [kg]");
    expect(block.rows).toEqual([
      { time: "0 [s]", value: "1 [m/s]" },
      { time: "0.01", value: "1.5" },
      { time: "0.02", value: "2 [kg]" },
    ]);
    expect(block.skipped).toEqual([]);
    // A blank cell stays a row with a blank, so the server names it; a line of text is left out and said.
    const gaps = parsePasted("0\t1\n0.01\t\nnote\tx\n0.02\t1");
    expect(gaps.rows).toEqual([
      { time: "0", value: "1" },
      { time: "0.01", value: "" },
      { time: "0.02", value: "1" },
    ]);
    expect(gaps.skipped).toEqual([3]);
    expect(pasteNote(gaps)).toEqual({ text: "3 rows pasted; line 3 not a row, left out", warn: true });
  });

  it("reads a header's units onto the bare numbers below it", () => {
    const block = parsePasted("Time (ms)\tSpeed [ft/s]\n0\t1\n50\t2");
    expect(block.units).toEqual({ time: "ms", value: "ft/s" });
    expect(block.rows).toEqual([
      { time: "0 [ms]", value: "1 [ft/s]" },
      { time: "50 [ms]", value: "2 [ft/s]" },
    ]);
    expect(pasteNote(block)).toEqual({ text: "2 rows pasted, in ms and ft/s", warn: false });
  });

  it("reads a comma- or space-separated header whole, with its units on the right column", () => {
    const kpa = parsePasted("Time,Pressure (kPa)\n0,100\n1,120");
    expect(kpa.separator).toBe("comma");
    expect(kpa.units).toEqual({ time: undefined, value: "kPa" });
    expect(kpa.rows).toEqual([
      { time: "0", value: "100 [kPa]" },
      { time: "1", value: "120 [kPa]" },
    ]);
    expect(pasteNote(kpa)).toEqual({ text: "2 rows pasted, in kPa", warn: false });
    for (const text of ["Time (ms),Speed (m/s)\n0,1\n10,2", "Time [ms],Speed [m/s]\n0,1\n10,2", "Time (ms) Speed (m/s)\n0 1\n10 2", "Time (ms);Speed (m/s)\n0;1\n10;2"]) {
      const block = parsePasted(text);
      expect(block.units, text).toEqual({ time: "ms", value: "m/s" });
      expect(block.rows, text).toEqual([
        { time: "0 [ms]", value: "1 [m/s]" },
        { time: "10 [ms]", value: "2 [m/s]" },
      ]);
    }
    // Words in a space-separated header: units placed in order when each column has one, else said, not guessed.
    expect(parsePasted("Time (s) Inlet speed (m/s)\n0 1").units).toEqual({ time: "s", value: "m/s" });
    const unsure = parsePasted("Time Inlet speed (m/s)\n0 1\n1 2");
    expect(unsure.units).toEqual({});
    expect(pasteNote(unsure).warn).toBe(true);
    expect(pasteNote(unsure).text).toContain("header units (m/s) not read");
  });

  it("picks the separator per paste: tab, comma, semicolon, then spaces", () => {
    expect(parsePasted("0\t1,5\n1\t2").separator).toBe("tab");
    expect(parsePasted("0,1\n1,2").separator).toBe("comma");
    expect(parsePasted("0;1,5\n1;2").separator).toBe("semicolon");
    expect(parsePasted("0 1\n1 2").separator).toBe("space");
    // Space-separated numbers with decimal commas are not a comma list.
    const spaced = parsePasted("0 1,5\n0,5 2,5");
    expect(spaced.separator).toBe("space");
    expect(spaced.rows).toEqual([
      { time: "0", value: "1.5" },
      { time: "0.5", value: "2.5" },
    ]);
  });

  it("never reads a thousands-grouped number as a decimal comma without evidence", () => {
    const block = parsePasted("0\t101,325\n1\t101,400");
    expect(block.decimalComma).toBe(false);
    expect(block.rows).toEqual([
      { time: "0", value: "101,325" },
      { time: "1", value: "101,400" },
    ]);
    expect(block.ambiguous).toEqual(["101,325", "101,400"]);
    const note = pasteNote(block);
    expect(note.warn).toBe(true);
    expect(note.text).toContain("'101,325'");
    expect(note.text).toContain("thousands separator or decimal comma?");
    // A comma that cannot be thousands (0,5) shows the paste uses decimal commas: then 101,325 is 101.325.
    expect(parsePasted("0\t0,5\n1\t101,325").rows[1]).toEqual({ time: "1", value: "101.325" });
    // A thousands comma before a decimal point can only be thousands.
    expect(parsePasted("0\t1,234.5\n1\t2").rows[0]).toEqual({ time: "0", value: "1234.5" });
    // The same in spaces.
    expect(parsePasted("0 101,325\n1 2").ambiguous).toEqual(["101,325"]);
    // A comma list cannot carry decimal commas: a line with more than two cells is left out and said.
    const crowded = parsePasted("0,101,325\n1,2");
    expect(crowded.rows).toEqual([{ time: "1", value: "2" }]);
    expect(pasteNote(crowded).text).toContain("line 1 has more than two comma-separated cells");
  });

  it("takes a paste whose every line was left out, and says why, rather than typing it into the cell", () => {
    // Checked by review: thousands written with commas on every line left no row and no skipped line, so the
    // browser pasted the raw text into the time cell and the note was lost.
    const thousands = parsePasted("0,101,325\n1,101,400");
    expect(thousands.rows).toEqual([]);
    expect(pasteTaken(thousands)).toBe(true);
    expect(pasteNote(thousands)).toEqual({ text: expect.stringContaining("lines 1, 2 have more than two comma-separated cells"), warn: true });
    expect(pasteTaken(parsePasted("0\t1\n1\t2"))).toBe(true);
    expect(pasteTaken(parsePasted("\n\n"))).toBe(false);
  });

  it("says which old rows stay after a shorter paste", () => {
    expect(keptAfter(5, 0, 4)).toBe("row 5 kept");
    expect(keptAfter(6, 0, 4)).toBe("rows 5-6 kept");
    expect(keptAfter(4, 0, 4)).toBeUndefined();
    const block = parsePasted("0\t1\n1\t2\n2\t3\n3\t4");
    expect(pasteNote(block, keptAfter(5, 0, block.rows.length))).toEqual({ text: "4 rows pasted; row 5 kept", warn: false });
  });

  it("puts pasted rows over as many rows as it has, keeping the rest", () => {
    const table = defaultDraft("table", "velocity", 1);
    const pasted = pasteRows(table, 1, [{ time: "9", value: "9" }]);
    expect(pasted.rows).toEqual([table.rows[0], { time: "9", value: "9" }, table.rows[2]]);
    const longer = pasteRows(table, 2, [{ time: "8", value: "8" }, { time: "9", value: "9" }]);
    expect(longer.rows.map((r) => r.time)).toEqual([table.rows[0].time, table.rows[1].time, "8", "9"]);
  });
});

describe("what the server says", () => {
  it("shows every refusal the server lists, else the one it names", () => {
    expect(previewRefusals({ ok: false, field: "rows.1.value", message: "row 2 value needs a value" })).toEqual([{ field: "rows.1.value", message: "row 2 value needs a value" }]);
    const both = previewRefusals({
      ok: false,
      field: "rows.1.value",
      message: "first",
      errors: [
        { field: "rows.1.value", message: "first" },
        { field: "rows.2.time", message: "second" },
      ],
    });
    expect(both).toEqual([
      { field: "rows.1.value", message: "first" },
      { field: "rows.2.time", message: "second" },
    ]);
    expect(previewRefusals({ ok: true })).toEqual([]);
    expect(previewRefusals(null)).toEqual([]);
  });

  it("puts each note under its field, and none while the preview is refused", () => {
    const notes = [{ field: "amplitude", message: "the swing is 278.15 K" }, { field: "value", message: "whole" }];
    expect(previewNotes({ ok: true, notes })).toEqual({ amplitude: "the swing is 278.15 K", form: "whole" });
    expect(previewNotes({ ok: false, notes })).toEqual({});
    expect(previewNotes({ ok: true })).toEqual({});
    expect(previewNotes(null)).toEqual({});
  });

  it("puts a refusal under the field it names", () => {
    expect(errorKey("frequency")).toBe("frequency");
    expect(errorKey("rows.2.time")).toBe("rows.2.time");
    expect(errorKey("value")).toBe("form");
    expect(errorKey(undefined)).toBe("form");
  });

  it("scales the waveform into the box with room above and below", () => {
    const shape = plotShape([0, 0.5, 1], [1, 3, 1], 100, 50, 2)!;
    expect(shape.path.startsWith("M0.0 ")).toBe(true);
    expect(shape.low).toBe(1);
    expect(shape.high).toBe(3);
    expect(shape.end).toBe(1);
    expect(shape.meanY).toBeCloseTo(25);
    expect(plotShape([0], [1], 10, 10)).toBeNull();
  });
});

describe("where a boundary varies", () => {
  const sine = { form: "sine", mean: { value: 2, unit: "m/s" }, amplitude: { value: 1, unit: "m/s" }, frequency: { value: 5, unit: "Hz" }, phase: { value: 0, unit: "rad" } };

  it("renders the server's varies as it is: the server applies the energy rule", () => {
    expect(varyingOf({ varies: ["velocity", "temperature"] })).toEqual(["velocity", "temperature"]);
    expect(varyingOf({ varies: ["velocity"] })).toEqual(["velocity"]);
    expect(varyingOf({})).toEqual([]);
  });

  it("says which report follows each varying value", () => {
    const tree = {
      type: "project",
      name: "p",
      params: {},
      children: [
        { type: "boundary", name: "inlet", params: { profiles: { velocity: sine, temperature: { form: "table", rows: [] } } }, varies: ["velocity", "temperature"] },
        // The server leaves out a temperature profile while energy is not solved.
        { type: "boundary", name: "inlet_2", params: { profiles: { temperature: { form: "table", rows: [] } } }, varies: [] },
        // A profile the server says is not in use (a components inlet) is not listed.
        { type: "boundary", name: "side", params: { profiles: { velocity: sine } }, varies: [] },
        { type: "boundary", name: "outlet", params: {} },
      ],
    };
    const varying = varyingInTree(tree);
    expect(varying.map((v) => `${v.boundary}.${v.parameter}`)).toEqual(["inlet.velocity", "inlet.temperature"]);
    const notes = historyNotes(["patchFlowRate(patch=inlet)", "patchAverage(patch=outlet,fields=(p))"], varying);
    expect(notes[0].text).toBe("inlet speed: sine 2 ± 1 m/s, 5 Hz");
    expect(notes[1].text).toBe("inlet temperature: no report over time");
    expect(followingReport(["patchAverage(patch=outlet,fields=(p))", "patchFlowRate(patch=inlet)"], varying)).toBe(1);
    expect(followingReport(["patchAverage(patch=outlet,fields=(p))"], varying)).toBe(0);
  });
});
