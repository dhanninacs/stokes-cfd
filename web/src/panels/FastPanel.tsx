import { useEffect, useState } from "react";
import { Play, Square, Zap } from "lucide-react";
import { Note } from "../components/Note";
import { Button } from "../components/ui";
import { AUTOMATIC_PLANE, FIDELITIES, PLANES, STORAGES, TURBULENCE, fieldsFor, frameWarning, gridNotes, heatText, legendOf, resolutionWarning, planeOffset, planeShare, readStatus, statsLines, streamRows, zonesLine, type FastField } from "../fast/fastModel";
import { useFastStore } from "../fast/fastStore";
import { steppedGradientCssFor } from "../state/colormap";
import { useStore } from "../state/store";
import "./fast.css";

/** The panel's full explanation, in its tooltip (notes on a panel stay at eight words or fewer). */
const ABOUT =
  "Fast mode solves the case on this computer's GPU on a grid of cubic cells, straight from the shapes " +
  "made with Create: no mesh, and results within seconds. It follows your edits while it runs. It is a " +
  "preview, like a quick look before the full solve: run the case for the numbers you rely on.";

/** How long the position slider keeps the place it was given while the frames have not reached it. */
const POSITION_HOLD_MS = 1500;

/**
 * Fast mode (ADR-0047): start and stop the GPU preview, choose its fidelity, the plane it shows and
 * the value that colours it. Every action is a `fast.*` command; the plane is drawn by the viewport's
 * `FastLayer` from what the server serves, and the numbers are the server's (ADR-0013).
 *
 * A `glass` card like the other panels of the right slot: without it the text lay straight on the view.
 */
