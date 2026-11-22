import { describe, expect, it } from "vitest";
import { MESH_TRANSFORM, MIXED_TRANSFORM, NOT_BUILT, NOT_IMPLICIT, PICK_BODY, PICK_TWO_IMPLICIT, SHAPES, SKETCHER_ID, SKETCH_TOOLS, createEntries, transformFor } from "./createEntries";
import { COLLAPSE_ORDER, TOOL_ICONS, nextToCollapse } from "./buildTools";
import { geometryDisplay, implicitShows, implicitTitle, implicitTreeValue, isImplicitOperation, optionLabel, prettyGeometry } from "../state/implicitFields";
import { operationInputs } from "../state/operations";

const group = (id: string, entries: ReturnType<typeof createEntries>) => entries.find((e) => e.id === id)!;

describe("createEntries", () => {
  it("offers every shape with nothing picked, and no transform or combine", () => {
    const entries = createEntries({ implicitBodies: 0, otherBodies: 0 });
    const shape = group("shape", entries);
    expect(shape.enabled).toBe(true);
    expect(shape.entries?.map((e) => e.params?.kind)).toEqual(SHAPES.map(([kind]) => kind));
    expect(shape.entries?.every((e) => e.op === "implicit_primitive" && e.enabled)).toBe(true);
    expect(group("transform", entries)).toMatchObject({ enabled: false, why: PICK_BODY });
    expect(group("combine", entries)).toMatchObject({ enabled: false, why: PICK_TWO_IMPLICIT });
  });

  it("transforms one implicit body and combines two or more", () => {
    const one = createEntries({ implicitBodies: 1, otherBodies: 0 });
    expect(group("transform", one).enabled).toBe(true);
    expect(group("transform", one).entries?.map((e) => [e.op, e.params?.kind])).toEqual([
      ["implicit_transform", "translate"],
      ["implicit_transform", "rotate"],
      ["implicit_transform", "mirror"],
      ["implicit_transform", "scale"],
    ]);
    // One body: the combine group opens for Split only, and the pattern and modify tools work.
    const combineOne = group("combine", one);
    expect(combineOne.enabled).toBe(true);
    expect(combineOne.entries?.filter((e) => e.enabled).map((e) => e.op)).toEqual(["implicit_split"]);
    expect(group("pattern", one).entries?.map((e) => [e.op, e.params?.kind, e.enabled])).toEqual([
      ["implicit_pattern", "linear", true],
      ["implicit_pattern", "polar", true],
    ]);
    expect(group("modify", one).entries?.map((e) => [e.op, e.params?.kind, e.enabled])).toEqual([
      ["implicit_offset", "offset", true],
      ["implicit_offset", "shell", true],
      ["implicit_lattice", "gyroid", true],
    ]);
    const two = createEntries({ implicitBodies: 2, otherBodies: 0 });
    expect(group("combine", two).enabled).toBe(true);
    expect(group("combine", two).entries?.map((e) => e.params?.op ?? e.op)).toEqual(["union", "difference", "intersection", "implicit_split"]);
    expect(group("combine", two).entries?.find((e) => e.op === "implicit_split")?.enabled).toBe(false);
    expect(group("pattern", two)).toMatchObject({ enabled: false });
    expect(group("transform", two)).toMatchObject({ enabled: false });
  });

  it("refuses imported bodies in a transform or a combine, and says why", () => {
    const mixed = createEntries({ implicitBodies: 1, otherBodies: 1 });
    expect(group("transform", mixed)).toMatchObject({ enabled: false, why: MIXED_TRANSFORM });
    expect(group("combine", mixed)).toMatchObject({ enabled: false, why: NOT_IMPLICIT });
    expect(group("modify", mixed)).toMatchObject({ enabled: false, why: NOT_IMPLICIT });
    expect(group("shape", mixed).enabled).toBe(true); // a shape reads no part
  });

  it("transforms imported B-rep bodies with the B-rep transform, under the same tools", () => {
    const one = group("transform", createEntries({ implicitBodies: 0, otherBodies: 1 }));
    expect(one.enabled).toBe(true);
    expect(one.entries?.map((e) => [e.id, e.label, e.op, e.params?.kind, e.enabled])).toEqual([
      ["transform-translate", "Move", "transform", "translate", true],
      ["transform-rotate", "Rotate", "transform", "rotate", true],
      ["transform-mirror", "Mirror", "transform", "mirror", true],
      ["transform-scale", "Scale", "transform", "scale", true],
    ]);
    // Several imported bodies turn together, one output each; Combine still wants modeller bodies.
    const three = createEntries({ implicitBodies: 0, otherBodies: 3 });
    expect(group("transform", three).enabled).toBe(true);
    expect(group("combine", three)).toMatchObject({ enabled: false, why: NOT_IMPLICIT });
  });

  it("refuses a mesh-only body in a transform and says what to do", () => {
    const mesh = group("transform", createEntries({ implicitBodies: 0, otherBodies: 2, meshBodies: 1 }));
    expect(mesh).toMatchObject({ enabled: false, why: MESH_TRANSFORM });
    expect(transformFor(0, 1, 0)).toEqual({ op: "transform", why: null });
    expect(transformFor(1, 0, 0)).toEqual({ op: "implicit_transform", why: null });
    expect(transformFor(2, 0, 0).why).toContain("pick just one");
  });

  it("refuses the B-rep transform when the server has not built it", () => {
    const absent = group("transform", createEntries({ implicitBodies: 0, otherBodies: 1, built: { transform: false } }));
    expect(absent).toMatchObject({ enabled: false, why: NOT_BUILT });
  });

  it("refuses a type the server has not built", () => {
    const entries = createEntries({ implicitBodies: 0, otherBodies: 0, built: { implicit_primitive: false } });
    expect(group("shape", entries)).toMatchObject({ enabled: false, why: NOT_BUILT });
  });
});

