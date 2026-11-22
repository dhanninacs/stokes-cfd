/**
 * Running a mesh from the interface (T-211b): what Generate and Cancel do, and the small amount of
 * state they share with the panel, the ribbon and the command palette. The pure decisions (is
 * Generate enabled, and if not why) are `meshPanelModel.ts`; this file reads the stores, calls the
 * `mesh.*` commands and puts the answers where the panel reads them.
 *
 * `mesh.generate` is the action id the palette and the ribbon trigger (`app/actions.ts`); it is
 * registered here, when this module loads, so it works whether or not the Mesh panel is on screen.
 */
import { useMemo } from "react";
import { create } from "zustand";
import { StokesApiError } from "../api/client";
import { registerAction } from "../app/actions";
import { useStore, type StokesState } from "../state/store";
import { applyHostTools, applyPrepareEvent, boxFilled, findMeshNodes, generateState, NO_PREPARE, phaseNow, readMeshers, readPrepareEvent, readResult, readHostTools, type GenerateInput, type GenerateState, type HostTools, type MesherInfo, type PrepareState, type PrepareStep } from "./meshPanelModel";

interface HostInfo {
  name: string;
  available: boolean;
  why: string;
}

/** What the panel learns from the server once per connection, and what is in flight from this side. */
interface MeshContext {
  /** The catalogue as the chosen host sees it: a mesher whose program the host lacks is listed, unbuilt, with the reason. */
  meshers: MesherInfo[];
  /** The catalogue as the server sent it, before any host is considered. */
  catalogue: MesherInfo[];
  hosts: HostInfo[];
  /** The Compute default host, for a settings node that names none (ADR-0024). */
  defaultHost: string;
  /** `mesh.meshers` has answered (the form can be built). */
  loaded: boolean;
  /** A configure or generate call is in flight. */
  busy: boolean;
  /** Which part of a Generate press is in flight, when it started (ms) and on which host: the preparing line. */
  busyStep: PrepareStep | "";
  busySince: number;
  busyHost: string;
  /** The refusal of the last Generate, in the server's words; cleared by the next press. */
  error: string;
  /** The server's preparation this press follows (`mesh.preparing` events): its id, step, progress and Cancel. */
  prep: PrepareState;
}

export const useMeshContext = create<MeshContext>(() => ({ meshers: [], catalogue: [], hosts: [], defaultHost: "", loaded: false, busy: false, busyStep: "", busySince: 0, busyHost: "", error: "", prep: NO_PREPARE }));

/** Preparations that have ended, by id: the `mesh.generate` answer can arrive after `mesh.prepared`. */
const endedPreparations = new Set<string>();

function endPreparation(outcome: string, message: string): void {
  useMeshContext.setState({ busy: false, busyStep: "", busySince: 0, prep: NO_PREPARE, ...(outcome === "failed" && message !== "" ? { error: message } : {}) });
  if (outcome === "reused") useStore.getState().pushToast("info", "Nothing has changed since the last mesh; it is still current.");
  void useStore.getState().refresh();
}

function messageOf(err: unknown): string {
  return err instanceof StokesApiError ? err.message : err instanceof Error ? err.message : String(err);
}

/** Ask for the mesher catalogue and the hosts. Both are read-only and neither needs a project. */
export async function loadMeshContext(): Promise<void> {
  const state = useStore.getState();
  if (state.client === null) return;
  const [meshers, hosts, compute] = await Promise.all([
    state.execute("mesh.meshers", {}, { silent: true }).catch(() => null),
    state.execute("host.list", {}, { silent: true }).catch(() => null),
    state.execute("compute.get", {}, { silent: true }).catch(() => null),
  ]);
  const list = (hosts as { hosts?: unknown } | null)?.hosts;
  const defaultHost = (compute as { effective_host?: unknown } | null)?.effective_host;
  // A new connection may mean programs were installed meanwhile: forget what the hosts said.
  toolsByHost.clear();
  const catalogue = readMeshers(meshers);
  useMeshContext.setState({
    meshers: catalogue,
    catalogue,
    hosts: Array.isArray(list)
      ? list.flatMap((h): HostInfo[] => {
          const r = h as Record<string, unknown>;
          return typeof r.name === "string" ? [{ name: r.name, available: r.available === true, why: typeof r.why === "string" ? r.why : "" }] : [];
        })
      : [],
    defaultHost: typeof defaultHost === "string" ? defaultHost : "",
    loaded: meshers !== null,
  });
  syncHostTools();
}

// ---- what the chosen host has (so the list of meshers follows the host)
/** The programs each host reported, by host name; null is "asked, no answer": nothing is refused on it. */
const toolsByHost = new Map<string, HostTools | null>();
const asking = new Set<string>();
let applied: { catalogue: MesherInfo[]; tools: HostTools | null } | null = null;

