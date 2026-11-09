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
import { boxFilled, findMeshNodes, generateState, phaseNow, readMeshers, readResult, type GenerateInput, type GenerateState, type MesherInfo } from "./meshPanelModel";

interface HostInfo {
  name: string;
  available: boolean;
  why: string;
}

/** What the panel learns from the server once per connection, and what is in flight from this side. */
interface MeshContext {
  meshers: MesherInfo[];
  hosts: HostInfo[];
  /** The Compute default host, for a settings node that names none (ADR-0024). */
  defaultHost: string;
  /** `mesh.meshers` has answered (the form can be built). */
  loaded: boolean;
  /** A configure or generate call is in flight. */
  busy: boolean;
  /** The refusal of the last Generate, in the server's words; cleared by the next press. */
  error: string;
}

export const useMeshContext = create<MeshContext>(() => ({ meshers: [], hosts: [], defaultHost: "", loaded: false, busy: false, error: "" }));

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
  useMeshContext.setState({
    meshers: readMeshers(meshers),
    hosts: Array.isArray(list)
      ? list.flatMap((h): HostInfo[] => {
          const r = h as Record<string, unknown>;
          return typeof r.name === "string" ? [{ name: r.name, available: r.available === true, why: typeof r.why === "string" ? r.why : "" }] : [];
        })
      : [],
    defaultHost: typeof defaultHost === "string" ? defaultHost : "",
    loaded: meshers !== null,
  });
}

type Inputs = Pick<StokesState, "tree" | "connected" | "project" | "partNodes" | "mesh">;

/** Everything `generateState` needs, from the stores. */
export function generateInput(s: Inputs, ctx: Pick<MeshContext, "meshers" | "hosts" | "defaultHost" | "busy">): GenerateInput {
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
  };
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
  return useMemo(() => generateState(generateInput({ tree, connected, project, partNodes, mesh }, { meshers, hosts, defaultHost, busy })), [tree, connected, project, partNodes, mesh, meshers, hosts, defaultHost, busy]);
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
  useMeshContext.setState({ busy: true, error: "" });
  try {
    if (findMeshNodes(state.tree).settings === null) await state.execute("mesh.configure", {}, { silent: true });
    // A new run starts with an empty log and no step: the events that follow are this run's.
    useStore.setState({ mesh: { nodeId: null, step: "", stage: "", at: 0, logs: [] } });
    const info = (await state.execute("mesh.generate", wanted.force ? { force: true } : {}, { silent: true })) as { reused?: boolean };
    if (info.reused === true) state.pushToast("info", "Nothing has changed since the last mesh; it is still current.");
    await useStore.getState().refresh();
  } catch (err) {
    const message = messageOf(err);
    useMeshContext.setState({ error: message });
    useStore.getState().log("error", `mesh.generate: ${message}`);
  } finally {
    useMeshContext.setState({ busy: false });
  }
}

/** Press Cancel: ask the job to stop. The job's own event reports that it did; until then the panel says it is stopping. */
export async function cancelMesh(): Promise<void> {
  const state = useStore.getState();
  try {
    const answer = (await state.execute("mesh.cancel", {}, { silent: true })) as { cancelled?: boolean };
    if (answer.cancelled === true) {
      const id = readResult(findMeshNodes(useStore.getState().tree).result)?.id ?? null;
      useStore.setState((s) => ({ mesh: { ...s.mesh, nodeId: s.mesh.nodeId ?? id, stage: "cancelling" } }));
    }
  } catch (err) {
    useMeshContext.setState({ error: messageOf(err) });
  }
}

// The catalogue and the hosts are asked for whenever the connection comes up, so the ribbon and the
// palette know about a missing host even with the Mesh panel hidden.
useStore.subscribe((state, previous) => {
  if (state.connected && !previous.connected) void loadMeshContext();
});

// The palette and the ribbon trigger these by id; the chord is none by default.
registerAction("mesh.generate", () => void generateMesh());