describe("implicitShows", () => {
  it("shows a primitive only the parameters its kind reads", () => {
    const shown = (kind: string) =>
      ["size", "radius", "length", "top_radius", "tube_radius", "corner_radius", "position", "axis", "normal", "auto_run", "inputs"].filter((name) =>
        implicitShows("implicit_primitive", { kind }, name),
      );
    expect(shown("sphere")).toEqual(["radius", "position", "auto_run"]);
    expect(shown("box")).toEqual(["size", "position", "auto_run"]);
    expect(shown("cone")).toEqual(["radius", "length", "top_radius", "position", "axis", "auto_run"]);
    expect(shown("torus")).toEqual(["radius", "tube_radius", "position", "axis", "auto_run"]);
    expect(shown("half_space")).toEqual(["size", "position", "normal", "auto_run"]);
  });

  it("shows a transform, an offset and a pattern the parameters of their kind, and a boolean its blend", () => {
    expect(implicitShows("implicit_transform", { kind: "translate" }, "offset")).toBe(true);
    expect(implicitShows("implicit_transform", { kind: "translate" }, "angle")).toBe(false);
    expect(implicitShows("implicit_transform", { kind: "rotate" }, "angle")).toBe(true);
    expect(implicitShows("implicit_transform", { kind: "scale" }, "factor")).toBe(true);
    expect(implicitShows("implicit_boolean", { op: "union" }, "blend_radius")).toBe(true);
    expect(implicitShows("implicit_offset", { kind: "offset" }, "outward")).toBe(false);
    expect(implicitShows("implicit_offset", { kind: "shell" }, "outward")).toBe(true);
    expect(implicitShows("implicit_pattern", { kind: "linear" }, "pitch")).toBe(true);
    expect(implicitShows("implicit_pattern", { kind: "linear" }, "centre")).toBe(false);
    expect(implicitShows("implicit_pattern", { kind: "polar" }, "angle")).toBe(true);
    expect(implicitShows("implicit_split", {}, "normal")).toBe(true);
    // The B-rep transform's form shows the same fields per kind, and is titled the same way.
    expect(implicitShows("transform", { kind: "rotate" }, "angle")).toBe(true);
    expect(implicitShows("transform", { kind: "translate" }, "angle")).toBe(false);
    expect(implicitShows("transform", { kind: "mirror" }, "centre")).toBe(true);
    expect(implicitTitle("transform", { kind: "rotate" })).toBe("Rotate");
    expect(implicitTreeValue("transform", { kind: "rotate" }, { angle: "1.5708 rad" })).toBe("Rotate · 90.0002°");
    expect(isImplicitOperation("transform")).toBe(false);
    expect(implicitShows("implicit_boolean", { op: "union" }, "tools")).toBe(true);
  });

  it("hides nothing of another type or of a kind it does not know", () => {
    expect(implicitShows("primitive", { shape: "box" }, "size")).toBe(true);
    expect(implicitShows("implicit_primitive", { kind: "gyroid" }, "radius")).toBe(true);
    expect(isImplicitOperation("implicit_boolean") && !isImplicitOperation("boolean")).toBe(true);
  });
});

