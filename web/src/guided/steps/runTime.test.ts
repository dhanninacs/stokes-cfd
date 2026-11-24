import { describe, expect, it } from "vitest";
import type { SetupFinding } from "../../api/types";
import { fixSwitchesTo, secondsInput, timeFindings } from "./runTimeModel";

const steady = (fix: SetupFinding["fix"]): SetupFinding => ({ code: "setup.profile_steady", severity: "warning", message: "inlet's speed varies in time", fix });

describe("the Run card's time", () => {
  it("offers Transient only where the check's fix goes there", () => {
    expect(fixSwitchesTo(steady({ command: "solve.configure", params: { values: { transient: true } } }))).toBe(true);
    // A conjugate case: steady only for now, so the fix goes back to steady or is the boundary's page.
    expect(fixSwitchesTo(steady({ command: "solve.configure", params: { values: { transient: false } } }))).toBe(false);
    expect(fixSwitchesTo(steady({ action: "condition.edit", node: "b1" }))).toBeNull();
    expect(fixSwitchesTo(steady(null))).toBeNull();
    expect(fixSwitchesTo(null)).toBeNull();
  });

  it("finds the steady and short findings", () => {
    const found = timeFindings({ findings: [steady(null), { code: "setup.profile_short", severity: "info", message: "ends early" }] } as never);
    expect(found.steady?.code).toBe("setup.profile_steady");
    expect(found.short).toBe("ends early");
  });

  it("takes seconds or quantity text and refuses what is plainly not a time", () => {
    expect(secondsInput("0.5")).toEqual({ value: 0.5 });
    expect(secondsInput(" 2 [ms] ")).toEqual({ value: "2 [ms]" });
    expect(secondsInput("0")).toEqual({ error: "must be above 0 s" });
    expect(secondsInput("-1")).toEqual({ error: "must be above 0 s" });
    expect(secondsInput("")).toEqual({ error: "needs a value in seconds" });
    // Text the box cannot read goes to the server, whose refusal is shown under the field.
    expect(secondsInput("abc")).toEqual({ value: "abc" });
  });
});
