/**
 * "A material on a body makes it the mesh domain": the command sequence the Material menu sends, run
 * against a scripted client that records every call and answers like the server does.
 */
import { describe, expect, it } from "vitest";
import { assignMaterial, domainPartOf, fluidRoleCall, roleAfterAssign } from "./materialPick";

interface Call {
  method: string;
  params: Record<string, unknown>;
}

/** A client that answers `region.assign` with a region of `kind` and refuses the methods in `refuse`. */
function scripted(kind: string, refuse: string[] = []) {
  const calls: Call[] = [];
  const execute = async (method: string, params: Record<string, unknown> = {}): Promise<unknown> => {
    calls.push({ method, params });
    if (refuse.includes(method)) throw new Error(`refused ${method}`);
    if (method === "region.assign") return { region: { id: "n_r", kind }, bodies: [], moved: [], created: [] };
    return {};
  };
  return { calls, execute };
}

const one = [{ part: "n_pipe", faces: "all" as const }];
const many = [
  { part: "n_a", faces: "all" as const },
  { part: "n_b", faces: "all" as const },
];

describe("assignMaterial", () => {
  it("assigns the material and then makes the body the fluid, in that order", async () => {
    const client = scripted("fluid");
    const outcome = await assignMaterial(client.execute, "water", one, null, "fluid");
    expect(client.calls).toEqual([
      { method: "region.assign", params: { material: "water", target: one } },
      { method: "mesh.body_role", params: { part: "n_pipe", role: "fluid" } },
    ]);
    expect(outcome).toEqual({ madeFluid: true, roleError: "" });
  });

  it("does nothing more when the body already is the domain", async () => {
    const client = scripted("fluid");
    const outcome = await assignMaterial(client.execute, "water", one, "n_pipe", "fluid");
    expect(client.calls.map((c) => c.method)).toEqual(["region.assign"]);
    expect(outcome.madeFluid).toBe(false);
  });

  it("moves the fluid role to a different body that is given a fluid material", async () => {
    const client = scripted("fluid");
    await assignMaterial(client.execute, "air", one, "n_other", "fluid");
    expect(client.calls[1]).toEqual({ method: "mesh.body_role", params: { part: "n_pipe", role: "fluid" } });
  });

  it("leaves a solid material alone: copper does not make its body the domain", async () => {
    const client = scripted("solid");
    const outcome = await assignMaterial(client.execute, "copper", one, null, "solid");
    expect(client.calls.map((c) => c.method)).toEqual(["region.assign"]);
    expect(outcome.madeFluid).toBe(false);
  });

  it("sends no role for several bodies, since the fluid is one body", async () => {
    const client = scripted("fluid");
    await assignMaterial(client.execute, "water", many, null, "fluid");
    expect(client.calls.map((c) => c.method)).toEqual(["region.assign"]);
  });

  it("keeps the assignment and reports the refusal when the role call fails", async () => {
    const client = scripted("fluid", ["mesh.body_role"]);
    const outcome = await assignMaterial(client.execute, "water", one, null, "fluid");
    expect(outcome).toEqual({ madeFluid: false, roleError: "refused mesh.body_role" });
    expect(client.calls.map((c) => c.method)).toEqual(["region.assign", "mesh.body_role"]);
  });

  it("does not swallow a refused assignment: no role is sent after it", async () => {
    const client = scripted("fluid", ["region.assign"]);
    await expect(assignMaterial(client.execute, "water", one, null, "fluid")).rejects.toThrow("refused region.assign");
    expect(client.calls.map((c) => c.method)).toEqual(["region.assign"]);
  });
});

describe("the library path", () => {
  it("gives the role after the add and the assign, from the reply's region kind", async () => {
    const client = scripted("fluid");
    const assigned = await client.execute("region.assign", { material: "water", target: one });
    const outcome = await roleAfterAssign(client.execute, assigned, one, null, "");
    expect(outcome.madeFluid).toBe(true);
    expect(client.calls.at(-1)).toEqual({ method: "mesh.body_role", params: { part: "n_pipe", role: "fluid" } });
  });
});

describe("fluidRoleCall", () => {
  it("falls back to the material's kind when the reply carries no region kind", () => {
    expect(fluidRoleCall({}, one, null, "fluid")).toEqual({ method: "mesh.body_role", params: { part: "n_pipe", role: "fluid" } });
    expect(fluidRoleCall({}, one, null, "solid")).toBeNull();
    expect(fluidRoleCall(null, one, null, "")).toBeNull();
  });

  it("counts the same body twice as one body", () => {
    expect(fluidRoleCall({ region: { kind: "fluid" } }, [one[0], one[0]], null, "")).not.toBeNull();
  });
});

describe("domainPartOf", () => {
  const tree = {
    path: "/",
    children: [
      { path: "/geometry", children: [] },
      { path: "/mesh", children: [{ path: "/mesh/settings", params: { domain_part: "n_pipe" } }] },
    ],
  };
  it("reads the part the mesh settings name", () => {
    expect(domainPartOf(tree)).toBe("n_pipe");
  });
  it("is null with no tree, no settings, or an empty domain part", () => {
    expect(domainPartOf(null)).toBeNull();
    expect(domainPartOf({ path: "/", children: [] })).toBeNull();
    expect(domainPartOf({ path: "/mesh/settings", params: { domain_part: null } })).toBeNull();
  });
});
