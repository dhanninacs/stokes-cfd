/**
 * The Looks card's hands: write what `panels/lookModel.ts` decided into the store, and make the two asks it
 * cannot answer itself (an iso-surface and streamlines both come from the server). The model is pure and tested;
 * this file only reads the store and the scene, applies the reducer's result, and reports in words.
 *
 * **One write per press.** `runLooks` builds the model's state from the store, runs the actions through
 * `applyLookActions`, and `setState`s only the fields that changed, in a single call: a tile turns the slice off
 * AND sets a section in one step, so `viewport/fields.ts` (which engages and releases the slice's cut as the
 * store changes) sees the section the user's press set rather than a half-way state.
 *
 * **The asks are the Full-mode sections' own** (`IsoSection`, `StreamlineSection`): the same commands, the same
 * stores (`setIso` with `showIso`, `setStreamlines`), the same re-ask for a lost `run.iso_ready`. Only the seeds
 * differ (the card's are `streamSeeds`: a line, a ring, the inlet), and so does where the answer's words go: to
 * the card's note through `report`, not to a section.
 */
import { StokesApiError } from "../api/client";
import { useStore, type StokesState } from "../state/store";
import { slicePlaneOf } from "../state/sectionState";
import { meshPatchesOf, meshSourceOf } from "../state/meshPatches";
import { currentScene } from "../viewport/scene";
import { forgetSavedSection } from "../viewport/fields";
import { fitSourcesFor, missingPatchSources } from "../viewport/fitTargets";
import { SLICE_SOURCE } from "../panels/fieldsModel";
import { sliceSourceOf, visibleSlices } from "../panels/sliceModel";
import { ISO_CODES, ISO_COMMAND, ISO_POLL_MAX_MS, ISO_POLL_MS, isoRefusalText, isoStateText, readIsoEvent, readIsoReply, sameSurface, showIso, type IsoSummary } from "../panels/isoModel";
import { FIT_POLL_MS, NEW_FIT_WATCH, applyLookActions, controlActions, lookSliceOf, faceOnDirection, streamlinesStalled, inletPatchOf, inletPatchesOf, isoRefusalWord, noLinesNote, stepFitWatch, streamField, streamSeeds, unionBox, viewFromFor, type Box, type LookAction, type LookContext, type LookRequest, type LookState, type SeedKind } from "../panels/lookModel";
import { findSolverSettings } from "../panels/runPanelModel";
import { NO_STREAMLINES, speedRangeOf } from "../panels/streamlineModel";
import { NO_VECTOR_CLOUD, volumeArrowsShown } from "../panels/arrowModel";
import { fluidBody, readBoundaries, readBodies } from "./guidedTree";

/** The part of the store the looks read and write. */
export function lookStateOf(s: StokesState): LookState {
  return { results: s.results, slices: s.slices, iso: s.iso, streamlines: s.streamlines, arrows: s.arrows, section: s.section, orthographic: s.orthographic, hiddenNodes: s.hiddenNodes };
}

/** The box of the inlet patch in the scene, or null when the case has no inlet or its patch is not drawn. */
function inletBox(s: StokesState): Box | null {
  const patch = inletPatchOf(readBoundaries(s.tree), meshPatchesOf(s.tree));
  return patch === null ? null : (currentScene()?.sourceBounds(meshSourceOf(patch.id)) ?? null);
}

/**
 * The plane of the run's own mid-plane slice: the solver settings' `slice_origin` and `slice_normal` when both are
 * set, else the flat axis of the slice that is in the scene (`slicePlaneOf`'s own fallback, the one the Section
 * view's cut uses). **The settings are empty for the default plane** (the agent picks the mesh's shortest axis and
 * nudges the origin off the faces, `default_plane`), so reading the settings alone left the Slice look's Axis row
 * with no chip lit on pipe3 and sent the stream seeds to a z plane the slice was not in. Null until the slice has
 * arrived: the card re-reads when the slice's cut sets the section.
 */
function midPlaneOf(s: StokesState): LookContext["midPlane"] {
  const params = findSolverSettings(s.tree)?.params as Record<string, unknown> | undefined;
  return slicePlaneOf(params, currentScene()?.sourceBounds(SLICE_SOURCE) ?? null);
}