/** Re-derive the meshers the panels list from the catalogue and the chosen host's programs, when either changed. */
function applyTools(): void {
  const ctx = useMeshContext.getState();
  const host = meshHostName(useStore.getState().tree, ctx.defaultHost);
  const tools = toolsByHost.get(host) ?? null;
  if (applied !== null && applied.catalogue === ctx.catalogue && applied.tools === tools) return;
  applied = { catalogue: ctx.catalogue, tools };
  useMeshContext.setState({ meshers: applyHostTools(ctx.catalogue, tools) });
}

/** Ask the chosen host once what it has (the answer is kept per host), then apply it. Never throws. */
function syncHostTools(): void {
  const state = useStore.getState();
  const ctx = useMeshContext.getState();
  if (!state.connected || state.client === null || !ctx.loaded) return;
  const host = meshHostName(state.tree, ctx.defaultHost);
  if (!toolsByHost.has(host) && !asking.has(host)) {
    asking.add(host);
    void state
      .execute("host.probe", { host }, { silent: true })
      .then((reply) => toolsByHost.set(host, readHostTools((reply as { record?: unknown } | null)?.record)))
      .catch(() => toolsByHost.set(host, null))
      .finally(() => {
        asking.delete(host);
        applyTools();
      });
  }
  applyTools();
}

type Inputs = Pick<StokesState, "tree" | "connected" | "project" | "partNodes" | "mesh">;

/** Everything `generateState` needs, from the stores. */
export function generateInput(s: Inputs, ctx: Pick<MeshContext, "meshers" | "hosts" | "defaultHost" | "busy"> & Partial<Pick<MeshContext, "busyStep">>): GenerateInput {
  const { settings, result } = findMeshNodes(s.tree);
  const view = readResult(result);
  const params = (settings?.params ?? {}) as Record<string, unknown>;
  const mesherId = typeof params.mesher === "string" ? params.mesher : "blockmesh";
  // **An empty Execution host is the Compute default**, which is the same rule the API applies
  // (`mesh.generate` resolves it in `compute.default_host`); showing a different host here would
  // disable Generate for a host the backend would not use.
  const hostName = typeof params.host === "string" && params.host !== "" ? params.host : ctx.defaultHost || "local";
  return {
    connected: s.connected,
    hasProject: s.project !== null,
    phase: phaseNow(view, s.mesh),
    stale: view !== null && view.status === "stale" && view.phase === "done",
    domain: typeof params.domain === "string" ? params.domain : "part",
    hasPart: s.partNodes.length > 0,
    boxFilled: boxFilled(params.box),
    mesher: ctx.meshers.find((m) => m.id === mesherId) ?? null,
    host: ctx.hosts.find((h) => h.name === hostName) ?? null,
    busy: ctx.busy,
    busyStep: ctx.busyStep ?? "",
  };
}

/** The host a Generate press will mesh on, by name: the settings' own, else the Compute default. */
export function meshHostName(tree: StokesState["tree"], defaultHost: string): string {
  const params = (findMeshNodes(tree).settings?.params ?? {}) as Record<string, unknown>;
  return typeof params.host === "string" && params.host !== "" ? params.host : defaultHost || "local";
}

/** Generate's state now, outside React (the palette and the shortcut ask it when they run). */
export function currentGenerateState(): GenerateState {
  return generateState(generateInput(useStore.getState(), useMeshContext.getState()));
}

/** Generate's state for a component: it follows the tree, the connection, the parts and what the server said about meshers and hosts. */
export function useGenerateState(): GenerateState {
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const project = useStore((s) => s.project);
  const partNodes = useStore((s) => s.partNodes);
  const mesh = useStore((s) => s.mesh);
  const meshers = useMeshContext((c) => c.meshers);
  const hosts = useMeshContext((c) => c.hosts);
  const defaultHost = useMeshContext((c) => c.defaultHost);
  const busy = useMeshContext((c) => c.busy);
  const busyStep = useMeshContext((c) => c.busyStep);
  return useMemo(
    () => generateState(generateInput({ tree, connected, project, partNodes, mesh }, { meshers, hosts, defaultHost, busy, busyStep })),
    [tree, connected, project, partNodes, mesh, meshers, hosts, defaultHost, busy, busyStep],
  );
}

/**
 * Press Generate. With no settings node yet, `mesh.configure` first makes one from the defaults (the
 * same command the form's edits use), so the first press works from a fresh project. A refusal is
 * kept for the panel to show where the user is looking, in the server's own words.
 */
