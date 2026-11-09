/**
 * The Material ▾ pick (ADR-0022 §4.4, T-045): the menu's sections, the recent names, and the two
 * commands the common path and the library path issue.
 *
 * §4.4 is specific about the shape of this, and the parts worth testing are the ones a screenshot
 * cannot settle:
 *
 * * the project's materials are **kind-filtered to what the picked bodies' regions allow**, in a
 *   fluid and a solid section;
 * * the five most recent names are a **per-browser convenience in `localStorage`**, wrapped so a
 *   storage failure (a private window, a full quota) cannot break the pick;
 * * **Browse library…** searches `material.library_search`, and Add runs
 *   `material.add_from_library` **and then** `region.assign` — two undo steps, with a toast that says
 *   so ("Added water · assigned to 3 bodies"), while the common path (an existing material) is one.
 *
 * The rules live here; `MaterialMenu.tsx` only renders and calls.
 */

import type { LibraryRecord, MaterialProvider } from "../api/types";

/** The `localStorage` key the recents live under. Namespaced so it cannot collide with a theme key. */
export const RECENTS_KEY = "stokes.materials.recent";

/** How many recent names §4.4 keeps. */
export const RECENTS_LIMIT = 5;

/** One entry of the menu's own section. */
export interface MaterialChoice {
  /** The material node's id (an existing project material) or the library's `fluid` id. */
  id: string;
  name: string;
  /** "fluid" or "solid", or "" when the project material does not say. */
  kind: string;
}

/**
 * Split the project's materials into the two sections §4.4 asks for, fluid first. A material whose
 * `kind` is neither is grouped by `fluid` default, since a fluid is what most set-ups need and an
 * unlabelled material is more likely to be one than a solid.
 */
export function splitByKind(materials: readonly MaterialChoice[]): { fluids: MaterialChoice[]; solids: MaterialChoice[] } {
  const fluids: MaterialChoice[] = [];
  const solids: MaterialChoice[] = [];
  for (const material of materials) {
    if (material.kind === "solid") solids.push(material);
    else fluids.push(material);
  }
  return { fluids, solids };
}

/**
 * The recent names, newest first, deduplicated. Reads `localStorage` inside a try/catch because a
 * browser may refuse it (a private window, a disabled store): §4.4 calls this a convenience, so a
 * refusal must give an empty list rather than break the menu.
 */
