/**
 * Which parameters an implicit operation's form shows (ADR-0035 decision 7).
 *
 * The node types carry every parameter of every kind (a primitive's `radius`, `length`, `size`…), and
 * each kind reads only some of them (`docs/engineering/implicit-schema.md`). A form that showed them
 * all would ask a sphere for its edge lengths. This table is presentation only: the server reads
 * the same parameters whatever the form shows, and keeps the hidden ones' values.
 *
 * It also holds the modeller's words and units (review findings A5 and B9): the title of an
 * operation by what it makes or does, a choice's value in words, and the geometry unit system
 * (millimetres and degrees) every geometry form and tree value shows.
 *
 * Pure data and pure functions: the tests run in Node.
 */
import { prettyDisplay } from "../util/units";

/** What each kind of a transform reads: the modeller's and the B-rep one share their parameters. */
const TRANSFORM_READS = {
  by: "kind",
  kinds: {
    translate: ["offset"],
    rotate: ["axis", "angle", "centre"],
    mirror: ["axis", "centre"],
    scale: ["factor", "centre"],
  },
} as const;

/** The parameters each kind of a type reads; a parameter not named for any kind is always shown. */
const READS: Record<string, { by: string; kinds: Record<string, readonly string[]> }> = {
  implicit_primitive: {
    by: "kind",
    kinds: {
      box: ["size", "position"],
      rounded_box: ["size", "corner_radius", "position"],
      cylinder: ["radius", "length", "axis", "position"],
      cone: ["radius", "top_radius", "length", "axis", "position"],
      sphere: ["radius", "position"],
      torus: ["radius", "tube_radius", "axis", "position"],
      capsule: ["radius", "length", "axis", "position"],
      half_space: ["normal", "position", "size"],
    },
  },
  implicit_transform: TRANSFORM_READS,
  // The B-rep transform of imported bodies.
  transform: TRANSFORM_READS,
  // ADR-0036's modelling tools.
  implicit_offset: {
    by: "kind",
    kinds: {
      offset: ["distance"],
      shell: ["distance", "outward"],
    },
  },
  implicit_pattern: {
    by: "kind",
    kinds: {
      linear: ["axis", "count", "pitch"],
      polar: ["axis", "count", "centre", "angle"],
    },
  },
  // A lattice reads its wall thickness as a sheet and its bias as a network.
  implicit_lattice: {
    by: "form",
    kinds: {
      sheet: ["thickness"],
      network: ["bias"],
      channel_a: ["thickness"],
      channel_b: ["thickness"],
    },
  },
};

/**
 * A sketch reads parameters by two choices: its profile (the 2D shape) and its method (pull or
 * revolve). A parameter named for either choice shows when the current value of that choice reads it.
 */
const SKETCH: { by: string; kinds: Record<string, readonly string[]> }[] = [
  {
    by: "profile",
    kinds: {
      rectangle: ["width", "height"],
      rounded_rectangle: ["width", "height", "corner_radius"],
      circle: ["radius"],
      ellipse: ["width", "height"],
      slot: ["radius", "slot_length"],
      polygon: ["radius", "sides"],
      // A drawn profile (the sketcher's entities) reads none of the named profiles' sizes.
      custom: ["dimensions"],
    },
  },
  {
    by: "method",
    kinds: {
      extrude: ["depth", "symmetric"],
      revolve: ["angle", "axis_distance"],
    },
  },
];

/**
 * Parameters a form of the type never shows. A shape reads no part, so its (always empty) list of
 * inputs says nothing; a sketch's entities are edited in the sketcher, not as JSON text.
 */
const NEVER: Record<string, ReadonlySet<string>> = {
  implicit_primitive: new Set(["inputs"]),
  implicit_sketch: new Set(["inputs", "entities"]),
};

/** Every parameter some kind of `type` reads. */
function kindParams(type: string): Set<string> {
  const table = READS[type];
  const out = new Set<string>();
  if (table === undefined) return out;
  for (const names of Object.values(table.kinds)) for (const name of names) out.add(name);
  return out;
}

/**
 * Whether the form of an operation of `type`, with these `params`, shows parameter `name`. True for
 * every parameter of a type this table does not know, and for a kind it does not know (a newer
 * server's), so nothing is ever hidden by mistake.
 */
export function implicitShows(type: string, params: Record<string, unknown>, name: string): boolean {
  if (NEVER[type]?.has(name)) return false;
  if (type === "implicit_sketch") {
    // A drawn profile is placed by its own coordinates: the revolve axis is the sketch's v axis.
    if (name === "axis_distance" && params.profile === "custom") return false;
    for (const table of SKETCH) {
      const named = Object.values(table.kinds).some((names) => names.includes(name));
      if (!named) continue;
      const kind = params[table.by];
      const reads = typeof kind === "string" ? table.kinds[kind] : undefined;
      return reads === undefined || reads.includes(name);
    }
    return true;
  }
  const table = READS[type];
  if (table === undefined) return true;
  const kind = params[table.by];
  const reads = typeof kind === "string" ? table.kinds[kind] : undefined;
  if (reads === undefined) return true;
  if (!kindParams(type).has(name)) return true;
  return reads.includes(name);
}

/** Whether `type` is an implicit operation (ADR-0035): its name starts with `implicit_`. */
export function isImplicitOperation(type: string): boolean {
  return type.startsWith("implicit_");
}

/** The order a primitive's or a transform's main value is looked for, for the tree's value column. */
const MAIN: readonly string[] = ["radius", "width", "size", "offset", "angle", "factor", "normal", "distance", "count", "cell"];

