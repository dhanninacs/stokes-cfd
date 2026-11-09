/**
 * The parity guard: every control a guided card offers is also a Basic field of the same node type in the
 * Full-mode Details pane, so the two modes cannot drift apart.
 *
 * The card's controls are read from the card's own source (the kinds its chips assign, the values its inline
 * fields send, the roles it offers) and from the tables the cards import (`INLINE_VALUE`),
 * so a control added to a card without a Basic field fails here. The Mesh and Run cards are read the same way
 * against the mesh settings and solver settings pages.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { BASIC, BODY_ROLES, INLINE_VALUE, DEFAULT_CONTEXT, KIND_WORDS, namesAt, visibleFields } from "./basicSets";
import { SWITCH_LAYERS, FINENESS } from "../guided/sizing";
import { layersFor } from "./meshBasic";
import { BOUNDARY_NAMES, KINDS } from "./kindFixtures";

function source(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

function boundaryBasic(kind: string, params: Record<string, unknown> = {}): string[] {
  const levels = visibleFields("boundary", BOUNDARY_NAMES, { kind, ...params }, DEFAULT_CONTEXT, KINDS);
  return namesAt(levels, "basic", BOUNDARY_NAMES);
}

describe("Boundaries card ⊂ boundary Basic", () => {
  const card = source("../guided/steps/BoundariesCard.tsx");

  it("every kind a chip assigns is a kind Basic offers, with Kind itself a Basic field", () => {
    const assigned = [...card.matchAll(/assign\("(\w+)"/g)].map((m) => m[1]);
    expect(assigned.length).toBeGreaterThanOrEqual(4);
    for (const id of assigned) {
      expect(KINDS.map((k) => k.id)).toContain(id);
      expect(KIND_WORDS[id]).toBeDefined();
      expect(boundaryBasic(id)).toContain("kind");
    }
  });

  it("every value an inline field sends is a Basic field of that kind", () => {
    const sent = [...card.matchAll(/\{ (velocity|pressure): n \}/g)].map((m) => m[1]);
    expect(sent.sort()).toEqual(["pressure", "velocity"]);
    for (const [id, inline] of Object.entries(INLINE_VALUE)) {
      expect(boundaryBasic(id)).toContain(inline.param);
    }
  });

  it("the list's inline edit (INLINE_VALUE) names a Basic field for each kind it covers", () => {
    expect(Object.keys(INLINE_VALUE).sort()).toEqual(["pressure_outlet", "velocity_inlet"]);
    expect(boundaryBasic("velocity_inlet")).toContain("velocity");
    expect(boundaryBasic("pressure_outlet")).toContain("pressure");
  });
});

describe("Geometry card ⊂ body Basic", () => {
  it("every role a chip offers is a role the body page offers", () => {
    const card = source("../guided/steps/GeometryCard.tsx");
    const block = card.slice(card.indexOf("const ROLES"), card.indexOf("];", card.indexOf("const ROLES")));
    const roles = [...block.matchAll(/value: "(\w+)"/g)].map((m) => m[1]);
    expect(roles).toEqual(["fluid", "solid", "left_out"]);
    for (const role of roles) expect(BODY_ROLES.map((r) => r.value)).toContain(role);
  });

  it("the body page's Basic part is its role chips, drawn from the same role list", () => {
    expect(BASIC.part).toEqual(["role"]);
    expect(source("../panels/BodyRoleChips.tsx")).toContain("BODY_ROLES");
  });
});

describe("Fluid card ⊂ region Basic", () => {
  it("the material it sets is the region's Basic field, whichever quick material it picks", () => {
    // The card assigns the material to the fluid body's region (`region.assign`); the region page's field is `material`.
    expect(source("../guided/commands.ts")).toContain('call("region.assign", { material: name');
    const names = ["kind", "tag", "material"];
    expect(namesAt(visibleFields("region", names, {}, DEFAULT_CONTEXT), "basic", names)).toEqual(["material"]);
  });
});

const MESH_NAMES = ["mesher", "base_size", "surface_size", "max_cells", "layers", "layer_mode", "cores", "host"];
const SOLVER_NAMES = ["engine", "turbulence", "energy", "transient", "delta_t", "end_time", "iterations", "residual_target", "cores"];

describe("Mesh card ⊂ mesh settings Basic", () => {
  const card = source("../guided/steps/MeshCard.tsx");
  const snappy = visibleFields("mesh_settings", MESH_NAMES, { mesher: "snappy" }, DEFAULT_CONTEXT);

  it("every setting the card writes (the fineness chips, the layers switch) is a Basic field of the mesh settings", () => {
    const written = [...card.matchAll(/configureMesh\(\{ (\w+):/g)].map((m) => m[1]);
    expect(written.sort()).toEqual(["base_size", "layers"]);
    for (const name of written) {
      expect(BASIC.mesh_settings).toContain(name);
      expect(namesAt(snappy, "basic", MESH_NAMES)).toContain(name);
    }
  });

  it("the card's three fineness choices and its layer count are the page's (one table, guided/sizing.ts)", () => {
    // The card's slider marks Coarse, Medium and Fine from the same table (`FINENESS`, `MARKS` in guided/sizing.ts).
    expect(card).toContain("FinenessSlider");
    expect(source("../guided/steps/FinenessSlider.tsx")).toContain("FINENESS.map");
    expect(FINENESS).toEqual(["coarse", "medium", "fine"]);
    expect(layersFor(true)).toBe(SWITCH_LAYERS);
    const basic = source("../panels/MeshBasic.tsx") + source("./meshBasic.ts");
    expect(basic).toContain("../guided/sizing");
    expect(basic).toContain("configureMesh");
  });

  it("Generate is the same command on the card and on the page", () => {
    expect(card).toContain("generateMesh");
    expect(source("../panels/MeshPanel.tsx")).toContain("generateMesh");
  });
});

describe("Run card ⊂ solver settings Basic", () => {
  const card = source("../guided/steps/RunCard.tsx");
  const steady = visibleFields("solver_settings", SOLVER_NAMES, {}, DEFAULT_CONTEXT);

  it("the one setting the card writes (iterations) is a Basic field of the solver settings", () => {
    const written = [...card.matchAll(/values: \{ (\w+):/g)].map((m) => m[1]);
    expect(written).toEqual(["iterations"]);
    expect(BASIC.solver_settings).toContain("iterations");
    expect(namesAt(steady, "basic", SOLVER_NAMES)).toContain("iterations");
  });

  it("the page adds the flow model to the card's iterations, and nothing the card lacks is Advanced-only for a first run", () => {
    expect(namesAt(steady, "basic", SOLVER_NAMES)).toEqual(["turbulence", "iterations"]);
  });

  it("Run is the same press on the card and on the page", () => {
    expect(card).toContain("pressRun");
    expect(source("../panels/RunPanel.tsx")).toContain("pressRun");
  });
});
