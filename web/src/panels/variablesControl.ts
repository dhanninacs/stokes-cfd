/**
 * The variables of the shown run (`results.variables`) and the one step that makes any of them colour the
 * model: `showVariable`.
 *
 * A field the run STORED has its range in `run.fields` and colours at once. Any other variable - a velocity
 * component, a built-in derived quantity such as total pressure or the pressure coefficient, a user's field
 * function - has no range there, so it is asked for once over every surface (`results.range`, the server's
 * min and max over each boundary and the slice), added to the run's field list as an entry like any other,
 * and then chosen. The field route serves its values (`/api/results/field`, the calculator fallback), so the
 * walls, the slices and the legend all follow it. Before this, a component had no range and no legend.
 *
 * When the results are read again (a new time, a new run) the stored list is replaced, so the shown variable
 * is ranged again (`useStore.subscribe` below).
 *
 * The catalogue is kept in a small store of its own: it is the server's answer about the run, not model
 * state, and the results store's shape stays the API's.
 */
import { create } from "zustand";
import { useStore } from "../state/store";
import { visibleSlices } from "./sliceModel";
import { planeQuery } from "../state/slicePlane";
import { chooseField, NO_FIELD } from "./fieldsModel";
import { readVariables, type VariablesReply } from "./variablesModel";
import { loadResults } from "./resultsControl";

interface VariablesState {
  run: string | null;
  reply: VariablesReply | null;
  loading: boolean;
  error: string;
}

export const useVariables = create<VariablesState>(() => ({ run: null, reply: null, loading: false, error: "" }));

let asked: string | null = null;

/** Ask `results.variables` for the run on screen, once per run (and again with `force`). */
export async function loadVariables(force = false): Promise<void> {
  const { client, results } = useStore.getState();
  const run = results.data?.run ?? null;
  if (client === null || run === null) return;
  if (!force && asked === run) return;
  asked = run;
  useVariables.setState({ loading: true, error: "" });
  try {
    const reply = readVariables(await client.call("results.variables", { run }));
    useVariables.setState({ run, reply, loading: false, error: reply === null ? "The server's list of variables was not understood." : "" });
  } catch (e) {
    useVariables.setState({ run, reply: null, loading: false, error: e instanceof Error ? e.message : String(e) });
  }
}

/** The surfaces a range is asked over: every boundary, and the slice on screen (with its plane). */
async function rangeSurfaces(): Promise<{ surfaces: string[]; plane?: string }> {
  const s = useStore.getState();
  const client = s.client!;
  const run = s.results.data!.run;
  const info = await client.call<{ surfaces: { id: string; kind: string }[] }>("results.surfaces", { run });
  const patches = info.surfaces.filter((x) => x.kind === "patch").map((x) => x.id);
  const listed = visibleSlices(s.slices)[0];
  const hasSlice = info.surfaces.some((x) => x.kind === "slice");
  if (listed !== undefined) {
    const plane = planeQuery(listed.plane);
    return plane === null ? { surfaces: patches } : { surfaces: [...patches, "slice"], plane };
  }
  return { surfaces: hasSlice ? [...patches, "slice"] : patches };
}

/** Add a variable's range to the run's field list, so the colours, the legend and the menus know it. */
export async function rangeVariable(name: string): Promise<void> {
  const s = useStore.getState();
  if (s.client === null || s.results.data === null) throw new Error("No results are loaded");
  if (s.results.data.fields.some((f) => f.name === name)) return;
  const ask = await rangeSurfaces();
  const out = await s.client.call<{ units: string; min: number; max: number; low: number; high: number }>("results.range", {
    run: s.results.data.run,
    field: name,
    share: 0.01,
    ...ask,
  });
  if (!(out.max >= out.min)) throw new Error(`${name} has no finite value on the model`);
  useStore.setState((st) => {
    const data = st.results.data;
    if (data === null || data.fields.some((f) => f.name === name)) return st;
    const entry = { name, units: out.units, min: out.min, max: out.max, ...(out.high > out.low ? { p01: out.low, p99: out.high } : {}) };
    return { results: { ...st.results, data: { ...data, fields: [...data.fields, entry] } } };
  });
}

/**
 * Read the run's fields again at the time on screen (`run.time`): a run extracted before the agent computed
 * vorticity, Q, wall shear stress and y+ gets them. The host reads in the background; `run.changed` follows and
 * the results are read again (`loadResults`), which reloads the catalogue. Resolves when that has happened,
 * or to the reason it could not.
 */
export async function readFieldsAgain(): Promise<string | null> {
  const s = useStore.getState();
  const client = s.client;
  const data = s.results.data;
  if (client === null || data === null) return "No results are loaded";
  const done = new Promise<string | null>((resolve) => {
    const off = client.onEvent((event, payload) => {
      if (payload.run_id !== data.run) return;
      if (event === "run.changed") {
        off();
        void loadResults().then(() => resolve(null));
      } else if (event === "run.failed_extract") {
        off();
        resolve(typeof payload.error === "string" && payload.error !== "" ? payload.error : "The fields could not be read again");
      }
    });
  });
  try {
    await client.call("run.time", { run: data.run, time: data.time });
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
  return done;
}

/** Colour the model by any variable: ranged first when the run did not store it. Resolves to the reason when it cannot. */
export async function showVariable(name: string): Promise<string | null> {
  try {
    await rangeVariable(name);
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
  useStore.setState((st) => ({ results: chooseField(st.results, name) }));
  return null;
}

// A new run or a new time replaces the field list: range the shown variable again, and read the catalogue.
useStore.subscribe((state, previous) => {
  const data = state.results.data;
  if (data === previous.results.data || data === null) return;
  // Read again for every new field list: a new run, a new time, or fields read again with more in them.
  void loadVariables(true);
  const field = state.results.view.field;
  if (field !== NO_FIELD && !data.fields.some((f) => f.name === field)) void rangeVariable(field).catch(() => undefined);
});