/** What the model needs besides the stores, read now: the scene's box, the run's slice plane and whether the inlet is there. */
export function lookContext(): LookContext {
  const s = useStore.getState();
  // An inlet the tree names is enough: the host seeds from its patch, whether or not the scene has drawn it.
  const hasInlet = inletPatchesOf(readBoundaries(s.tree), meshPatchesOf(s.tree)).length > 0;
  return { data: s.results.data, bounds: currentScene()?.modelBounds() ?? null, midPlane: midPlaneOf(s), hasInlet, hasRake: s.line.a !== null && s.line.b !== null };
}

/**
 * A word or two about an ask, or "" for nothing to say, whether the ask is still going (the card shows the tile of
 * the look being made while it is), and the sentence for the note's tooltip: the card carries no sentences.
 */
export type Report = (text: string, busy: boolean, tip?: string) => void;

/** Writes the actions in one `setState` and makes the asks they leave. */
export function runLooks(actions: readonly LookAction[], report: Report = () => undefined): void {
  if (actions.length === 0) return;
  // An ask still in flight is answered for a look the user has left: the answer must not draw it back.
  if (actions.some((a) => a.kind === "clearStreamlines")) streamSeq++;
  if (actions.some((a) => a.kind === "arrows" && !a.shown)) vectorSeq++;
  if (actions.some((a) => a.kind === "hideIso")) cancelIsoAsk();
  const store = useStore.getState();
  const before = lookStateOf(store);
  const { state, requests } = applyLookActions(before, actions);
  // **Before the write**: the slice's cut gives the user's section back when the same press takes the slice away.
  if (requests.some((r) => r.kind === "dropSavedSection")) forgetSavedSection();
  const patch: Partial<StokesState> = {};
  for (const key of Object.keys(state) as (keyof LookState)[]) {
    if (state[key] !== before[key]) Object.assign(patch, { [key]: state[key] });
  }
  if (Object.keys(patch).length > 0) useStore.setState(patch);
  report("", false);
  for (const request of requests) ask(request, report);
}

function ask(request: LookRequest, report: Report): void {
  if (request.kind === "dropSavedSection") return;
  if (request.kind === "fit") {
    const how: FitHow = { faceOn: request.faceOn === true, slice: request.slice === true };
    if (request.settle === true) fitWhenSettled(how);
    else fitNow(how);
  } else if (request.kind === "askIso") void askIso(request.field, request.value, report);
  else if (request.kind === "askVectors") void askVectors(request.count, report);
  else void askStreamlines(request.seeds, request.count, request.tubes, report);
}

// ---------------------------------------------------------------------------------------------- volume vectors
let vectorSeq = 0;

/**
 * `run.vectors`: the velocity on an even lattice through the fluid, drawn as arrows by the viewport (`vectorCloud`).
 * **A host that cannot sample falls back to the arrows on the slice** (the Where control's other choice), so the
 * Vectors look never goes empty for want of an agent op.
 */
async function askVectors(count: number, report: Report): Promise<void> {
  const s = useStore.getState();
  const mine = ++vectorSeq;
  s.setVectorCloud({ ...NO_VECTOR_CLOUD, phase: "computing" });
  report("Sampling…", true);
  try {
    type Reply = { points?: [number, number, number][]; vectors?: [number, number, number][]; spacing?: number };
    const reply = (await s.execute("run.vectors", { count }, { silent: true })) as Reply;
    if (mine !== vectorSeq) return;
    const points = reply.points ?? [];
    const vectors = reply.vectors ?? [];
    useStore.getState().setVectorCloud({ phase: points.length > 0 ? "ready" : "failed", points, vectors, spacing: reply.spacing ?? 0, message: "" });
    report(points.length > 0 ? "" : "No arrows", false, points.length > 0 ? undefined : "The lattice found no fluid to sample");
  } catch (err) {
    if (mine !== vectorSeq) return;
    useStore.getState().setVectorCloud({ ...NO_VECTOR_CLOUD, phase: "failed", message: String((err as Error).message ?? err) });
    // The slice's arrows instead: the same look, on the plane the run already cut.
    const st = useStore.getState();
    if (volumeArrowsShown(st.arrows)) {
      runLooks(controlActions("vectors", "where", "slice", lookStateOf(st), lookContext()).actions);
      report("On the slice", false, `The host could not sample the volume (${String((err as Error).message ?? err)}), so the arrows are on the slice`);
    }
  }
}

