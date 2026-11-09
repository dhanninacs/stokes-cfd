/**
 * The Slices section of the Results panel: cut the results at a plane and look at the cut. "Add slice" puts a
 * plane normal to X, Y or Z in the middle of the model (a slider then moves it, bounded by the model), "From
 * the section plane" copies the Section view's plane; each slice in the list can be shown or hidden, painted
 * with its own field (or the surfaces' field) and removed. Up to eight at a time.
 *
 * Each slice is painted with the colour map (smooth, or in flat **Bands**) and can carry contour **Lines**, N
 * levels (5 to 20) for both: N bands have N - 1 lines between them, so the lines are the bands' edges.
 *
 * Everything that decides something is `sliceModel.ts` (tested); `viewport/sliceLayer.ts` cuts and draws what
 * the list holds and reports what each cut is doing (cutting, ready, the plane misses the model). This file
 * only draws and writes the store's `slices` field. View state: nothing is a project command or journaled.
 *
 * A slider drag is debounced: the plane is asked of the server 300 ms after the slider rests, and the old cut
 * stays on screen until the new one arrives.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ChevronsUpDown, CircleAlert, Eye, EyeOff, LoaderCircle, Plus, X } from "lucide-react";
import { Button, IconButton, Segmented, Switch } from "../components/ui";
import { useStore } from "../state/store";
import { axisOfPlane, axisPlane, middleAlong, planeOfSection } from "../state/slicePlane";
import type { SectionAxis } from "../state/sectionState";
import { currentScene } from "../viewport/scene";
import { NO_FIELD } from "./fieldsModel";
import { useFieldOptions } from "./resultsControl";
import {
  CAP_STATUS_KEY,
  FOLLOW_FIELD,
  MAX_LEVELS,
  MAX_SLICES,
  MIN_LEVELS,
  SLICE_DEBOUNCE_MS,
  addAxisSlice,
  addSlice,
  createDebounce,
  isFull,
  moveSlice,
  removeSlice,
  setFadeWalls,
  sliceLabel,
  sliderRange,
  statusText,
  updateSlice,
  type LineColour,
  type SlicePatch,
  type SliceList,
  type SliceSpec,
} from "./sliceModel";
import { Note } from "../components/Note";

const AXES: { value: SectionAxis; label: string }[] = [
  { value: "x", label: "X" },
  { value: "y", label: "Y" },
  { value: "z", label: "Z" },
];

/** Millimetres per step of a slice's slider and the text field beside it. */
const STEP_MM = 0.5;

function edit(change: (slices: SliceList) => SliceList): void {
  useStore.setState((s) => {
    const next = change(s.slices);
    return next === s.slices ? s : { slices: next };
  });
}

/** The model's extent along an axis in metres, from the scene, or null while nothing is drawn. */
function extentOf(axis: SectionAxis): [number, number] | null {
  return currentScene()?.axisRange(axis) ?? null;
}

/** A number field that commits on Enter or when it loses focus, and puts back what it had on Escape or on text that is not a number. */
function MmInput({ value, onCommit, label }: { value: number; onCommit: (mm: number) => void; label: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? String(Math.round(value * 10) / 10);
  const commit = () => {
    const parsed = draft === null ? Number.NaN : Number(draft.trim().replace(",", "."));
    setDraft(null);
    if (Number.isFinite(parsed)) onCommit(parsed);
  };
  const keys = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") commit();
    else if (e.key === "Escape") setDraft(null);
  };
  return (
    <span className="slice-mm">
      <input className="field mono" inputMode="decimal" spellCheck={false} aria-label={label} value={shown} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={keys} data-ws="slice-position-text" />
      <span className="muted">mm</span>
    </span>
  );
}

