/**
 * Boundaries: click a face of the fluid, then say what it is.
 *
 * While this card is open the view picks faces (and puts the pick mode back when the card goes). A picked
 * face set becomes an Inlet (a velocity inlet, with its speed beside the chip), an Outlet (a pressure outlet,
 * with its pressure), a Wall or a Symmetry plane, through the same `boundary.assign` the Assign menu makes.
 * Below, each boundary made so far is listed with its colour, its kind and its value, which can be changed
 * in place, and a cross removes it. Faces nobody assigned become walls when the mesh is made (the case
 * compiler's own rule), and the card says how many. A face the camera cannot see (the far end of a pipe
 * inside solids) is listed under that line, one row each from `boundary.uncovered`, and Pick makes it the
 * live pick so the chips apply to it without orbiting.
 */
import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import type { ConditionKind, UncoveredAnswer } from "../../api/types";
import { useStore } from "../../state/store";
import { readSetupContext } from "../../setup/basicSets";
import GuidedCard from "../GuidedCard";
import { ErrorLine, Note } from "../controls";
import { assignPickedFaces, removeBoundary, renameBoundary, setBoundaryValue } from "../commands";
import { faceRowText, type GuidedStep } from "../guidedModel";
import { fluidBody, readBodies, readBoundaries, unassignedFaces, type BoundaryRow } from "../guidedTree";
import { useAction } from "../useAction";

/** The pick mode the window was in, put back when the card goes (kept for the one card open at a time). */
let before: "body" | "face" | "edge" | "point" | null = null;

/**
 * **One empty array, not a fresh `[]` per call.** A store selector's result is compared by reference;
 * a selector that makes a new array every time never compares equal, so React re-renders until it
 * gives up ("Maximum update depth exceeded", error #185, the whole window blank). Measured: opening
 * this card on a project with no face picked yet did exactly that.
 */
const NO_FACES: number[] = [];

function useFacePicking(): void {
  useEffect(() => {
    const state = useStore.getState();
    if (before === null) before = state.pickMode === "face" ? "body" : state.pickMode;
    // Whatever an earlier step picked (the fluid body) would turn into all of its faces; start clean.
    state.clearPick();
    if (state.pickMode !== "face") state.setPickMode("face");
    return () => {
      const back = before;
      before = null;
      const now = useStore.getState();
      if (back !== null && now.pickMode === "face") now.setPickMode(back);
    };
  }, []);
}

/** What `boundary.uncovered` says of the fluid body; asked again when the tree or the set-up check changes. */
function useUncovered(fluidId: string | null): UncoveredAnswer | null {
  const tree = useStore((s) => s.tree);
  const setup = useStore((s) => s.setup);
  const [answer, setAnswer] = useState<UncoveredAnswer | null>(null);
  useEffect(() => {
    const client = useStore.getState().client;
    if (client === null || fluidId === null) {
      setAnswer(null);
      return;
    }
    let live = true;
    client
      .call<UncoveredAnswer>("boundary.uncovered", { part: fluidId, describe: true })
      .then((reply) => live && setAnswer(reply))
      .catch(() => live && setAnswer(null));
    return () => {
      live = false;
    };
  }, [fluidId, tree, setup]);
  return answer;
}

function numberOf(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** One number of a boundary, edited in place: shown as stored, applied on Enter or blur, Esc puts it back. */
function ValueBox({ label, unit, value, onCommit, fail, className = "" }: { label: string; unit: string; value: number | null; onCommit: (n: number) => void; fail: (message: string) => void; className?: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null ? "" : String(Number(value.toPrecision(6))));
  const commit = () => {
    const text = draft;
    setDraft(null);
    if (text === null) return;
    const n = numberOf(text);
    if (n === null) {
      fail(`${label} needs a number in ${unit}.`);
      return;
    }
    if (n !== value) onCommit(n);
  };
  return (
    <label className={`guided-bnd-value ${className}`}>
      <input
        className="field"
        inputMode="decimal"
        spellCheck={false}
        autoComplete="off"
        aria-label={`${label} in ${unit}`}
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") setDraft(null);
        }}
      />
      <span className="muted">{unit}</span>
    </label>
  );
}

/**
 * A boundary's label, renamed in place: a double-click (or Enter on it) opens the name, Enter or a click away
 * renames the boundary and its patch, Esc keeps the old one. Two inlets were "inlet" and "inlet_2" with no way
 * to say which was the hot one short of Full mode's tree.
 */