export default function FastPanel() {
  const execute = useStore((s) => s.execute);
  const project = useStore((s) => s.project);
  const { field, status, frame, warning, range, setField, setStatus, setWarning } = useFastStore();
  const [available, setAvailable] = useState<{ ok: boolean; why: string; adapter: string } | null>(null);
  const [fidelity, setFidelity] = useState(2);
  const [plane, setPlane] = useState<number>(AUTOMATIC_PLANE);
  const [moved, setMoved] = useState<{ share: number; axis: number; at: number } | null>(null);
  const [turbulence, setTurbulence] = useState<string>("auto");
  const [storage, setStorage] = useState<string>("f32");
  const [warmStart, setWarmStart] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = status !== null && (status.state === "running" || status.state === "starting");

  useEffect(() => {
    let live = true;
    void execute("fast.check", {})
      .then((answer) => {
        const a = answer as { available?: boolean; adapter?: { name?: string }; error?: { message?: string } };
        if (live) setAvailable({ ok: a.available === true, why: a.error?.message ?? "", adapter: a.adapter?.name ?? "" });
      })
      .catch((e: unknown) => live && setAvailable({ ok: false, why: e instanceof Error ? e.message : String(e), adapter: "" }));
    void execute("fast.status", {})
      .then((answer) => live && setStatus(readStatus(answer)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [execute, setStatus]);

  // While a preview runs, its grid and model come from `fast.status` once a second; frames arrive as events.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      void execute("fast.status", {})
        .then((answer) => setStatus(readStatus(answer)))
        .catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [running, execute, setStatus]);

  // A preview started elsewhere (the palette, an agent) shows its own fidelity and plane here.
  const live = running ? status : null;
  const shownFidelity = live?.requested ?? fidelity;
  const shownFrame = frame ?? live?.frame ?? null;
  const shownPlane = live !== null && shownFrame !== null ? (live.automaticPlane ? AUTOMATIC_PLANE : shownFrame.axis) : plane;

  const run = (name: string, params: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    setWarning("");
    execute(name, params)
      .then(() => execute("fast.status", {}))
      .then((answer) => setStatus(readStatus(answer)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  const start = () => run("fast.start", { fidelity, turbulence, storage, warm_start: warmStart, ...(plane >= 0 ? { axis: plane } : {}) });
  const stop = () => run("fast.stop", {});
  const configure = (params: Record<string, unknown>) => {
    if (running) run("fast.configure", params);
  };
  // The slider shows where the user put it until the frames catch up (or a moment has passed):
  // following the frames alone, each key press started from the plane of a frame ago.
  const served = planeShare(shownFrame, live?.spans ?? null);
  const cell = live?.spans != null && shownFrame !== null ? 1 / live.spans[shownFrame.axis].count : 1;
  const holding = moved !== null && moved.axis === shownFrame?.axis && Math.abs(served - moved.share) > cell && Date.now() - moved.at < POSITION_HOLD_MS;
  const position = holding && moved !== null ? moved.share : served;
  /** The plane moved along its axis: no busy state, the next frame shows it. */
  const movePlane = (share: number) => {
    if (live === null || shownFrame === null) return;
    const offset = planeOffset(share, shownFrame.axis, live.spans);
    if (offset === null) return;
    setMoved({ share, axis: shownFrame.axis, at: Date.now() });
    setPlane(shownFrame.axis);
    void execute("fast.configure", { axis: shownFrame.axis, offset })
      .then((answer) => setStatus(readStatus(answer)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const lines = live !== null ? statsLines({ ...live, frame: shownFrame }) : [];
  const notes = live !== null ? gridNotes(live) : [];
  const view = useStore((s) => s.results.view);
  const thermal = live?.thermal === true;
  const fields = fieldsFor(thermal);
  const zones = live !== null ? zonesLine(live) : "";
  const rows = live !== null && (thermal || (shownFrame?.streams.length ?? 0) > 1) ? streamRows(shownFrame) : [];
  const balance = shownFrame?.heatBalance ?? null;
  // A value the running preview does not have (the temperature, after a thermal case): back to velocity.
  useEffect(() => {
    if (live !== null && field === "T" && !thermal) setField("U");
  }, [live, field, thermal, setField]);
  const caution = running ? frameWarning(frame) : "";
  const coarse = live !== null ? resolutionWarning(live) : "";
  const legend = running && range !== null ? legendOf(range, live?.density ?? null) : null;
  const failed = status?.state === "failed" ? status.error : "";

  return (
    <section className="fast-panel glass" aria-label="Fast mode" data-ws="fast-panel">
      <header className="fast-title">
        <Zap size={13} aria-hidden="true" />
        <span className="section-title">Fast mode</span>
        <span className="muted">GPU preview</span>
      </header>
      <div className="fast-body">
        <Note className="muted text-[11.5px]" tip={ABOUT}>
          A quick look; run the case for results.
        </Note>
        {available !== null && !available.ok && (
          <p className="fast-alert" role="alert">
            {available.why || "Fast mode is not available on this computer."}
          </p>
        )}
        {available?.ok && available.adapter && <p className="muted text-[11px]">GPU: {available.adapter}</p>}

        <label className="fast-field">
          <span>Fidelity</span>
          <span className="select-wrap">
            <select
              className="field"
              aria-label="Fidelity"
              value={String(shownFidelity)}
              disabled={busy}
              onChange={(e) => {
                const next = Number(e.target.value);
                setFidelity(next);
                configure({ fidelity: next });
              }}
              data-ws="fast-fidelity"
            >
              {FIDELITIES.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          </span>
        </label>
        <label className="fast-field">
          <span>Plane shown</span>
          <span className="select-wrap">
            <select
              className="field"
              aria-label="Plane shown"
              value={String(shownPlane)}
              onChange={(e) => {
                const next = Number(e.target.value);
                setPlane(next);
                configure({ axis: next });
              }}
              data-ws="fast-plane"
            >
              {PLANES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </span>
        </label>
        {live !== null && shownFrame !== null && live.spans !== null && (
          <label className="fast-field">
            <span>Position along it</span>
            <input
              type="range"
              className="fast-slider"
              aria-label="Plane position"
              min={0}
              max={1}
              step={0.01}
              value={position}
              onChange={(e) => movePlane(Number(e.target.value))}
              data-ws="fast-position"
            />
          </label>
        )}
        <label className="fast-field">
          <span>Colour by</span>
          <span className="select-wrap">
            <select className="field" aria-label="Colour by" value={field} onChange={(e) => setField(e.target.value as FastField)} data-ws="fast-field">
              {fields.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label} ({f.units})
                </option>
              ))}
            </select>
          </span>
        </label>

        <label className="fast-field">
          <span>Turbulence</span>
          <span className="select-wrap">
            <select
              className="field"
              aria-label="Turbulence"
              value={live?.turbulence ?? turbulence}
              onChange={(e) => {
                setTurbulence(e.target.value);
                configure({ turbulence: e.target.value });
              }}
              data-ws="fast-turbulence"
            >
              {TURBULENCE.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </span>
        </label>

        <label className="fast-field">
          <span>Storage</span>
          <span className="select-wrap">
            <select
              className="field"
              aria-label="Storage"
              title="How the lattice keeps its numbers. 16-bit halves the memory, so a fine lattice fits; set before Start."
              value={live?.storage ?? storage}
              disabled={running}
              onChange={(e) => setStorage(e.target.value)}
              data-ws="fast-storage"
            >
              {STORAGES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </span>
        </label>
        <label className="fast-check" title="A fine lattice (fidelity 4 or 5) first settles a coarse one and starts from its flow; a fidelity raised while running keeps the flow.">
          <input type="checkbox" checked={live?.warmStart ?? warmStart} disabled={running} onChange={(e) => setWarmStart(e.target.checked)} data-ws="fast-warm" />
          <span>Warm start from a coarser lattice</span>
        </label>

        {legend !== null && (
          <div className="fast-legend" data-ws="fast-legend">
            <span className="muted">{legend.title}</span>
            <div className="fast-legend-bar" style={{ background: steppedGradientCssFor(view.map, view.bands ?? 0, view.reverse === true, "to right") }} aria-hidden="true" />
            <div className="fast-legend-ends">
              <span>{legend.low}</span>
              <span>{legend.high}</span>
            </div>
          </div>
        )}
        {zones !== "" && (
          <p className="text-[11.5px]" data-ws="fast-zones">
            {zones}
          </p>
        )}
        {rows.length > 0 && (
          <table className="fast-streams" data-ws="fast-streams">
            <thead>
              <tr>
                <th>Stream</th>
                <th>Flow</th>
                <th>Δp</th>
                <th>T in → out</th>
                <th>Q</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row[0]}>
                  {row.map((cell, index) => (
                    <td key={index}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
            {balance !== null && rows.length > 1 && (
              <tfoot>
                <tr>
                  <td colSpan={4}>Heat balance</td>
                  <td>{heatText(balance)}</td>
                </tr>
              </tfoot>
            )}
          </table>
        )}
      </div>
      {/* Start, Stop and what the preview says stay in view: the options above scroll, these do not. */}
      <div className="fast-foot" data-ws="fast-foot">
        <div className="fast-actions">
          {!running ? (
            <Button
              variant="primary"
              disabled={busy || project === null || available?.ok !== true}
              title={project === null ? "Open a project first" : available === null ? "Looking for a GPU…" : available.ok ? "Solve the case on the GPU" : available.why || "Fast mode is not available on this computer"}
              onClick={start}
              data-ws="fast-start"
            >
              <Play size={12} /> Start
            </Button>
          ) : (
            <Button disabled={busy} onClick={stop} data-ws="fast-stop">
              <Square size={12} /> Stop
            </Button>
          )}
        </div>
        {lines.length > 0 && (
          <div className="fast-stats" role="status" aria-live="polite" data-ws="fast-stats">
            {lines.map((line, index) => (
              <p key={index} className={index === 0 ? "font-semibold" : "muted"}>
                {line}
              </p>
            ))}
          </div>
        )}
        {notes.map((note) => (
          <Note key={note.text} className="muted text-[11.5px]" tip={note.tip} data-ws="fast-note">
            {note.text}
          </Note>
        ))}
        {coarse !== "" && (
          <p className="fast-alert fast-caution" role="alert" data-ws="fast-coarse">
            {coarse}
          </p>
        )}
        {(caution || warning) && (
          <p className="fast-alert fast-caution" role="alert">
            {caution || warning}
          </p>
        )}
        {(error || failed) && (
          <p className="fast-alert" role="alert">
            {error || failed}
          </p>
        )}
      </div>
    </section>
  );
}