/** The position of an axis slice: a slider that rests 300 ms before the plane is asked of the server, and a field to type it. */
function Position({ spec, axis, position }: { spec: SliceSpec; axis: SectionAxis; position: number }) {
  const [draft, setDraft] = useState<number | null>(null);
  const mm = draft ?? position * 1000;
  const { min, max } = sliderRange(extentOf(axis));
  const lo = Math.min(min, mm);
  const hi = Math.max(max, mm);
  const id = spec.id;
  const debounce = useRef(createDebounce<number>(() => undefined, SLICE_DEBOUNCE_MS));
  useEffect(() => {
    const d = createDebounce<number>((value) => {
      edit((s) => moveSlice(s, id, axisPlane(axis, value / 1000)));
      setDraft(null);
    }, SLICE_DEBOUNCE_MS);
    debounce.current = d;
    return () => d.flush();
  }, [id, axis]);
  const move = (value: number) => {
    setDraft(value);
    debounce.current.call(value);
  };
  const jump = (value: number) => {
    debounce.current.cancel();
    setDraft(null);
    edit((s) => moveSlice(s, id, axisPlane(axis, value / 1000)));
  };
  return (
    <div className="sp-row slice-position">
      <span className="sp-label">{axis.toUpperCase()}</span>
      <input
        type="range"
        className="slider"
        min={lo}
        max={hi}
        step={STEP_MM}
        value={mm}
        aria-label={`Position of the ${sliceLabel(spec)} slice in millimetres`}
        onChange={(e) => move(Number(e.target.value))}
        data-ws="slice-position"
      />
      <MmInput value={mm} onCommit={jump} label="Position in millimetres" />
    </div>
  );
}

function SliceRow({ spec, state, fields }: { spec: SliceSpec; state: SliceList["status"][string] | undefined; fields: { name: string; label: string }[] }) {
  const axis = useMemo(() => (spec.origin === "axis" ? axisOfPlane(spec.plane) : null), [spec.origin, spec.plane]);
  const label = sliceLabel(spec);
  const text = statusText(state);
  const set = (patch: SlicePatch) => edit((s) => updateSlice(s, spec.id, patch));
  return (
    <li className="slice-row" data-ws="slice-row" data-slice={spec.id} data-status={state?.status ?? "idle"} data-visible={spec.visible ? "true" : "false"}>
      <div className="slice-head">
        <IconButton label={spec.visible ? `Hide the ${label} slice` : `Show the ${label} slice`} aria-pressed={spec.visible} onClick={() => set({ visible: !spec.visible })} data-ws="slice-eye">
          {spec.visible ? <Eye size={14} aria-hidden="true" /> : <EyeOff size={14} aria-hidden="true" />}
        </IconButton>
        <span className="slice-name" data-ws="slice-name">
          {label}
        </span>
        {spec.visible && state?.status === "loading" && <LoaderCircle size={14} aria-hidden="true" className="mesh-spin slice-spin" data-ws="slice-spinner" />}
        {spec.visible && state !== undefined && (state.status === "empty" || state.status === "error" || state.status === "host") && <CircleAlert size={14} aria-hidden="true" className="slice-alert" />}
        <IconButton label={`Remove the ${label} slice`} onClick={() => edit((s) => removeSlice(s, spec.id))} data-ws="slice-remove">
          <X size={14} aria-hidden="true" />
        </IconButton>
      </div>
      {axis !== null && <Position spec={spec} axis={axis.axis} position={axis.position} />}
      <div className="sp-row">
        <span className="sp-label">Field</span>
        <div className="select-wrap">
          <select className="field" aria-label={`Field of the ${label} slice`} value={spec.field} onChange={(e) => set({ field: e.target.value })} data-ws="slice-field">
            <option value={FOLLOW_FIELD}>Same as the surfaces</option>
            {fields.map((o) => (
              <option key={o.name} value={o.name}>
                {o.label}
              </option>
            ))}
            <option value={NO_FIELD}>None</option>
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      </div>
      <div data-ws="slice-fill">
        <Switch checked={spec.fill} label="Fill" onChange={(next) => set({ fill: next })} />
      </div>
      <div data-ws="slice-edges">
        <Switch checked={spec.edges} label="Cell edges" description="The mesh's cell edges on the cut" onChange={(next) => set({ edges: next })} />
      </div>
      <div data-ws="slice-bands">
        <Switch checked={spec.bands} label="Bands" description="Flat colours instead of the smooth map" onChange={(next) => set({ bands: next })} />
      </div>
      <div data-ws="slice-lines">
        <Switch checked={spec.lines} label="Lines" description="Contour lines at the band edges" onChange={(next) => set({ lines: next })} />
      </div>
      {spec.lines && (
        <div className="sp-row" data-ws="slice-line-colour">
          <span className="sp-label">Lines</span>
          <Segmented<LineColour>
            label={`Colour of the lines of the ${label} slice`}
            value={spec.lineColour}
            options={[
              { value: "neutral", label: "Neutral" },
              { value: "map", label: "Colour map" },
            ]}
            onChange={(next) => set({ lineColour: next })}
          />
        </div>
      )}
      {((spec.bands && spec.fill) || spec.lines) && (
        <div className="sp-row" data-ws="slice-levels">
          <span className="sp-label">Levels</span>
          <input
            type="range"
            className="slider"
            min={MIN_LEVELS}
            max={MAX_LEVELS}
            step={1}
            value={spec.levels}
            aria-label={`Levels of the ${label} slice`}
            aria-valuetext={`${spec.levels} levels`}
            title={`${spec.levels} bands, ${spec.levels - 1} lines between them`}
            onChange={(e) => set({ levels: Number(e.target.value) })}
            data-ws="slice-levels-input"
          />
          <span className="slice-levels-count mono" data-ws="slice-levels-count">
            {spec.levels}
          </span>
        </div>
      )}
      <p className="muted mesh-hint slice-status" aria-live="polite" data-ws="slice-status">
        {spec.visible ? text : "Hidden"}
      </p>
    </li>
  );
}