// ---------------------------------------------------------------------------------------------- words, not enum values
/**
 * The unit system every geometry form shows, whatever the solver's display units (review finding
 * A5): lengths in millimetres and angles in degrees, as the sketcher and the scale bar count, so a
 * bare number means the same thing in every field of the modeller.
 */
export const GEOMETRY_UNIT_SYSTEM = "mm";

/** What a value of a choice is called in a form, by parameter and value; others are spelled out from the value. */
const OPTION_WORDS: Record<string, Record<string, string>> = {
  kind: {
    box: "Box",
    rounded_box: "Rounded box",
    cylinder: "Cylinder",
    cone: "Cone",
    sphere: "Sphere",
    torus: "Torus",
    capsule: "Capsule",
    half_space: "Half-space",
    translate: "Move",
    rotate: "Rotate",
    mirror: "Mirror",
    scale: "Scale",
    offset: "Offset",
    shell: "Shell",
    linear: "Linear",
    polar: "Polar",
    gyroid: "Gyroid",
    schwarz_p: "Schwarz P",
    diamond: "Diamond",
  },
  form: { sheet: "Sheet (walls)", network: "Network (solid)", channel_a: "Channel A (one side of the walls)", channel_b: "Channel B (the other side)" },
  op: { union: "Union", difference: "Subtract", intersection: "Intersect" },
  profile: {
    rectangle: "Rectangle",
    rounded_rectangle: "Rounded rectangle",
    circle: "Circle",
    ellipse: "Ellipse",
    slot: "Slot",
    polygon: "Polygon",
    custom: "Drawn sketch",
  },
  method: { extrude: "Pull", revolve: "Revolve" },
  plane: { xy: "XY plane", yz: "YZ plane", zx: "ZX plane" },
};

/** A choice's value in words: "rounded_box" is "Rounded box", "difference" is "Subtract", "xy" is "XY plane". */
export function optionLabel(name: string, value: string): string {
  const known = OPTION_WORDS[name]?.[value];
  if (known !== undefined) return known;
  // Axes and signed directions ("x", "+z", "-y") read best in capitals.
  if (/^[+-]?[xyz]$/.test(value)) return value.toUpperCase();
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * The card's and the page's title for an implicit operation: what it makes or does, in words
 * ("Cylinder", "Move", "Subtract", "Sketch · Pull"), never the type's name. The B-rep `transform` of
 * imported bodies is titled as the modeller's is ("Rotate"). Null for another type.
 */
export function implicitTitle(type: string, params: Record<string, unknown>): string | null {
  const word = (name: string): string | null => (typeof params[name] === "string" && params[name] !== "" ? optionLabel(name, params[name] as string) : null);
  switch (type) {
    case "implicit_primitive":
      return word("kind") ?? "Shape";
    case "implicit_sketch":
      return `Sketch · ${word("method") ?? "Pull"}`;
    case "implicit_transform":
    case "transform":
      return word("kind") ?? "Transform";
    case "implicit_boolean":
      return word("op") ?? "Combine";
    case "implicit_split":
      return "Split";
    case "implicit_offset":
      return word("kind") ?? "Offset";
    case "implicit_pattern": {
      const kind = word("kind");
      return kind === null ? "Pattern" : `${kind} pattern`;
    }
    case "implicit_lattice": {
      const kind = word("kind");
      return kind === null ? "Lattice" : `${kind} lattice`;
    }
    default:
      return null;
  }
}

/** Factors from a stored SI unit to the geometry unit, as the display texts spell them. */
const TO_GEOMETRY: Record<string, { unit: string; factor: number }> = {
  m: { unit: "mm", factor: 1000 },
  rad: { unit: "deg", factor: 180 / Math.PI },
  "m**2": { unit: "mm**2", factor: 1e6 },
  "m**3": { unit: "mm**3", factor: 1e9 },
};

/**
 * A display text in the geometry unit: "0.003 m" is "3 mm", "6.28319 rad" is "360 deg"; any other
 * text (already in mm, a count, a word) is returned as it is.
 */
export function geometryDisplay(text: string): string {
  const m = /^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s+(\S+)\s*$/.exec(text);
  if (m === null) return text;
  const to = TO_GEOMETRY[m[2]];
  if (to === undefined) return text;
  const value = Number(m[1]) * to.factor;
  // Six significant digits, the server's display precision, without a trailing ".0".
  const shown = Number(value.toPrecision(6)).toString();
  return `${shown === "-0" ? "0" : shown} ${to.unit}`;
}

/** A display text in the geometry unit, for reading: "6.28319 rad" is "360°", "2e-6 m**2" is "2 mm²". */
export function prettyGeometry(text: string): string {
  return prettyDisplay(geometryDisplay(text)).replace(/ deg$/, "°");
}

/**
 * The tree row's value for an implicit operation: what it is in words, then the first value its
 * kind reads in the geometry unit ("Sphere · 8 mm", "Rotate · 45 deg", "Subtract"). Null for any
 * other type, which keeps the tree's own rule.
 */
export function implicitTreeValue(
  type: string,
  params: Record<string, unknown>,
  displays: Record<string, string> | undefined,
): string | null {
  const label = implicitTitle(type, params);
  if (label === null) return null;
  for (const name of MAIN) {
    if (!implicitShows(type, params, name)) continue;
    const shown = displays?.[name];
    if (typeof shown === "string" && shown !== "") return `${label} · ${prettyGeometry(shown)}`;
    const value = params[name];
    if (typeof value === "string" && name === "normal") return `${label} · ${optionLabel(name, value)}`;
    if (Array.isArray(value) && name === "size") return label;
  }
  return label;
}