describe("operationInputs for implicit operations", () => {
  it("reads no part for a shape, and the first pick minus the rest for a combine", () => {
    expect(operationInputs("implicit_primitive", ["n_1", "n_2"])).toEqual({ inputs: [], tools: [] });
    expect(operationInputs("implicit_boolean", ["n_1", "n_2", "n_3"])).toEqual({ inputs: ["n_1"], tools: ["n_2", "n_3"] });
    expect(operationInputs("implicit_transform", ["n_1"])).toEqual({ inputs: ["n_1"], tools: [] });
  });
});

describe("implicitTreeValue", () => {
  it("says what the operation is in words and the value its kind reads, in mm and degrees", () => {
    expect(implicitTreeValue("implicit_primitive", { kind: "sphere" }, { radius: "8 mm", length: "10 mm" })).toBe("Sphere · 8 mm");
    expect(implicitTreeValue("implicit_primitive", { kind: "box", size: [0.01, 0.01, 0.01] }, { radius: "5 mm" })).toBe("Box");
    expect(implicitTreeValue("implicit_primitive", { kind: "half_space", normal: "+z" }, {})).toBe("Half-space · +Z");
    expect(implicitTreeValue("implicit_transform", { kind: "rotate" }, { angle: "45 deg" })).toBe("Rotate · 45°");
    expect(implicitTreeValue("implicit_boolean", { op: "difference" }, { blend_radius: "0 m" })).toBe("Subtract");
    // A project shown in SI: the tree still counts the modeller in millimetres and degrees (review A5).
    expect(implicitTreeValue("implicit_primitive", { kind: "cylinder" }, { radius: "0.003 m" })).toBe("Cylinder · 3 mm");
    expect(implicitTreeValue("implicit_sketch", { profile: "custom", method: "revolve" }, { angle: "6.28319 rad" })).toBe("Sketch · Revolve · 360°");
    expect(implicitTreeValue("primitive", { shape: "box" }, {})).toBeNull();
  });

  it("converts only SI lengths and angles to the geometry unit", () => {
    expect(geometryDisplay("0.01 m")).toBe("10 mm");
    expect(geometryDisplay("1.5708 rad")).toBe("90.0002 deg");
    expect(geometryDisplay("2e-6 m**2")).toBe("2 mm**2");
    expect(geometryDisplay("12 mm")).toBe("12 mm");
    expect(geometryDisplay("3")).toBe("3");
    expect(prettyGeometry("2e-6 m**2")).toBe("2 mm²");
  });
});

