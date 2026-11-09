/**
 * The Mesh panel (ADR-0024, T-211b): the Mesh stage's settings, the Generate and Cancel buttons, the
 * progress and the result, beside Properties and Conditions in the right slot.
 *
 * Everything that decides something is `meshPanelModel.ts` (states, labels, verdicts, button
 * enablement, with their tests); `meshRun.ts` presses the buttons; this file only draws. The data is
 * the server's: the settings and the result are nodes of the tree (`/mesh/settings`,
 * `/mesh/results/<mesher>`), the step and the log come from the `mesh.*` events, and every edit is
 * one `mesh.configure` (one undo step), so a refusal is the command's own message under the field.
 *
 * Importers must write `./SchemaFields.tsx` with the extension: on Windows `./SchemaFields` resolves to
 * the helpers' `schemaFields.ts`.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, CircleAlert, CircleCheck, CircleHelp, CircleX, LoaderCircle } from "lucide-react";
import { StokesApiError } from "../api/client";
import { Button } from "../components/ui";
import { meshPatchesOf, type MeshDisplay } from "../state/meshPatches";
import { hiddenPatchIds, meshOnScreen } from "../state/meshReveal";
import { resultsShown } from "./fieldsModel";
import { findNode, useStore } from "../state/store";
import { useWorkspace } from "../workspace/store";
import { placeOf } from "../workspace/geometry";
import {
  LOG_CAP,
  POLL_MS,
  applyMeshEvent,
  cancelState,
  findMeshNodes,
  formatCount,
  formatSeconds,
  isActive,
  isStale,
  logText,
  mappingRows,
  mergeLogTail,
  phaseNow,
  progressOf,
  fidelityRows,
  fidelityVerdict,
  qualityRows,
  readResult,
  shouldPoll,
  stateLine,
  worstVerdict,
  type Tone,
  type Verdict,
} from "./meshPanelModel";
import { cancelMesh, generateMesh, loadMeshContext, useGenerateState, useMeshContext } from "./meshRun";
import MeshBasic from "./MeshBasic";
import SettingsNodeFields from "./SettingsNodeFields";

const TONE_ICON: Record<Tone, typeof CircleCheck> = { neutral: CircleHelp, busy: LoaderCircle, ok: CircleCheck, warn: CircleAlert, bad: CircleX };
const VERDICT_ICON: Record<Verdict, typeof CircleCheck> = { pass: CircleCheck, warn: CircleAlert, unknown: CircleHelp };

function messageOf(err: unknown): string {
  return err instanceof StokesApiError ? err.message : err instanceof Error ? err.message : String(err);
}

export default function MeshPanel({ hideInAdvanced = [] }: {
  /** Fields a task page shown with this panel already draws (the Sizing task's base size), left out of Advanced. */
  hideInAdvanced?: readonly string[];
} = {}) {
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const stage = useStore((s) => s.stage);
  const setStage = useStore((s) => s.setStage);
  const requestFit = useStore((s) => s.requestFit);
  const live = useStore((s) => s.mesh);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const execute = useStore((s) => s.execute);
  const meshers = useMeshContext((c) => c.meshers);
  const loaded = useMeshContext((c) => c.loaded);
  const busy = useMeshContext((c) => c.busy);
  const runError = useMeshContext((c) => c.error);
  const generate = useGenerateState();

  const { settings, result } = useMemo(() => findMeshNodes(tree), [tree]);
  const view = useMemo(() => readResult(result), [result]);

  /**
   * The host and install this mesh will use.
   *
   * **The project's Execution host, not the Compute default alone.** `mesh.generate` runs on
   * `/mesh/settings.host`, so a line that always showed `compute.get`'s effective host could name
   * a different machine from the one that meshes — measured on a WSL project while Compute said
   * `local`. An empty Execution host means the Compute default (ADR-0024), which is the one case
   * where `compute.get` is the right answer; otherwise `host.probe` asks that host for the install
   * the agent will actually use.
   *
   * Re-read when the panel mounts, when the host changes and when a mesh finishes.
   */
  const [engine, setEngine] = useState<{ host: string; install: string } | null>(null);
  const settingsHost = typeof settings?.params.host === "string" ? settings.params.host : "";
  useEffect(() => {
    // **Waits for the connection**, which the Mesh panel can outrun: it mounts with the workspace,
    // before the socket is up, and `execute` throws "not connected". That showed as a red Console
    // badge - measured, and found because claude-win2 asked what the dot was. A panel that cannot
    // ask yet has nothing to report, which is not an error.
    if (!connected) return;
    let live = true;
    const load = async () => {
      if (settingsHost === "") {
        const reply = (await execute("compute.get")) as {
          effective_host?: string;
          effective_install?: string;
        };
        if (live) {
          setEngine({ host: reply.effective_host ?? "", install: reply.effective_install ?? "" });
        }
        return;
      }
      const reply = (await execute("host.probe", { host: settingsHost })) as {
        record?: { default_install?: string };
      };
      if (live) setEngine({ host: settingsHost, install: reply.record?.default_install ?? "" });
    };
    void load().catch(() => live && setEngine(null));
    return () => {
      live = false;
    };
  }, [connected, execute, settingsHost, result?.params.state]);
  const phase = phaseNow(view, live);
  const line = stateLine(view, live);
  const progress = progressOf(view, live);
  const cancel = cancelState(phase);
  const stale = isStale(view);
  // A run that has started and reports nothing yet (the first step, no fraction) shows a sliding bar rather than an empty one.
  const indeterminate = (phase === "running" || phase === "cancelling") && (progress.fraction === null || progress.fraction === 0);
  const rows = useMemo(() => qualityRows(view), [view]);
  // **The fidelity block, separate from the quality rows on purpose** (Claude's 1115): OpenFOAM's
  // checks say whether the mesh is well formed, these say whether it is the RIGHT SHAPE.
  const fid = useMemo(() => fidelityRows(view), [view]);
  const fidWord = fidelityVerdict(view);
  const mapping = useMemo(() => mappingRows(view, (id) => findNode(tree, id)?.name ?? null), [view, tree]);

  // The viewport draws the patches while the Mesh stage is active (T-211); this is the way to it from the panel.
  const patches = useMemo(() => meshPatchesOf(tree), [tree]);
  const patchCount = patches.length;
  const showInView = () => {
    if (stage !== "Mesh") setStage("Mesh");
    requestFit();
  };
  // A finished mesh that nothing draws: another stage is in front (the viewport draws the mesh in the Mesh stage, and in Results while a
  // field colours it) or every patch has been switched off. "Show mesh" is the one action that puts it on screen.
  const resultsOn = useStore((s) => resultsShown(s.stage, s.results.view, s.results.data !== null));
  const offScreen = view !== null && view.phase === "done" && patchCount > 0 && !meshOnScreen(stage, patches, resultsOn);
  const showMesh = async (display: MeshDisplay) => {
    useStore.getState().setMeshDisplay(display);
    for (const id of hiddenPatchIds(patches)) {
      try {
        await execute("parameter.set", { node: id, name: "visible", value: true }, { silent: true });
      } catch {
        break; // the server's refusal is already in the console; the stage and the fit still follow
      }
    }
    if (useStore.getState().stage !== "Mesh") setStage("Mesh");
    requestFit();
  };

  const [logOpen, setLogOpen] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // What the server says about meshers and hosts is asked for by `meshRun.ts` when the connection comes up; a panel mounted after that asks again.
  useEffect(() => {
    if (connected && !useMeshContext.getState().loaded) void loadMeshContext();
  }, [connected]);

  // In the Mesh stage this is the tab in front, wherever the workspace put it (the slot's other tabs stay).
  useEffect(() => {
    if (stage !== "Mesh") return;
    const workspace = useWorkspace.getState();
    const place = placeOf(workspace.document.layout, "mesh");
    if (place === "left" || place === "right" || place === "bottom") workspace.setActiveTab(place, "mesh");
  }, [stage]);

  // The events carry the progress; this asks `mesh.status` (and the log, while it is open) every two
  // seconds as well, so a lost event or a reconnect cannot leave the panel saying "running" for ever.
  useEffect(() => {
    if (!shouldPoll(phase)) return;
    let live_ = true;
    const tick = async () => {
      try {
        const status = (await execute("mesh.status", {}, { silent: true })) as Record<string, unknown>;
        if (!live_) return;
        const now = findMeshNodes(useStore.getState().tree).result;
        const nodeState = typeof now?.params.state === "string" ? now.params.state : "";
        if (typeof status.state === "string" && status.state !== nodeState) await useStore.getState().refresh();
        if (typeof status.result === "string" && typeof status.step === "string" && status.step !== "") {
          const id = status.result;
          const step = status.step;
          useStore.setState((s) => ({ mesh: applyMeshEvent(s.mesh, { kind: "progress", nodeId: id, step, stage: s.mesh.step === step && s.mesh.stage !== "" ? s.mesh.stage : "running", fraction: null }, Date.now()) }));
        }
        if (logOpen) {
          const tail = (await execute("mesh.log", { lines: LOG_CAP }, { silent: true })) as { step?: unknown; lines?: unknown };
          if (!live_ || typeof tail.step !== "string" || !Array.isArray(tail.lines)) return;
          const lines = tail.lines.filter((l): l is string => typeof l === "string");
          const step = tail.step;
          useStore.setState((s) => ({ mesh: mergeLogTail(s.mesh, step, lines) }));
        }
      } catch {
        // A poll that fails is retried in two seconds; the events are the primary path.
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      live_ = false;
      window.clearInterval(timer);
    };
  }, [phase, logOpen, execute]);

  // The log stays at its end unless the user scrolled up to read something.
  const logRef = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  const lines = useMemo(() => logText(live), [live]);
  useEffect(() => {
    const el = logRef.current;
    if (el !== null && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines, logOpen]);

  // ---- settings edits: each is one `mesh.configure`, so one undo step.
  const schema = nodeTypes.mesh_settings;
  const configure = async (params: Record<string, unknown>, field: string | null) => {
    setSettingsError("");
    if (field !== null) setFieldErrors((e) => ({ ...e, [field]: "" }));
    try {
      await execute("mesh.configure", params, { silent: true });
      // **The line that names the host reads it from the tree**, and a host edit is the one change
      // whose whole point is that line. The node event usually patches the tree, but a field edit
      // can leave the panel's copy behind - measured: the Host field said `wsl:Ubuntu-24.04` while
      // the line below still said `local` until a reload. Re-read on a host edit only.
      if (field === "host") await useStore.getState().refresh();
    } catch (err) {
      if (field !== null) setFieldErrors((e) => ({ ...e, [field]: messageOf(err) }));
      else setSettingsError(messageOf(err));
      throw err;
    }
  };
  const locked = isActive(phase) || !connected;
  const Icon = TONE_ICON[line.tone];

  return (
    <div className="mesh-panel" data-ws="mesh-panel" data-phase={phase}>
      {stale && (
        <div className="mesh-banner" role="status" data-ws="mesh-stale">
          <CircleAlert size={14} aria-hidden="true" />
          {/* The one Generate of the page is below; the banner only says why it reads Regenerate. */}
          <span className="mesh-banner-text" title="The geometry, the tags or the settings changed since this mesh was built.">Mesh out of date</span>
        </div>
      )}

      {offScreen && (
        <div className="mesh-banner" role="status" data-kind="show" data-ws="mesh-offscreen">
          <CircleAlert size={14} aria-hidden="true" />
          <span className="mesh-banner-text" title="The mesh is made but nothing draws it in the view.">Mesh not shown</span>
          <Button variant="primary" title="Go to the Mesh stage and draw the mesh surface" onClick={() => void showMesh("surface")} data-ws="mesh-show-surface">
            Show mesh
          </Button>
          <Button title="Go to the Mesh stage and draw the mesh with its cell edges" onClick={() => void showMesh("cells")} data-ws="mesh-show-cells">
            With cells
          </Button>
        </div>
      )}

      <section className="mesh-section" aria-label="Run">
        <div className="mesh-state" data-tone={line.tone} aria-live="polite" data-ws="mesh-state">
          <Icon size={14} aria-hidden="true" className={line.tone === "busy" ? "mesh-spin" : ""} />
          <span data-ws="mesh-state-text">{line.text}</span>
        </div>
        <div
          className="mesh-bar"
          role="progressbar"
          aria-label="Mesh progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={indeterminate || progress.fraction === null ? undefined : Math.round(progress.fraction * 100)}
          aria-valuetext={line.text}
          data-indeterminate={indeterminate}
          data-tone={line.tone}
        >
          <span style={{ width: `${Math.round((progress.fraction ?? 0) * 100)}%` }} />
        </div>
        <div className="mesh-actions">
          <Button
            variant="primary"
            aria-label={generate.label === "Generate" ? "Generate mesh" : generate.label}
            aria-disabled={!generate.enabled || undefined}
            /*
              **The reason is not repeated here** (claude-win2's review item 9): it printed twice on
              the card, once as this tooltip and once as the line under the button. The visible line is
              the one that reaches everybody - a tooltip needs a hover and a pointer - so the title
              keeps the short description and the sentence below it does the explaining.
            */
            title={generate.enabled ? "Build the mesh" : "Not yet: see the line below"}
            onClick={() => generate.enabled && void generateMesh()}
            data-ws="mesh-generate"
          >
            {busy && !cancel.visible ? "Starting…" : generate.label}
          </Button>
          {cancel.visible && (
            <Button aria-label="Cancel mesh" aria-disabled={!cancel.enabled || undefined} title={cancel.enabled ? "Stop the running mesh" : cancel.reason} onClick={() => cancel.enabled && void cancelMesh()} data-ws="mesh-cancel">
              Cancel
            </Button>
          )}
        </div>
        {/*
          **Which OpenFOAM this mesh will run on** (claude-win2's 1630 checklist item 6). The host
          is the Execution field in this panel's Settings section, and the install is what that
          host's agent reports - the same values `mesh.generate` uses. Naming the Compute screen
          here was wrong once a project could name its own host: the line and the run could name
          different machines.
        */}
        <p
          className="muted mesh-hint"
          data-ws="mesh-engine"
          title={engine === null ? undefined : engine.install ? `${engine.install}. The host is under Advanced, Execution.` : `No OpenFOAM install was found on ${engine.host}. The host is under Advanced, Execution.`}
        >
          {engine === null ? (
            "Looking for OpenFOAM…"
          ) : engine.install ? (
            <>
              OpenFOAM · <strong>{engine.host}</strong>
            </>
          ) : (
            <>
              No OpenFOAM on <strong>{engine.host}</strong>
            </>
          )}
        </p>
        {!generate.enabled && !cancel.visible && (
          <p className="muted mesh-hint" data-ws="mesh-why">
            {generate.reason}
          </p>
        )}
        {runError !== "" && (
          <p role="alert" className="mesh-error" data-ws="mesh-error">
            {runError}
          </p>
        )}
      </section>

      {view !== null && (view.phase === "done" || phase === "running" || phase === "cancelling") && (
        <section className="mesh-section mesh-result" aria-label="Result" data-ws="mesh-result">
          <div className="section-title">Result</div>
          {(view.phase === "done" || view.cells > 0) && (
            <dl className="mesh-counts">
              <div>
                <dt className="muted">{view.phase === "done" ? "Cells" : "Cells (planned)"}</dt>
                <dd data-ws="mesh-cells">{formatCount(view.cells)}</dd>
              </div>
              <div>
                <dt className="muted">Points</dt>
                <dd>{view.points > 0 ? formatCount(view.points) : "-"}</dd>
              </div>
              <div>
                <dt className="muted">Faces</dt>
                <dd>{view.faces > 0 ? formatCount(view.faces) : "-"}</dd>
              </div>
              <div>
                <dt className="muted">Time</dt>
                <dd>{formatSeconds(view.seconds) || "-"}</dd>
              </div>
            </dl>
          )}
          {view.phase === "done" && (
            <div className="mesh-verdict" data-verdict={rows.length === 0 ? "unknown" : worstVerdict(rows)} data-ws="mesh-ok">
              {view.ok ? <CircleCheck size={13} aria-hidden="true" /> : <CircleAlert size={13} aria-hidden="true" />}
              <span>{view.ok ? "Mesh OK: every check passed" : rows.length === 0 ? "Quality was not measured" : "Quality not met"}</span>
            </div>
          )}
          {rows.length > 0 && (
            <table className="mesh-quality" aria-label="Mesh quality against its limits" data-ws="mesh-quality">
              <tbody>
                {rows.map((r) => {
                  const VIcon = VERDICT_ICON[r.verdict];
                  return (
                    <tr key={r.key} data-verdict={r.verdict}>
                      <th scope="row">{r.label}</th>
                      <td className="mesh-q-value">{r.valueText}</td>
                      <td className="muted mesh-q-limit">{r.limitText}</td>
                      <td className="mesh-q-verdict">
                        <VIcon size={13} aria-hidden="true" />
                        <span>{r.verdictText}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {fid.length > 0 && (
            <div className="mesh-fidelity" data-ws="mesh-fidelity">
              <div className="mesh-fidelity-head">
                <span>Fidelity to CAD</span>
                {fidWord !== "" && (
                  <span className="mesh-fidelity-verdict" data-verdict={fidWord.toLowerCase()}>
                    {fidWord}
                  </span>
                )}
              </div>
              <table className="mesh-quality" aria-label="How closely the mesh follows the CAD" data-ws="mesh-fidelity-table">
                <tbody>
                  {fid.map((r) => {
                    const VIcon = VERDICT_ICON[r.verdict];
                    return (
                      <tr key={r.key} data-verdict={r.verdict}>
                        <th scope="row">{r.label}</th>
                        <td className="mesh-q-value">{r.valueText}</td>
                        <td className="muted mesh-q-limit">{r.limitText}</td>
                        <td className="mesh-q-verdict">
                          <VIcon size={13} aria-hidden="true" />
                          <span>{r.verdictText}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {/* **The sample size is shown, not hidden.** A max deviation over 200 of 200,000
                  points is a lower bound, and a number that hides how much was measured claims
                  more than it did. */}
              {view?.fidelity?.points !== null && view?.fidelity !== null && (view.fidelity.points ?? 0) > 0 && (
                <p className="muted mesh-fidelity-note">
                  measured on {formatCount(view.fidelity.sampled ?? 0)} of {formatCount(view.fidelity.points ?? 0)} boundary points
                  {(view.fidelity.features ?? 0) === 0
                    ? " · no sharp edges to capture"
                    : ` · ${view.fidelity.features} sharp edges`}
                </p>
              )}
            </div>
          )}
          {view.warnings.length > 0 && (
            <ul className="mesh-warnings" aria-label="Warnings" data-ws="mesh-warnings">
              {view.warnings.map((w) => (
                <li key={w}>
                  <CircleAlert size={13} aria-hidden="true" />
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          )}
          {mapping.length > 0 && (
            <div className="mesh-mapping" data-ws="mesh-mapping">
              <div className="muted mb-1 text-[12px]">Box sides and the tags on them</div>
              {mapping.map((m) => (
                <div key={m.side} className="mesh-map-row">
                  <span className="mono">{m.side}</span>
                  <span className={m.known ? "" : "muted"}>{m.known ? m.tag : `${m.tag} (tag was deleted)`}</span>
                </div>
              ))}
            </div>
          )}
          {view.phase === "done" && (
            <div className="mesh-show">
              <Button
                disabled={patchCount === 0}
                title={patchCount === 0 ? "The mesh has no boundary patches to draw" : "Go to the Mesh stage and fit the mesh in the view"}
                aria-label="Show mesh in the view"
                onClick={showInView}
                data-ws="mesh-show"
              >
                Show in view
              </Button>
              <span className="muted text-[11.5px]" title="The Display menu draws the cell edges">{patchCount === 0 ? "No patches to draw" : `${patchCount} patches`}</span>
            </div>
          )}
        </section>
      )}

      <section className="mesh-section" aria-label="Settings" data-ws="mesh-settings">
        <div className="section-title">Settings</div>
        {!loaded && <p className="muted mesh-hint">Looking for meshers…</p>}
        {settingsError !== "" && (
          <p role="alert" className="mesh-error">
            {settingsError}
          </p>
        )}
        {settings === null ? (
          <div className="mesh-empty">
            <p className="muted m-0 text-[12px]" title="Create them to choose a mesher and sizes, or press Generate to mesh with the defaults.">No mesh settings yet</p>
            <Button disabled={!connected || locked} onClick={() => void configure({}, null).catch(() => undefined)} data-ws="mesh-create">
              Create settings
            </Button>
          </div>
        ) : (
          schema && (
            <div className="mesh-fields">
              <SettingsNodeFields
                key={settings.id}
                node={settings}
                schema={schema}
                mode="mesh"
                layout="basic"
                basicContent={<MeshBasic node={settings} locked={locked} />}
                hideInAdvanced={hideInAdvanced}
                onCommit={(name, value) => configure(name === "mesher" ? { mesher: value } : { values: { [name]: value } }, name)}
                onEdit={(name) => setFieldErrors((e) => ({ ...e, [name]: "" }))}
                tree={tree}
                meshers={meshers}
                meshersLoaded={loaded}
                disabled={locked}
                errors={fieldErrors}
              />
            </div>
          )
        )}
      </section>

      <section className="mesh-section" aria-label="Log">
        <button type="button" className="mesh-log-toggle" aria-expanded={logOpen} aria-controls="mesh-log-body" onClick={() => setLogOpen((o) => !o)} data-ws="mesh-log-toggle">
          <ChevronRight size={13} aria-hidden="true" style={{ transform: logOpen ? "rotate(90deg)" : "none" }} />
          <span className="section-title">Log</span>
          <span className="muted text-[11.5px]">{lines.length === 0 ? "empty" : `${lines.length} lines`}</span>
        </button>
        {logOpen && (
          <pre
            id="mesh-log-body"
            ref={logRef}
            role="log"
            aria-label="Mesh log"
            tabIndex={0}
            className="mesh-log"
            data-ws="mesh-log"
            onScroll={(e) => {
              const el = e.currentTarget;
              stick.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
            }}
          >
            {lines.length === 0 ? "No log lines yet" : lines.join("\n")}
          </pre>
        )}
      </section>
    </div>
  );
}