// ---------------------------------------------------------------------------------------------- the settled fit
let fitTimer = 0;

/**
 * The scene sources the first show waits for, and whether all are in the scene: the mid-plane slice (when it is on),
 * the slices of the list, and every mesh patch the tree lists. The patches are the body to frame: a fit made before
 * the last of them landed framed the ones that had (R4c).
 */
function wantedSourcesReady(): boolean {
  const s = useStore.getState();
  const scene = currentScene();
  if (scene === null) return false;
  const wanted: string[] = [];
  if (s.results.view.slice && s.results.data?.slice === true) wanted.push(SLICE_SOURCE);
  for (const spec of visibleSlices(s.slices)) wanted.push(sliceSourceOf(spec.id));
  if (!wanted.every((source) => scene.hasSource(source))) return false;
  const listed = meshPatchesOf(s.tree).map((entry) => meshSourceOf(entry.id));
  return missingPatchSources(listed, scene.sourceIds()).length === 0;
}

/** The sources a Looks fit frames now, and the box they make (the scene's own bounds of each, shown or not). */
function fitTargetsNow(scene: NonNullable<ReturnType<typeof currentScene>>): { targets: string[] | null; bounds: Box | null } {
  const fluid = fluidBody(readBodies(useStore.getState().tree))?.id ?? null;
  const targets = fitSourcesFor(scene.sourceIds(), fluid);
  return { targets, bounds: targets === null ? scene.modelBounds() : unionBox(targets.map((source) => scene.sourceBounds(source))) };
}

/** How a fit looks: along the model's thin axis (a sheet), and/or at the slice that is on, face-on (R4d). */
interface FitHow {
  faceOn: boolean;
  slice: boolean;
}

/**
 * The direction the fit looks from, read now: the slice's normal when the fit is for a slice and one is showing
 * (`viewFromFor`, the Slice look's rule; the axis is the slice's at the time of the fit), else the sheet's thin axis,
 * else undefined (the camera's own direction).
 */
function fitFromNow(scene: NonNullable<ReturnType<typeof currentScene>>, how: FitHow): [number, number, number] | undefined {
  // A view the user has turned is theirs: entering Results frames the slice but keeps the direction they chose.
  if (scene.rig.touched) return undefined;
  if (how.slice) {
    const found = lookSliceOf(lookStateOf(useStore.getState()), lookContext());
    const from = viewFromFor("slice", found?.axis ?? null, useStore.getState().results.data?.twoD === true);
    if (from !== null) return from;
  }
  return how.faceOn ? faceOnDirection(scene.modelBounds()) : undefined;
}

/**
 * The Looks card's fit: the fluid body's patches with the slice kept inside (`fitSourcesFor`), through
 * `Scene.fitSources`, looking along the model's thin axis when `faceOn` (a sheet). The `F` key's fit frames every
 * shown body, which in the Results stage is also the cut and the iso. Without a scene it is the store's request.
 * `note` is the breadcrumb's label (`console.debug`, silent at default levels): what was fitted, and its box.
 */
function fitNow(how: FitHow, note?: string): void {
  const scene = currentScene();
  if (scene === null) {
    useStore.getState().requestFit();
    return;
  }
  const { targets, bounds } = fitTargetsNow(scene);
  const from = fitFromNow(scene, how);
  if (note !== undefined) console.debug(`[stokes] ${note}`, { targets, bounds, sources: scene.sourceIds(), faceOn: how.faceOn, slice: how.slice, from: from ?? null });
  scene.fitSources(targets, from);
}

/** Drops a first-show fit still waiting for the scene to settle: a saved view that is applied has its own camera, which that fit would override. */
export function cancelSettledFit(): void {
  window.clearTimeout(fitTimer);
}

