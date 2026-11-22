/**
 * The direct flow in the viewport (ADR-0039): the ghost of a body being pulled or rebuilt, and the
 * Pull handle on a body an extrude made.
 *
 * The handle is an arrow at the profile's centre on the pulled face, along the pull direction,
 * drawn over the canvas from projected world points. Dragging it shows a ghost built in the browser
 * from the 2D profile (no mesher, no API) and, on release, writes the depth with one
 * `parameter.update` (one undo step, one rebuild). The value at the arrow's tip is a field: click,
 * type, Enter. While the operation rebuilds, the ghost of the target stays until the new body is on
 * screen, so the view is never empty. Nothing here is model state: the depth is read from the tree
 * and written through the API.
 *
 * While a body builds (after Finish or a pull), a status line at the top of the view says what is built, the
 * depth, what the run reports and the time so far, with Cancel (`operation.cancel`). After Finish the Pull field
 * opens on the new body as soon as its handle is on screen (`requestPullFocus`), so the depth Finish chose is in
 * front of the user, ready to type over.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { findNode, useStore } from "../state/store";
import { currentScene } from "../viewport/scene";
import { operationOf } from "../panels/bodySteps";
import { ghostObject } from "./ghost";
import { buildGhost, clearGhost, currentGhost, dragGhost, subscribeGhost, takePullFocus } from "./ghostState";
import { buildStatusText } from "./buildStatus";
import { operationText } from "../state/operations";
import { LoaderCircle, X } from "lucide-react";
import { boxLoop, pullOf, type PullInfo } from "./opPull";
import { planeAxes, sketchToWorld, type Vec3 } from "./plane";
import { loopsBox, type Loop } from "./profile";
import { depthFromDrag, depthLabel, depthText, typedDepth } from "./pull";
import "./direct.css";

/** Screen length of the arrow, pixels. */
const ARROW_PX = 46;
/** A build ghost gives up after this long: the run failed quietly or never started. */
const GIVE_UP_MS = 90_000;
/** A build ghost whose operation never started a run is dropped after this long (the value did not change). */
const NO_RUN_MS = 2_500;

function accent(): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  return value !== "" ? value : "#19c0a8";
}

export default function DirectLayer() {
  // The Pull handle is a modelling control: in Physics, Mesh, Solve and Results it sat over the body
  // as a stray "25 mm" while the user picked faces or read fields (walkthrough 2026-11-18).
  const modelling = useStore((s) => s.stage === "Geometry");
  return (
    <>
      <GhostLayer />
      {modelling && <PullHandle />}
      <BuildStatus />
    </>
  );
}

// ---------------------------------------------------------------------------------------------- the ghost
/**
 * Puts the ghost in the scene and takes it out when the body it stands for is drawn: the run of its
 * operation ended and the output's mesh reached the scene (or the run failed, or none started).
 */
function GhostLayer() {
  const ghost = useSyncExternalStore(subscribeGhost, currentGhost);

  useEffect(() => {
    const scene = currentScene();
    if (scene === null) return;
    scene.setGhost(ghost === null ? null : ghostObject(ghost.spec, accent()));
  }, [ghost?.spec]);

  useEffect(() => () => currentScene()?.setGhost(null), []);

  useEffect(() => {
    if (ghost === null || ghost.phase !== "build") return;
    const scene = currentScene();
    const meshed = new Set<string>();
    let output: string | null = null;
    let sawRun = false;
    let lostRunAt = 0;
    const doneBefore = useStore.getState().lastOperationDone;
    const check = () => {
      if (ghost.opId === null) return;
      const s = useStore.getState();
      const running = s.operations[ghost.opId] !== undefined;
      if (running) {
        sawRun = true;
        lostRunAt = 0;
      }
      const done = s.lastOperationDone;
      if (done !== doneBefore && done !== null && done.nodeId === ghost.opId) output = done.output ?? "";
      // A first, coarse look of an output already shows the body: the ghost has done its job.
      const outputs = findNode(s.tree, ghost.opId)?.params.outputs;
      if (Array.isArray(outputs) && outputs.some((id) => typeof id === "string" && meshed.has(id))) {
        clearGhost();
        return;
      }
      const now = performance.now();
      if (output !== null) {
        // The body is on screen once its mesh reached the scene; an output with no mesh, or one that
        // came before the done event, lets the ghost go at once.
        if (output === "" || meshed.has(output)) clearGhost();
        return;
      }
      if (sawRun && !running) {
        // The done event follows the end of progress in a second update: wait a moment for it.
        if (lostRunAt === 0) lostRunAt = now;
        else if (now - lostRunAt > 1500) clearGhost();
      } else if (!sawRun && now - ghost.since > NO_RUN_MS) clearGhost();
      if (now - ghost.since > GIVE_UP_MS) clearGhost();
    };
    const offSource = scene?.addSourceListener((source) => {
      meshed.add(source);
      check();
    });
    const offStore = useStore.subscribe(check);
    const timer = window.setInterval(check, 250);
    check();
    return () => {
      offSource?.();
      offStore();
      window.clearInterval(timer);
    };
  }, [ghost]);

  return null;
}

