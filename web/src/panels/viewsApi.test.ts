import { describe, expect, it, vi } from "vitest";
import { VIEW_COMMANDS, getView, listViews, readSaveReply, readViewList, removeView, saveView, undoRemoveView, type Execute } from "./viewsApi";

function fake(reply: unknown): { execute: Execute; calls: { method: string; params: Record<string, unknown> | undefined; options: unknown }[] } {
  const calls: { method: string; params: Record<string, unknown> | undefined; options: unknown }[] = [];
  const execute: Execute = vi.fn(async (method, params, options) => {
    calls.push({ method, params, options });
    return reply;
  });
  return { execute, calls };
}

describe("view.list", () => {
  it("reads the rows in the order sent and drops one without an id", () => {
    expect(readViewList({ views: [{ id: "n_1", name: "A", created: "2026-11-01T10:00:00+00:00" }, { name: "no id" }, { id: "n_2", name: 5 }, 7, null] })).toEqual([
      { id: "n_1", name: "A", created: "2026-11-01T10:00:00+00:00" },
      { id: "n_2", name: "", created: "" },
    ]);
  });

  it("reads nothing from nonsense", () => {
    expect(readViewList(null)).toEqual([]);
    expect(readViewList({ views: "none" })).toEqual([]);
    expect(readViewList([])).toEqual([]);
  });

  it("asks quietly (a list is not a command the console should repeat)", async () => {
    const { execute, calls } = fake({ views: [{ id: "n_1", name: "A", created: "" }] });
    expect(await listViews(execute)).toEqual([{ id: "n_1", name: "A", created: "" }]);
    expect(calls).toEqual([{ method: VIEW_COMMANDS.list, params: {}, options: { silent: true } }]);
  });
});

describe("view.save", () => {
  it("sends the name and the record as they are", async () => {
    const record = { version: 1, stokes: { map: "viridis" } };
    const { execute, calls } = fake({ id: "n_1", name: "A", created: "2026-11-01T10:00:00+00:00", replaced: true });
    expect(await saveView(execute, "A", record)).toEqual({ id: "n_1", name: "A", created: "2026-11-01T10:00:00+00:00", replaced: true });
    expect(calls).toEqual([{ method: "view.save", params: { name: "A", record }, options: undefined }]);
  });

  it("reads replaced only when it is true", () => {
    expect(readSaveReply({ id: "n_1", name: "A", created: "", replaced: "yes" })?.replaced).toBe(false);
    expect(readSaveReply({ name: "A" })).toBeNull();
    expect(readSaveReply(null)).toBeNull();
  });

  it("rejects an answer it cannot read", async () => {
    await expect(saveView(fake({}).execute, "A", {})).rejects.toThrow(/not understood/);
  });

  it("lets the server's refusal through", async () => {
    const execute: Execute = async () => {
      throw new Error("a view needs a name");
    };
    await expect(saveView(execute, "", {})).rejects.toThrow("a view needs a name");
  });
});

describe("view.get and view.remove", () => {
  it("gets the record by view reference, or null for something that is not one", async () => {
    const record = { version: 1, stokes: {} };
    const { execute, calls } = fake(record);
    expect(await getView(execute, "n_1")).toEqual(record);
    expect(calls[0]).toEqual({ method: "view.get", params: { view: "n_1" }, options: { silent: true } });
    expect(await getView(fake(null).execute, "n_1")).toBeNull();
    expect(await getView(fake([1]).execute, "n_1")).toBeNull();
  });

  it("removes by reference", async () => {
    const { execute, calls } = fake({ id: "n_1", name: "A" });
    await removeView(execute, "n_1");
    expect(calls).toEqual([{ method: "view.remove", params: { view: "n_1" }, options: undefined }]);
  });
});

describe("undoRemoveView", () => {
  it("steps back a removal that is still the newest step", async () => {
    const calls: string[] = [];
    const execute: Execute = async (method) => {
      calls.push(method);
      return method === "history.state" ? { undo_kind: "remove_node" } : {};
    };
    expect(await undoRemoveView(execute)).toBe(true);
    expect(calls).toEqual(["history.state", "history.undo"]);
  });

  it("leaves the stack alone when other changes were made since", async () => {
    const calls: string[] = [];
    const execute: Execute = async (method) => {
      calls.push(method);
      return { undo_kind: "set_params" };
    };
    expect(await undoRemoveView(execute)).toBe(false);
    expect(await undoRemoveView(async () => null)).toBe(false);
    expect(calls).toEqual(["history.state"]);
  });
});
