import { describe, expect, it } from "vitest";
import type { NodeSummary } from "../api/types";
import { drawnSketchOf } from "./drawnSketch";

const node = (id: string, type: string, params: Record<string, unknown>, children: NodeSummary[] = []): NodeSummary =>
  ({ id, type, name: id, status: "ok", params, path: `/${id}`, children_count: children.length, children }) as NodeSummary;

describe("drawnSketchOf", () => {
  const drawn = node("n_s", "implicit_sketch", { profile: "custom", outputs: ["n_p"] });
  const named = node("n_c", "implicit_sketch", { profile: "circle", outputs: ["n_q"] });
  const part = node("n_p", "part", {});
  const other = node("n_q", "part", {});
  const tree = node("root", "project", {}, [node("g", "group", {}, [part, other]), node("ops", "group", {}, [drawn, named])]);

  it("opens a drawn sketch, and the drawn sketch a part was made by", () => {
    expect(drawnSketchOf(tree, drawn)?.id).toBe("n_s");
    expect(drawnSketchOf(tree, part)?.id).toBe("n_s");
  });

  it("opens nothing for a named profile or a part of one", () => {
    expect(drawnSketchOf(tree, named)).toBeNull();
    expect(drawnSketchOf(tree, other)).toBeNull();
    expect(drawnSketchOf(tree, null)).toBeNull();
  });
});
