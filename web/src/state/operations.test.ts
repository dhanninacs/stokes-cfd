/**
 * The operation helpers (T-029): the `operation.types` catalogue, the run state the
 * `geometry.op_*` events build, the card's initial form, the read-only line a `json` parameter
 * gets, and the seed a picked point makes. No DOM and no store (vitest runs in Node).
 */
import { describe, expect, it } from "vitest";
import type { NodeTypeSchema, UnitsSchema } from "../api/types";
import {
  dropOperation,
  initialValues,
  latestOperation,
  noteOperationProgress,
  operationCreatePayload,
  operationInputs,
  operationJsonText,
  operationText,
  ownParams,
  readOperationEvent,
  readOperationTypes,
  seedAt,
  type OperationProgress,
} from "./operations";

const UNITS: UnitsSchema = {
  unit_systems: ["si", "mm"],
  dimensions: { length: { si_unit: "m", display: { si: "m", mm: "mm" } } },
};

describe("operation.types", () => {
  it("reads the catalogue by type, with its schema", () => {
    const types = readOperationTypes({
      types: [
        {
          type: "enclosure",
          label: "Enclosure",
          built: true,
          min_inputs: 1,
          part_params: [],
          tag_params: [],
          schema: { title: "Enclosure", description: "a box", "x-type": "enclosure", "x-children": [], properties: { shape: { type: "string", enum: ["box"] } } },
        },
      ],
    });
    expect(Object.keys(types)).toEqual(["enclosure"]);
    expect(types.enclosure.built).toBe(true);
    expect(types.enclosure.schema.properties.shape.enum).toEqual(["box"]);
  });

  it("drops entries without a type and survives a payload that is not one of ours", () => {
    expect(readOperationTypes(null)).toEqual({});
    expect(readOperationTypes({ types: [{ label: "no type" }] })).toEqual({});
    const types = readOperationTypes({ types: [{ type: "heal" }] });
    expect(types.heal).toMatchObject({ label: "heal", built: false, min_inputs: 1, part_params: [] });
    expect(types.heal.schema.properties).toEqual({});
  });
});

describe("the op_* events", () => {
  it("reads a progress event, clamping its fraction", () => {
    expect(readOperationEvent("geometry.op_progress", { node_id: "n1", stage: "build", fraction: 2, message: "cutting" })).toEqual({
      kind: "progress",
      nodeId: "n1",
      stage: "build",
      fraction: 1,
      message: "cutting",
    });
    expect(readOperationEvent("geometry.op_progress", { node_id: "n1" })).toEqual({ kind: "progress", nodeId: "n1", stage: "", fraction: null, message: "" });
  });

  it("reads done and failed, and ignores another event", () => {
    expect(readOperationEvent("geometry.op_done", { node_id: "n1", outputs: ["n2", "n3"], seconds: 1.5 })).toEqual({ kind: "done", nodeId: "n1", outputs: ["n2", "n3"], seconds: 1.5 });
    expect(readOperationEvent("geometry.op_failed", { node_id: "n1", code: "geometry.cancelled", message: "the operation was cancelled" })).toEqual({
      kind: "failed",
      nodeId: "n1",
      code: "geometry.cancelled",
      message: "the operation was cancelled",
    });
    expect(readOperationEvent("geometry.op_failed", { node_id: "n1" })).toMatchObject({ kind: "failed", code: "geometry.op_failed" });
    expect(readOperationEvent("geometry.op_progress", {})).toBeNull();
    expect(readOperationEvent("geometry.part_ready", { node_id: "n1" })).toBeNull();
  });
});

describe("the runs under way", () => {
  const progress = (nodeId: string, fraction: number, at: number): OperationProgress => ({ nodeId, label: "Enclosure", stage: "build", fraction, message: "cutting 1 body", at });

  it("keeps the newest event of each run, and the label the tree gave it", () => {
    const first = noteOperationProgress({}, { nodeId: "n1", stage: "build", fraction: 0.2, message: "measuring" }, "Enclosure", 10);
    expect(first.n1).toEqual({ nodeId: "n1", label: "Enclosure", stage: "build", fraction: 0.2, message: "measuring", at: 10 });
    const again = noteOperationProgress(first, { nodeId: "n1", stage: "subtract", fraction: 0.6, message: "cutting" }, "", 20);
    expect(again.n1).toMatchObject({ label: "Enclosure", stage: "subtract", fraction: 0.6, at: 20 });
  });

  it("drops a run that ended, and picks the one that spoke last", () => {
    const map = { n1: progress("n1", 0.5, 10), n2: progress("n2", 0.5, 20) };
    expect(latestOperation(map)?.nodeId).toBe("n2");
    expect(dropOperation(map, "n2")).toEqual({ n1: map.n1 });
    expect(dropOperation(map, "n9")).toBe(map);
    expect(latestOperation({})).toBeNull();
  });

  it("writes the line the viewport and the card show", () => {
    expect(operationText(progress("n1", 0.6, 1))).toBe("Enclosure: cutting 1 body 60 %");
    expect(operationText({ ...progress("n1", 0, 1), message: "measuring" })).toBe("Enclosure: measuring…");
    expect(operationText({ ...progress("n1", 1, 1), message: "" })).toBe("Enclosure: 100 %");
    expect(operationText({ ...progress("n1", 0, 1), label: "", message: "" })).toBe("Operation…");
  });
});

