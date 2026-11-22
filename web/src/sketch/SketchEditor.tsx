/**
 * The sketcher (ADR-0036 decision 4): a 2D editor over the viewport for a sketch's profile.
 *
 * Tools on the left (Select, Line, Rectangle, Circle, Arc, Spline, Polygon; one key each), the
 * drawing in the middle (a grid that follows the zoom, the plane's u and v axes, snapping to points,
 * to the grid and to horizontal and vertical from the last point; the model shows dimmed behind
 * it), the entities and dimensions as tables on the right. While a draft takes sizes they are typed
 * into fields on the canvas (`entry.ts`); rectangles, circles and polygons show their sizes on the
 * drawing, and a click on one edits it. The sketch owns the keyboard while open (`keys.ts`).
 * One undo entry is the whole sketch (`history.ts`). Finish writes the sketch as one command
 * (`parameter.update`: one undo step, one rebuild), or opens a new one's card to choose pull or
 * revolve; Cancel asks before it drops an edit.
 *
 * Why this file is over 500 lines: it is the one component that owns the canvas's pointer and key
 * state; the rules live in the pure modules beside it.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactElement, type WheelEvent as ReactWheelEvent } from "react";
import { Check, Circle, Hexagon, Magnet, Maximize, MousePointer2, PenLine, RectangleHorizontal, Redo2, Spline, Undo2, Waypoints, X, type LucideIcon } from "lucide-react";
import { useStore, type SketchSession } from "../state/store";
import { Tooltip } from "../components/Tooltip";
import { claimKeyboard } from "../workspace/keyOwner";
import { TOOLS, arcPath, checkProfile, circleThrough, click, distanceTo, extentOf, filletCorners, finishSpline, gridStep, movePoint, samePoint, snap, snapTargets, splinePoints, type Draft, type Entity, type Point, type Snap, type Tool } from "./model";
import { afterDelete, applyValues, bindScalar, boundStored, expressionsOf, freeEntity, freePoint } from "./bindings";
import { cornersOf, isShape } from "./shapes";
import { isDirty, push, redo, refresh, start, undo, type History, type SketchDoc } from "./history";
import { backspace, entryLabels, isEmpty, liveValues, newEntry, nextField, resolveEntry, typeInto, type Entry } from "./entry";
import { sketchKey, type KeyAction } from "./keys";
import { DimensionsPanel, EntityTable, NUMBER } from "./SketchTables";
import { currentScene } from "../viewport/scene";
import { planeCamera, type Vec3 } from "../direct/plane";
import { drawnLoops } from "../direct/profile";
import { defaultDepth, depthText } from "../direct/pull";
import { buildGhost, clearGhost, ghostFor, requestPullFocus } from "../direct/ghostState";
import { finishToastText } from "../direct/buildStatus";
import { pullOf } from "../direct/opPull";
import { selectWhenDone } from "../direct/flow";
import { findNode } from "../state/store";
import "./sketch.css";

const ICONS: Record<Tool, LucideIcon> = {
  select: MousePointer2,
  line: PenLine,
  rectangle: RectangleHorizontal,
  circle: Circle,
  arc: Waypoints,
  spline: Spline,
  polygon: Hexagon,
};

const PLANES: ReadonlyArray<{ id: SketchSession["plane"]; label: string; axes: [string, string] }> = [
  { id: "xy", label: "XY", axes: ["x", "y"] },
  { id: "yz", label: "YZ", axes: ["y", "z"] },
  { id: "zx", label: "ZX", axes: ["z", "x"] },
];

const SNAP_PX = 9;
/** Below this width between the side docks the sketch covers them rather than squeeze (review B1). */
const MIN_WIDTH = 980;

export default function SketchEditorLayer({ leftInset, rightInset }: { leftInset: number; rightInset: number }) {
  const session = useStore((s) => s.sketch);
  if (session === null) return null;
  return <SketchEditor key={session.opId ?? "new"} session={session} leftInset={leftInset} rightInset={rightInset} />;
}

/** A size label on the drawing: which size of which entity, and where. */
interface SizeLabel {
  index: number;
  key: "w" | "h" | "r";
  x: number;
  y: number;
  value: number;
  text: string;
}

