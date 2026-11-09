import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

let state: Record<string, unknown>;
vi.mock("../state/store", () => ({
  STAGES: ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"],
  useStore: Object.assign((select: (current: Record<string, unknown>) => unknown) => select(state), { getState: () => state }),
}));
vi.mock("./meshRun", () => ({ useMeshContext: (select: (current: { busy: boolean }) => unknown) => select({ busy: false }) }));

import SizingPage from "./meshTaskPages/SizingPage";
import SurfacePage from "./meshTaskPages/SurfacePage";
import LayersPage from "./meshTaskPages/LayersPage";

const schema = {
  title: "Mesh settings",
  description: "",
  "x-type": "mesh_settings",
  "x-children": [],
  properties: {
    mesher: { type: "string", title: "Mesher" },
    base_size: { type: "number", title: "Base size" },
    surface_size: { type: "number", title: "Surface size" },
    feature_angle: { type: "number", title: "Feature angle" },
    layers: { type: "integer", title: "Layers" },
    layer_mode: { type: "string", title: "Layer mode", enum: ["final_ratio", "first_height"] },
    expansion_ratio: { type: "number", title: "Expansion ratio" },
    final_ratio: { type: "number", title: "Final layer ratio" },
    first_layer_height: { type: "number", title: "First layer height" },
    min_thickness_ratio: { type: "number", title: "Min thickness ratio" },
  },
};

function setState(mesher: string, connected = true) {
  const settings = {
    id: "settings",
    type: "mesh_settings",
    name: "settings",
    path: "/mesh/settings",
    status: "ok",
    children_count: 0,
    params: {
      mesher,
      base_size: 0.005,
      surface_size: 0,
      feature_angle: 30,
      layers: 0,
      layer_mode: "final_ratio",
      expansion_ratio: 1.2,
      final_ratio: 0.3,
      first_layer_height: 0.001,
      min_thickness_ratio: 0.1,
    },
    children: [],
  };
  const mesh = { id: "mesh", type: "group", name: "mesh", path: "/mesh", status: "ok", children_count: 1, params: {}, children: [settings] };
  state = {
    execute: () => Promise.resolve({ surfaces: [], staging_limit: 1024 }),
    tree: { id: "root", type: "project", name: "project", path: "/", status: "ok", children_count: 1, params: {}, children: [mesh] },
    connected,
    nodeTypes: { mesh_settings: schema },
    mesh: { nodeId: null, step: "", stage: "", at: 0, logs: [] },
    client: null,
  };
}

describe("mesh task setting fields", () => {
  beforeEach(() => setState("snappy"));

  it("hides surface size for blockMesh and shows it for snappyHexMesh", () => {
    setState("blockmesh");
    const block = renderToStaticMarkup(createElement(SizingPage));
    expect(block).toContain("Base size");
    expect(block).not.toContain("Surface size");

    setState("snappy");
    expect(renderToStaticMarkup(createElement(SizingPage))).toContain("Surface size");
  });

  it("renders the surface angle and layer settings from their schemas", () => {
    const surface = renderToStaticMarkup(createElement(SurfacePage));
    expect(surface).toContain("Feature angle");
    const layers = renderToStaticMarkup(createElement(LayersPage));
    expect(layers).toContain("Expansion ratio");
    expect(layers).toContain("Final layer ratio");
    expect(layers).not.toContain('aria-label="First layer height"');
  });

  it("disables schema settings while disconnected", () => {
    setState("snappy", false);
    expect(renderToStaticMarkup(createElement(SizingPage))).toContain("disabled=");
  });
});