export default function SlicesSection() {
  const slices = useStore((s) => s.slices);
  const section = useStore((s) => s.section);
  const data = useStore((s) => s.results.data);
  const fields = useFieldOptions();
  const [axis, setAxis] = useState<SectionAxis>("x");
  const full = isFull(slices);
  const ready = data !== null;
  const blocked = !ready ? "Show the results first" : full ? `At most ${MAX_SLICES} slices` : "";

  const add = () => {
    if (blocked !== "") return;
    const range = extentOf(axis);
    const box = currentScene()?.modelBounds() ?? null;
    const middle = range !== null ? (range[0] + range[1]) / 2 : box !== null ? middleAlong(axis, box) : 0;
    edit((s) => addAxisSlice(s, axis, middle));
  };
  const fromSection = () => {
    if (blocked !== "" || section === null) return;
    edit((s) => addSlice(s, planeOfSection(section), "section"));
  };

  return (
    <section className="mesh-section" aria-label="Slices" data-ws="results-slices">
      <div className="section-title">Slices</div>
      <Note className="mesh-hint" tip="Cut the results at a plane and colour the cut by a field.">Cut at a plane</Note>
      <div className="slice-add">
        <Segmented<SectionAxis> label="Axis of the new slice" value={axis} options={AXES} onChange={setAxis} />
        <Button aria-label="Add slice" aria-disabled={blocked !== "" || undefined} title={blocked !== "" ? blocked : `Add a slice normal to ${axis.toUpperCase()}, in the middle of the model`} onClick={add} data-ws="slice-add">
          <Plus size={13} aria-hidden="true" />
          Add slice
        </Button>
      </div>
      <div className="mesh-actions">
        <Button
          aria-label="Add a slice from the section plane"
          aria-disabled={blocked !== "" || section === null || undefined}
          title={section === null ? "Turn the Section view on to use its plane" : "Cut at the plane the Section view is on now"}
          onClick={fromSection}
          data-ws="slice-from-section"
        >
          From the section plane
        </Button>
      </div>
      {section !== null && data !== null && slices.status[CAP_STATUS_KEY] !== undefined && (
        <p className="muted mesh-hint" aria-live="polite" data-ws="slice-cap-status" data-status={slices.status[CAP_STATUS_KEY].status}>
          {slices.status[CAP_STATUS_KEY].status === "ready" ? "The Section view's cut face shows the field." : `Section cut: ${statusText(slices.status[CAP_STATUS_KEY])}`}
        </p>
      )}
      {slices.list.length > 0 ? (
        <>
          <ul className="slice-list" aria-label="Slices" data-ws="slice-list">
            {slices.list.map((spec) => (
              <SliceRow key={spec.id} spec={spec} state={slices.status[spec.id]} fields={fields} />
            ))}
          </ul>
          <div data-ws="slice-fade">
            <Switch checked={slices.fadeWalls} label="Fade the walls" description="See the slices inside the body" onChange={(next) => edit((s) => setFadeWalls(s, next))} />
          </div>
        </>
      ) : (
        <p className="muted mesh-hint">{ready ? "No slices yet." : "Show the results to cut slices."}</p>
      )}
    </section>
  );
}
