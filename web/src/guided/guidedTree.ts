/**
 * What guided mode reads out of the project tree: the bodies and the role each one has, the fluid
 * region and its material, and the boundaries with their colour and value. Pure functions over
 * `NodeSummary`, so the flow strip's model and the cards share one reading and the tests can use a
 * hand-built tree.
 *
 * Nothing here decides anything new. A body's role is read by `bodiesModel.roleOf` (the Mesh stepper's
 * own rule), the fluid region is the project's region of kind `fluid`, and the boundaries are the
 * `boundary` nodes the set-up check already counts.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { NodeSummary, UncoveredAnswer } from "../api/types";
import { roleOf, type Role } from "../panels/meshTaskPages/bodiesModel";
import { INLINE_VALUE, KIND_WORDS, type InlineValue } from "../setup/basicSets";

/** One body of the project, with the role it has for the mesh. */
export interface Body {
  id: string;
  name: string;
  role: Role;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function visit(node: NodeSummary | null | undefined, each: (node: NodeSummary) => void): void {
  if (!node) return;
  each(node);
  for (const child of node.children ?? []) visit(child, each);
}

function find(tree: NodeSummary | null, path: string): NodeSummary | null {
  return tree?.children?.find((c) => c.path === path) ?? null;
}

/** The boundary kinds that carry the flow in or out (`FLOW_KINDS` in `stokes/run/snapshot.py`). */
const FLOW_KINDS: ReadonlySet<string> = new Set(["velocity_inlet", "mass_flow_inlet", "pressure_inlet", "pressure_outlet"]);

/**
 * The inlets and outlets the built mesh has no patch for, by boundary name; empty with no mesh yet. A patch is
 * named after its tag, so a boundary is meshed when a `mesh_patch` of a mesh result has its tag's name.
 * Measured on the mixing elbow: cfMesh at 5 mm cells lost the 25 mm hot inlet and still said "quality ok".
 */
export function unmeshedFlowBoundaries(tree: NodeSummary | null): string[] {
  const patches = new Set<string>();
  visit(find(tree, "/mesh"), (node) => {
    if (node.type === "mesh_patch") patches.add(node.name);
  });
  if (patches.size === 0) return [];
  const tagNames = new Map<string, string>();
  visit(tree, (node) => {
    if (node.type === "tag") tagNames.set(node.id, node.name);
  });
  const missing: string[] = [];
  visit(find(tree, "/regions"), (node) => {
    if (node.type !== "boundary" || !FLOW_KINDS.has(String(node.params.kind ?? ""))) return;
    const tag = tagNames.get(String(node.params.tag ?? ""));
    if (tag !== undefined && !patches.has(tag)) missing.push(node.name);
  });
  return missing;
}

/** The mesh settings node (`/mesh/settings`), or null before the first Generate or configure. */
export function meshSettings(tree: NodeSummary | null): NodeSummary | null {
  return find(tree, "/mesh")?.children?.find((c) => c.type === "mesh_settings") ?? null;
}

/** Every body (`part` node) under Geometry, however the tree nests them, with the role it has. */
export function readBodies(tree: NodeSummary | null): Body[] {
  const settings = meshSettings(tree);
  const domainPart = typeof settings?.params.domain_part === "string" ? settings.params.domain_part : null;
  const enclosure = typeof settings?.params.box === "string" ? settings.params.box : null;
  const out: Body[] = [];
  visit(find(tree, "/geometry"), (node) => {
    if (node.type === "part") out.push({ id: node.id, name: node.name, role: roleOf(node, domainPart, enclosure) });
  });
  return out;
}

/** The body that is the fluid, or null. */
export function fluidBody(bodies: readonly Body[]): Body | null {
  return bodies.find((b) => b.role === "fluid") ?? null;
}

/** An imported OpenFOAM case (a `foam_case` node, ADR-0029): the project is the case, not CAD. */
export interface FoamCase {
  id: string;
  name: string;
  /** The solver the case names (`incompressibleFluid`, or a legacy application), or "". */
  solver: string;
  /** The case node's status word (`ready`, `needs_recipe`, `unverified`, `missing`), or "". */
  status: string;
}

/**
 * The project's imported case, or null. **A project holds either CAD bodies or a case**, so the
 * strip and the cards read this first: a tutorial opened from the start screen has no geometry to
 * import, no fluid to pick and no faces to name, and the hints that say so would be wrong.
 */
export function readCase(tree: NodeSummary | null): FoamCase | null {
  let found: FoamCase | null = null;
  visit(tree, (node) => {
    if (found !== null || node.type !== "foam_case") return;
    const solver = node.params.solver;
    const status = node.params.status;
    found = {
      id: node.id,
      name: node.name,
      solver: typeof solver === "string" ? solver : isRecord(solver) ? Object.values(solver).filter((v): v is string => typeof v === "string").join(", ") : "",
      status: typeof status === "string" ? status : "",
    };
  });
  return found;
}

/** A body named like the thing the flow fills. */
const FLUID_NAME = /fluid|air|water|domain/i;

/**
 * The body to mark as the fluid when the user has not chosen: the one named like it, else the only
 * body. Null when there is none to guess, so a project of several unnamed solids is left for the user.
 * A body already chosen as the fluid, or a hole, means the choice is made.
 */
export function defaultFluidBody(bodies: readonly Body[]): Body | null {
  if (bodies.some((b) => b.role === "fluid" || b.role === "hole")) return null;
  const candidates = bodies.filter((b) => b.role !== "left_out");
  const named = candidates.find((b) => FLUID_NAME.test(b.name));
  if (named) return named;
  return candidates.length === 1 ? candidates[0] : null;
}

/** The fluid region and the material it holds. */
export interface FluidRegion {
  id: string;
  name: string;
  materialId: string | null;
  /** The material's name in the plain words the cards use: "Water", not `water_liquid`. */
  materialName: string | null;
  /** The library id the material was added from ("water_liquid"), or "" for a material made by hand. */
  libraryId: string;
  /** The material's reference temperature in kelvin, where its properties are read. */
  refTemperature: number;
}

function first(root: NodeSummary | null | undefined, match: (node: NodeSummary) => boolean): NodeSummary | null {
  const found: NodeSummary[] = [];
  visit(root, (node) => {
    if (found.length === 0 && match(node)) found.push(node);
  });
  return found[0] ?? null;
}

/** The library ids the cards know by a plain name. */
const PLAIN_NAMES: Record<string, string> = { water_liquid: "Water", air: "Air" };

/** "Water" for the built-in water, else the node's name with its underscores read as spaces. */
export function plainMaterialName(name: string, libraryId: string): string {
  const known = PLAIN_NAMES[libraryId];
  if (known !== undefined) return known;
  const spaced = name.replaceAll("_", " ").trim();
  return spaced === "" ? name : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** The first region of kind `fluid`, with its material's name looked up in the tree. */
export function readFluidRegion(tree: NodeSummary | null): FluidRegion | null {
  const region = first(find(tree, "/regions"), (node) => node.type === "region" && node.params.kind === "fluid");
  if (region === null) return null;
  const held = region.params.material;
  const materialId = typeof held === "string" && held !== "" ? held : null;
  const material = materialId === null ? null : first(tree, (node) => node.type === "material" && node.id === materialId);
  const t = material === null ? null : refTemperatureOf(material);
  return {
    id: region.id,
    name: region.name,
    materialId,
    materialName: material === null ? null : plainMaterialName(material.name, typeof material.params.fluid === "string" ? material.params.fluid : ""),
    libraryId: typeof material?.params.fluid === "string" ? material.params.fluid : "",
    refTemperature: t ?? 300,
  };
}

/**
 * The boundary kinds the card offers, by the catalogue's id. The table lives in `setup/basicSets.ts`, where
 * the Full-mode Details pane reads it too; it is re-exported here under its old name.
 */
export const BOUNDARY_KIND_WORDS: Readonly<Record<string, string>> = KIND_WORDS;

/** One boundary, as the card lists it. */
export interface BoundaryRow {
  id: string;
  name: string;
  kind: string;
  /** "Inlet", "Outlet", "Wall"...: the kind in plain words. */
  word: string;
  /** The kind in plain words, whatever `word` became (the name, when two share a kind). */
  kindWord: string;
  /** The boundary's tag: its name is the mesh patch's, so a rename renames it. */
  tag: string;
  /** The swatch colour: the colour of the boundary's tag, or "" when the tag says none. */
  color: string;
  /** The parameter the card lets the user edit in place for this kind, or null. */
  editable: InlineValue | null;
  /** The value as the project shows it ("0.5 m/s"), or "". */
  valueText: string;
  /** The value in SI, for the edit field. */
  value: number | null;
  /** An inlet's temperature in kelvin (the energy equation's boundary value), or null for any other kind. */
  temperature: number | null;
}

/** A material's reference temperature in kelvin, or null when it states none that is positive. */
export function refTemperatureOf(material: NodeSummary): number | null {
  const t = quantityValue(material.params.T_ref);
  return t !== null && t > 0 ? t : null;
}

function quantityValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (isRecord(value) && typeof value.value === "number" && Number.isFinite(value.value)) return value.value;
  return null;
}

const HEX = /^#[0-9a-fA-F]{6}$/;

/** The kinds that bring fluid in, and so take a temperature when the energy equation is on. */
const INLET_KINDS: ReadonlySet<string> = new Set(["velocity_inlet", "mass_flow_inlet", "pressure_inlet"]);

/** Every `boundary` node of the project, in tree order. */
export function readBoundaries(tree: NodeSummary | null): BoundaryRow[] {
  const colours = new Map<string, string>();
  visit(tree, (node) => {
    if (node.type === "tag" && typeof node.params.color === "string" && HEX.test(node.params.color)) colours.set(node.id, node.params.color);
  });
  const out: BoundaryRow[] = [];
  visit(find(tree, "/regions"), (node) => {
    if (node.type !== "boundary") return;
    const kind = typeof node.params.kind === "string" ? node.params.kind : "";
    const editable = INLINE_VALUE[kind] ?? null;
    const tag = typeof node.params.tag === "string" ? node.params.tag : "";
    const value = editable === null ? null : quantityValue(node.params[editable.param]);
    const kindWord = BOUNDARY_KIND_WORDS[kind] ?? (kind.replaceAll("_", " ") || "Boundary");
    out.push({
      id: node.id,
      name: node.name,
      kind,
      word: kindWord,
      kindWord,
      tag,
      color: colours.get(tag) ?? "",
      editable,
      valueText: editable === null ? "" : node.displays?.[editable.param] ?? (value === null ? "" : `${value} ${editable.unit}`),
      value,
      temperature: INLET_KINDS.has(kind) ? quantityValue(node.params.temperature) : null,
    });
  });
  // **Two of a kind are told apart by name.** Two inlets both read "Inlet" in the same colour, so a
  // user could not tell which speed belonged to which end; the boundary's own name ("inlet_1") is
  // what the tree, the case and the reports call it.
  const seen = new Map<string, number>();
  for (const row of out) seen.set(row.kind, (seen.get(row.kind) ?? 0) + 1);
  // The name alone: "Inlet · inlet_2" did not fit the card's name column and was cut to "Inlet · inl…" (the
  // elbow's two inlets read the same); the kind is the swatch's colour and the name's tooltip.
  return out.map((row) => ((seen.get(row.kind) ?? 0) > 1 ? { ...row, word: row.name } : row));
}

/** The faces of one body no boundary holds: the first few ids, and how many more there are. */
export interface UnassignedFaces {
  ids: number[];
  more: number;
}

/**
 * The faces of the body `partId` that `boundary.uncovered` says no boundary holds, as ids in the server's
 * order, at most `cap` of them. The server answers merged ranges, so a large body is never expanded.
 */
export function unassignedFaces(answer: UncoveredAnswer | null, partId: string, cap = 8): UnassignedFaces {
  const entry = answer?.parts.find((p) => p.part === partId);
  if (entry === undefined) return { ids: [], more: 0 };
  const ids: number[] = [];
  let total = 0;
  for (const [first, last] of entry.ranges) {
    total += last - first + 1;
    for (let id = first; id <= last && ids.length < cap; id++) ids.push(id);
  }
  return { ids, more: total - ids.length };
}