describe("the card's form", () => {
  const schema: NodeTypeSchema = {
    title: "Enclosure",
    description: "",
    "x-type": "enclosure",
    "x-children": [],
    properties: {
      shape: { type: "string", enum: ["box", "cylinder"], default: "box" },
      relative: { type: "boolean", default: true },
      margins: { type: "array", items: { type: "number" }, "x-dimension": "length", default: [0, 0, 0, 0, 0, 0] },
      fuzzy: { type: ["string", "number"], "x-kind": "quantity", "x-dimension": "length", default: 0 },
      inputs: { type: "json", "x-kind": "json", default: [] },
      outputs: { type: "json", "x-kind": "json", default: [] },
    },
  };

  it("takes the schema's defaults, the preset on top, and writes the display text of a quantity", () => {
    const form = initialValues(schema, UNITS, { shape: "cylinder" });
    expect(form.values.shape).toBe("cylinder");
    expect(form.values.relative).toBe(true);
    expect(form.values.fuzzy).toBe(0);
    expect(form.displays.fuzzy).toBe("0 m");
    expect(form.displays.margins).toBeUndefined();
    expect("outputs" in form.values).toBe(false);
  });

  it("keeps a quantity's field readable without a dimension to name the unit", () => {
    expect(initialValues(schema, null).displays.fuzzy).toBe("0");
  });

  it("sends the type's own params, never the command's", () => {
    expect(ownParams({ shape: "box", inputs: ["n1"], outputs: ["n2"], auto_run: true })).toEqual({ shape: "box" });
  });

  it("gives a boolean the first body and the rest as tools, and every other type all of them", () => {
    expect(operationInputs("boolean", ["a", "b", "c"])).toEqual({ inputs: ["a"], tools: ["b", "c"] });
    expect(operationInputs("boolean", ["a"])).toEqual({ inputs: ["a"], tools: [] });
    expect(operationInputs("enclosure", ["a", "b"])).toEqual({ inputs: ["a", "b"], tools: [] });
  });
});

describe("a json parameter in words", () => {
  it("counts the faces and the bodies of members", () => {
    expect(operationJsonText([{ part: "n1", faces: [[0, 4], 7] }])).toBe("6 faces of 1 body");
    expect(operationJsonText([{ part: "n1", faces: [[0, 1]] }, { part: "n2", faces: "all" }])).toBe("2 faces of 2 bodies");
  });

  it("counts a list of part ids", () => {
    expect(operationJsonText(["n_3fa9c2d1", "n_11223344"])).toBe("2 parts");
  });

  it("gives up on an empty list, a word list and anything else", () => {
    expect(operationJsonText([])).toBeNull();
    expect(operationJsonText(["x", "y"])).toBeNull();
    expect(operationJsonText("n1")).toBeNull();
    expect(operationJsonText([{ part: 7 }])).toBeNull();
  });
});

describe("the seed of a picked point", () => {
  it("moves the point 1 % of the part's diagonal along the normal", () => {
    expect(seedAt([1, 2, 3], [0, 1, 0], 100)).toEqual([1, 1, 3]);
    expect(seedAt([1, 2, 3], [0, 0, 1], 0)).toEqual([1, 2, 3]);
  });
});

describe("the create payload the card sends", () => {
  /** A type whose schema has a `faces` parameter, as `defeature` ("Remove faces") does. */
  const WITH_FACES: NodeTypeSchema = {
    title: "Remove faces",
    description: "",
    "x-type": "defeature",
    "x-children": [],
    properties: { faces: { title: "Faces", type: "array", "x-kind": "json" }, unify: { title: "Unify", type: "boolean" } },
  };
  /** A type that takes no faces at all, as `enclosure` does. */
  const NO_FACES: NodeTypeSchema = {
    title: "Enclosure",
    description: "",
    "x-type": "enclosure",
    "x-children": [],
    properties: { shape: { title: "Shape", type: "string", enum: ["box"] } },
  };
  const FACES = [
    { part: "p1", faces: [[0, 2]] as Array<number | [number, number]> },
    { part: "p2", faces: [7] as Array<number | [number, number]> },
  ];

  it("sends the picked faces as params.faces when the type has that parameter", () => {
    const out = operationCreatePayload("defeature", { unify: true }, ["p1", "p2"], FACES, WITH_FACES);
    expect(out.params.faces).toEqual(FACES);
    expect(out.inputs).toEqual(["p1", "p2"]);
    expect(out.params.unify).toBe(true);
    // The common keys never ride in `params`: the command takes them as its own arguments.
    expect(out.params.outputs).toBeUndefined();
    expect(out.params.auto_run).toBeUndefined();
    expect(out.params.inputs).toBeUndefined();
  });

  it("leaves faces out of a type that does not take them", () => {
    const out = operationCreatePayload("enclosure", { shape: "box" }, ["p1"], FACES, NO_FACES);
    expect(out.params.faces).toBeUndefined();
    expect(out.params.shape).toBe("box");
    expect(out.inputs).toEqual(["p1"]);
  });

  it("sends nothing for faces when the pick held none", () => {
    const out = operationCreatePayload("defeature", {}, ["p1"], null, WITH_FACES);
    expect(out.params.faces).toBeUndefined();
    const empty = operationCreatePayload("defeature", {}, ["p1"], [], WITH_FACES);
    expect(empty.params.faces).toBeUndefined();
  });

  it("gives a boolean its extra bodies as tools, and only when the type has tools", () => {
    const schema: NodeTypeSchema = {
      title: "Boolean",
      description: "",
      "x-type": "boolean",
      "x-children": [],
      properties: { op: { title: "Op", type: "string" }, tools: { title: "Tools", type: "array", "x-kind": "json" } },
    };
    const out = operationCreatePayload("boolean", { op: "cut" }, ["a", "b", "c"], null, schema);
    expect(out.inputs).toEqual(["a"]);
    expect(out.params.tools).toEqual(["b", "c"]);
  });
});