describe("the sketch group and the ribbon's icons", () => {
  it("offers the sketcher and its drawing tools, never a form that draws nothing (review B7)", () => {
    const sketch = group("sketch", createEntries({ implicitBodies: 0, otherBodies: 0 }));
    expect(sketch.enabled).toBe(true);
    // The sketcher first, then its tools: each opens the sketcher with the tool chosen, no card.
    expect(sketch.entries?.[0]).toMatchObject({ id: SKETCHER_ID, enabled: true });
    expect(sketch.entries?.slice(1).map((e) => [e.tool, e.op])).toEqual(SKETCH_TOOLS.map(([tool]) => [tool, undefined]));
    expect(sketch.entries?.some((e) => e.op === "implicit_sketch")).toBe(false);
    const absent = group("sketch", createEntries({ implicitBodies: 0, otherBodies: 0, built: { implicit_sketch: false } }));
    expect(absent.entries?.every((e) => !e.enabled && e.why === NOT_BUILT)).toBe(true);
  });

  it("folds the long Shape group first and the Sketch group last when the ribbon does not fit (review B10)", () => {
    const shown = createEntries({ implicitBodies: 0, otherBodies: 0 }).map((g) => g.id);
    expect(nextToCollapse([], shown)).toBe("shape");
    expect(nextToCollapse(["shape"], shown)).toBe("modify");
    expect(nextToCollapse(COLLAPSE_ORDER.slice(0, -1), shown)).toBe("sketch");
    expect(nextToCollapse([...COLLAPSE_ORDER], shown)).toBeNull();
    // Every group the ribbon shows can fold.
    for (const id of shown) expect(COLLAPSE_ORDER).toContain(id);
  });

  it("gives every tool of every group an icon", () => {
    for (const g of createEntries({ implicitBodies: 2, otherBodies: 0 })) {
      for (const tool of g.entries ?? []) expect(TOOL_ICONS[tool.id], tool.id).toBeDefined();
    }
  });

  it("shows a sketch the parameters of its profile and of its method", () => {
    const shown = (profile: string, method: string) =>
      ["width", "height", "radius", "corner_radius", "slot_length", "sides", "depth", "symmetric", "angle", "axis_distance", "plane", "origin"].filter((name) =>
        implicitShows("implicit_sketch", { profile, method }, name),
      );
    expect(shown("circle", "extrude")).toEqual(["radius", "depth", "symmetric", "plane", "origin"]);
    expect(shown("polygon", "revolve")).toEqual(["radius", "sides", "angle", "axis_distance", "plane", "origin"]);
    expect(shown("rounded_rectangle", "extrude")).toEqual(["width", "height", "corner_radius", "depth", "symmetric", "plane", "origin"]);
    expect(implicitTreeValue("implicit_sketch", { profile: "circle", method: "revolve" }, { radius: "50 mm" })).toBe("Sketch · Revolve · 50 mm");
    // A drawn profile reads none of the named sizes, nor the axis distance; its entities stay in the sketcher.
    expect(shown("custom", "revolve")).toEqual(["angle", "plane", "origin"]);
    expect(implicitShows("implicit_sketch", { profile: "custom" }, "entities")).toBe(false);
  });
});

describe("the cards' words (review B9)", () => {
  it("titles an implicit operation by what it makes or does", () => {
    expect(implicitTitle("implicit_primitive", { kind: "cylinder" })).toBe("Cylinder");
    expect(implicitTitle("implicit_transform", { kind: "translate" })).toBe("Move");
    expect(implicitTitle("implicit_boolean", { op: "difference" })).toBe("Subtract");
    expect(implicitTitle("implicit_sketch", { method: "extrude" })).toBe("Sketch · Pull");
    expect(implicitTitle("implicit_pattern", { kind: "polar" })).toBe("Polar pattern");
    expect(implicitTitle("implicit_split", {})).toBe("Split");
    expect(implicitTitle("boolean", { op: "cut" })).toBeNull();
  });

  it("says a choice in words, never the enum value", () => {
    expect(optionLabel("kind", "rounded_box")).toBe("Rounded box");
    expect(optionLabel("kind", "half_space")).toBe("Half-space");
    expect(optionLabel("op", "difference")).toBe("Subtract");
    expect(optionLabel("profile", "custom")).toBe("Drawn sketch");
    expect(optionLabel("plane", "xy")).toBe("XY plane");
    expect(optionLabel("axis", "z")).toBe("Z");
    expect(optionLabel("normal", "-y")).toBe("-Y");
    expect(optionLabel("anything", "some_value")).toBe("Some value");
  });
});