// ---------------------------------------------------------------------------------------------- the build status
/** What is being built, from the moment Finish (or a pull) is pressed until the body is on screen, with Cancel. */
function BuildStatus() {
  const ghost = useSyncExternalStore(subscribeGhost, currentGhost);
  const building = ghost !== null && ghost.phase === "build";
  const opId = building ? ghost.opId : null;
  const progress = useStore((s) => (opId === null ? null : (s.operations[opId] ?? null)));
  const execute = useStore((s) => s.execute);
  const [now, setNow] = useState(() => performance.now());
  const [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    setCancelling(false);
    if (!building) return;
    setNow(performance.now());
    const timer = window.setInterval(() => setNow(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, [building, ghost?.started]);
  if (!building || ghost === null) return null;
  const text = buildStatusText({ progress: progress === null ? "" : operationText(progress), seconds: (now - ghost.started) / 1000, depth: ghost.spec.depth, known: opId !== null });
  const cancel = () => {
    if (opId === null) return;
    setCancelling(true);
    void execute("operation.cancel", { op: opId })
      .then(() => clearGhost())
      .catch(() => setCancelling(false));
  };
  return (
    <div className="build-status glass" role="status" data-ws="build-status">
      <LoaderCircle size={13} className="mesh-spin" aria-hidden="true" />
      <span className="build-status-text" data-ws="build-status-text">
        {cancelling ? "Cancelling…" : text}
      </span>
      <button type="button" className="build-status-cancel no-drag" onClick={cancel} disabled={opId === null || cancelling} title={opId === null ? "The operation is being created" : "Stop building this body"} data-ws="build-status-cancel">
        <X size={13} aria-hidden="true" />
        Cancel
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- the handle
interface HandleGeometry {
  base: { x: number; y: number };
  tip: { x: number; y: number };
  /** Pixels one millimetre along the pull moves on screen. */
  axis: [number, number];
}

/** The loops a ghost of `info` draws: the profile, or the body's box when the profile cannot be drawn here. */
function ghostLoops(info: PullInfo, partId: string): Loop[] | null {
  if (info.loops !== null) return info.loops;
  const box = currentScene()?.sourceBounds(partId) ?? null;
  return box === null ? null : boxLoop(info.plane, info.origin, box);
}

/** The world point the handle sits on: the profile's centre on the pulled face. */
function anchorOf(info: PullInfo, loops: Loop[] | null, depth: number): Vec3 {
  const box = loops ? loopsBox(loops) : null;
  const cu = box ? (box[0] + box[2]) / 2 : 0;
  const cv = box ? (box[1] + box[3]) / 2 : 0;
  return sketchToWorld(info.plane, info.origin, cu, cv, info.symmetric ? depth / 2 : depth);
}

function geometryAt(info: PullInfo, anchor: Vec3, host: DOMRect): HandleGeometry | null {
  const scene = currentScene();
  if (scene === null) return null;
  const w = planeAxes(info.plane).w;
  const a = scene.worldToClient(anchor);
  const b = scene.worldToClient([anchor[0] + w[0] / 1000, anchor[1] + w[1] / 1000, anchor[2] + w[2] / 1000]);
  if (a === null || b === null) return null;
  const axis: [number, number] = [b.x - a.x, b.y - a.y];
  const len = Math.hypot(axis[0], axis[1]);
  // Looking straight down the pull: the arrow points up the screen and a drag up still pulls.
  const dir = len > 0.05 ? [axis[0] / len, axis[1] / len] : [0, -1];
  const base = { x: a.x - host.left, y: a.y - host.top };
  return { base, tip: { x: base.x + dir[0] * ARROW_PX, y: base.y + dir[1] * ARROW_PX }, axis: len > 0.05 ? axis : [0, -Math.max(len, 1)] };
}

function PullHandle() {
  const tree = useStore((s) => s.tree);
  const part = useStore((s) => (s.pickMode === "body" && s.picked.bodies.length === 1 ? s.picked.bodies[0] : null));
  const blocked = useStore((s) => s.sketch !== null || s.operationCard !== null || s.measureMode || s.paintMode);
  const execute = useStore((s) => s.execute);
  const info = useMemo(() => (part === null || blocked ? null : pullOf(operationOf(tree, part))), [tree, part, blocked]);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [geo, setGeo] = useState<HandleGeometry | null>(null);
  // The depth while dragging (mm); null when not dragging.
  const [dragDepth, setDragDepth] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  // The drag in progress; `depth` is the latest, ahead of the render (a release can follow a move in the same frame).
  const drag = useRef<{ x: number; y: number; start: number; depth: number; axis: [number, number]; loops: Loop[] | null; moved: boolean } | null>(null);
  // The depth just written, shown until the tree says so (no flash of the old value on release).
  const [written, setWritten] = useState<{ opId: string; from: number; to: number } | null>(null);
  // After Finish: the new body's field opens once, as soon as its handle is on screen.
  useEffect(() => {
    if (info !== null && geo !== null && info.expression === null && takePullFocus(info.opId)) setEditing(true);
  }, [info, geo]);
  const settled = written !== null && (info === null || info.opId !== written.opId || Math.abs(info.depth - written.from) > 1e-9);
  useEffect(() => {
    if (settled) setWritten(null);
  }, [settled]);
  const pendingDepth = written !== null && !settled ? written.to : null;
  const depth = dragDepth ?? pendingDepth ?? info?.depth ?? 0;
  const key = info === null ? "" : `${info.opId}|${info.plane}|${info.origin.join(",")}|${depth}|${info.symmetric}|${info.loops === null ? "box" : info.loops.length}`;

  // Follow the camera: the handle is re-projected whenever the view moves, once per frame at most.
  const recompute = useCallback(() => {
    const host = hostRef.current;
    if (info === null || part === null || host === null) {
      setGeo(null);
      return;
    }
    const loops = ghostLoops(info, part);
    setGeo(geometryAt(info, anchorOf(info, loops, depth), host.getBoundingClientRect()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, part]);

  useEffect(() => {
    recompute();
    const scene = currentScene();
    if (scene === null) return;
    let frame = 0;
    const later = () => {
      if (frame !== 0) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        recompute();
      });
    };
    const offView = scene.addViewListener(later);
    const offSource = scene.addSourceListener(later);
    window.addEventListener("resize", later);
    return () => {
      offView();
      offSource();
      window.removeEventListener("resize", later);
      if (frame !== 0) cancelAnimationFrame(frame);
    };
  }, [recompute]);

  const commit = useCallback(
    async (value: string, ghostDepth: number | null) => {
      if (info === null) return;
      const loops = part === null ? null : ghostLoops(info, part);
      if (ghostDepth !== null && loops !== null) buildGhost({ plane: info.plane, origin: info.origin, loops, depth: ghostDepth, symmetric: info.symmetric }, info.opId);
      try {
        await execute("parameter.update", { node: info.opId, values: { depth: value } });
      } catch {
        // `execute` raised the toast; the body did not change, so neither does the ghost.
        clearGhost();
        setWritten(null);
      }
    },
    [info, part, execute],
  );

  if (info === null || geo === null) return <div ref={hostRef} className="direct-layer" aria-hidden="true" />;
  const draggable = info.expression === null;

  const onPointerDown = (e: ReactPointerEvent<SVGElement>) => {
    if (e.button !== 0 || !draggable) return;
    e.stopPropagation();
    e.preventDefault();
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      // A pointer the browser does not know (a synthetic one) drags without capture.
    }
    drag.current = { x: e.clientX, y: e.clientY, start: info.depth, depth: info.depth, axis: geo.axis, loops: part === null ? null : ghostLoops(info, part), moved: false };
  };
  const onPointerMove = (e: ReactPointerEvent<SVGElement>) => {
    const d = drag.current;
    if (d === null) return;
    if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 2) d.moved = true;
    if (!d.moved) return;
    const next = depthFromDrag(d.start, d.axis, [e.clientX - d.x, e.clientY - d.y], info.symmetric ? 2 : 1);
    d.depth = next;
    setDragDepth(next);
    if (d.loops !== null) dragGhost({ plane: info.plane, origin: info.origin, loops: d.loops, depth: next, symmetric: info.symmetric });
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    const next = d?.depth ?? null;
    setDragDepth(null);
    if (d === null || !d.moved || next === null || Math.abs(next - info.depth) < 1e-9) {
      if (currentGhost()?.phase === "drag") clearGhost();
      if (d !== null && !d.moved) setEditing(true);
      return;
    }
    setWritten({ opId: info.opId, from: info.depth, to: next });
    void commit(depthText(next), next);
  };
  const onKeyDownDrag = (e: ReactKeyboardEvent<SVGElement>) => {
    if (e.key === "Escape" && drag.current !== null) {
      drag.current = null;
      setDragDepth(null);
      clearGhost();
    }
  };

  const submit = (text: string) => {
    setEditing(false);
    const value = typedDepth(text);
    if (value === null) return;
    const n = Number(text.trim());
    if (Number.isFinite(n) && Math.abs(n - info.depth) < 1e-9) return;
    if (Number.isFinite(n) && n > 0) setWritten({ opId: info.opId, from: info.depth, to: n });
    void commit(value, Number.isFinite(n) && n > 0 ? n : null);
  };

  const { base, tip } = geo;
  const dx = tip.x - base.x;
  const dy = tip.y - base.y;
  const len = Math.hypot(dx, dy) || 1;
  const [ux, uy] = [dx / len, dy / len];
  const head = `${tip.x},${tip.y} ${tip.x - ux * 11 - uy * 6},${tip.y - uy * 11 + ux * 6} ${tip.x - ux * 11 + uy * 6},${tip.y - uy * 11 - ux * 6}`;
  const label = info.expression !== null ? `${info.expression} = ${depthLabel(info.depth)} mm` : `${depthLabel(depth)} mm`;
  // **Clear of the arrowhead.** The chip is centred on this point, and 14 px from the tip put half of
  // it over the head: a press on the head landed on the chip, so a drag opened the depth field
  // and the arrow could only be dragged by the few pixels of its shaft (the build walk, 2026-11-19).
  // Its half-extent along the arrow, from its width (about 7 px a character) and its 22 px height.
  const chipReach = Math.abs(ux) * (label.length * 3.5 + 10) + Math.abs(uy) * 11;
  const labelAt = { x: tip.x + ux * (8 + chipReach), y: tip.y + uy * (8 + chipReach) };

  return (
    <div ref={hostRef} className="direct-layer" data-ws="pull-handle">
      <svg className="direct-svg">
        <g
          className={`pull-arrow ${draggable ? "" : "locked"} ${dragDepth !== null ? "dragging" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDownDrag}
          tabIndex={-1}
          data-ws="pull-arrow"
        >
          <title>{draggable ? "Drag to pull; click to type the depth" : `The depth is ${info.expression}: click to change it`}</title>
          {/* A wide invisible stroke: the target is easier to hit than the arrow is thick. */}
          <line className="pull-hit" x1={base.x} y1={base.y} x2={tip.x} y2={tip.y} />
          <line className="pull-shaft" x1={base.x} y1={base.y} x2={tip.x - ux * 9} y2={tip.y - uy * 9} />
          <polygon className="pull-head" points={head} />
          <circle className="pull-base" cx={base.x} cy={base.y} r={3.5} />
        </g>
      </svg>
      {editing ? (
        <input
          className="pull-field"
          style={{ left: labelAt.x, top: labelAt.y }}
          aria-label="Pull depth (mm)"
          defaultValue={info.expression ?? depthLabel(info.depth)}
          autoFocus
          spellCheck={false}
          data-ws="pull-field"
          onFocus={(e) => e.target.select()}
          onBlur={(e) => submit(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") submit((e.target as HTMLInputElement).value);
            else if (e.key === "Escape") setEditing(false);
          }}
        />
      ) : (
        <button type="button" className={`pull-chip ${dragDepth !== null ? "dragging" : ""}`} style={{ left: labelAt.x, top: labelAt.y }} onClick={() => setEditing(true)} data-ws="pull-chip" title="Type the depth: millimetres, or an expression">
          {label}
        </button>
      )}
    </div>
  );
}

