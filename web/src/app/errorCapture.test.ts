import { describe, expect, it, vi } from "vitest";
import { errorReport, installErrorCapture } from "./errorCapture";

describe("the stack of an uncaught error reaches the log", () => {
  it("writes the stack of an error, and nothing for a value that has none", () => {
    const err = new TypeError("Cannot read properties of undefined (reading 'length')");
    expect(errorReport("error", err)).toContain("TypeError: Cannot read properties of undefined");
    expect(errorReport("rejection", err)).toMatch(/^\[stokes\] uncaught promise rejection: /);
    expect(errorReport("error", "a string")).toBeNull();
    expect(errorReport("error", undefined)).toBeNull();
  });

  it("logs each distinct error once", () => {
    const handlers: Record<string, (e: unknown) => void> = {};
    const target = { addEventListener: (name: string, fn: (e: unknown) => void) => (handlers[name] = fn) } as unknown as Pick<Window, "addEventListener">;
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    installErrorCapture(target);
    const err = new TypeError("boom");
    handlers.error?.({ error: err });
    handlers.error?.({ error: err });
    handlers.unhandledrejection?.({ reason: new Error("later") });
    expect(log).toHaveBeenCalledTimes(2);
    log.mockRestore();
  });
});
