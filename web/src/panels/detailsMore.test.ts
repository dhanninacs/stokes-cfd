import { describe, expect, it, vi } from "vitest";
import { detailsMoreItems } from "./detailsMore";

const entry = (items: ReturnType<typeof detailsMoreItems>, id: string) =>
  items.find((item) => item.id === id) as { onSelect?: () => void; description?: string } | undefined;

describe("detailsMoreItems", () => {
  it("offers the path, the id and the log for a node", () => {
    const items = detailsMoreItems({ path: "/regions/fluid", id: "n_1", copy: vi.fn(), openLog: vi.fn() });
    expect(items.map((item) => item.id)).toEqual(["copy-path", "copy-id", "sep-log", "open-log"]);
  });

  it("copies what the row says, and opens the log", () => {
    const copy = vi.fn();
    const openLog = vi.fn();
    const items = detailsMoreItems({ path: "/regions/fluid", id: "n_1", copy, openLog });
    entry(items, "copy-path")?.onSelect?.();
    entry(items, "copy-id")?.onSelect?.();
    entry(items, "open-log")?.onSelect?.();
    expect(copy.mock.calls).toEqual([["/regions/fluid"], ["n_1"]]);
    expect(openLog).toHaveBeenCalledOnce();
    expect(entry(items, "copy-path")?.description).toBe("/regions/fluid");
  });

  it("leaves out what a node does not have, and keeps the log", () => {
    const items = detailsMoreItems({ path: null, id: null, copy: vi.fn(), openLog: vi.fn() });
    expect(items.map((item) => item.id)).toEqual(["open-log"]);
  });
});
