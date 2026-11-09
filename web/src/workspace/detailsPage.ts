/**
 * Which page the Details pane shows for what is selected (the layout decision, 2026-10-18).
 *
 * **The map is a pure function of the selection, so it can be tested without a browser** - and it is
 * the whole of the new behaviour: the panel components already exist and are read from the registry,
 * so this decides *which* one and nothing else.
 *
 * ## Why a map rather than each panel deciding
 *
 * The owner's suggestion is that "the tree selection IS the tab": a mesh settings node shows the Mesh
 * settings, a tag shows Conditions, a run shows its summary. A panel that hid itself when the
 * selection was wrong would need every panel to know about every node type; one table means adding a
 * node type is a row here.
 *
 * ## The keys are node TYPES, never paths
 *
 * The decision says **presentation only: no node type, id or path changes**. So this reads the types
 * the tree already produces, and a type it does not name falls back to Properties rather than to
 * nothing - which is what makes the pane safe to land before every page exists.
 */

/** The panel ids a page can name, as the workspace registry knows them. */
export type DetailsTarget =
  | { kind: "panel"; id: string }
  /** Several selected: the common actions rather than one page. */
  | { kind: "many" }
  /** Nothing selected: the stage's "what to do next" checklist. */
  | { kind: "checklist" };

/**
 * Node type to panel id.
 *
 * **Grouped by what a user is looking at, not by the tree's shape.** A part is a body; a tag or a
 * boundary is a condition; the mesh settings node is the mesh; a mesh result is its quality; the
 * physics, regions and solver sections are their own pages; a run is its summary.
 *
 * A type with no row here falls back to Properties, which is the page that already renders any
 * node's own fields - so a node type added later is never a blank pane.
 */
const BY_TYPE: Record<string, string> = {
  // **Every key is a node type `node.types` really returns**, listed on this machine rather than
  // guessed: `part`, `tag`, `boundary`, `mesh_settings`, `mesh_result`, `material`, `region`,
  // `solver_settings`, `parameter`, `variable` and the geometry operations. My first version named
  // `physics`, `run`, `results`, `scene` and `slice`, **none of which exist** - a map keyed on
  // invented types falls back silently and looks like it works.
  //
  // A part is a body: its own facts, plus the include-in-simulation and mesh-size rows as they land.
  part: "properties",
  // **A tag and a boundary show their OWN page** - the Properties panel - and not `conditions`.
  //
  // Measured: selecting the `inlet` boundary opened CONDITIONS, which lays out the whole set-up and
  // **never reads the selection** (it has no `selectedId`), so the boundary's own condition picker was
  // unreachable. `conditions` is the page for set-up AS A WHOLE, which is what a user opens when they
  // want the overview; a selected tag or boundary has its own verbs and belongs on its own page.
  tag: "properties",
  boundary: "properties",
  // The mesh: its settings node and its result both show the mesh page, because the result IS what
  // that page reports (quality, fidelity, patches).
  mesh_settings: "mesh",
  mesh_result: "mesh",
  mesh_patch: "mesh",
  // The solver's settings own page. The solver's own settings node.
  solver_settings: "solve",
  // **A study shows its OWN card, not Solve** (claude-win2's package-two review item 7: *"a study
  // (`velocity_sweep`) opens the Solve page; it should open its own page"*). Measured: `useStudyCard`
  // already returns a full card for a study node - variables, responses, objectives, constraints -
  // and `PropertiesPanel` renders it, so the `solve` mapping sent a user to a page about something
  // else while the card they wanted was one panel away.
  study: "properties",
  // **A region, a material, a parameter and a variable show their OWN page** - the Properties panel -
  // and not the Conditions panel. That mapping was wrong and the picture showed it: clicking the
  // `fluid` region opened Conditions, which is the whole set-up rather than that region, so the
  // region's Material picker never appeared. `conditions` is the page for the set-up AS A WHOLE (a
  // tag or a boundary, which Conditions does lay out), not for every node that happens to live in it.
  region: "properties",
  material: "properties",
  parameter: "properties",
  variable: "properties",
};

/**
 * The page for a selection.
 *
 * `count` is how many things are selected, because "several" is a different page from any one of
 * them: the decision says common actions rather than a page that is right for the first and wrong for
 * the rest.
 */
export function detailsPage(type: string | null, count = type === null ? 0 : 1): DetailsTarget {
  // **Nothing chosen is the checklist**, and several chosen is the multi-selection page - IN THAT ORDER.
  // A mixed selection has no single type, so it arrives here with `type === null` AND `count > 1`;
  // checking `type === null` first sent it to the checklist, and the page vanished. Measured: two parts
  // said "2 parts" and adding a tag of any kind replaced it with "NEXT STEP".
  if (count === 0) return { kind: "checklist" };
  if (count > 1) return { kind: "many" };
  if (type === null) return { kind: "checklist" };
  return { kind: "panel", id: BY_TYPE[type] ?? "properties" };
}

/**
 * The panel ids this map can name, for a test that checks they all exist in the registry.
 *
 * **Exported so the map cannot name a panel nothing registers** - a typo would show an empty pane,
 * which reads as "this node has no settings" rather than as a mistake.
 */
export const NAMED_PANELS: readonly string[] = [...new Set(Object.values(BY_TYPE))].sort();
