/** What each folder is for: a few words for its page, the sentence for the tooltip. Pure, so a test can read it. */

/**
 * One line saying what the folder is for, per folder. Falls back to the count.
 *
 * **Every folder a user meets has a line** (claude-win2's package-two review item 7: *"`physics`
 * folder page has no purpose line - give every folder one"*). The fallback stays, because a count is a
 * truthful answer for a folder nobody has described - but "2 items inside" is what a page says when
 * somebody has not thought about it, and the top-level folders are exactly where that shows.
 *
 * **On the left is the path, because that is what a folder IS in this model**: the code finds several
 * of them by path, and the paths here are the ones that exist in a real project.
 */
export const PURPOSE: Record<string, string> = {
  // Geometry.
  "/geometry": "Everything that makes the shape: the bodies, the named faces and the operations.",
  "/geometry/parts": "The solids in this project: imported files and operation results.",
  "/geometry/tags": "Named sets of faces: boundaries, regions and working sets.",
  "/geometry/operations": "Prepare operations and the parts they produce.",
  // Mesh.
  "/mesh": "The mesh settings and the result of the last mesh.",
  "/mesh/results": "The mesh that was last built, and the patches it produced.",
  // Physics.
  "/physics": "What the fluid is: the materials and the parameters the model uses.",
  "/physics/parameters": "Named values and expressions the model uses.",
  "/physics/materials": "The materials a region can be given.",
  // Regions, solver and the rest of the stages.
  "/regions": "The fluid and solid regions, and the boundaries that make them.",
  "/solver": "The solver's own settings.",
  "/monitors": "Readings taken while a solve runs.",
  "/scenes": "Fields, slices and residuals appear here once a run has finished.",
  "/runs": "One entry per run, with its case and its logs.",
  "/studies": "Design studies: variables, responses, objectives and constraints.",
};
/**
 * The same folders in a few words, for the page; `PURPOSE` is the tooltip (the owner's rule: a few words
 * on the screen, the sentence in the tooltip).
 */
export const PURPOSE_WORDS: Record<string, string> = {
  "/geometry": "Bodies, faces and operations",
  "/geometry/parts": "Solids in this project",
  "/geometry/tags": "Named sets of faces",
  "/geometry/operations": "Operations and their parts",
  "/mesh": "Mesh settings and result",
  "/mesh/results": "The last mesh",
  "/physics": "Materials and parameters",
  "/physics/parameters": "Named values",
  "/physics/materials": "Materials for regions",
  "/regions": "Regions and boundaries",
  "/solver": "Solver settings",
  "/monitors": "Readings during a run",
  "/scenes": "Results of a run",
  "/runs": "One entry per run",
  "/studies": "Design studies",
};

/** What a folder's page says: a few words, with the sentence as the tooltip. */
export function folderPurpose(path: string, total: number): { words: string; tip: string | undefined } {
  const words = PURPOSE_WORDS[path];
  if (words !== undefined) return { words, tip: PURPOSE[path] };
  return { words: `${total} ${total === 1 ? "item" : "items"} inside`, tip: undefined };
}

