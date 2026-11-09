/**
 * The pure helpers of `SchemaFields` (T-041), which the component's render loop is built from:
 * which of the panel's field kinds a schema property is, and which nodes of a type the tree holds
 * for a `ref` select. The component itself is presentational; the panel's screenshot pairs cover
 * it.
 */
import { describe, expect, it } from "vitest";
import type { NodeSummary, PropertySchema } from "../api/types";
import { enterStaysInField, fieldKindOf, nodesOfType, numberRefusal, quantityRefusal, vectorRefusal } from "./schemaFields";

describe("fieldKindOf", () => {
  it("lets x-kind decide: quantity, ref, json", () => {
    // The shapes the server's json_schema gives them (stokes.model.schema).
    expect(fieldKindOf({ type: ["string", "number"], "x-kind": "quantity", "x-dimension": "velocity" })).toBe("quantity");
    expect(fieldKindOf({ type: "string", "x-kind": "ref", "x-ref-type": "tag" })).toBe("ref");
    expect(fieldKindOf({ "x-kind": "json", "x-max-bytes": 1048576 })).toBe("json");
  });

  it("gives a string's enum before the bare string type", () => {
    expect(fieldKindOf({ type: "string", enum: ["steady", "transient"] })).toBe("enum");
  });

  it("reads the bare types", () => {
    expect(fieldKindOf({ type: "boolean" })).toBe("bool");
    expect(fieldKindOf({ type: "integer" })).toBe("int");
    expect(fieldKindOf({ type: "number" })).toBe("number");
    expect(fieldKindOf({ type: "string" })).toBe("string");
    expect(fieldKindOf({ type: "string", pattern: "^\\S+$" })).toBe("string");
  });

  it("checks boolean, then integer, then number within a type list", () => {
    expect(fieldKindOf({ type: ["boolean", "null"] })).toBe("bool");
    expect(fieldKindOf({ type: ["integer", "number"] })).toBe("int");
    expect(fieldKindOf({ type: ["string", "number"] })).toBe("number");
  });

  it("is array3 when the default is three numbers, array otherwise", () => {
    const array: PropertySchema = { type: "array", items: { type: "number" } };
    expect(fieldKindOf({ ...array, default: [0.1, 0.2, 0.3] })).toBe("array3");
    expect(fieldKindOf(array)).toBe("array"); // no default at all
    expect(fieldKindOf({ ...array, default: [0.1, 0.2] })).toBe("array");
    expect(fieldKindOf({ ...array, default: [0.1, 0.2, 0.3, 0.4] })).toBe("array");
    expect(fieldKindOf({ ...array, default: [0.1, "x", 0.3] })).toBe("array");
  });

  it("defaults to string when nothing else decides", () => {
    expect(fieldKindOf({})).toBe("string");
  });
});

/** A node summary the way the tree holds it: the required fields, no optional ones. */
function node(id: string, type: string, name: string, path: string, children: NodeSummary[] = []): NodeSummary {
  return { id, type, name, status: "ok", params: {}, path, children_count: children.length, children };
}

describe("nodesOfType", () => {
  // The shape of the demo project: parameters, then groups that hold the nodes of interest.
  const root = node("root", "project", "demo", "/", [
    node("p1", "parameter", "U_in", "/parameters/U_in"),
    node("g1", "group", "parts", "/geometry/parts", [
      node("part1", "part", "pipe", "/geometry/parts/pipe"),
      node("sub", "group", "nested", "/geometry/parts/nested", [node("part2", "part", "flange", "/geometry/parts/nested/flange")]),
    ]),
    node("g2", "group", "tags", "/geometry/tags", [node("tag1", "tag", "inlet", "/geometry/tags/inlet")]),
  ]);

  it("lists the nodes of a type in tree order", () => {
    expect(nodesOfType(root, "part")).toEqual([
      { id: "part1", name: "pipe", path: "/geometry/parts/pipe" },
      { id: "part2", name: "flange", path: "/geometry/parts/nested/flange" },
    ]);
  });

  it("filters out every other type", () => {
    expect(nodesOfType(root, "tag")).toEqual([{ id: "tag1", name: "inlet", path: "/geometry/tags/inlet" }]);
    expect(nodesOfType(root, "boundary")).toEqual([]);
  });

  it("walks depth first, starting at the root itself", () => {
    expect(nodesOfType(root, "group")).toEqual([
      { id: "g1", name: "parts", path: "/geometry/parts" },
      { id: "sub", name: "nested", path: "/geometry/parts/nested" },
      { id: "g2", name: "tags", path: "/geometry/tags" },
    ]);
    expect(nodesOfType(root, "project")).toEqual([{ id: "root", name: "demo", path: "/" }]);
  });

  it("gives no nodes for a null tree", () => {
    expect(nodesOfType(null, "part")).toEqual([]);
  });
});

describe("the refusals that keep an Enter inside the field", () => {
  it("refuses an empty quantity, a non-number, a fraction for an integer and a vector with a blank component", () => {
    expect(quantityRefusal("")).not.toBeNull();
    expect(quantityRefusal("   ")).not.toBeNull();
    expect(quantityRefusal("0.1")).toBeNull();
    expect(quantityRefusal("2*pi")).toBeNull();
    expect(numberRefusal("", false)).toBe("Enter a number.");
    expect(numberRefusal("abc", false)).toBe("Enter a number.");
    expect(numberRefusal("1e999", false)).toBe("Enter a number.");
    expect(numberRefusal("1.5", true)).toBe("Enter a whole number.");
    expect(numberRefusal("3", true)).toBeNull();
    expect(vectorRefusal(["1", "", "3"])).not.toBeNull();
    expect(vectorRefusal(["1", "x", "3"])).not.toBeNull();
    expect(vectorRefusal(["1", "2", "3"])).toBeNull();
  });

  it("an Enter stays in the field only when the text changed and the field refuses it", () => {
    // Clear a value and press Enter: changed, refused, so the card never sees the keypress.
    expect(enterStaysInField(true, quantityRefusal(""))).toBe(true);
    expect(enterStaysInField(true, quantityRefusal("0.1"))).toBe(false);
    // Unchanged text is the field's to leave alone, and a card may commit.
    expect(enterStaysInField(false, quantityRefusal(""))).toBe(false);
  });
});