/**
 * The Looks card's fit (`fitNow`), asked once the scene has stopped changing: the box the fit will frame the same for
 * `FIT_STABLE_POLLS` looks and the slice and the last mesh patch in the scene (`stepFitWatch`, which also gives up
 * waiting). A second call replaces the first, so two first shows never queue two fits. The box watched is that of the
 * fit's own targets, not the model's (the slice's alone while the patches are still on their way).
 */
function fitWhenSettled(how: FitHow): void {
  cancelSettledFit();
  let watch = NEW_FIT_WATCH;
  const look = (): void => {
    const scene = currentScene();
    const step = stepFitWatch(watch, { box: scene === null ? null : fitTargetsNow(scene).bounds, sourcesReady: wantedSourcesReady() });
    watch = step.watch;
    if (step.fit) {
      fitNow(how, "first-show fit");
      return;
    }
    fitTimer = window.setTimeout(look, FIT_POLL_MS);
  };
  fitTimer = window.setTimeout(look, FIT_POLL_MS);
}

// ---------------------------------------------------------------------------------------------- iso
let isoSeq = 0;
let isoStop: (() => void) | null = null;

/**
 * Drop an iso ask still in flight, so its answer cannot draw the surface back after the iso was hidden. The
 * Results panel's `IsoSection` calls it when a new run's results replace the old (it hides the iso then), because
 * a cut the card started for the old run can still be answering.
 */
export function cancelIsoAsk(): void {
  isoSeq++;
  isoStop?.();
  isoStop = null;
}

/**
 * `run.iso_surface`, then what `IsoSection` does with the answer: a ready surface goes to the viewport, an empty
 * one is a line in words, a cut under way is waited for (the event is the answer; a slow re-ask covers an event
 * lost to a reconnect). A newer ask cancels the older, so a slider drag never lets a late answer win.
 */
async function askIso(field: string, value: number, report: Report): Promise<void> {
  const { client, results } = useStore.getState();
  const data = results.data;
  if (client === null || data === null) return;
  const mine = ++isoSeq;
  isoStop?.();
  isoStop = null;
  report("Cutting…", true);

  const settle = (summary: IsoSummary): void => {
    if (mine !== isoSeq) return;
    const { iso } = useStore.getState();
    useStore.getState().setIso(showIso(iso, summary));
    if (summary.state === "empty") report("Nothing there", false, isoStateText(summary));
    else report("", false);
  };
  const refuse = (code: string, message: string): void => {
    if (mine === isoSeq) report(isoRefusalWord(code), false, isoRefusalText(code, message));
  };
  const call = async (): Promise<IsoSummary | null> => {
    try {
      const summary = readIsoReply(await client.call(ISO_COMMAND, { run: data.run, field, value }));
      if (mine !== isoSeq) return null;
      if (summary === null) refuse("", "The server's answer about the iso-surface was not understood.");
      return summary;
    } catch (err) {
      refuse(err instanceof StokesApiError ? err.code : "", err instanceof Error ? err.message : String(err));
      return null;
    }
  };

  const first = await call();
  if (first === null || mine !== isoSeq) return;
  if (first.state !== "cutting") {
    settle(first);
    return;
  }
  // **Cutting**: wait for `run.iso_ready` / `run.iso_failed`, re-asking slowly in case the event is lost.
  const off = client.onEvent((event, payload) => {
    const found = readIsoEvent(event, payload);
    if (found === null || mine !== isoSeq) return;
    if (found.kind === "ready") {
      if (sameSurface(found.summary, first)) stop(() => settle(found.summary));
    } else if (sameSurface(found, first) && found.code !== ISO_CODES.busy) {
      stop(() => refuse(found.code, found.message));
    }
  });
  let timer = 0;
  let delay = ISO_POLL_MS;
  const stop = (then?: () => void): void => {
    off();
    window.clearTimeout(timer);
    if (isoStop === halt) isoStop = null;
    then?.();
  };
  const halt = (): void => stop();
  isoStop = halt;
  const again = (): void => {
    timer = window.setTimeout(() => {
      void call().then((next) => {
        if (next === null || mine !== isoSeq) return;
        if (next.state === "cutting") {
          delay = Math.min(delay * 2, ISO_POLL_MAX_MS);
          again();
        } else {
          stop(() => settle(next));
        }
      });
    }, delay);
  };
  again();
}

