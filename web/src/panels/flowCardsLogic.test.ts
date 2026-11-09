/**
 * The two flow helpers (ADR-0022 §10.2, T-050).
 *
 * These test the state machine §10.2 describes — inlet → Next → outlet → Finish — and the two rules
 * that would otherwise be discovered from a server error: the openings must be **disjoint** (one face
 * cannot be both an inlet and an outlet), and an empty step is inert rather than advancing with
 * nothing. Also that the External card's switch really governs whether `setup.defaults` is called.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_EXTERNAL,
  NO_OPENINGS,
  beginOpenings,
  defaultsParams,
  openingsDisjoint,
  openingsPrompt,
  openingsFinished,
  openingsReady,
  shouldAddDefaults,
  takeOpenings,
  type OpeningsState,
} from "./flowCardsLogic";
import type { TagRegionMember } from "../api/types";

const cap = (part: string, faces: [number, number][]): TagRegionMember => ({ part, faces });

describe("the openings sequence", () => {
  it("starts at the inlet", () => {
    const state = beginOpenings();
    expect(state.step).toBe("inlet");
    expect(openingsPrompt(state)).toBe("Pick the inlet cap, or caps");
  });

  it("advances to the outlet once an inlet is taken", () => {
    const state = takeOpenings(beginOpenings(), [cap("n_1", [[0, 1]])]);
    expect(state.step).toBe("outlet");
    expect(state.inlet).toHaveLength(1);
    expect(openingsPrompt(state)).toBe("Pick the outlet cap, or caps");
  });

  it("finishes once both are taken", () => {
    let state = beginOpenings();
    state = takeOpenings(state, [cap("n_1", [[0, 1]])]);
    state = takeOpenings(state, [cap("n_1", [[2, 3]])]);
    expect(state.step).toBeNull();
    expect(state.inlet).toHaveLength(1);
    expect(state.outlet).toHaveLength(1);
  });

  it("is inert when a step has nothing picked", () => {
    const started = beginOpenings();
    expect(takeOpenings(started, [])).toBe(started);
    const atOutlet = takeOpenings(started, [cap("n_1", [[0, 0]])]);
    expect(takeOpenings(atOutlet, [])).toBe(atOutlet);
  });

  it("is inert when it is not running at all", () => {
    expect(takeOpenings(NO_OPENINGS, [cap("n_1", [[0, 0]])])).toBe(NO_OPENINGS);
  });

  it("removes from the outlet any face already taken as the inlet", () => {
    // One face cannot be both (§10.2 assumes disjoint); the overlap is dropped, not sent to fail.
    let state = beginOpenings();
    state = takeOpenings(state, [cap("n_1", [[0, 1]])]);
    state = takeOpenings(state, [cap("n_1", [[1, 2]])]);
    expect(state.outlet).toEqual([]);
  });

  it("keeps an outlet face that does not overlap, and other parts entirely", () => {
    let state = beginOpenings();
    state = takeOpenings(state, [cap("n_1", [[0, 1]])]);
    state = takeOpenings(state, [cap("n_2", [[5, 5]]), cap("n_1", [[4, 6]])]);
    expect(state.outlet).toEqual([cap("n_2", [[5, 5]]), cap("n_1", [[4, 6]])]);
  });

  it("never removes a whole-part member for the wrong reason", () => {
    let state = beginOpenings();
    state = takeOpenings(state, [cap("n_1", [[0, 1]])]);
    // "all" faces of a part the inlet also names overlaps by definition.
    state = takeOpenings(state, [cap("n_1", "all")]);
    expect(state.outlet).toEqual([]);
  });
});

describe("openingsReady", () => {
  it("is false before anything is taken", () => {
    expect(openingsReady(beginOpenings())).toBe(false);
  });

  it("is false at the outlet with no inlet", () => {
    // Cannot happen through the API, but the predicate must not claim it is ready.
    const odd: OpeningsState = { step: "outlet", inlet: [], outlet: [] };
    expect(openingsReady(odd)).toBe(false);
  });

  it("is true at the outlet step, once an inlet is held", () => {
    let state = beginOpenings();
    expect(openingsReady(state)).toBe(false); // still choosing the inlet
    state = takeOpenings(state, [cap("n_1", [[0, 0]])]);
    expect(openingsReady(state)).toBe(true); // Finish will produce something
  });

  it("reports finished only when both openings are held", () => {
    let state = beginOpenings();
    expect(openingsFinished(state)).toBe(false);
    state = takeOpenings(state, [cap("n_1", [[0, 0]])]);
    expect(openingsFinished(state)).toBe(false);
    state = takeOpenings(state, [cap("n_1", [[2, 2]])]);
    expect(openingsFinished(state)).toBe(true);
  });
});

describe("openingsDisjoint", () => {
  it("is true for a finished, non-overlapping pair", () => {
    let state = beginOpenings();
    state = takeOpenings(state, [cap("n_1", [[0, 1]])]);
    state = takeOpenings(state, [cap("n_1", [[2, 3]])]);
    expect(openingsDisjoint(state)).toBe(true);
  });

  it("detects an overlap that the take-time filter could not remove across parts", () => {
    const state: OpeningsState = {
      step: null,
      inlet: [cap("n_1", [[0, 5]])],
      outlet: [cap("n_1", [[3, 7]])],
    };
    expect(openingsDisjoint(state)).toBe(false);
  });

  it("is true when the two parts are different", () => {
    const state: OpeningsState = {
      step: null,
      inlet: [cap("n_1", [[0, 5]])],
      outlet: [cap("n_2", [[0, 5]])],
    };
    expect(openingsDisjoint(state)).toBe(true);
  });
});

describe("defaultsParams", () => {
  const finished: OpeningsState = {
    step: null,
    inlet: [cap("n_1", [[0, 1]])],
    outlet: [cap("n_1", [[2, 3]])],
  };

  it("passes the template and target for an external flow", () => {
    const params = defaultsParams("external", "n_1", NO_OPENINGS, { velocity: "2 [m/s]", addDefaults: true });
    expect(params).toEqual({ target: "n_1", template: "external", velocity: "2 [m/s]" });
  });

  it("omits a velocity that was not typed, so the server's own default applies", () => {
    const params = defaultsParams("external", "n_1", NO_OPENINGS, DEFAULT_EXTERNAL);
    expect(params).not.toHaveProperty("velocity");
  });

  it("passes the two openings for an internal flow, and no velocity", () => {
    const params = defaultsParams("internal", "n_1", finished, { velocity: "ignored", addDefaults: false });
    expect(params.template).toBe("internal");
    expect(params.inlet).toEqual(finished.inlet);
    expect(params.outlet).toEqual(finished.outlet);
    expect(params).not.toHaveProperty("velocity");
  });

  it("omits an opening that was not taken", () => {
    const params = defaultsParams("internal", "n_1", NO_OPENINGS);
    expect(params).not.toHaveProperty("inlet");
    expect(params).not.toHaveProperty("outlet");
  });

  it("passes a material only when one was chosen", () => {
    expect(defaultsParams("external", "n_1", NO_OPENINGS, DEFAULT_EXTERNAL, "air")).toMatchObject({ material: "air" });
    expect(defaultsParams("external", "n_1", NO_OPENINGS, DEFAULT_EXTERNAL, "")).not.toHaveProperty("material");
    expect(defaultsParams("external", "n_1", NO_OPENINGS)).not.toHaveProperty("material");
  });
});

describe("shouldAddDefaults", () => {
  it("follows the External card's switch", () => {
    expect(shouldAddDefaults("external", { velocity: "", addDefaults: true })).toBe(true);
    expect(shouldAddDefaults("external", { velocity: "", addDefaults: false })).toBe(false);
  });

  it("is always true for an internal flow: the openings are the point of it", () => {
    expect(shouldAddDefaults("internal", { velocity: "", addDefaults: false })).toBe(true);
  });

  it("defaults the switch on, as §10.2 says", () => {
    expect(DEFAULT_EXTERNAL.addDefaults).toBe(true);
  });
});
