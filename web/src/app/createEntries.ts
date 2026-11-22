/**
 * The Create menu's entries as data (ADR-0035): the implicit modeller's operations, in three groups.
 *
 * - **Sketch**: the sketcher, and its drawing tools (Line, Rectangle, Circle, Arc, Polygon, Spline),
 *   each of which opens the sketcher with that tool chosen (review finding B7: the named-profile
 *   forms that drew nothing are gone from here; a named profile is still a choice of the sketch
 *   operation's own page). It reads no part, so it is always available.
 * - **Shape**: a primitive, which reads no part, so it is always available.
 * - **Transform**: moves, turns, mirrors or scales ONE picked implicit body (`implicit_transform`), or
 *   any number of picked imported B-rep bodies (`transform`, each its own output). A mesh-only body
 *   (STL, OBJ) and a pick that mixes the two kinds are refused, with why.
 * - **Pattern**: linear or polar copies of ONE picked body (ADR-0036).
 * - **Combine**: union, intersection or difference of TWO or more picked implicit bodies; the first
 *   picked is the input and the rest are the tools, so "Subtract" takes the later picks away from the
 *   first. Split cuts ONE picked body by a plane into two.
 * - **Modify**: offset or shell ONE picked body.
 *
 * The menu component (`app/CreateMenu.tsx`) only renders this list; the card it opens is the ordinary
 * operation card (`panels/OperationCard.tsx`) over the server's schema.
 *
 * Pure data and pure functions: the tests run in Node.
 */

/** What the pick holds, as the entries read it. */
export interface CreateOptions {
  /** Picked bodies that are implicit parts (`family: implicit`). */
  implicitBodies: number;
  /** Picked bodies of another family (imported or B-rep): implicit operations cannot read them. */
  otherBodies: number;
  /** Of `otherBodies`, the mesh-only ones (STL, OBJ: `family: mesh`), which no transform reads. */
  meshBodies?: number;
  /** The operation types the server has, by type (`operation.types`); empty while it did not answer. */
  built?: Record<string, boolean>;
}

export interface CreateEntry {
  id: string;
  label: string;
  /** The action id the entry belongs to (the palette reaches the three groups). */
  action: string;
  /** The operation type the entry opens; absent on a group. */
  op?: string;
  /** Parameter values the card starts from (the kind, the boolean's `op`). */
  params?: Record<string, unknown>;
  /** The sketcher's tool this entry opens a new sketch with (the Sketch group's drawing tools). */
  tool?: SketchTool;
  /** The leaves of a group, in order. */
  entries?: readonly CreateEntry[];
  enabled: boolean;
  why?: string;
}

export const CREATE_SHAPE_ACTION = "create.shape";
export const CREATE_SKETCH_ACTION = "create.sketch";
export const CREATE_PATTERN_ACTION = "create.pattern";
export const CREATE_MODIFY_ACTION = "create.modify";
/** The entry that opens the sketcher rather than an operation card. */
export const SKETCHER_ID = "sketch-new";
export const CREATE_TRANSFORM_ACTION = "create.transform";
export const CREATE_COMBINE_ACTION = "create.combine";

export const PICK_IMPLICIT = "Pick a body made with Create first";
export const PICK_TWO_IMPLICIT = "Combine needs two bodies made with Create: Shift-click a second one";
export const NOT_IMPLICIT = "The pick holds an imported body: Transform and Combine work on bodies made with Create";
export const NOT_BUILT = "Not built on this server";
export const PICK_BODY = "Pick a body first";
export const MIXED_TRANSFORM = "Transform one kind at a time: a body made with Create, or imported bodies";
export const MESH_TRANSFORM = "A mesh-only body (STL, OBJ) cannot be transformed: import its CAD file (STEP, IGES, BREP)";

/**
 * Which transform the pick takes, and why not when it takes none: one implicit body takes the
 * modeller's `implicit_transform`; imported B-rep bodies (one or more) take `transform`.
 */
export function transformFor(implicitBodies: number, otherBodies: number, meshBodies = 0): { op: string; why: string | null } {
  if (implicitBodies > 0 && otherBodies > 0) return { op: "implicit_transform", why: MIXED_TRANSFORM };
  if (meshBodies > 0) return { op: "transform", why: MESH_TRANSFORM };
  if (otherBodies > 0) return { op: "transform", why: null };
  if (implicitBodies > 1) return { op: "implicit_transform", why: "Transform works on one body made with Create: pick just one" };
  if (implicitBodies === 1) return { op: "implicit_transform", why: null };
  return { op: "implicit_transform", why: PICK_BODY };
}

/** The sketcher's drawing tools, as `sketch/model.ts` names them. */
export type SketchTool = "line" | "rectangle" | "circle" | "arc" | "spline" | "polygon";

/** The Sketch group's drawing tools, in ribbon order, with their labels: each opens the sketcher with it chosen. */
export const SKETCH_TOOLS: ReadonlyArray<readonly [SketchTool, string]> = [
  ["line", "Line"],
  ["rectangle", "Rectangle"],
  ["circle", "Circle"],
  ["arc", "Arc"],
  ["polygon", "Polygon"],
  ["spline", "Spline"],
];

/** The shapes, in menu order, with their labels. */
export const SHAPES: ReadonlyArray<readonly [string, string]> = [
  ["box", "Box"],
  ["rounded_box", "Rounded box"],
  ["cylinder", "Cylinder"],
  ["cone", "Cone"],
  ["sphere", "Sphere"],
  ["torus", "Torus"],
  ["capsule", "Capsule"],
  ["half_space", "Half-space"],
];

