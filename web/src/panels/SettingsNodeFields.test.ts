import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

let storeState: Record<string, unknown>;
vi.mock("../state/store", () => ({
  STAGES: ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"],
  useStore: Object.assign((select: (current: Record<string, unknown>) => unknown) => select(storeState), { getState: () => storeState }),
}));

import SettingsNodeFields from "./SettingsNodeFields";
import type { NodeSummary, NodeTypeSchema } from "../api/types";
import type { SolveEngine } from "./runApi";
import type { MesherInfo } from "./meshPanelModel";

const schema: NodeTypeSchema = {
  title: "Settings",
  description: "",
  "x-type": "settings",
  "x-children": [],
  properties: {
    engine: { type: "string", title: "Engine" },
    turbulence: { type: "string", title: "Turbulence", enum: ["laminar", "kOmegaSST"] },
    energy: { type: "boolean", title: "Energy equation" },
    transient: { type: "boolean", title: "Transient simulation" },
    delta_t: { type: "number", title: "Time step" },
    end_time: { type: "number", title: "End time" },
    initial_intensity: { type: "number", title: "Initial turbulence intensity" },
    iterations: { type: "integer", title: "Iterations" },
    residual_target: { type: "number", title: "Residual target" },
    write_interval: { type: "integer", title: "Write every" },
    host: { type: "string", title: "Host" },
    cores: { type: "integer", title: "Cores" },
    slice_origin: { type: "array", title: "Slice origin" },
    slice_normal: { type: "array", title: "Slice normal" },
    mesher: { type: "string", title: "Mesher" },
    domain: { type: "string", title: "Domain", enum: ["part", "box"] },
    domain_part: { type: "string", title: "Domain part" },
    box: { type: "array", title: "Box" },
    margin: { type: "number", title: "Margin" },
    base_size: { type: "number", title: "Base size" },
    surface_size: { type: "number", title: "Surface size" },
    max_cells: { type: "integer", title: "Max cells" },
    feature_angle: { type: "number", title: "Feature angle" },
    layers: { type: "integer", title: "Layers" },
    layer_mode: { type: "string", title: "Layer mode", enum: ["final_ratio", "first_height"] },
    expansion_ratio: { type: "number", title: "Expansion ratio" },
    final_ratio: { type: "number", title: "Final layer ratio" },
    first_layer_height: { type: "number", title: "First layer height" },
    min_thickness_ratio: { type: "number", title: "Min thickness ratio" },
    layer_feature_angle: { type: "number", title: "Layer feature angle" },
    max_non_ortho: { type: "number", title: "Max non-orthogonality" },
    max_skewness: { type: "number", title: "Max skewness" },
    accept_unassigned: { type: "boolean", title: "Accept unassigned faces" },
  },
};
const solverSchema: NodeTypeSchema = {
  ...schema,
  properties: Object.fromEntries(["engine", "turbulence", "energy", "transient", "delta_t", "end_time", "initial_intensity", "iterations", "residual_target", "write_interval", "host", "cores", "slice_origin", "slice_normal"].map((name) => [name, schema.properties[name]])),
};
const meshSchema: NodeTypeSchema = {
  ...schema,
  properties: Object.fromEntries(["mesher", "domain", "domain_part", "box", "margin", "base_size", "surface_size", "max_cells", "feature_angle", "layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height", "min_thickness_ratio", "layer_feature_angle", "max_non_ortho", "max_skewness", "accept_unassigned", "host", "cores"].map((name) => [name, schema.properties[name]])),
};

const node = (type: string, params: Record<string, unknown>): NodeSummary => ({
  id: "settings",
  type,
  name: "Settings",
  path: `/${type}`,
  status: "ok",
  params,
  children_count: 0,
});

const engines: SolveEngine[] = [
  { id: "solver-a", label: "Solver A", built: true, turbulence: ["laminar"], why: "" },
  { id: "solver-b", label: "External Solver", built: false, turbulence: [], why: "Not installed" },
];
const meshers: MesherInfo[] = [
  { id: "snappy", label: "snappyHexMesh", built: true, domains: ["part", "box"], why: "", outputStructures: [], title: "" },
  { id: "blocked", label: "External mesher", built: false, domains: ["part"], why: "Not installed", outputStructures: [], title: "" },
];

function markup(props: Parameters<typeof SettingsNodeFields>[0]): string {
  return renderToStaticMarkup(createElement(SettingsNodeFields, props));
}

