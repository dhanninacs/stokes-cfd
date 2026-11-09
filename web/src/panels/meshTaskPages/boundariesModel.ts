/**
 * The Boundaries page's deciding logic (brief M-502 §UI 5.3), pure so the tests pin it without a
 * browser: when the unassigned row shows, and what its button sends.
 */

/** The unassigned row exists only while there are unassigned faces: a control about nothing is noise. */
export function unassignedRowVisible(unassignedFaces: number): boolean {
  return unassignedFaces > 0;
}

/**
 * What "Keep as a wall" sends: `mesh.configure {accept_unassigned: true}` (M-501). The setting is
 * STORED — which is why there is a button at all — and shows checked afterwards, undoable like any
 * edit. The page never composes anything else.
 */
export function keepAsWallPayload(): { accept_unassigned: true } {
  return { accept_unassigned: true };
}