const TRANSFORMS: ReadonlyArray<readonly [string, string]> = [
  ["translate", "Move"],
  ["rotate", "Rotate"],
  ["mirror", "Mirror"],
  ["scale", "Scale"],
];

const COMBINES: ReadonlyArray<readonly [string, string]> = [
  ["union", "Union"],
  ["difference", "Subtract"],
  ["intersection", "Intersect"],
];

export function createEntries(options: CreateOptions): CreateEntry[] {
  const { implicitBodies, otherBodies, meshBodies = 0, built = {} } = options;
  const has = (op: string) => built[op] !== false;
  const foreign = otherBodies > 0;
  const transform = transformFor(implicitBodies, otherBodies, meshBodies);
  const canCombine = !foreign && implicitBodies >= 2 && has("implicit_boolean");
  const combineWhy = foreign ? NOT_IMPLICIT : PICK_TWO_IMPLICIT;

  const group = (
    id: string,
    label: string,
    action: string,
    op: string,
    by: string,
    items: ReadonlyArray<readonly [string, string]>,
    enabled: boolean,
    why: string,
  ): CreateEntry => {
    const absent = !has(op);
    return {
      id,
      label,
      action,
      enabled: enabled && !absent,
      why: absent ? NOT_BUILT : why,
      entries: items.map(([value, text]) => ({
        id: `${id}-${value}`,
        label: text,
        action,
        op,
        params: { [by]: value },
        enabled: enabled && !absent,
        why: absent ? NOT_BUILT : why,
      })),
    };
  };

  const oneBody = !foreign && implicitBodies === 1;
  const oneWhy = foreign ? NOT_IMPLICIT : implicitBodies > 1 ? "This works on one body: pick just one" : PICK_IMPLICIT;
  /** One tool of its own type (an op with its own rule), as a leaf of a group. */
  const leaf = (id: string, label: string, action: string, op: string, params: Record<string, unknown>, enabled: boolean, why: string): CreateEntry => {
    const absent = !has(op);
    return { id, label, action, op, params, enabled: enabled && !absent, why: absent ? NOT_BUILT : why };
  };
  // The sketcher comes first: a free profile of lines, arcs, circles and splines (ADR-0036); then
  // its drawing tools, which open it with the tool chosen.
  const canSketch = has("implicit_sketch");
  const sketchWhy = canSketch ? "" : NOT_BUILT;
  const sketcher: CreateEntry = { id: SKETCHER_ID, label: "Sketch…", action: CREATE_SKETCH_ACTION, enabled: canSketch, why: sketchWhy };
  const sketch: CreateEntry = {
    id: "sketch",
    label: "Sketch",
    action: CREATE_SKETCH_ACTION,
    enabled: canSketch,
    why: sketchWhy,
    entries: SKETCH_TOOLS.map(([tool, label]) => ({ id: `sketch-${tool}`, label, action: CREATE_SKETCH_ACTION, tool, enabled: canSketch, why: sketchWhy })),
  };
  const combine = group("combine", "Combine", CREATE_COMBINE_ACTION, "implicit_boolean", "op", COMBINES, canCombine, combineWhy);
  return [
    { ...sketch, entries: [sketcher, ...(sketch.entries ?? [])] },
    group("shape", "Shape", CREATE_SHAPE_ACTION, "implicit_primitive", "kind", SHAPES, true, ""),
    group(
      "transform",
      "Transform",
      CREATE_TRANSFORM_ACTION,
      transform.op,
      "kind",
      TRANSFORMS,
      transform.why === null,
      transform.why ?? "",
    ),
    {
      id: "pattern",
      label: "Pattern",
      action: CREATE_PATTERN_ACTION,
      enabled: oneBody && has("implicit_pattern"),
      why: oneWhy,
      entries: [
        leaf("pattern-linear", "Linear", CREATE_PATTERN_ACTION, "implicit_pattern", { kind: "linear" }, oneBody, oneWhy),
        leaf("pattern-polar", "Polar", CREATE_PATTERN_ACTION, "implicit_pattern", { kind: "polar" }, oneBody, oneWhy),
      ],
    },
    {
      ...combine,
      // Split reads one body: the group is open when either kind of tool fits the pick.
      enabled: combine.enabled || (oneBody && has("implicit_split")),
      entries: [
        ...(combine.entries ?? []),
        leaf("combine-split", "Split", CREATE_COMBINE_ACTION, "implicit_split", {}, oneBody, oneWhy),
      ],
    },
    {
      id: "modify",
      label: "Modify",
      action: CREATE_MODIFY_ACTION,
      enabled: oneBody && (has("implicit_offset") || has("implicit_lattice")),
      why: oneWhy,
      entries: [
        leaf("modify-offset", "Offset", CREATE_MODIFY_ACTION, "implicit_offset", { kind: "offset" }, oneBody, oneWhy),
        leaf("modify-shell", "Shell", CREATE_MODIFY_ACTION, "implicit_offset", { kind: "shell" }, oneBody, oneWhy),
        // Fill the body with a triply periodic surface (ADR-0035 slice 3): gyroid first, the one a
        // heat exchanger core is usually made of; the card offers Schwarz P and diamond.
        leaf("modify-lattice", "Lattice", CREATE_MODIFY_ACTION, "implicit_lattice", { kind: "gyroid", form: "sheet" }, oneBody, oneWhy),
      ],
    },
  ];
}