// ---------------------------------------------------------------------------------------------- streamlines
let streamSeq = 0;

/** Says that an ask found nothing, as `noLinesNote` words it. */
function reportNoLines(report: Report, kind: SeedKind, seeds: number): void {
  const note = noLinesNote(kind, seeds);
  report(note.text, false, note.tip);
}

/**
 * `run.streamlines` THROUGH THE VOLUME, seeded by the card's choice (ADR-0030).
 *
 * **Inlet seeds are the host's**: the command names every inlet's patch (`inletPatchesOf`) and the host spreads the
 * seeds over those faces and moves them into the fluid, so each inlet gets its share of the lines wherever its face
 * lies. The other kinds are points: a line, a grid or a ring on the mid-plane (the solver settings' `slice_normal` and
 * `slice_origin`, the middle of the model when they say none), or the rake the user picked.
 *
 * **The slice trace is only the fallback** for a host that cannot trace through the volume (no VTK, an older agent):
 * the points are traced on the run's cached slice, and the inlet kind uses the row where the inlet meets the plane.
 */
async function askStreamlines(kind: SeedKind, count: number, tubes: boolean, report: Report): Promise<void> {
  const s = useStore.getState();
  const field = streamField(s.results.data);
  const box = currentScene()?.modelBounds() ?? null;
  if (field === null) return report("No velocity", false, "The run has no velocity to follow");
  if (box === null) return report("Not drawn yet", false, "The model is not drawn yet");
  const plane = midPlaneOf(s);
  const normal = plane === null ? 2 : (["x", "y", "z"] as const).indexOf(plane.axis);
  const offset = plane?.offset ?? (box.min[normal] + box.max[normal]) / 2;
  const rake = s.line.a !== null && s.line.b !== null ? ([s.line.a, s.line.b] as const) : null;
  const inlets = kind === "inlet" ? inletPatchesOf(readBoundaries(s.tree), meshPatchesOf(s.tree)).map((p) => p.name) : [];
  const seeds = streamSeeds(kind, { box, normal: normal as 0 | 1 | 2, offset, inlet: kind === "inlet" ? inletBox(s) : null, rake }, count);
  const mine = ++streamSeq;
  if (seeds.length === 0 && inlets.length === 0) {
    s.setStreamlines(NO_STREAMLINES);
    return reportNoLines(report, kind, 0);
  }
  s.setStreamlines({ ...NO_STREAMLINES, phase: "computing" });
  report("Tracing…", true);
  try {
    const maxLength = 2 * Math.max(box.max[0] - box.min[0], box.max[1] - box.min[1], box.max[2] - box.min[2]);
    type Reply = { lines?: { points: [number, number, number][]; speeds: number[] }[] };
    const volume = { patch: "volume", source: "volume", field, direction: "forward", max_length: maxLength, step: 0.2 };
    let reply: Reply;
    let onSlice = false;
    try {
      reply = (await s.execute("run.streamlines", inlets.length > 0 ? { ...volume, seed_patches: inlets, count } : { ...volume, seeds }, { silent: true })) as Reply;
    } catch {
      if (seeds.length === 0) throw new Error("The host cannot trace through the volume, and the slice does not cross the inlet");
      onSlice = true;
      reply = (await s.execute("run.streamlines", { patch: "slice", field, seeds, direction: "forward", max_length: maxLength })) as Reply;
    }
    if (mine !== streamSeq) return;
    const lines = reply.lines ?? [];
    useStore.getState().setStreamlines({ phase: lines.length === 0 ? "failed" : "ready", lines, range: speedRangeOf(lines), message: "", tubes });
    if (lines.length === 0) reportNoLines(report, kind, Math.max(seeds.length, inlets.length));
    else if (onSlice && streamlinesStalled(lines, box)) report("Flow crosses slice", false, "The flow goes through this slice, not along it, so lines traced in it stay put. Use a slice along the flow.");
    else report("", false);
  } catch (err) {
    if (mine !== streamSeq) return;
    useStore.getState().setStreamlines({ ...NO_STREAMLINES, phase: "failed", message: String((err as Error).message ?? err) });
    report("Not traced", false, String((err as Error).message ?? err));
  }
}
