/**
 * The Bodies page's deciding logic (brief M-502 §UI 5.2), pure so the tests can pin it without a
 * browser: which role a body carries (read the way M-501's reader reads it), why Fluid and Hole
 * are sometimes disabled, and the exact `mesh.body_role` payload a choice makes.
 */

export type Role = "fluid" | "solid" | "hole" | "left_out";

/**
 * The role a body carries, read the way M-501's `_roles` reads it: the settings' `domain_part` is
 * the fluid, an excluded body is left out, and everything else simulated is a solid (until the
 * multi-region work gives solids their own slot). The `enclosure` id is the hole slot.
 */
export function roleOf(
  part: { id: string; params: Record<string, unknown> },
  domainPart: string | null,
  enclosure: string | null,
): Role {
  const simulate = part.params.simulate !== false;
  if (!simulate) return "left_out";
  if (domainPart === part.id) return "fluid";
  if (enclosure === part.id) return "hole";
  return "solid";
}

/** Why Fluid and Hole cannot be chosen now: the one-slot rule, in the page's words. */
export function slotReason(count: number): string {
  return "only one body can be the fluid or the hole in the flow";
}

/**
 * The `mesh.body_role` call a choice makes, and the ONLY shape it can take: one body names
 * `part`; a multi-selection names `bodies` with every id, which is ONE call with ONE undo step.
 * The page never composes the underlying commands itself — this function is the whole of its
 * vocabulary, and the test pins it.
 */
export function bodiesPayload(
  ids: string[],
  role: Role,
): { part: string; role: Role } | { bodies: string[]; role: Role } {
  return ids.length > 1 ? { bodies: ids, role } : { part: ids[0] ?? "", role };
}