export async function generateMesh(): Promise<void> {
  const state = useStore.getState();
  const wanted = currentGenerateState();
  if (!wanted.enabled) {
    state.pushToast("info", wanted.reason);
    return;
  }
  const host = meshHostName(state.tree, useMeshContext.getState().defaultHost);
  const needsSettings = findMeshNodes(state.tree).settings === null;
  useMeshContext.setState({ busy: true, busyStep: needsSettings ? "configure" : "prepare", busySince: Date.now(), busyHost: host, error: "", prep: NO_PREPARE });
  // **The server prepares on a worker** (ADR-0024 addendum 2026-11-14): `mesh.generate` answers `preparing` at
  // once, and the press stays busy until `mesh.prepared` arrives; only an answer that is already final ends it here.
  let handedOver = false;
  try {
    if (needsSettings) {
      await state.execute("mesh.configure", {}, { silent: true });
      useMeshContext.setState({ busyStep: "prepare" });
    }
    // A new run starts with an empty log and no step: the events that follow are this run's.
    useStore.setState({ mesh: { nodeId: null, step: "", stage: "", at: 0, logs: [] } });
    const info = (await state.execute("mesh.generate", wanted.force ? { force: true } : {}, { silent: true })) as { reused?: boolean; state?: string; result?: string | null };
    const id = typeof info.result === "string" ? info.result : "";
    if (info.state === "preparing" && id !== "" && !endedPreparations.has(id)) {
      handedOver = true;
      useMeshContext.setState((c) => ({ prep: { ...c.prep, nodeId: id } }));
      return;
    }
    if (info.reused === true) state.pushToast("info", "Nothing has changed since the last mesh; it is still current.");
    await useStore.getState().refresh();
  } catch (err) {
    const message = messageOf(err);
    useMeshContext.setState({ error: message });
    useStore.getState().log("error", `mesh.generate: ${message}`);
  } finally {
    if (!handedOver) useMeshContext.setState({ busy: false, busyStep: "", busySince: 0, prep: NO_PREPARE });
  }
}

/** Follow the server's preparation: each `mesh.preparing` names the step, `mesh.prepared` ends the press. */
function onPrepareEvent(event: string, data: Record<string, unknown>): void {
  const parsed = readPrepareEvent(event, data);
  if (parsed === null) return;
  if (parsed.kind === "end") endedPreparations.add(parsed.nodeId);
  const ctx = useMeshContext.getState();
  if (!ctx.busy || ctx.busyStep === "configure") return;
  const next = applyPrepareEvent(ctx.prep, parsed);
  if (next.ended !== null) {
    endPreparation(next.ended.outcome, next.ended.message);
    return;
  }
  if (next.state !== ctx.prep) useMeshContext.setState({ prep: next.state, busyStep: next.state.step });
}

/** After a reconnect the events of a preparation may be lost: ask the server whether it is still preparing. */
async function recheckPreparation(): Promise<void> {
  const ctx = useMeshContext.getState();
  if (!ctx.busy || ctx.prep.nodeId === "") return;
  const status = (await useStore.getState().execute("mesh.status", {}, { silent: true }).catch(() => null)) as { preparing?: { result?: unknown } } | null;
  if (status?.preparing?.result !== ctx.prep.nodeId) endPreparation("", "");
}

/** Press Cancel: ask the job to stop. The job's own event reports that it did; until then the panel says it is stopping. */
export async function cancelMesh(): Promise<void> {
  const state = useStore.getState();
  const preparing = useMeshContext.getState().busy && useMeshContext.getState().busyStep !== "configure";
  try {
    const answer = (await state.execute("mesh.cancel", {}, { silent: true })) as { cancelled?: boolean };
    if (answer.cancelled === true && preparing) {
      // **Stopped between steps** on the server; `mesh.prepared` (outcome `cancelled`) ends the press.
      useMeshContext.setState((c) => ({ prep: { ...c.prep, cancelling: true } }));
    } else if (answer.cancelled === true) {
      const id = readResult(findMeshNodes(useStore.getState().tree).result)?.id ?? null;
      useStore.setState((s) => ({ mesh: { ...s.mesh, nodeId: s.mesh.nodeId ?? id, stage: "cancelling" } }));
    }
  } catch (err) {
    useMeshContext.setState({ error: messageOf(err) });
  }
}

// The catalogue and the hosts are asked for whenever the connection comes up, so the ribbon and the
// palette know about a missing host even with the Mesh panel hidden.
const initialClient = useStore.getState().client;
let unsubscribeEvents: (() => void) | null = initialClient !== null ? initialClient.onEvent(onPrepareEvent) : null;
useStore.subscribe((state, previous) => {
  if (state.connected && !previous.connected) {
    void loadMeshContext();
    void recheckPreparation();
  }
  if (state.tree !== previous.tree) syncHostTools();
  if (state.client !== previous.client) {
    unsubscribeEvents?.();
    unsubscribeEvents = state.client !== null ? state.client.onEvent(onPrepareEvent) : null;
  }
});

// The palette and the ribbon trigger these by id; the chord is none by default.
registerAction("mesh.generate", () => void generateMesh());