export function readRecents(storage: Pick<Storage, "getItem"> | null): string[] {
  if (storage === null) return [];
  try {
    const raw = storage.getItem(RECENTS_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const names: string[] = [];
    for (const item of parsed) {
      // Only strings, deduplicated, and never more than the limit: the stored value may have been
      // written by another version or edited by hand.
      if (typeof item === "string" && item !== "" && !names.includes(item)) names.push(item);
      if (names.length === RECENTS_LIMIT) break;
    }
    return names;
  } catch {
    return [];
  }
}

/** The recents after `name` is used: it moves to the front and the list is capped. */
export function withRecent(recents: readonly string[], name: string): string[] {
  if (name === "") return [...recents];
  return [name, ...recents.filter((other) => other !== name)].slice(0, RECENTS_LIMIT);
}

/** Write the recents, swallowing a storage failure as `readRecents` does. */
export function writeRecents(storage: Pick<Storage, "setItem"> | null, recents: readonly string[]): void {
  if (storage === null) return;
  try {
    storage.setItem(RECENTS_KEY, JSON.stringify(recents.slice(0, RECENTS_LIMIT)));
  } catch {
    // A convenience, never a failure: the pick still works without a remembered list.
  }
}

/**
 * The commands the library path issues, in order (§4.4): add the record, then assign the new
 * material to the pick. Two commands, so **two undo steps**, which the caller states in its toast.
 *
 * `provider` comes from the record rather than a separate argument, so a caller cannot add a record
 * from one provider while telling the server another.
 */
export function libraryCommands(
  record: Pick<LibraryRecord, "provider" | "fluid" | "name">,
  target: readonly { part: string; faces: "all" }[],
): { add: { method: string; params: Record<string, unknown> }; assign: { method: string; params: Record<string, unknown> } } {
  return {
    add: {
      method: "material.add_from_library",
      // Let the API choose a valid, unique project node name from the provider's fluid id.
      // `record.name` is a display label and can contain spaces or punctuation.
      params: { provider: record.provider, fluid: record.fluid },
    },
    // The actual material name is returned by `add_from_library` and supplied by the caller.
    assign: { method: "region.assign", params: { target } },
  };
}

/** The store's `execute`, as far as a material assignment needs it. */
export type Execute = (
  method: string,
  params?: Record<string, unknown>,
  options?: { silent?: boolean },
) => Promise<unknown>;

/** The part the mesh settings name as the fluid (`/mesh/settings`'s `domain_part`), or null when none is set. */
export function domainPartOf(tree: { path: string; params?: Record<string, unknown>; children?: unknown[] } | null): string | null {
  const stack: unknown[] = tree === null ? [] : [tree];
  while (stack.length > 0) {
    const node = stack.pop() as { path?: string; params?: Record<string, unknown>; children?: unknown[] };
    if (node.path === "/mesh/settings") {
      const held = node.params?.domain_part;
      return typeof held === "string" && held !== "" ? held : null;
    }
    if (node.children) stack.push(...node.children);
  }
  return null;
}

/**
 * The call that makes a freshly materialled body the fluid the mesher fills, or null when none is due.
 *
 * "This body is the fluid" is one decision: the region and its material (`region.assign`), the body's
 * role and the mesh settings' `domain_part`. `mesh.body_role {role: "fluid"}` sets the last two in one
 * undo step (it is what the Mesh stepper's Fluid button sends), so the Material menu follows its
 * `region.assign` with it. Nothing is sent when:
 *
 * * the region is not a fluid region (a solid material does not make its body the domain; the kind
 *   is the region's, from the reply, else the material's);
 * * more than one body was given (the fluid is one body by definition);
 * * the body already is the domain.
 */
export function fluidRoleCall(
  assigned: unknown,
  bodies: readonly { part: string }[],
  domainPart: string | null,
  materialKind: string,
): { method: string; params: Record<string, unknown> } | null {
  const region = (assigned as { region?: { kind?: unknown } } | null | undefined)?.region;
  const kind = typeof region?.kind === "string" && region.kind !== "" ? region.kind : materialKind;
  if (kind !== "fluid") return null;
  const unique = [...new Set(bodies.map((b) => b.part))];
  if (unique.length !== 1) return null;
  if (domainPart !== null && domainPart === unique[0]) return null;
  return { method: "mesh.body_role", params: { part: unique[0], role: "fluid" } };
}

/**
 * Assign a project material to bodies and, when that makes them the fluid, give the body the fluid role
 * too. The assignment is the command that matters: when the role call is refused the material stays
 * assigned and the refusal comes back as `roleError` for the caller to say.
 */
export async function assignMaterial(
  execute: Execute,
  material: string,
  bodies: readonly { part: string; faces: "all" }[],
  domainPart: string | null,
  materialKind: string,
): Promise<{ madeFluid: boolean; roleError: string }> {
  const assigned = await execute("region.assign", { material, target: bodies }, { silent: true });
  return roleAfterAssign(execute, assigned, bodies, domainPart, materialKind);
}

/** The second half of `assignMaterial`, shared with the library path that adds the material first. */
export async function roleAfterAssign(
  execute: Execute,
  assigned: unknown,
  bodies: readonly { part: string }[],
  domainPart: string | null,
  materialKind: string,
): Promise<{ madeFluid: boolean; roleError: string }> {
  const call = fluidRoleCall(assigned, bodies, domainPart, materialKind);
  if (call === null) return { madeFluid: false, roleError: "" };
  try {
    await execute(call.method, call.params, { silent: true });
    return { madeFluid: true, roleError: "" };
  } catch (err) {
    return { madeFluid: false, roleError: err instanceof Error ? err.message : String(err) };
  }
}

/** The toast §4.4 asks for after the library path: "Added water · assigned to 3 bodies". */
export function addedMessage(
  record: Pick<LibraryRecord, "name">,
  materialName: string,
  bodies: number,
): string {
  const noun = bodies === 1 ? "body" : "bodies";
  const label = materialName === record.name ? record.name : `${record.name} as ${materialName}`;
  return `Added ${label} · assigned to ${bodies} ${noun}`;
}

/** The providers worth offering as filter chips: the available ones, in the server's order. */
export function providerChips(providers: readonly MaterialProvider[]): MaterialProvider[] {
  return providers.filter((provider) => provider.available);
}

/** Parameters accepted by `material.library_search` (the API uses `providers`, plural). */
export function librarySearchParams(query: string, provider: string | null): Record<string, unknown> {
  return {
    query,
    ...(provider === null ? {} : { providers: [provider] }),
  };
}

/**
 * Whether a library record matches a free-text query.
 *
 * The server does the real search (`material.library_search`), so this is only for the case where the
 * client filters a list it already has: it matches the name, the provider's fluid id and any alias,
 * case-insensitively, because an engineer types "r718" as readily as "water".
 */
export function recordMatches(record: LibraryRecord, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  if (record.name.toLowerCase().includes(needle)) return true;
  if (record.fluid.toLowerCase().includes(needle)) return true;
  if (record.formula.toLowerCase().includes(needle)) return true;
  return record.aliases.some((alias) => alias.toLowerCase().includes(needle));
}
