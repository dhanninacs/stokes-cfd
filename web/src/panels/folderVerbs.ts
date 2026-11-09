/**
 * What each folder that LOOKS like a container can create (claude-win2's 0200 item 3).
 *
 * **The tree guide's finding 3**: materials, regions, scenes, studies, monitors and the solver folder
 * look like containers but offered only "Add group" (now removed) and a greyed Rename and Delete — so
 * a user could not add the thing the folder is named for. The guide's words: *"there is no 'New
 * material', 'New study', 'New region'."*
 *
 * **A table, because the alternative is each folder knowing its own verb** and the viewport menus
 * (which already create materials, regions and conditions) drifting from the tree. One row per folder
 * means adding a folder's verb is one line, and it is the same line the tree reads.
 *
 * ## What is deliberately absent
 *
 * * **`/monitors`** — nothing creates or reads it; see the message to claude-win2 for the evidence. It
 *   gets no verb rather than a button that fails.
 * * **`/runs`** — a run is created by pressing Run. A "New run" entry would be a second way to do it
 *   that skips the settings.
 * * **`/solver`** — its settings node is edited in the Details pane, which is where the decision puts
 *   it; there is nothing to add to the folder.
 */
export interface FolderVerb {
  /** The command the entry runs. */
  command: string;
  /** The menu label, in the user's words. */
  label: string;
  /** The command's arguments, minus anything the caller fills in (a name it asks for). */
  params?: Record<string, unknown>;
  /** What the name field asks for, as a noun: "material", "region", "study". */
  placeholder: string;
  /**
   * What the entry needs before it can run.
   *
   * **`name` is the default and was the only kind until Geometry needed the others**
   * (claude-win2's package-two review item 2): "Import geometry…" needs a FILE, and "Add primitive"
   * needs a SHAPE, so a single name-prompt shape would have made both of them wrong. Naming the three
   * kinds is what keeps one table serving every folder.
   */
  asks?: "name" | "file" | "shape" | "selection";
  /** For `asks: "shape"`, the choices to offer. */
  shapes?: readonly string[];
}

const VERBS: Record<string, FolderEntry> = {
  "/physics/materials": { command: "material.create", label: "Add from library…", placeholder: "material" },
  "/regions": { command: "region.create", label: "New region…", params: { kind: "fluid" }, placeholder: "region" },
  // **`/scenes` gets NO verb**, and this is the mistake I nearly shipped: I wrote
  // `scene.create` before checking, and **there are no scene commands at all** - `Fields`, `Slice`,
  // `Residuals` are virtual rows the UI draws from a run, not nodes. A button calling a command that
  // does not exist is worse than no button. (`study.create` does exist, checked in `study.py:231`.)
  "/studies": { command: "study.create", label: "New study…", placeholder: "study" },
  // **Geometry's Add verbs** (review item 2: *"`geometry`, `parts`, `tags` have no Add"*). Both
  // commands exist and were checked through the registry before naming them here: `geometry.import`
  // takes a path, `operation.create` makes a `primitive` whose `shape` is box, cylinder or sphere.
  //
  // **A part is not created directly** - it arrives from an import or as an operation's output - so
  // `/geometry/parts` offers the two ways a body can come into being rather than a "New part" that
  // nothing implements.
  "/geometry/parts": {
    command: "geometry.import",
    label: "Import geometry…",
    placeholder: "geometry",
    asks: "file",
  },
  // **"Add tag from selection"** (review item 2's third entry): the tag is named faces, so this is the
  // `selection.name` ACTION rather than a command - the naming card already exists and the viewport
  // toolbar opens it, so one flow serves both entry points.
  "/geometry/tags": {
    label: "Add tag from selection…",
    placeholder: "tag",
    asks: "selection",
    action: "selection.name",
  },
  "/geometry/operations": {
    command: "operation.create",
    label: "Add primitive…",
    placeholder: "primitive",
    asks: "shape",
    shapes: ["box", "cylinder", "sphere"],
  },
};

/**
 * **The fourth kind: an ACTION rather than a command.** "Add tag from selection" names the picked
 * faces, so it is not a command with a name or a path - it is the `selection.name` action, which opens
 * the naming card the viewport toolbar already provides. A verb with `asks: "selection"` therefore
 * carries an `action` instead of a `command`, and the caller runs it through the action bus rather
 * than `execute` - one flow for both entry points rather than a second copy of the naming card.
 */
export interface SelectionVerb extends Omit<FolderVerb, "command"> {
  command?: undefined;
  /** The registered action to run, such as `selection.name`. */
  action: string;
}

/** `folderVerb`'s answer: a command verb, a selection verb, or none. */
export type FolderEntry = FolderVerb | SelectionVerb;

/** The verb for a folder, or None when it has none - which is most folders, and is not a fault. */
export function folderVerb(path: string): FolderEntry | null {
  return VERBS[path] ?? null;
}

/** Every path the table names, so a test can check each one is a real folder. */
export const VERB_PATHS: readonly string[] = Object.keys(VERBS).sort();

/**
 * The one-line hint a folder with no content shows in the Details pane.
 *
 * **The guide's second half of finding 3**: "a folder with no Add and no content shows a one-line
 * hint". The hint names the verb when there is one, and says what fills the folder when there is not
 * - which is the honest answer for `/runs` and `/monitors`.
 */
export function emptyHint(path: string, name: string): string | null {
  const verb = folderVerb(path);
  if (verb !== null) return `${name} you add appear here. Add one with +.`;
  if (path === "/runs") return "Runs appear here when you press Run.";
  if (path === "/scenes") return "Fields, slice and residuals appear here once a run has finished.";
  return null;
}
