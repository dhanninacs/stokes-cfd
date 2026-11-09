/**
 * What the cards send. Every function is the existing API commands in a fixed order, so a card's one
 * decision is one call here and a refusal comes back as the server's own sentence.
 *
 * Nothing new is added to the model. The commands are the ones the Mesh stepper (`mesh.body_role`), the
 * Material menu (`material.add_from_library`, `region.assign`), the Assign menu (`boundary.assign`) and
 * the settings pages (`mesh.configure`) already send; the commands run silently (the console is not
 * a place a card's user looks) and the caller shows a failure on the card.
 */
import { StokesApiError } from "../api/client";
import type { ConditionKind, NodeSummary } from "../api/types";
import { assignCommand, assignTarget } from "../panels/assignTarget";
import { useStore } from "../state/store";
import type { Role } from "../panels/meshTaskPages/bodiesModel";
import { configureSolve } from "../panels/runControl";
import { findSolverSettings } from "../panels/runPanelModel";
import { meshSettings } from "./guidedTree";

/** The sentence of a failure: the server's message for an API error. */
export function messageOf(err: unknown): string {
  return err instanceof StokesApiError ? err.message : err instanceof Error ? err.message : String(err);
}

async function call(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  return useStore.getState().execute(method, params, { silent: true });
}

async function refreshed<T>(value: T): Promise<T> {
  await useStore.getState().refresh();
  return value;
}

/** The mesh settings exist before a role is set: `mesh.body_role` writes `domain_part` onto them. */
async function ensureMeshSettings(): Promise<void> {
  if (meshSettings(useStore.getState().tree) === null) await call("mesh.configure", {});
}

/**
 * One decision, three facts: the body's role, and for the fluid the settings' `domain_part` too.
 * `mesh.body_role` does both in one undo step, so this is the Mesh stepper's own button.
 */
export async function setBodyRole(partId: string, role: Role): Promise<void> {
  await ensureMeshSettings();
  await refreshed(await call("mesh.body_role", { part: partId, role }));
}

/** What a library record the card offers is, by the library's own id. */
export interface QuickMaterial {
  label: string;
  provider: string;
  fluid: string;
}

/** The two the card offers at one press. The rest is the library dialog. */
export const QUICK_MATERIALS: readonly QuickMaterial[] = [
  { label: "Water", provider: "builtin", fluid: "water_liquid" },
  { label: "Air", provider: "builtin", fluid: "air" },
];

/**
 * Give the fluid body a material from the library: reuse the project's copy when it already has one,
 * else add it, then `region.assign` to the body. Returns the material's name in the project.
 */
export async function assignLibraryMaterial(partId: string, material: QuickMaterial): Promise<string> {
  const known = (await call("material.list")) as { name: string; provider?: string | null; fluid?: string | null }[];
  const existing = known.find((m) => m.provider === material.provider && m.fluid === material.fluid);
  let name = existing?.name ?? "";
  if (name === "") {
    const added = (await call("material.add_from_library", { provider: material.provider, fluid: material.fluid })) as { name?: unknown };
    if (typeof added.name !== "string" || added.name === "") throw new Error("The material was added, but the server did not say what it is called.");
    name = added.name;
  }
  await call("region.assign", { material: name, target: [{ part: partId, faces: "all" }] });
  return refreshed(name);
}

/** The density and the viscosity of a project material at its reference temperature, as text. */
export async function materialReadout(materialId: string, refTemperature: number): Promise<string> {
  const answer = (await call("material.evaluate", { material: materialId, properties: ["density", "viscosity"], T: [refTemperature] })) as {
    properties?: Record<string, { method?: (number | null)[] | null; unit?: string }>;
  };
  const parts: string[] = [];
  const density = answer.properties?.density?.method?.[0];
  const viscosity = answer.properties?.viscosity?.method?.[0];
  if (typeof density === "number" && Number.isFinite(density)) parts.push(`density ${Number(density.toPrecision(4))} kg/m³`);
  if (typeof viscosity === "number" && Number.isFinite(viscosity)) parts.push(`viscosity ${Number(viscosity.toPrecision(3))} Pa·s`);
  return parts.join(" · ");
}

/** The faces that are picked, counted across parts. */
export function pickedFaceCount(): number {
  return Object.values(useStore.getState().picked.faces).reduce((sum, ids) => sum + ids.length, 0);
}

/** What `boundary.assign` answered about the boundary it made. */
interface AssignAnswer {
  boundary?: { id?: string; name?: string };
}

/**
 * Give the picked faces a kind: one `boundary.assign`, the same call the Assign menu makes. The pick is
 * cleared afterwards so the next click starts a new set. Returns the boundary's name.
 */
export async function assignPickedFaces(kind: ConditionKind, values: Record<string, unknown>): Promise<string> {
  const state = useStore.getState();
  const target = assignTarget(state);
  if (target.value === null) throw new Error(target.refused ?? "Click a face of the fluid first.");
  const call_ = assignCommand(target.value, kind, values);
  const answer = (await call(call_.method, call_.params)) as AssignAnswer;
  await refreshed(null);
  useStore.getState().clearPick();
  return answer.boundary?.name ?? kind.label;
}