function NameBox({ row, action }: { row: BoundaryRow; action: ReturnType<typeof useAction> }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <span
        className="guided-bnd-name"
        role="button"
        tabIndex={0}
        title={`${row.kindWord} · ${row.name} - double-click to rename`}
        onDoubleClick={() => setDraft(row.name)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "F2") setDraft(row.name);
        }}
        data-ws="guided-boundary-name"
      >
        {row.word}
      </span>
    );
  }
  const commit = () => {
    const name = draft.trim();
    setDraft(null);
    if (name === "" || name === row.name) return;
    void action.run(() => renameBoundary(row.id, row.tag, name));
  };
  return (
    <input
      className="field guided-bnd-name"
      autoFocus
      spellCheck={false}
      autoComplete="off"
      aria-label={`New name for ${row.name}`}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}

/** One boundary of the list: colour, kind, a value to change, and a cross to remove it. */
function Item({ row, action, energy }: { row: BoundaryRow; action: ReturnType<typeof useAction>; energy: boolean }) {
  const editable = row.editable;

  return (
    <li className="guided-bnd" data-ws="guided-boundary" data-kind={row.kind} data-temp={energy && row.temperature !== null ? "" : undefined}>
      <span className="guided-swatch" style={row.color !== "" ? { background: row.color } : undefined} aria-hidden="true" />
      <NameBox row={row} action={action} />
      {editable !== null ? (
        <ValueBox label={`${row.word} ${editable.param}`} unit={editable.unit} value={row.value} fail={action.fail} onCommit={(n) => void action.run(() => setBoundaryValue(row.id, editable.param, n))} />
      ) : (
        <span className="muted guided-bnd-value">{row.valueText}</span>
      )}
      {energy && row.temperature !== null && (
        <ValueBox className="guided-bnd-temp" label={`${row.word} temperature`} unit="K" value={row.temperature} fail={action.fail} onCommit={(n) => void action.run(() => setBoundaryValue(row.id, "temperature", n))} />
      )}
      <button type="button" className="guided-x no-drag" aria-label={`Remove ${row.name}`} title="Remove this boundary" disabled={action.busy} onClick={() => void action.run(() => removeBoundary(row.id))}>
        <X size={13} aria-hidden="true" />
      </button>
    </li>
  );
}