function SketchEditor({ session, leftInset, rightInset }: { session: SketchSession; leftInset: number; rightInset: number }) {
  const execute = useStore((s) => s.execute);
  const [hist, setHist] = useState<History>(() =>
    start({ entities: session.entities, bindings: session.bindings ?? {}, dims: Object.entries(session.dimensions ?? {}).map(([name, text]) => ({ name, text })) }),
  );
  const doc = hist.present;
  // The entities while a point is dragged: drawn live, one history entry on release.
  const [live, setLive] = useState<Entity[] | null>(null);
  const entities = live ?? doc.entities;
  const bindings = doc.bindings;
  // Expression text to its value (mm), and what did not evaluate (by text, or `dim:<name>`).
  const [values, setValues] = useState<Record<string, number>>({});
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [dimShown, setDimShown] = useState<Record<string, string>>({});
  const [evaluating, setEvaluating] = useState(false);
  const [plane, setPlane] = useState<SketchSession["plane"]>(session.plane);
  // A new, empty sketch starts by choosing its plane from three thumbnails (ADR-0039).
  const [choosing, setChoosing] = useState(session.opId === null && session.entities.length === 0);
  const origin: Vec3 = session.origin ?? [0, 0, 0];
  // Re-editing starts in Select, so the first click on the drawing does not start a line (review B6).
  const [tool, setTool] = useState<Tool>(session.tool ?? (session.entities.length > 0 ? "select" : "line"));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [entry, setEntryState] = useState<Entry | null>(null);
  // The entry as the key handler last set it, ahead of the render (fast typing).
  const entryRef = useRef<Entry | null>(null);
  const setEntry = useCallback((next: Entry | null | ((e: Entry | null) => Entry | null)) => {
    entryRef.current = typeof next === "function" ? next(entryRef.current) : next;
    setEntryState(entryRef.current);
  }, []);
  const [cursor, setCursor] = useState<Snap | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [overPoint, setOverPoint] = useState(false);
  const [sides, setSides] = useState(6);
  const [filletRadius, setFilletRadius] = useState("2");
  const [snapOn, setSnapOn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState<SizeLabel | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [size, setSize] = useState({ w: 800, h: 500 });
  const [winW, setWinW] = useState(() => window.innerWidth);
  // The view: millimetres to pixels (`scale`) and the sketch point at the canvas centre.
  const [view, setView] = useState({ scale: 6, cu: 0, cv: 0 });
  const drag = useRef<{ kind: "point"; from: Point; start: Entity[] } | { kind: "pan"; x: number; y: number; cu: number; cv: number; right: boolean; moved: boolean } | null>(null);

  const check = useMemo(() => checkProfile(entities), [entities]);
  // The first expression that does not evaluate, named (a dimension's message names it already).
  const bindError = Object.entries(problems).map(([k, v]) => (k.startsWith("dim:") ? v : `${k}: ${v}`))[0] ?? "";
  const grid = gridStep(view.scale);
  const toX = useCallback((u: number) => size.w / 2 + (u - view.cu) * view.scale, [size.w, view]);
  const toY = useCallback((v: number) => size.h / 2 - (v - view.cv) * view.scale, [size.h, view]);
  const fromScreen = useCallback(
    (x: number, y: number): Point => [view.cu + (x - size.w / 2) / view.scale, view.cv - (y - size.h / 2) / view.scale],
    [size, view],
  );

  /** One edit of the sketch: one undo entry (review A1). */
  const edit = useCallback((fn: (d: SketchDoc) => SketchDoc) => {
    setHist((h) => push(h, fn(h.present)));
    setError("");
  }, []);

  /** Fillet: one edit, so one undo takes every round back. A corner a dimension drives is freed. */
  const roundCorners = useCallback(() => {
    const radius = Number(filletRadius);
    if (!(radius > 0)) {
      setError("A fillet needs a radius above 0 (mm)");
      return;
    }
    const only = selected.size > 1 ? selected : undefined;
    const tried = filletCorners(hist.present.entities, radius, only);
    if (tried.corners.length === 0) {
      setError(only ? "No corner between the selected lines can take that radius" : "No corner can take that radius: the lines are too short, or none meet at a corner");
      return;
    }
    edit((d) => {
      const made = filletCorners(d.entities, radius, only);
      let bindings = d.bindings;
      for (const corner of made.corners) bindings = freePoint(d.entities, bindings, corner);
      return { ...d, entities: made.entities, bindings };
    });
    setSelected(new Set());
  }, [edit, filletRadius, hist.present.entities, selected]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setSize({ w: el.clientWidth || 800, h: el.clientHeight || 500 }));
    observer.observe(el);
    setSize({ w: el.clientWidth || 800, h: el.clientHeight || 500 });
    const onResize = () => setWinW(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const fit = useCallback(() => {
    const pts = entities.flatMap(extentOf);
    if (pts.length === 0) {
      setView({ scale: 6, cu: 0, cv: 0 });
      return;
    }
    const us = pts.map((p) => p[0]);
    const vs = pts.map((p) => p[1]);
    const w = Math.max(...us) - Math.min(...us) || 10;
    const h = Math.max(...vs) - Math.min(...vs) || 10;
    setView({ scale: Math.min((size.w * 0.8) / w, (size.h * 0.8) / h), cu: (Math.max(...us) + Math.min(...us)) / 2, cv: (Math.max(...vs) + Math.min(...vs)) / 2 });
  }, [entities, size]);

  // A sketch that opens with entities opens fitted to them.
  const fitted = useRef(false);
  useEffect(() => {
    if (!fitted.current && size.w > 0 && session.entities.length > 0) {
      fitted.current = true;
      fit();
    }
  }, [fit, size, session.entities.length]);

  // The server evaluates every bound coordinate and every dimension (`expression.evaluate`): the
  // drawing shows what the part will be built from, and a name that does not resolve is flagged on
  // its field. It runs again whenever the bindings or the dimensions change, undo and redo included.
  useEffect(() => {
    const { bindings: b, dims } = doc;
    const texts = expressionsOf(b);
    if (texts.length === 0 && dims.length === 0) {
      setProblems({});
      setDimShown({});
      setEvaluating(false);
      return;
    }
    const names = Object.fromEntries(dims.map((d) => [d.name, d.text]));
    let stale = false;
    setEvaluating(true);
    const timer = window.setTimeout(() => {
      void (async () => {
        const vals: Record<string, number> = {};
        const shown: Record<string, string> = {};
        const probs: Record<string, string> = {};
        for (const text of texts) {
          try {
            const r = (await execute("expression.evaluate", { text, dimension: "length", names }, { silent: true })) as { value: number };
            vals[text] = r.value * 1000;
          } catch (err) {
            probs[text] = (err as Error).message;
          }
        }
        for (const d of dims) {
          try {
            const r = (await execute("expression.evaluate", { text: d.name, names }, { silent: true })) as { value: number; unit: string };
            shown[d.name] = r.unit === "m" || r.unit === "meter" ? `${Number((r.value * 1000).toPrecision(6))} mm` : r.unit === "rad" || r.unit === "radian" ? `${Number(((r.value * 180) / Math.PI).toPrecision(6))}°` : `${Number(r.value.toPrecision(6))}`;
          } catch (err) {
            shown[d.name] = "?";
            probs[`dim:${d.name}`] = `${d.name}: ${(err as Error).message}`;
          }
        }
        if (stale) return;
        setValues((v) => ({ ...v, ...vals }));
        setDimShown(shown);
        setProblems(probs);
        setEvaluating(false);
        setHist((h) => (h.present.bindings === b && h.present.dims === dims ? refresh(h, { ...h.present, entities: applyValues(h.present.entities, b, vals) }) : h));
      })();
    }, 120);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [doc.bindings, doc.dims, execute]);

  // A draft that takes sizes gets empty fields at each step.
  useEffect(() => {
    const labels = draft ? entryLabels(draft.tool, draft.points) : null;
    setEntry(labels ? newEntry(labels) : null);
  }, [draft]);

  const resetTransient = () => {
    setDraft(null);
    setSelected(new Set());
    setEditing(null);
  };
  const doUndo = useCallback(() => {
    setHist(undo);
    resetTransient();
  }, []);
  const doRedo = useCallback(() => {
    setHist(redo);
    resetTransient();
  }, []);

  const snapped = useCallback(
    (x: number, y: number): Snap => {
      const raw = fromScreen(x, y);
      const radius = SNAP_PX / view.scale;
      const from = draft?.points[draft.points.length - 1] ?? null;
      const targets = [...snapTargets(entities), ...(draft?.points ?? []), [0, 0] as Point];
      return snap(raw, targets, snapOn ? grid / 2 : 0, radius, from);
    },
    [fromScreen, view.scale, draft, entities, snapOn, grid],
  );

  /** A click of the drawing tool at `point`; `binds` are sizes typed as expressions. */
  const placeAt = useCallback(
    (point: Point, binds: Record<string, string> = {}) => {
      if (tool === "select") return;
      const result = click(tool, draft, point, sides, check.open);
      if (result.add.length > 0) {
        edit((d) => {
          const base = d.entities.length;
          let b = d.bindings;
          for (const [k, expr] of Object.entries(binds)) b = bindScalar(b, base, k, expr);
          return { ...d, entities: [...d.entities, ...result.add], bindings: b };
        });
      }
      setDraft(result.draft);
    },
    [tool, draft, sides, check.open, edit],
  );

  const finishDraft = useCallback(() => {
    if (draft?.tool === "spline") {
      const r = finishSpline(draft.points);
      if (r.add.length) edit((d) => ({ ...d, entities: [...d.entities, ...r.add] }));
    }
    setDraft(null);
  }, [draft, edit]);

  const commitEntry = useCallback(() => {
    const entry = entryRef.current;
    if (!draft || !entry) return;
    const r = resolveEntry(draft.tool, draft.points, cursor?.point ?? null, entry, (t) => values[t]);
    if ("error" in r) {
      setError(r.error);
      return;
    }
    placeAt(r.point, r.binds);
  }, [draft, cursor, values, placeAt]);

  const deleteSelected = useCallback(() => {
    if (selected.size === 0) return;
    const gone = [...selected].sort((x, y) => y - x);
    edit((d) => ({
      ...d,
      entities: d.entities.filter((_, i) => !selected.has(i)),
      bindings: gone.reduce((acc, i) => afterDelete(acc, i), d.bindings),
    }));
    setSelected(new Set());
  }, [selected, edit]);

  const chooseTool = useCallback((t: Tool) => {
    setTool(t);
    setDraft(null);
    setHover(null);
  }, []);

  const closeSketch = () => useStore.getState().closeSketch();
  const cancel = () => {
    if (isDirty(hist) && !confirming) setConfirming(true);
    else closeSketch();
  };

  // ------------------------------------------------------------------ the keyboard (review A2)
  const onAction = (a: KeyAction) => {
    if (choosing && (a.kind === "escape" || a.kind === "enter" || a.kind === "tool")) setChoosing(false);
    if (confirming) {
      if (a.kind === "escape") setConfirming(false);
      return;
    }
    switch (a.kind) {
      case "tool":
        chooseTool(a.tool);
        return;
      case "undo":
        doUndo();
        return;
      case "redo":
        doRedo();
        return;
      case "escape":
        if (entryRef.current && !isEmpty(entryRef.current)) setEntry(newEntry(entryRef.current.labels));
        else if (draft) setDraft(null);
        else if (selected.size > 0) setSelected(new Set());
        else chooseTool("select");
        setError("");
        return;
      case "enter":
        if (!isEmpty(entryRef.current)) commitEntry();
        else finishDraft();
        return;
      // Functional updates: keys typed faster than a render must all land (review B4).
      case "tab":
        setEntry((e) => (e ? nextField(e, a.back) : e));
        return;
      case "char":
        setEntry((e) => (e ? typeInto(e, a.ch) : e));
        return;
      case "backspace":
        setEntry((e) => (e ? backspace(e) : e));
        return;
      case "delete":
        deleteSelected();
        return;
      case "fit":
        fit();
        return;
      case "snap":
        setSnapOn((s) => !s);
        return;
      case "swallow":
        return;
    }
  };
  const actionRef = useRef(onAction);
  actionRef.current = onAction;

  useEffect(() => claimKeyboard("sketch"), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) {
        // A field types its own keys; Esc in one of the sketch's fields returns to the drawing.
        if (e.key === "Escape" && target.closest("[data-ws='sketch-editor']")) {
          e.preventDefault();
          e.stopPropagation();
          target.blur();
        }
        return;
      }
      const action = sketchKey(e, { entryOpen: entryRef.current !== null, entryEmpty: isEmpty(entryRef.current) });
      e.stopPropagation();
      if (action.kind !== "swallow") e.preventDefault();
      if (e.repeat && !["undo", "redo", "backspace", "char"].includes(action.kind)) return;
      actionRef.current(action);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // ------------------------------------------------------------------ the pointer
  const local = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = svgRef.current?.getBoundingClientRect();
    return r ? [e.clientX - r.left, e.clientY - r.top] : [0, 0];
  };
  const pick = (p: Point): number | null => {
    const radius = SNAP_PX / view.scale;
    const near = entities.map((en, i) => [distanceTo(en, p), i] as const).filter(([d]) => d <= radius).sort((a, b) => a[0] - b[0])[0];
    return near ? near[1] : null;
  };
  const pointAt = (p: Point): Point | undefined => {
    const radius = SNAP_PX / view.scale;
    return snapTargets(entities).find((t) => Math.hypot(t[0] - p[0], t[1] - p[1]) <= radius);
  };

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    // A click on the drawing while the planes are offered draws on the plane shown.
    if (choosing) setChoosing(false);
    const [x, y] = local(e);
    setEditing(null);
    if (e.button === 1 || e.button === 2) {
      drag.current = { kind: "pan", x, y, cu: view.cu, cv: view.cv, right: e.button === 2, moved: false };
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }
    if (e.button !== 0) return;
    if (tool === "select") {
      const p = fromScreen(x, y);
      const hit = pointAt(p);
      if (hit) {
        drag.current = { kind: "point", from: hit, start: entities };
        (e.target as Element).setPointerCapture?.(e.pointerId);
        return;
      }
      const near = pick(p);
      setSelected((s) => {
        if (near === null) return e.shiftKey ? s : new Set();
        const next = new Set(e.shiftKey ? s : []);
        if (next.has(near)) next.delete(near);
        else next.add(near);
        return next;
      });
      return;
    }
    placeAt(snapped(x, y).point);
  };

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const [x, y] = local(e);
    const d = drag.current;
    if (d?.kind === "pan") {
      if (Math.hypot(x - d.x, y - d.y) > 3) d.moved = true;
      setView((v) => ({ ...v, cu: d.cu - (x - d.x) / v.scale, cv: d.cv + (y - d.y) / v.scale }));
      return;
    }
    const s = snapped(x, y);
    setCursor(s);
    if (d?.kind === "point") {
      setLive(movePoint(d.start, d.from, s.point));
      return;
    }
    if (tool === "select") {
      const p = fromScreen(x, y);
      setOverPoint(pointAt(p) !== undefined);
      setHover(pick(p));
    }
  };

  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "pan") {
      // A right click that did not pan finishes the chain or the spline (review B6).
      if (d.right && !d.moved) finishDraft();
      return;
    }
    if (d?.kind === "point") {
      const moved = live;
      setLive(null);
      if (moved === null) return;
      edit((doc0) => {
        // A point dragged by hand is placed by hand: its expressions go; a shape reshaped by its
        // corner loses the bindings of its sizes and centre.
        let b = freePoint(d.start, doc0.bindings, d.from);
        d.start.forEach((en, i) => {
          if (isShape(en) && !samePoint(en.c, d.from) && cornersOf(en).some((q) => samePoint(q, d.from))) b = freeEntity(b, i);
        });
        return { ...doc0, entities: moved, bindings: b };
      });
    }
  };

  const onWheel = (e: ReactWheelEvent<SVGSVGElement>) => {
    const [x, y] = local(e);
    const before = fromScreen(x, y);
    const factor = Math.exp(-e.deltaY * 0.0015);
    setView((v) => {
      const scale = Math.min(5000, Math.max(0.01, v.scale * factor));
      // Keep the point under the cursor where it is.
      return { scale, cu: before[0] - (x - size.w / 2) / scale, cv: before[1] + (y - size.h / 2) / scale };
    });
  };

  // ------------------------------------------------------------------ Finish (review A6)
  const finishBlocked = check.message !== "" ? check.message : bindError !== "" ? bindError : evaluating ? "Working out the dimensions…" : "";
  const finish = async () => {
    if (finishBlocked !== "" || busy) {
      if (bindError) setError(bindError);
      return;
    }
    const stored = boundStored(entities, bindings);
    const dimensions = Object.fromEntries(doc.dims.map((d) => [d.name, d.text]));
    // The ghost of the body to come (ADR-0039): the drawn profile pulled, until the mesher's body arrives.
    const loops = drawnLoops(entities);
    if (session.opId === null) {
      // Finish pulls the profile at once (ADR-0039): no card; the body's Pull handle sets the depth.
      const depth = defaultDepth(loops);
      setBusy(true);
      if (loops !== null) buildGhost({ plane, origin, loops, depth, symmetric: false }, null);
      try {
        const params = { profile: "custom", plane, entities: stored, dimensions, method: "extrude", depth: depthText(depth), ...(session.origin ? { origin: session.origin } : {}) };
        const made = (await execute("operation.create", { type: "implicit_sketch", params })) as { id?: unknown };
        if (typeof made.id === "string") {
          ghostFor(made.id);
          selectWhenDone(made.id);
          // The depth Finish chose, said at once; the Pull field opens on the body as soon as its handle is there.
          requestPullFocus(made.id);
          useStore.getState().pushToast("info", finishToastText(depth));
        }
        closeSketch();
      } catch (err) {
        clearGhost();
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
      return;
    }
    setBusy(true);
    const pull = pullOf(findNode(useStore.getState().tree, session.opId));
    if (pull !== null && loops !== null) buildGhost({ plane, origin: pull.origin, loops, depth: pull.depth, symmetric: pull.symmetric }, session.opId);
    try {
      // One command: one undo step and one rebuild. The dimensions before the entities that name them.
      await execute("parameter.update", { node: session.opId, values: { plane, dimensions, entities: stored, profile: "custom" } });
      closeSketch();
    } catch (err) {
      clearGhost();
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------ drawing
  const gridLines: ReactElement[] = [];
  {
    const left = fromScreen(0, 0);
    const right = fromScreen(size.w, size.h);
    const u0 = Math.floor(left[0] / grid) * grid;
    const v0 = Math.floor(right[1] / grid) * grid;
    for (let u = u0, i = 0; u <= right[0] && i < 400; u += grid, i++) {
      const major = Math.abs(Math.round(u / grid) % 5) === 0;
      gridLines.push(<line key={`u${i}`} x1={toX(u)} x2={toX(u)} y1={0} y2={size.h} className={major ? "sketch-grid-major" : "sketch-grid"} />);
    }
    for (let v = v0, i = 0; v <= left[1] && i < 400; v += grid, i++) {
      const major = Math.abs(Math.round(v / grid) % 5) === 0;
      gridLines.push(<line key={`v${i}`} y1={toY(v)} y2={toY(v)} x1={0} x2={size.w} className={major ? "sketch-grid-major" : "sketch-grid"} />);
    }
  }

  const drawEntity = (e: Entity, key: string, className: string): ReactElement => {
    switch (e.kind) {
      case "line":
        return <line key={key} className={className} x1={toX(e.a[0])} y1={toY(e.a[1])} x2={toX(e.b[0])} y2={toY(e.b[1])} />;
      case "circle":
        return <circle key={key} className={className} cx={toX(e.c[0])} cy={toY(e.c[1])} r={e.r * view.scale} />;
      case "arc":
        return <path key={key} className={className} d={arcPath(e.a, e.m, e.b, toY, toX, view.scale)} />;
      case "spline":
        return <polyline key={key} className={className} points={splinePoints(e.points).map((p) => `${toX(p[0])},${toY(p[1])}`).join(" ")} />;
      default:
        return <polygon key={key} className={className} points={cornersOf(e).map((p) => `${toX(p[0])},${toY(p[1])}`).join(" ")} />;
    }
  };

  // Sizes on the drawing (review B5): a rectangle's W and H, a circle's Ø, a polygon's R.
  const fmt = (v: number) => `${Number(v.toFixed(2))}`;
  const labels: SizeLabel[] = [];
  entities.forEach((e, i) => {
    const say = (key: "w" | "h" | "r", value: number, prefix: string) => {
      const expr = bindings[`${i}.${key}`];
      return expr ? `${prefix}${expr} = ${fmt(value)}` : `${prefix}${fmt(value)}`;
    };
    if (e.kind === "rectangle") {
      labels.push({ index: i, key: "w", x: toX(e.c[0]), y: toY(e.c[1] + e.h / 2) - 8, value: e.w, text: say("w", e.w, "") });
      labels.push({ index: i, key: "h", x: toX(e.c[0] + e.w / 2) + 8, y: toY(e.c[1]) + 4, value: e.h, text: say("h", e.h, "") });
    } else if (e.kind === "circle" || e.kind === "polygon") {
      const a = Math.PI / 4;
      labels.push({ index: i, key: "r", x: toX(e.c[0] + e.r * Math.cos(a)) + 6, y: toY(e.c[1] + e.r * Math.sin(a)) - 6, value: e.r, text: say("r", e.r, "R ") });
    }
  });

  const commitLabel = (label: SizeLabel, text: string) => {
    setEditing(null);
    const t = text.trim();
    if (t === "" || t === (bindings[`${label.index}.${label.key}`] ?? fmt(label.value))) return;
    edit((d) => {
      if (!NUMBER.test(t)) return { ...d, bindings: bindScalar(d.bindings, label.index, label.key, t) };
      const v = Number(t);
      if (!(v > 0)) return d;
      return { ...d, entities: d.entities.map((e, i) => (i === label.index ? ({ ...e, [label.key]: v } as Entity) : e)), bindings: bindScalar(d.bindings, label.index, label.key, null) };
    });
  };

  // The rubber band from the draft to the cursor.
  let preview: ReactElement | null = null;
  const c = cursor?.point;
  if (draft && c) {
    const pts = draft.points;
    const last = pts[pts.length - 1];
    if (draft.tool === "line") {
      preview = drawEntity({ kind: "line", a: last, b: c }, "pv", "sketch-preview");
    } else if (draft.tool === "rectangle") {
      preview = <rect className="sketch-preview" x={Math.min(toX(pts[0][0]), toX(c[0]))} y={Math.min(toY(pts[0][1]), toY(c[1]))} width={Math.abs(toX(c[0]) - toX(pts[0][0]))} height={Math.abs(toY(c[1]) - toY(pts[0][1]))} />;
    } else if (draft.tool === "circle") {
      preview = <circle className="sketch-preview" cx={toX(pts[0][0])} cy={toY(pts[0][1])} r={Math.hypot(c[0] - pts[0][0], c[1] - pts[0][1]) * view.scale} />;
    } else if (draft.tool === "polygon") {
      const r = Math.hypot(c[0] - pts[0][0], c[1] - pts[0][1]);
      preview = r > 0 ? drawEntity({ kind: "polygon", c: pts[0], r, n: sides, angle: Math.atan2(c[1] - pts[0][1], c[0] - pts[0][0]) }, "pv", "sketch-preview") : null;
    } else if (draft.tool === "arc") {
      preview = pts.length === 1 ? drawEntity({ kind: "line", a: pts[0], b: c }, "pv", "sketch-preview") : circleThrough(pts[0], c, pts[1]) ? drawEntity({ kind: "arc", a: pts[0], m: c, b: pts[1] }, "pv", "sketch-preview") : null;
    } else if (draft.tool === "spline") {
      preview = drawEntity({ kind: "spline", points: [...pts, c] }, "pv", "sketch-preview");
    }
  }
  const liveNow = draft ? liveValues(draft.tool, draft.points, c ?? null) : [];
  const entryAt = c ?? draft?.points[draft.points.length - 1] ?? null;

  const axes = PLANES.find((p) => p.id === plane)?.axes ?? ["u", "v"];
  const toolInfo = TOOLS.find((t) => t.id === tool);

  // The status line: red only for what the user must fix; the hint colour while drawing (review c).
  let status: { text: string; tone: "bad" | "hint" | "good" };
  if (error) status = { text: error, tone: "bad" };
  else if (bindError) status = { text: bindError, tone: "bad" };
  else if (draft) status = { text: entry ? `Type ${entry.labels.join(", Tab, ").toLowerCase()} (mm${entry.labels.includes("Angle") ? ", degrees" : ""}), Enter to place; or click` : toolInfo?.hint ?? "", tone: "hint" };
  else if (entities.length === 0) status = { text: "Draw a closed profile: L line, R rectangle, C circle, A arc, S spline, P polygon", tone: "hint" };
  else if (check.message) status = { text: check.message, tone: "bad" };
  else status = { text: `${check.loops} closed loop${check.loops === 1 ? "" : "s"}: ready to pull or revolve`, tone: "good" };

  const squeezed = winW - leftInset - rightInset < MIN_WIDTH;
  const layerStyle = squeezed ? { left: 12, right: 12 } : { left: leftInset + 12, right: rightInset + 12 };

  // The drawing lies over the 3D view (ADR-0039): the camera looks at the plane face-on, orthographic,
  // with the drawing's origin and millimetres per pixel, so the model behind is registered to the grid.
  // Covering the docks (a narrow window), the drawing is opaque over the docks only (`docks`: where
  // they end and begin across the drawing, pixels), so their text does not show through.
  const originKey = origin.join(",");
  const [docks, setDocks] = useState<{ a: number; b: number } | null>(null);
  useLayoutEffect(() => {
    const scene = currentScene();
    const el = svgRef.current;
    if (el === null) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const a = Math.round(Math.max(0, leftInset - r.left));
    const b = Math.round(Math.min(r.width, winW - rightInset - r.left));
    setDocks((d) => (squeezed ? (d?.a === a && d.b === b ? d : { a, b }) : null));
    scene?.pinView(planeCamera(plane, origin, view, { left: r.left, top: r.top, width: r.width, height: r.height }, scene.canvasRect()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plane, view, size, leftInset, rightInset, winW, squeezed, originKey]);
  const canvasBackground =
    docks === null
      ? undefined
      : `linear-gradient(to right, var(--panel-solid) ${docks.a}px, var(--sketch-dim) ${docks.a}px, var(--sketch-dim) ${docks.b}px, var(--panel-solid) ${docks.b}px)`;
  useEffect(() => () => currentScene()?.pinView(null), []);

  return (
    <div className={`sketch-layer registered ${squeezed ? "covering" : ""}`} style={layerStyle} data-ws="sketch-editor">
      <div className="sketch-panel">
        <header className="sketch-header">
          <span className="sketch-title">Sketch</span>
          <div className="sketch-planes" role="radiogroup" aria-label="Sketch plane">
            {PLANES.map((p) => (
              <button key={p.id} type="button" role="radio" aria-checked={plane === p.id} className={plane === p.id ? "on" : ""} onMouseDown={(e) => e.preventDefault()} onClick={() => setPlane(p.id)}>
                {p.label}
              </button>
            ))}
          </div>
          <span className="sketch-spacer" />
          <div className="sketch-actions">
            <Tooltip label="Undo" hint="Ctrl Z" side="bottom" describe={false}>
              <button type="button" className="sketch-icon" aria-label="Undo" onMouseDown={(e) => e.preventDefault()} onClick={doUndo} disabled={hist.past.length === 0}>
                <Undo2 size={15} />
              </button>
            </Tooltip>
            <Tooltip label="Redo" hint="Ctrl Y" side="bottom" describe={false}>
              <button type="button" className="sketch-icon" aria-label="Redo" onMouseDown={(e) => e.preventDefault()} onClick={doRedo} disabled={hist.future.length === 0}>
                <Redo2 size={15} />
              </button>
            </Tooltip>
            <Tooltip label="Fit the sketch in view" hint="F" side="bottom" describe={false}>
              <button type="button" className="sketch-icon" aria-label="Fit" onMouseDown={(e) => e.preventDefault()} onClick={fit}>
                <Maximize size={15} />
              </button>
            </Tooltip>
            <Tooltip label={snapOn ? "Snapping to the grid is on" : "Snapping to the grid is off"} hint="G" side="bottom" describe={false}>
              <button type="button" className={`sketch-icon ${snapOn ? "on" : ""}`} aria-label="Snap to grid" aria-pressed={snapOn} onMouseDown={(e) => e.preventDefault()} onClick={() => setSnapOn(!snapOn)}>
                <Magnet size={15} />
              </button>
            </Tooltip>
            <button type="button" className="sketch-button" onClick={cancel} data-ws="sketch-cancel">
              <X size={14} /> Cancel
            </button>
            <Tooltip label={finishBlocked || "Use this profile"} side="bottom" describe={false}>
              <button type="button" className="sketch-button primary" onClick={() => void finish()} aria-disabled={finishBlocked !== "" || busy} data-ws="sketch-finish">
                <Check size={14} /> Finish
              </button>
            </Tooltip>
          </div>
        </header>
        {confirming && (
          <div className="sketch-confirm" role="alertdialog" aria-label="Discard the sketch's changes?" data-ws="sketch-confirm">
            <span>Discard the changes to this sketch? They cannot be brought back.</span>
            <button type="button" className="sketch-button" onClick={() => setConfirming(false)} autoFocus>
              Keep editing
            </button>
            <button type="button" className="sketch-button danger" onClick={closeSketch} data-ws="sketch-discard">
              Discard
            </button>
          </div>
        )}
        <div className="sketch-body">
          <div className="sketch-tools" role="toolbar" aria-label="Sketch tools" aria-orientation="vertical">
            {TOOLS.map((t) => {
              const Icon = ICONS[t.id];
              return (
                <Tooltip key={t.id} label={`${t.label}: ${t.hint}`} hint={t.key} side="right" describe={false}>
                  <button
                    type="button"
                    className={`sketch-tool ${tool === t.id ? "on" : ""}`}
                    aria-label={t.label}
                    aria-pressed={tool === t.id}
                    aria-keyshortcuts={t.key}
                    data-ws={`sketch-tool-${t.id}`}
                    // The sketch keeps the keyboard: a clicked tool does not hold the focus.
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => chooseTool(t.id)}
                  >
                    <Icon size={18} strokeWidth={1.6} />
                    <span>{t.label}</span>
                    <kbd className="sketch-key">{t.key}</kbd>
                  </button>
                </Tooltip>
              );
            })}
            {/* Fillet: round the corners where two lines meet (the selected lines' corners, or every one). */}
            {tool === "select" && (
              <div className="sketch-sides sketch-fillet" data-ws="sketch-fillet">
                <span>Fillet</span>
                <input type="number" min={0.01} step={0.5} value={filletRadius} onChange={(e) => setFilletRadius(e.target.value)} aria-label="Fillet radius (mm)" title="The radius of the round, in mm" />
                <Tooltip label={selected.size > 1 ? "Round the corners between the selected lines" : "Round every sharp corner where two lines meet"} side="right" describe={false}>
                  <button type="button" className="sketch-button" aria-label="Round corners" data-ws="sketch-fillet-apply" onMouseDown={(e) => e.preventDefault()} onClick={roundCorners}>
                    Round
                  </button>
                </Tooltip>
              </div>
            )}
            {tool === "polygon" && (
              <label className="sketch-sides" title="Sides of the polygon tool">
                <span>Sides</span>
                <input type="number" min={3} max={64} value={sides} onChange={(e) => setSides(Math.max(3, Math.min(64, Number(e.target.value) || 6)))} aria-label="Polygon sides" />
              </label>
            )}
          </div>
          <div className="sketch-stage">
            <svg
              ref={svgRef}
              style={canvasBackground === undefined ? undefined : { background: canvasBackground }}
              className={`sketch-canvas tool-${tool} ${tool === "select" && overPoint ? "over-point" : ""} ${tool === "select" && hover !== null && !overPoint ? "over-entity" : ""}`}
              data-ws="sketch-canvas"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={() => {
                setCursor(null);
                setHover(null);
              }}
              onDoubleClick={finishDraft}
              onWheel={onWheel}
              onContextMenu={(e) => e.preventDefault()}
            >
              {gridLines}
              <line className="sketch-axis-u" x1={0} x2={size.w} y1={toY(0)} y2={toY(0)} />
              <line className="sketch-axis-v" y1={0} y2={size.h} x1={toX(0)} x2={toX(0)} />
              <text className="sketch-axis-label" x={size.w - 18} y={toY(0) - 6}>{axes[0]}</text>
              <text className="sketch-axis-label" x={toX(0) + 6} y={14}>{axes[1]}</text>
              {entities.map((e, i) => drawEntity(e, `e${i}`, `sketch-entity${selected.has(i) ? " selected" : ""}${hover === i && !selected.has(i) ? " hover" : ""}`))}
              {snapTargets(entities).map((p, i) => (
                <circle key={`p${i}`} className="sketch-point" cx={toX(p[0])} cy={toY(p[1])} r={2.5} />
              ))}
              {/* Open ends are marked once there is nothing in progress: during a chain they are expected. */}
              {!draft && check.open.map((p, i) => <circle key={`o${i}`} className="sketch-open" cx={toX(p[0])} cy={toY(p[1])} r={6} />)}
              {check.branches.map((p, i) => (
                <circle key={`b${i}`} className="sketch-open" cx={toX(p[0])} cy={toY(p[1])} r={8} />
              ))}
              {labels.map((l) => {
                const expr = bindings[`${l.index}.${l.key}`];
                const bad = expr ? problems[expr] : undefined;
                // A chip behind the text: a target big enough to click, and it reads as a dimension.
                const width = l.text.length * 6.6 + 12;
                const left = l.key === "w" ? l.x - width / 2 : l.x - 6;
                return (
                  <g
                    key={`l${l.index}${l.key}`}
                    className={`sketch-size ${expr ? "bound" : ""} ${bad ? "invalid" : ""} ${tool === "select" ? "editable" : ""}`}
                    data-ws={`sketch-size-${l.index}-${l.key}`}
                    onPointerDown={(e) => {
                      if (tool !== "select" || e.button !== 0) return;
                      e.stopPropagation();
                      setEditing(l);
                    }}
                    // The mousedown after it would take the focus from the field that just opened.
                    onMouseDown={(e) => e.preventDefault()}
                  >
                    <title>{bad ? `${expr}: ${bad}` : tool === "select" ? "Click to type a size (mm) or a dimension name" : "Select (V), then click to change this size"}</title>
                    <rect className="sketch-size-chip" x={left} y={l.y - 13} width={width} height={18} rx={4} />
                    <text x={l.key === "w" ? l.x : l.x} y={l.y} textAnchor={l.key === "w" ? "middle" : "start"}>
                      {l.text}
                    </text>
                  </g>
                );
              })}
              {preview}
              {draft?.points.map((p, i) => (
                <circle key={`d${i}`} className="sketch-draft-point" cx={toX(p[0])} cy={toY(p[1])} r={3.5} />
              ))}
              {c && tool !== "select" && (
                <g className="sketch-cursor">
                  <circle cx={toX(c[0])} cy={toY(c[1])} r={cursor?.kind === "point" ? 6 : 3} className={cursor?.kind === "point" ? "snap-point" : ""} />
                  {!entry && (
                    <text x={toX(c[0]) + 10} y={toY(c[1]) - 10}>
                      {`${c[0].toFixed(2)}, ${c[1].toFixed(2)}`}
                      {cursor && cursor.kind !== "grid" && cursor.kind !== "free" ? `  ·  ${cursor.kind}` : ""}
                    </text>
                  )}
                </g>
              )}
            </svg>
            {choosing && (
              <div className="sketch-plane-pick" role="dialog" aria-label="Choose the sketch plane" data-ws="sketch-plane-pick">
                <span className="sketch-plane-pick-title">Sketch on a plane</span>
                <div className="sketch-plane-pick-row">
                  {PLANES.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={`sketch-plane-thumb ${plane === p.id ? "on" : ""}`}
                      onMouseDown={(e) => e.preventDefault()}
                      onMouseEnter={() => setPlane(p.id)}
                      onClick={() => {
                        setPlane(p.id);
                        setChoosing(false);
                      }}
                      data-ws={`sketch-plane-${p.id}`}
                    >
                      <PlaneThumb plane={p.id} />
                      <span>{p.label}</span>
                      <span className="muted">{`${p.axes[0]} right, ${p.axes[1]} up`}</span>
                    </button>
                  ))}
                </div>
                <span className="muted sketch-plane-pick-hint">Hover to look at a plane, click to draw on it; Enter keeps {PLANES.find((p) => p.id === plane)?.label}.</span>
              </div>
            )}
            {entry && entryAt && (
              <div className="sketch-entry" style={{ left: Math.min(toX(entryAt[0]) + 14, size.w - 220), top: Math.min(toY(entryAt[1]) + 14, size.h - 40) }} data-ws="sketch-entry" aria-live="polite">
                {entry.labels.map((label, i) => (
                  <span key={label} className={`sketch-entry-field ${i === entry.active ? "active" : ""}`} data-ws={`sketch-entry-${label.toLowerCase()}`}>
                    <span className="sketch-entry-label">{label}</span>
                    {entry.fields[i] !== "" ? <span className="sketch-entry-text">{entry.fields[i]}</span> : <span className="sketch-entry-live">{liveNow[i] !== undefined ? Number(liveNow[i].toFixed(2)) : ""}</span>}
                    <span className="sketch-entry-unit">{label === "Angle" ? "°" : "mm"}</span>
                  </span>
                ))}
              </div>
            )}
            {editing && (
              <input
                className="sketch-size-edit"
                style={{ left: editing.x - 40, top: editing.y - 14 }}
                aria-label={`${editing.key === "w" ? "Width" : editing.key === "h" ? "Height" : "Radius"} of ${entities[editing.index]?.kind ?? "entity"} ${editing.index + 1}`}
                defaultValue={bindings[`${editing.index}.${editing.key}`] ?? fmt(editing.value)}
                autoFocus
                spellCheck={false}
                onFocus={(e) => e.target.select()}
                onBlur={(e) => commitLabel(editing, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitLabel(editing, (e.target as HTMLInputElement).value);
                  else if (e.key === "Escape") setEditing(null);
                }}
              />
            )}
          </div>
          <div className="sketch-side">
            <EntityTable entities={entities} bindings={bindings} problems={problems} selected={selected} onSelect={setSelected} onEdit={edit} onDelete={(index) => edit((d) => ({ ...d, entities: d.entities.filter((_, k) => k !== index), bindings: afterDelete(d.bindings, index) }))} />
            <DimensionsPanel dims={doc.dims} shown={dimShown} problems={problems} onChange={(next) => edit((d) => ({ ...d, dims: next }))} />
          </div>
        </div>
        <footer className="sketch-status" data-ws="sketch-status">
          <span className={`sketch-status-text ${status.tone}`}>{status.text}</span>
          <span className="muted sketch-status-aside">{!draft && toolInfo ? `${toolInfo.label}: ${toolInfo.hint} · ` : ""}grid {grid} mm</span>
        </footer>
      </div>
    </div>
  );
}

/**
 * A plane's thumbnail: the three axes in an isometric view with the plane's square tinted, so the
 * choice reads as a place in space and not as two letters.
 */
function PlaneThumb({ plane }: { plane: SketchSession["plane"] }) {
  // Isometric projections of the world axes (screen y down); y is up, as in the viewport.
  const iso: Record<"x" | "y" | "z", [number, number]> = { x: [0.87, 0.5], y: [0, -1], z: [-0.87, 0.5] };
  const [a, b] = ({ xy: ["x", "y"], yz: ["y", "z"], zx: ["z", "x"] } as const)[plane];
  const c = [36, 40];
  const s = 22;
  const pt = (k: "x" | "y" | "z", f = 1) => [c[0] + iso[k][0] * s * f, c[1] + iso[k][1] * s * f];
  const pa = pt(a);
  const pb = pt(b);
  const quad = `${c[0]},${c[1]} ${pa[0]},${pa[1]} ${pa[0] + pb[0] - c[0]},${pa[1] + pb[1] - c[1]} ${pb[0]},${pb[1]}`;
  return (
    <svg width={72} height={64} viewBox="0 0 72 64" aria-hidden="true" className="sketch-plane-svg">
      <polygon points={quad} className="sketch-plane-quad" />
      {(["x", "y", "z"] as const).map((k) => {
        const e = pt(k, 1.35);
        return (
          <g key={k} className={`sketch-plane-axis axis-${k}`}>
            <line x1={c[0]} y1={c[1]} x2={e[0]} y2={e[1]} />
            <text x={e[0] + (k === "z" ? -8 : 3)} y={e[1] + (k === "y" ? -2 : 4)}>{k}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Whether two points are the same, for the table's keys. */
export const same = samePoint;