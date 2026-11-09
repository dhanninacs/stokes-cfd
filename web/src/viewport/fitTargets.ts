/** Fit includes every visible CAD body, including displayed region domains. */
export interface FitCandidate {
  id: string;
  regionBody: boolean;
}
export function fitCandidates(bodies: readonly FitCandidate[]): FitCandidate[] {
  return [...bodies];
}
export function fitTargetIds(bodies: readonly FitCandidate[]): Set<string> {
  return new Set(fitCandidates(bodies).map((b) => b.id));
}

/**
 * The scene sources that are cuts of the results rather than the body they cut: the mid-plane slice
 * (`mesh:slice`), the slices of the list (`mesh:slice:<id>`), the section's cap and the iso-surface. Written
 * out here, not imported, so this file stays free of the panels (the names are fixed by `sliceModel.ts` and
 * `isoModel.ts`, and the test pins them).
 */
export function isCutSource(source: string): boolean {
  return source === "mesh:slice" || source.startsWith("mesh:slice:") || source === "mesh:slice-cap" || source === "mesh:iso";
}

/** True for a source that is a slice (the mid-plane's or one of the list), which a fit keeps inside the frame. */
function isSliceSource(source: string): boolean {
  return source === "mesh:slice" || source.startsWith("mesh:slice:");
}

/**
 * The sources a Looks fit frames, or null for the scene's own rule (every shown body).
 *
 * **The fluid body, not whatever the scene holds.** In the Results stage the mesh patches (`mesh:<id>`) are the
 * fluid's boundary and replace the geometry parts, so the box to frame is the union of those patches; a slice is
 * kept inside only when one is on (it lies in the body, so it adds nothing but the guarantee that it is seen).
 * Before there are patches the fluid part itself (`fluidId`, the geometry source) is the target. The first show of
 * pipe3 came up on the outlet end because the fit followed the parts that had arrived (R4a) and then the cut and
 * the iso, none of which is the body.
 */
export function fitSourcesFor(sources: readonly string[], fluidId: string | null): string[] | null {
  const patches = sources.filter((source) => source.startsWith("mesh:") && !isCutSource(source));
  if (patches.length > 0) return [...patches, ...sources.filter(isSliceSource)];
  if (fluidId !== null && sources.includes(fluidId)) return [fluidId];
  return null;
}

/**
 * The patch sources the tree lists that the scene does not hold yet. The first show's fit waits for this to be
 * empty: framed with only some of the patches in the scene it frames what has arrived (R4c), and the scene's
 * frame is kept afterwards (a cut arriving does not re-frame). A patch the server cannot give never lands, so
 * the caller's own time limit decides in that case.
 */
export function missingPatchSources(listed: readonly string[], held: readonly string[]): string[] {
  const have = new Set(held);
  return listed.filter((source) => !have.has(source));
}

/**
 * The part names a fit frames: those of `candidates` whose source is in `only`. Null `only`, or a selection that
 * matches no part (the scene lost the source, or it is hidden), is the scene's own rule: null is returned and the
 * caller falls back, so a fit never frames nothing.
 */
export function namesOfSources(candidates: readonly { name: string; source: string }[], only: ReadonlySet<string> | null): Set<string> | null {
  if (only === null) return null;
  const names = candidates.filter((part) => only.has(part.source)).map((part) => part.name);
  return names.length === 0 ? null : new Set(names);
}