export default function BoundariesCard({ step }: { step: GuidedStep }) {
  useFacePicking();
  const tree = useStore((s) => s.tree);
  const kinds = useStore((s) => s.conditionKinds);
  const setup = useStore((s) => s.setup);
  const faces = useStore((s) => Object.values(s.picked.faces).reduce((sum, ids) => sum + ids.length, 0));
  const rows = useMemo(() => readBoundaries(tree), [tree]);
  const fluidId = useMemo(() => fluidBody(readBodies(tree))?.id ?? null, [tree]);
  const uncovered = useUncovered(fluidId);
  const open = useMemo(() => (fluidId === null ? { ids: [], more: 0 } : unassignedFaces(uncovered, fluidId)), [uncovered, fluidId]);
  const looks = useMemo(() => new Map((uncovered?.parts.find((p) => p.part === fluidId)?.described ?? []).map((d) => [d.face, d])), [uncovered, fluidId]);
  const pickedHere = useStore((s) => (fluidId === null ? NO_FACES : (s.picked.faces[fluidId] ?? NO_FACES)));
  const action = useAction();
  const energy = useMemo(() => readSetupContext(tree).energy, [tree]);
  const [speed, setSpeed] = useState("0.5");
  const [temperature, setTemperature] = useState("293.15");
  const [pressure, setPressure] = useState("0");

  const kind = (id: string): ConditionKind | undefined => kinds.find((k) => k.id === id && k.available);
  const loose = setup?.counts.uncovered_faces ?? 0;
  const why = faces === 0 ? "Click a face of the fluid first." : undefined;

  const assign = (id: string, values: () => Record<string, unknown> | string) => {
    if (action.busy) return;
    if (faces === 0) {
      action.fail("Click a face of the fluid first.");
      return;
    }
    const found = kind(id);
    if (found === undefined) {
      action.fail("This server does not offer that kind of boundary.");
      return;
    }
    const given = values();
    if (typeof given === "string") {
      action.fail(given);
      return;
    }
    void action.run(() => assignPickedFaces(found, given));
  };

  const inlet = () => {
    const n = numberOf(speed);
    if (n === null || n <= 0) return "The inlet needs a speed above zero, in m/s.";
    if (!energy) return { velocity: n };
    const t = numberOf(temperature);
    return t === null || t <= 0 ? "The inlet needs a temperature above zero, in K." : { velocity: n, temperature: t };
  };
  const outlet = () => {
    const n = numberOf(pressure);
    return n === null ? "The outlet needs a pressure in Pa (0 is the usual choice)." : { pressure: n };
  };

  return (
    <GuidedCard step={step}>
      <Note tip="Click a face of the fluid in the view, then press what it is: Inlet, Outlet, Wall or Symmetry.">{faces === 0 ? "Pick a face in the view" : `${faces} ${faces === 1 ? "face" : "faces"} picked · what is it?`}</Note>
      <div className="guided-assign">
        <div className="guided-assign-row">
          <button type="button" className="guided-chip wide no-drag" aria-disabled={faces === 0 || action.busy || undefined} title={why} onClick={() => assign("velocity_inlet", inlet)} data-ws="guided-inlet">
            Inlet
          </button>
          <label className="guided-bnd-value">
            <input className="field" inputMode="decimal" spellCheck={false} autoComplete="off" aria-label="Inlet speed in m/s" value={speed} onChange={(e) => setSpeed(e.target.value)} />
            <span className="muted">m/s</span>
          </label>
        </div>
        {energy && (
          <div className="guided-assign-row">
            <span className="muted guided-assign-cap">Inlet temperature</span>
            <label className="guided-bnd-value">
              <input className="field" inputMode="decimal" spellCheck={false} autoComplete="off" aria-label="Inlet temperature in K" value={temperature} onChange={(e) => setTemperature(e.target.value)} data-ws="guided-inlet-temperature" />
              <span className="muted">K</span>
            </label>
          </div>
        )}
        <div className="guided-assign-row">
          <button type="button" className="guided-chip wide no-drag" aria-disabled={faces === 0 || action.busy || undefined} title={why} onClick={() => assign("pressure_outlet", outlet)} data-ws="guided-outlet">
            Outlet
          </button>
          <label className="guided-bnd-value">
            <input className="field" inputMode="decimal" spellCheck={false} autoComplete="off" aria-label="Outlet pressure in Pa" value={pressure} onChange={(e) => setPressure(e.target.value)} />
            <span className="muted">Pa</span>
          </label>
        </div>
        <div className="guided-assign-row">
          <button type="button" className="guided-chip wide no-drag" aria-disabled={faces === 0 || action.busy || undefined} title={why} onClick={() => assign("wall", () => ({}))} data-ws="guided-wall">
            Wall
          </button>
          <button type="button" className="guided-chip wide no-drag" aria-disabled={faces === 0 || action.busy || undefined} title={why} onClick={() => assign("symmetry", () => ({}))} data-ws="guided-symmetry">
            Symmetry
          </button>
        </div>
      </div>
      {rows.length > 0 && (
        <ul className="guided-bnds" aria-label="Boundaries so far" data-ws="guided-boundaries">
          {rows.map((row) => (
            <Item key={row.id} row={row} action={action} energy={energy} />
          ))}
        </ul>
      )}
      {loose > 0 && (
        <Note tip="A face nobody assigned becomes a wall when the mesh is made.">
          {loose} {loose === 1 ? "face" : "faces"} unassigned · wall by default
        </Note>
      )}
      {loose > 0 && fluidId !== null && open.ids.length > 0 && (
        <ul className="guided-bnds" aria-label="Faces not assigned yet" data-ws="guided-unassigned">
          {open.ids.map((id) => {
            const on = pickedHere.length === 1 && pickedHere[0] === id;
            return (
              <li key={id} className="guided-bnd" data-ws="guided-unassigned-face">
                <span className="guided-face-name">{faceRowText(id, looks.get(id))}</span>
                <button type="button" className="btn no-drag" aria-pressed={on} disabled={action.busy} onClick={() => useStore.getState().pickFace(fluidId, id, "replace")}>
                  Pick
                </button>
              </li>
            );
          })}
          {open.more > 0 && <li className="muted guided-bnd">and {open.more} more</li>}
        </ul>
      )}
      <ErrorLine text={action.error} />
    </GuidedCard>
  );
}
