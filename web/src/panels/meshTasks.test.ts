/**
 * The stepper's pure model (brief M-502 §UI 1): every state → its dot, the value line, and which
 * row is current. The reply shape is the backend's, taken verbatim from `mesh.tasks`.
 */
import { describe, expect, it } from "vitest";
import { headWords, stepHeading, stepperRows, stepperVisibility, type TasksReply } from "./meshTasks";

/** A reply with the nine tasks in the backend's order, every one in the state given. */
function reply(states: Record<string, string>, todos: Record<string, string[]> = {}): TasksReply {
  const ids = [
    "geometry", "bodies", "boundaries", "sizing", "surface",
    "layers", "volume", "quality", "fidelity",
  ];
  const rows: Record<string, string> = { geometry: "fluid · closed · 1.8 MiB to send" };
  return {
    tasks: ids.map((id) => ({
      id,
      title: id === "fidelity" ? "Fidelity to CAD" : id,
      state: states[id] ?? "done",
      todo: todos[id] ?? [],
      row: rows[id] ?? `${id} summary`,
    })),
    current: "geometry",
    next: "bodies",
    mesher: "snappy",
    host: "local",
  };
}

describe("stepperRows", () => {
  it("maps each state to its dot, and a done task earns no dot at all", () => {
    const rows = stepperRows(
      reply({
        geometry: "idle",
        bodies: "todo",
        boundaries: "running",
        sizing: "done",
        surface: "stale",
        layers: "failed",
        volume: "done",
        quality: "done",
        fidelity: "ready",
      }),
    );
    const dot = Object.fromEntries(rows.map((r) => [r.id, r.dot]));
    expect(dot).toEqual({
      geometry: "new", // idle → new
      bodies: "warning", // todo → warning
      boundaries: "running", // running → the spinner
      sizing: "", // done → no dot: green is not news
      surface: "stale",
      layers: "failed",
      volume: "",
      quality: "",
      fidelity: "new", // ready → new
    });
    expect(rows.find((r) => r.id === "boundaries")?.spin).toBe(true);
    expect(rows.find((r) => r.id === "bodies")?.spin).toBe(false);
  });

  it("an unknown state warns rather than going silent", () => {
    const rows = stepperRows(reply({ geometry: "hibernating" }));
    expect(rows[0].dot).toBe("warning");
  });

  it("the value line is the first todo, else the task's summary", () => {
    const rows = stepperRows(
      reply({ geometry: "done" }, { geometry: ["the inlet has no boundary condition"] }),
    );
    expect(rows[0].value).toBe("the inlet has no boundary condition");
    const quiet = stepperRows(reply({}));
    expect(quiet[0].value).toBe("fluid · closed · 1.8 MiB to send");
    const bare = stepperRows({ ...reply({}), tasks: [{ id: "x", title: "x", state: "todo", todo: [] }] });
    expect(bare[0].value).toBe("");
  });

  it("current follows the selected task while it stays chosen, else the reply's", () => {
    const nine = reply({});
    expect(stepperRows(nine, "quality").find((r) => r.current)?.id).toBe("quality");
    // A selection that is not a task id falls back to the reply's current, not to nothing.
    expect(stepperRows(nine, "not-a-task").find((r) => r.current)?.id).toBe("geometry");
    expect(stepperRows(nine).find((r) => r.current)?.id).toBe("geometry");
  });

  it("every row is clickable, so every task reaches its page", () => {
    const rows = stepperRows(reply({}));
    expect(rows).toHaveLength(9);
    expect(rows.every((r) => r.clickable)).toBe(true);
  });

  it("task 9 keeps the owner's title, Fidelity to CAD", () => {
    const rows = stepperRows(reply({ fidelity: "done" }));
    expect(rows[8].title).toBe("Fidelity to CAD");
  });
});

describe("stepHeading", () => {
  it("says which step of nine, by the same current rule as the rows", () => {
    const nine = reply({});
    expect(stepHeading(nine)).toBe("Step 1 of 9 · geometry");
    expect(stepHeading(nine, "quality")).toBe("Step 8 of 9 · quality");
  });
});

describe("headWords", () => {
  it("joins the mesher and host, and stays out of the way when both are empty", () => {
    expect(headWords(reply({}))).toBe("snappy · local");
    expect(headWords({ ...reply({}), mesher: "", host: "" })).toBe("");
    expect(headWords({ ...reply({}), host: "" })).toBe("snappy");
  });
});
describe("stepperVisibility", () => {
  it("shows the line on every Mesh-stage page and the block only when the preference says so", () => {
    expect(stepperVisibility("Mesh", "shown")).toEqual({ line: true, block: true });
    // "Hidden" hides the BLOCK, never the line: Q2's "always shown at the top" is the line's job.
    expect(stepperVisibility("Mesh", "hidden")).toEqual({ line: true, block: false });
    // Outside the Mesh stage neither shows.
    expect(stepperVisibility("Geometry", "shown")).toEqual({ line: false, block: false });
    expect(stepperVisibility("Results", "shown")).toEqual({ line: false, block: false });
  });
});
