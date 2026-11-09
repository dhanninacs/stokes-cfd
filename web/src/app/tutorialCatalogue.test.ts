/**
 * The Tutorials card's states, driven by a mocked `execute`.
 *
 * **Why this needs a test.** "No folder yet" is the normal first state, and it arrives as an error
 * (`tutorial.no_root`); if it were treated as a failure the first screen a new user sees would show
 * a red message. The other half: the status words are the server's, shown as they came.
 */
import { describe, expect, it, vi } from "vitest";
import { categoriesOf, loadCatalogue, openTutorial, setRoot, statusTone, statusWord, totalOf, type Execute } from "./tutorialCatalogue";

const reply = {
  root: "/synthetic/tutorials",
  probed: true,
  totals: { ready: 1, missing_module: 1, unverified: 0 },
  counts: { ready: 1, missing_module: 0, unverified: 0 },
  cases: [
    { id: "fluid/box", category: "fluid", path: "/x", application: "foamRun", solver: "fluid", regions: null, recipe_kind: "default", status: "ready", why: "" },
    { id: "solid/slab", category: "solid", path: "/y", application: "foamRun", solver: "solid", regions: null, recipe_kind: "allrun", status: "missing_module", why: "no solid module" },
  ],
};

describe("loadCatalogue", () => {
  it("reads no configured folder as the empty state, not a failure", async () => {
    const execute = vi.fn<Execute>().mockRejectedValue(Object.assign(new Error("no tutorial folder"), { code: "tutorial.no_root" }));
    expect(await loadCatalogue(execute)).toEqual({ kind: "none" });
    expect(execute).toHaveBeenCalledWith("tutorial.catalogue", {}, { silent: true });
  });

  it("reports any other refusal inline with the server's sentence", async () => {
    const execute = vi.fn<Execute>().mockRejectedValue(Object.assign(new Error("the folder is gone"), { code: "tutorial.root_missing" }));
    expect(await loadCatalogue(execute)).toEqual({ kind: "error", message: "the folder is gone" });
  });

  it("lists the configured folder and passes the filter through", async () => {
    const execute = vi.fn<Execute>().mockResolvedValue(reply);
    const state = await loadCatalogue(execute, { category: "fluid", text: "  box " });
    expect(execute).toHaveBeenCalledWith("tutorial.catalogue", { category: "fluid", text: "box" }, { silent: true });
    expect(state.kind).toBe("listed");
    if (state.kind !== "listed") return;
    expect(state.reply.cases.map((c) => c.status)).toEqual(["ready", "missing_module"]);
    expect(totalOf(state.reply.totals)).toBe(2);
    expect(totalOf(state.reply.counts)).toBe(1);
    expect(categoriesOf(state.reply)).toEqual(["fluid", "solid"]);
  });

  it("drops malformed rows rather than guessing", async () => {
    const execute = vi.fn<Execute>().mockResolvedValue({ root: "/r", probed: false, totals: {}, counts: {}, cases: [{ id: 3 }, null] });
    const state = await loadCatalogue(execute);
    expect(state).toEqual({ kind: "listed", reply: { root: "/r", probed: false, totals: {}, counts: {}, cases: [] } });
  });
});

describe("setRoot", () => {
  it("sends only the picked path and returns the refusal", async () => {
    const ok = vi.fn<Execute>().mockResolvedValue({});
    expect(await setRoot(ok, "/picked")).toBeNull();
    expect(ok).toHaveBeenCalledWith("tutorial.set_root", { path: "/picked" }, { silent: true });
    const refused = vi.fn<Execute>().mockRejectedValue(new Error("the folder holds no case"));
    expect(await setRoot(refused, "/empty")).toBe("the folder holds no case");
  });
});

describe("status", () => {
  it("keeps the server's word and gives each kind a dot", () => {
    expect(statusWord("missing_module")).toBe("missing module");
    expect(statusTone("ready")).toBe("ok");
    expect(statusTone("unverified")).toBe("muted");
    expect(statusTone("needs_recipe")).toBe("warn");
  });
});

describe("openTutorial", () => {
  it("asks for one tutorial and names the project it opened", async () => {
    const execute = vi.fn<Execute>().mockResolvedValue({ project: { name: "cavity_2" }, case: { status: "ready", refusals: [] } });
    expect(await openTutorial(execute, "incompressibleFluid/cavity")).toEqual({ ok: true, text: "Opened cavity in project cavity_2" });
    expect(execute).toHaveBeenCalledWith("tutorial.open", { tutorial: "incompressibleFluid/cavity" }, { silent: true });
  });

  it("says why a refused case cannot be opened, and closes the empty project again", async () => {
    const execute = vi.fn<Execute>().mockResolvedValue({
      project: { name: "coded" },
      case: { status: "refused", refusals: [{ file: "system/fvSolution", line: 3, why: "#codeStream runs code" }] },
    });
    const outcome = await openTutorial(execute, "x/coded");
    expect(outcome.ok).toBe(false);
    expect(outcome.text).toBe("coded cannot be opened (system/fvSolution:3): #codeStream runs code");
    expect(execute).toHaveBeenCalledWith("project.close", {}, { silent: true });
  });

  it("reports the server's sentence when nothing opened", async () => {
    const execute = vi.fn<Execute>().mockRejectedValue(new Error("no tutorial folder"));
    expect(await openTutorial(execute, "a/b")).toEqual({ ok: false, text: "no tutorial folder" });
  });
});