describe("SettingsNodeFields server render", () => {
  beforeEach(() => {
    storeState = { tree: null, units: null, project: null };
  });

  it("starts with essential sections open and immediately reveals rejected advanced settings", () => {
    const props = { node: node("solver_settings", { cores: 2 }), schema: solverSchema, mode: "solver" as const, onCommit: () => undefined };
    const html = markup(props);
    expect(html).toContain('aria-label="Search settings"');
    expect(html).toMatch(/<details[^>]*open=""[^>]*><summary[^>]*><span[^>]*>Model/);
    expect(html).toMatch(/<details class="settings-fields__section"><summary[^>]*><span[^>]*>Execution/);
    const rejected = markup({ ...props, errors: { cores: "Invalid process count" } });
    expect(rejected).toMatch(/<details[^>]*open=""[^>]*><summary[^>]*><span[^>]*>Execution/);
    expect(rejected).toContain("Invalid process count");
  });

  it("preserves catalog unavailable states and current solver values", () => {
    const html = markup({
      node: node("solver_settings", { engine: "legacy-solver", turbulence: "legacy-model", cores: 2 }),
      schema: solverSchema,
      mode: "solver",
      engines,
      enginesLoaded: true,
      onCommit: () => undefined,
    });
    expect(html).toContain("External Solver is not available: Not installed");
    expect(html).toContain("Current engine unavailable");
    expect(html).toContain("legacy-model");
    expect(html).toContain("disabled=");
  });

  it("keeps all mesher catalogue entries visible and retains an unavailable current mesher", () => {
    const html = markup({
      node: node("mesh_settings", { mesher: "old-mesher", domain: "part", layer_mode: "final_ratio", cores: 3 }),
      schema: meshSchema,
      mode: "mesh",
      meshers,
      meshersLoaded: true,
      onCommit: () => undefined,
    });
    expect(html).toContain("snappyHexMesh");
    expect(html).toContain('<select aria-label="Mesher"');
    expect(html).not.toContain('role="radiogroup"');
    expect(html).toMatch(/<option[^>]*disabled=""[^>]*selected=""/);
    expect(html).toContain("External mesher is not available: Not installed");
    expect(html).toContain("Current mesher unavailable for this domain");
  });

  it("locks custom catalog and box controls while leaving solver and mesh cores editable in collapsed sections", () => {
    const solverHtml = markup({
      node: node("solver_settings", { engine: "solver-a", turbulence: "laminar", cores: 2 }),
      schema: solverSchema,
      mode: "solver",
      engines,
      enginesLoaded: true,
      disabled: true,
      onCommit: () => undefined,
    });
    expect(solverHtml).toContain('class="settings-fields__section"');
    // The section's sentence is the summary's tooltip, not a paragraph under it.
    expect(solverHtml).toContain('<summary class="settings-fields__summary" title="1 runs serially; larger counts use MPI."');
    expect(solverHtml).toContain('<span class="settings-fields__title">Execution</span>');
    expect(solverHtml).not.toContain("settings-fields__description");
    expect(solverHtml).toContain('aria-label="Solver engine"');
    expect(solverHtml).toContain('aria-label="Cores"');
    const solverCore = solverHtml.match(/<input[^>]*aria-label="Cores"[^>]*>/)?.[0] ?? "";
    expect(solverCore).toContain("disabled=");

    const meshHtml = markup({
      node: node("mesh_settings", { mesher: "snappy", domain: "box", box: [], layer_mode: "final_ratio", cores: 3 }),
      schema: meshSchema,
      mode: "mesh",
      meshers,
      meshersLoaded: true,
      disabled: true,
      onCommit: () => undefined,
    });
    expect(meshHtml).toContain('aria-label="Mesher"');
    expect(meshHtml).toContain('aria-label="Box x min in m"');
    expect(meshHtml).toContain('aria-label="Cores"');
    expect(meshHtml).toContain('<span class="settings-fields__title">Execution</span>');
    expect(meshHtml).toContain('aria-label="Box x min in m" aria-invalid="false" disabled=""');
    const editableSolver = markup({ node: node("solver_settings", { engine: "solver-a", turbulence: "laminar", cores: 2 }), schema: solverSchema, mode: "solver", onCommit: () => undefined });
    const editableMesh = markup({ node: node("mesh_settings", { mesher: "snappy", domain: "box", box: [], layer_mode: "final_ratio", cores: 3 }), schema: meshSchema, mode: "mesh", onCommit: () => undefined });
    expect(editableSolver.match(/<input[^>]*aria-label="Cores"[^>]*>/)?.[0] ?? "").not.toContain("disabled=");
    expect(editableMesh.match(/<input[^>]*aria-label="Cores"[^>]*>/)?.[0] ?? "").not.toContain("disabled=");
  });

  it("conditionally renders layer mode fields and retains errors in a collapsed section", () => {
    const html = markup({
      node: node("mesh_settings", { mesher: "snappy", domain: "part", layer_mode: "final_ratio", cores: 3 }),
      schema: meshSchema,
      mode: "mesh",
      meshers,
      meshersLoaded: true,
      errors: { first_layer_height: "Rejected height", cores: "Rejected core count" },
      onCommit: () => undefined,
    });
    expect(html).toContain("Final layer ratio");
    expect(html).not.toContain('aria-label="First layer height"');
    expect(html).not.toContain("Rejected height");
    expect(html).toContain("Rejected core count");
    expect(html).toContain("Needs attention");

    const visibleAgain = markup({
      node: node("mesh_settings", { mesher: "snappy", domain: "part", layer_mode: "first_height", cores: 3 }),
      schema: meshSchema,
      mode: "mesh",
      meshers,
      meshersLoaded: true,
      errors: { first_layer_height: "Rejected height" },
      onCommit: () => undefined,
    });
    expect(visibleAgain).toContain("Rejected height");
  });

  describe("Basic layout", () => {
    const solverProps = { node: node("solver_settings", { engine: "solver-a", turbulence: "laminar", cores: 2 }), schema: solverSchema, mode: "solver" as const, layout: "basic" as const, onCommit: () => undefined };

    it("solver: Basic is the flow model and the iterations, then a closed Advanced holding the rest", () => {
      const html = markup(solverProps);
      const basic = html.slice(0, html.indexOf('data-ws="advanced"'));
      expect(basic).toContain("Flow model");
      expect(basic).toContain("Iterations");
      expect(basic).not.toContain("Residual target");
      expect(basic).not.toContain("Execution");
      expect(html).toMatch(/<details class="basic-advanced" data-ws="advanced" data-type="solver_settings">/);
      expect(html).toContain("Residual target");
    });

    it("solver: a steady laminar run has no transient or initial-turbulence field anywhere; transient and turbulence bring them", () => {
      const steady = markup(solverProps);
      for (const word of ["Time step", "End time", "Initial turbulence intensity", "Initial turbulence"]) expect(steady).not.toContain(word);
      const transient = markup({ ...solverProps, node: node("solver_settings", { turbulence: "laminar", transient: true }) });
      expect(transient).toContain("Time step");
      expect(transient).toContain("End time");
      const turbulent = markup({ ...solverProps, node: node("solver_settings", { turbulence: "kOmegaSST" }) });
      expect(turbulent).toContain("Initial turbulence intensity");
      expect(turbulent).not.toContain("Time step");
    });

    it("Advanced has the search and every section listed, without the category select or Show all", () => {
      const advanced = markup(solverProps).slice(markup(solverProps).indexOf('data-ws="advanced"'));
      expect(advanced).toContain('aria-label="Search settings"');
      expect(advanced).not.toContain("Settings category");
      expect(advanced).not.toContain("Show all sections");
      // Every section is listed (none hidden behind a picked category).
      expect(advanced).toContain("Run limits");
      expect(advanced).not.toContain("hidden=\"\"");
      // The flat page keeps its tool bar.
      const flat = markup({ ...solverProps, layout: "flat" });
      expect(flat).toContain("Settings category");
      expect(flat).toContain("Show all sections");
    });

    it("solver: the flat layout hides the same fields (the rule is shared)", () => {
      const html = markup({ ...solverProps, layout: "flat" });
      expect(html).not.toContain("Time step");
      expect(html).toContain("Transient simulation");
    });

    it("mesh: the page's own Basic controls come first, then Advanced with the exact size and layer count", () => {
      const html = markup({
        node: node("mesh_settings", { mesher: "snappy", domain: "part", layer_mode: "final_ratio" }),
        schema: meshSchema,
        mode: "mesh",
        layout: "basic",
        basicContent: createElement("div", { "data-ws": "mesh-basic-stub" }, "Coarse Medium Fine"),
        onCommit: () => undefined,
      });
      expect(html.indexOf("mesh-basic-stub")).toBeLessThan(html.indexOf('data-ws="advanced"'));
      const advanced = html.slice(html.indexOf('data-ws="advanced"'));
      expect(advanced).toContain("Base size");
      expect(advanced).toContain("Layers");
    });

    it("mesh: a task page that draws a field leaves it out of Advanced", () => {
      const html = markup({
        node: node("mesh_settings", { mesher: "snappy", domain: "part", layer_mode: "final_ratio" }),
        schema: meshSchema,
        mode: "mesh",
        layout: "basic",
        hideInAdvanced: ["base_size", "surface_size"],
        onCommit: () => undefined,
      });
      expect(html).not.toContain("Base size");
      expect(html).not.toContain("Surface size");
      expect(html).toContain("Max cells");
    });

    it("an error on an Advanced field is drawn inside Advanced, and the section says it needs attention", () => {
      // The disclosure itself opens in an effect (BasicAdvanced `hasError`), which a static render does not run.
      const html = markup({ ...solverProps, errors: { cores: "Rejected core count" } });
      const advanced = html.slice(html.indexOf('data-ws="advanced"'));
      expect(advanced).toContain("Rejected core count");
      expect(advanced).toContain("Needs attention");
      expect(html.slice(0, html.indexOf('data-ws="advanced"'))).not.toContain("Rejected core count");
    });
  });
});