/** Change one value of a boundary (the inlet speed, the outlet pressure): `boundary.set`. */
export async function setBoundaryValue(boundaryId: string, param: string, value: number): Promise<void> {
  await refreshed(await call("boundary.set", { boundary: boundaryId, values: { [param]: value } }));
}

/**
 * Rename a boundary: its tag first (the tag's name is the mesh patch's, ADR-0020), then the boundary node, so
 * the card, the tree, the mesh and the case all say the same name. The server checks the name.
 */
export async function renameBoundary(boundaryId: string, tagId: string, name: string): Promise<void> {
  if (tagId !== "") await call("tag.rename", { tag: tagId, name });
  await refreshed(await call("node.rename", { node: boundaryId, name }));
}

/** Remove a boundary and its tag, so its faces are free again; the tag stays if something else uses it. */
export async function removeBoundary(boundaryId: string): Promise<void> {
  try {
    await call("boundary.delete", { boundary: boundaryId, delete_tag: true });
  } catch {
    await call("boundary.delete", { boundary: boundaryId });
  }
  await refreshed(null);
}

/** Set mesh settings in one `mesh.configure`, making the settings first when there are none. */
export async function configureMesh(values: Record<string, unknown>): Promise<void> {
  await refreshed(await call("mesh.configure", { values }));
}

/** Choose the mesher (`mesh.configure {mesher}`, as the Mesh panel does), making the settings first. */
export async function setMesher(mesher: string): Promise<void> {
  await ensureMeshSettings();
  await refreshed(await call("mesh.configure", { mesher }));
}

/**
 * Use a kept mesh (ADR-0034): `mesh.use` makes it the one the viewport shows and a run solves on, and the
 * settings take its mesher, so the card, the staleness check and Generate all describe the mesh in use.
 */
export async function chooseMeshResult(resultId: string, mesher: string): Promise<void> {
  await call("mesh.use", { result: resultId });
  const current = mesherOf(meshSettingsNode());
  if (mesher !== "" && current !== mesher) await setMesher(mesher);
  else await useStore.getState().refresh();
}

function meshSettingsNode(): NodeSummary | null {
  const mesh = useStore.getState().tree?.children?.find((c) => c.path === "/mesh");
  return mesh?.children?.find((c) => c.type === "mesh_settings") ?? null;
}

/** The mesh settings' mesher id, or null when there are no settings yet. */
export function mesherOf(settings: NodeSummary | null): string | null {
  const raw = settings?.params.mesher;
  return typeof raw === "string" && raw !== "" ? raw : null;
}

/**
 * Set the number of MPI processes snappyHexMesh uses: the mesh settings' own `cores`, not the solver's.
 * The Run card's Cores is the solve's, so before this a Guided mesh always ran on one core.
 */
export async function setMeshCores(cores: number): Promise<void> {
  await ensureMeshSettings();
  await configureMesh({ cores });
}

/** The mesh settings' base size in metres, or null. */
export function baseSizeOf(settings: NodeSummary | null): number | null {
  const raw = settings?.params.base_size;
  const value = typeof raw === "number" ? raw : typeof raw === "object" && raw !== null && "value" in raw ? (raw as { value: unknown }).value : null;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** How many boundary layers the settings ask for on every wall. */
export function layersOf(settings: NodeSummary | null): number {
  const raw = settings?.params.layers;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

/** The flow models the Fluid card offers at one press; the rest is the Solve settings. */
export const FLOW_MODELS = [
  { value: "laminar", label: "Laminar", sub: "smooth, slow flow" },
  { value: "realizableKE", label: "Turbulent", sub: "realizable k-ε" },
  { value: "kOmegaSST", label: "Turbulent", sub: "k-ω SST" },
] as const;

/** The solver settings exist before a card changes one: `solve.configure` makes them when there are none. */
async function ensureSolverSettings(): Promise<void> {
  if (findSolverSettings(useStore.getState().tree) === null) await configureSolve({});
}

/** Choose the flow model: one `solve.configure`, the same call the Solve settings page makes. */
export async function setFlowModel(model: string): Promise<void> {
  await ensureSolverSettings();
  await refreshed(await configureSolve({ values: { turbulence: model } }));
}

/**
 * Heat transfer on or off. The energy equation needs the thermal solver, so turning it on picks that
 * engine in the same call (the Solve settings page refuses energy on the other one); off leaves the
 * engine as it is.
 */
export async function setHeatTransfer(on: boolean): Promise<void> {
  await ensureSolverSettings();
  await refreshed(await configureSolve({ values: on ? { engine: "of14.fluid", energy: true } : { energy: false } }));
}

/** How many processor cores the solve asks for, or null while the settings say nothing. */
export function coresOf(settings: NodeSummary | null): number | null {
  const raw = settings?.params.cores;
  return typeof raw === "number" && Number.isInteger(raw) && raw >= 1 ? raw : null;
}

/** Set the number of cores the solve uses. */
export async function setCores(cores: number): Promise<void> {
  await ensureSolverSettings();
  await refreshed(await configureSolve({ values: { cores } }));
}
