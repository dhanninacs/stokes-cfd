import { beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  const scope = globalThis as { window?: unknown };
  scope.window ??= globalThis;
});

import { useStore } from "../state/store";
import { droppedOf, droppedText, toastChange } from "./droppedProfiles";

describe("a kind change that drops time profiles", () => {
  it("reads profiles_dropped, and nothing from an answer without it", () => {
    expect(droppedOf({ profiles_dropped: ["velocity"] })).toEqual(["velocity"]);
    expect(droppedOf({ boundary: { id: "b" } })).toEqual([]);
    expect(droppedOf(null)).toEqual([]);
    expect(droppedOf({ profiles_dropped: "velocity" })).toEqual([]);
  });

  it("names the dropped values", () => {
    expect(droppedText(["velocity"])).toBe("the speed profile was dropped");
    expect(droppedText(["mass_flow", "temperature"])).toBe("the mass flow and temperature profiles were dropped");
  });

  describe("the toast", () => {
    const pushToast = vi.fn();
    const execute = vi.fn();
    const refresh = vi.fn(async () => undefined);
    beforeEach(() => {
      pushToast.mockReset();
      execute.mockReset();
      useStore.setState({ pushToast, execute, refresh } as unknown as Partial<ReturnType<typeof useStore.getState>>);
    });

    it("is the plain one when nothing was dropped", async () => {
      await toastChange({ boundary: {} }, "Condition assigned");
      expect(pushToast).toHaveBeenCalledWith("ok", "Condition assigned");
      expect(execute).not.toHaveBeenCalled();
    });

    it("names the dropped profile, and its Undo steps back while the change is the newest", async () => {
      execute.mockResolvedValue({ undo: "edit boundary inlet", undo_kind: "batch" });
      await toastChange({ profiles_dropped: ["velocity"] }, "Condition assigned");
      expect(pushToast).toHaveBeenCalledTimes(1);
      const [kind, text, action] = pushToast.mock.calls[0] as [string, string, { label: string; run: () => void }];
      expect(kind).toBe("info");
      expect(text).toBe("Condition assigned; the speed profile was dropped");
      expect(action.label).toBe("Undo");
      action.run();
      await vi.waitFor(() => expect(execute).toHaveBeenCalledWith("history.undo"));
    });

    it("does not undo a later change", async () => {
      execute.mockResolvedValueOnce({ undo: "edit boundary inlet", undo_kind: "batch" });
      await toastChange({ profiles_dropped: ["velocity"] }, "Condition assigned");
      execute.mockResolvedValueOnce({ undo: "rename", undo_kind: "rename" });
      const action = (pushToast.mock.calls[0] as [string, string, { run: () => void }])[2];
      action.run();
      await vi.waitFor(() => expect(pushToast).toHaveBeenCalledTimes(2));
      expect(execute).not.toHaveBeenCalledWith("history.undo");
    });
  });
});
