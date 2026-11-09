/**
 * The streamlines control: seed a rake on a slice and draw the lines (results item 4).
 *
 * **Seeds come from a SLICE, not a wall** - and that is a measured constraint, not a preference: the
 * audit's duct has its inlet and outlet planes perpendicular to the flow and its walls at exactly zero
 * velocity, so **no patch of that model can carry a streamline**. A slice through the fluid can, and it
 * is the surface a user is already looking at.
 *
 * **The section is disabled until a slice exists**, with the reason printed - the same rule the rest of
 * this panel follows: no control that will be refused, and no silence about why.
 */
import { useMemo, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { currentScene } from "../viewport/scene";
import { MAX_SEEDS, NO_STREAMLINES, seedRake, speedRangeOf } from "./streamlineModel";
import { inletPatchesOf } from "./lookModel";
import { meshPatchesOf } from "../state/meshPatches";
import { readBoundaries } from "../guided/guidedTree";
import { Note } from "../components/Note";

/**
 * The fields a streamline can follow, by the names a solver writes them under - in order of preference.
 *
 * **A named list, not "any 3-component field"**: the cache does not carry a component count in
 * `run.fields`, so asking for velocity by its conventional name is the honest way rather than guessing
 * from a shape.
 */
const VECTOR_PREFERENCE = ["U", "U_0", "velocity", "V"] as const;

export default function StreamlineSection() {
  const slices = useStore((s) => s.slices);
  const execute = useStore((s) => s.execute);
  const lines = useStore((s) => s.streamlines);
  const setLines = useStore((s) => s.setStreamlines);
  /*
    **A VECTOR field, not the scalar the legend shows.** Measured: the Results panel's field is `U_mag`,
    and `run.streamlines` refuses it correctly - *"not a vector field; streamlines need a direction"*.
    A streamline follows a direction, so the control asks the run for its velocity, by the conventional
    name, and says which field it used.
  */
  /*
    **`?? []` in a selector returns a FRESH ARRAY on every read**, which makes every render a state
    change and React throws #185 with a blank window - the second time this session. The data object
    itself is stable, so the field list is derived from it in a `useMemo`.
  */
  const data = useStore((s) => s.results.data);
  const fields = useMemo(() => data?.fields ?? [], [data]);
  const field = VECTOR_PREFERENCE.find((name) => fields.some((f) => f.name === name)) ?? null;
  const [busy, setBusy] = useState(false);
  /*
    **Ready when the RUN has slice data, not when a slice ROW exists.** Measured: the audit's run has a
    cached slice (`run.fields` answers `slice: true`) while `slices.list` is empty - the rows are the
    user's, the data is the run's, and a streamlines control that waited for a row refused to work on a
    project that had exactly what it needed. The mid-plane toggle is the user's way to SHOW that slice;
    it is not a precondition for tracing on it.
  */
  const hasSlice = useStore((s) => s.results.data?.slice === true);
  const ready = hasSlice || slices.list.some((s) => s.visible);

  const seed = async () => {
    if (field === null) {
      setLines({ ...NO_STREAMLINES, phase: "failed", message: "this run has no velocity field to trace" });
      return;
    }
    const box = currentScene()?.modelBounds() ?? null;
    if (box === null) {
      setLines({ ...NO_STREAMLINES, phase: "failed", message: "the model is not loaded yet" });
      return;
    }
    // **A rake across the model, at the slice's height.** The slice itself decides which surface the
    // lines run on (the command reads `patch: "slice"`), so the seeds only need to be points INSIDE it.
    const normal = 2 as const;
    const seeds = seedRake(
      { min: box.min, max: box.max },
      normal,
      MAX_SEEDS,
    );
    setBusy(true);
    setLines({ ...NO_STREAMLINES, phase: "computing" });
    try {
      type Reply = { lines?: { points: [number, number, number][]; speeds: number[] }[] };
      const maxLength = 2 * Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
      /*
        **Through the volume first, from every inlet** (2026-11-04, as the Looks card does): the host spreads the
        seeds over the inlet faces and traces through the 3-D mesh. A host that cannot falls back to the rake on
        the slice, which is what this section always drew.
      */
      const tree = useStore.getState().tree;
      const inlets = inletPatchesOf(readBoundaries(tree), meshPatchesOf(tree)).map((p) => p.name);
      const volume = { patch: "volume", source: "volume", field, direction: "forward", max_length: maxLength, step: 0.2 };
      let reply: Reply;
      try {
        reply = (await execute("run.streamlines", inlets.length > 0 ? { ...volume, seed_patches: inlets, count: MAX_SEEDS } : { ...volume, seeds }, { silent: true })) as Reply;
      } catch {
        reply = (await execute("run.streamlines", { patch: "slice", field, seeds, direction: "forward", max_length: maxLength })) as Reply;
      }
      const found = reply.lines ?? [];
      setLines({
        phase: found.length === 0 ? "failed" : "ready",
        lines: found,
        range: speedRangeOf(found),
        /*
          **Told to a USER, not to a developer** (claude-win2: *"make the empty-result message
          user-facing and useful, no jargon"*). The measured fact is that the flow crosses the plane
          rather than running along it, so a line leaves the surface almost at once - and the fix is a
          different plane or a different tool. The message says both.
        */
        message:
          found.length === 0
            ? "The flow crosses this plane rather than running along it, so a line leaves the surface at once. Try a plane along the flow, or use the colouring to read the speed."
            : "",
      });
    } catch (err) {
      setLines({ ...NO_STREAMLINES, phase: "failed", message: String((err as Error).message ?? err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mesh-section" aria-label="Streamlines" data-ws="results-streamlines">
      <div className="section-title">Streamlines</div>
      <TooltipFree label="A slice gives the lines something to follow. Without one the model has no surface with flow along it.">
        <Button
          aria-label="Draw streamlines on the slice"
          disabled={!ready || busy}
          onClick={() => void seed()}
          data-ws="streamlines-draw"
        >
          {busy ? <LoaderCircle size={13} className="mesh-spin" aria-hidden="true" /> : null}
          Draw streamlines
        </Button>
      </TooltipFree>
      {!ready && <Note className="mesh-hint" tip="Add a slice first: the lines follow a surface, and the walls carry no flow.">Add a slice first</Note>}
      {ready && field === null && (
        <Note className="mesh-hint" tip={`This run has no velocity field (${VECTOR_PREFERENCE.join(", ")}) to trace.`}>No velocity field</Note>
      )}
      {lines.phase === "failed" && <p className="muted mesh-hint" data-ws="streamlines-failed">{lines.message}</p>}
      {lines.phase === "ready" && (
        <p className="muted mesh-hint" data-ws="streamlines-count">
          {lines.lines.length} {lines.lines.length === 1 ? "line" : "lines"} of {field}
          {lines.range ? `, ${lines.range.min.toFixed(3)} to ${lines.range.max.toFixed(3)} m/s` : ""}
        </p>
      )}
      {lines.phase === "ready" && (
        <Button aria-label="Clear the streamlines" onClick={() => setLines(NO_STREAMLINES)} data-ws="streamlines-clear">
          Clear
        </Button>
      )}
    </section>
  );
}

/** A caption with the panel's usual muted style, so a disabled control says why it is disabled. */
function TooltipFree({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="streamline-row">
      {children}
      <span className="muted streamline-why">{label}</span>
    </div>
  );
}
