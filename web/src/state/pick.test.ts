/**
 * The pick rules of `state/pick.ts` (T-040): what a click, a Shift-click (add) and a Ctrl-click
 * (toggle) leave of the bodies and the faces, and which member of the result is the active one.
 * T-042 adds the selection's own rules: the mode switch keeps the pick, the double- and triple-click
 * escalate it, and the history keeps ten picks for `selection.previous`. T-043 adds `applyMany`, the
 * bulk pick of the paint stroke and, later, of T-032's box and lasso.
 */
import { describe, expect, it } from "vitest";
import {
  activePatch,
  allFacesOf,
  applyMany,
  convertPick,
  NO_PICK,
  PICK_HISTORY_LIMIT,
  pickAfterBodies,
  pickAfterBody,
  pickAfterFace,
  pickAfterFaces,
  pickInMode,
  pushHistory,
  siblingsOf,
  toggleBody,
  toggleFace,
  type ConversionTopology,
  pendingConversionText,
  type Picked,
  pickAfterTopology,
  pickModeOf,
} from "./pick";

/** A pick of the bodies named, applied one click at a time, as the store does. */
function bodies(...clicks: readonly [string, "replace" | "add" | "toggle"][]): { picked: Picked; active: string | null } {
  let state: { picked: Picked; active: string | null } = { picked: { ...NO_PICK, faces: {} }, active: null };
  for (const [part, how] of clicks) state = pickAfterBody(state.picked, part, how);
  return state;
}

/** The same over faces, each click naming the part and the face id. */
function faces(...clicks: readonly [string, number, "replace" | "add" | "toggle"][]): { picked: Picked; active: string | null } {
  let state: { picked: Picked; active: string | null } = { picked: { bodies: [], faces: {}, edges: {}, vertices: {} }, active: null };
  for (const [part, face, how] of clicks) state = pickAfterFace(state.picked, part, face, how);
  return state;
}

describe("bodies", () => {
  it("replaces the whole pick on a plain click", () => {
    const { picked, active } = bodies(["p1", "add"], ["p2", "replace"]);
    expect(picked.bodies).toEqual(["p2"]);
    expect(picked.faces).toEqual({});
    expect(active).toBe("p2");
  });

  it("appends on a Shift-click, and adding a picked body keeps it once", () => {
    expect(bodies(["p1", "replace"], ["p2", "add"]).picked.bodies).toEqual(["p1", "p2"]);
    expect(bodies(["p1", "replace"], ["p1", "add"]).picked.bodies).toEqual(["p1"]);
  });

  it("adds on a Ctrl-click and removes on the next one", () => {
    expect(toggleBody([], "p1", "toggle")).toEqual(["p1"]);
    expect(toggleBody(["p1", "p2"], "p1", "toggle")).toEqual(["p2"]);
    expect(toggleBody(["p1"], "p2", "toggle")).toEqual(["p1", "p2"]);
  });

  it("clears the faces, as a body pick always does", () => {
    const picked: Picked = { bodies: [], faces: { p1: [1, 2] }, edges: {}, vertices: {} };
    expect(pickAfterBody(picked, "p1", "add").picked.faces).toEqual({});
  });
});

describe("faces", () => {
  it("replaces the whole pick, the faces of other parts too, on a plain click", () => {
    const { picked, active } = faces(["p1", 3, "replace"], ["p2", 7, "replace"]);
    expect(picked.faces).toEqual({ p2: [7] });
    expect(picked.bodies).toEqual([]);
    expect(active).toBe("p2");
  });

  it("keeps the faces of other parts on a Shift-click and sorts the ids", () => {
    expect(faces(["p1", 3, "replace"], ["p2", 7, "add"]).picked.faces).toEqual({ p1: [3], p2: [7] });
    expect(faces(["p1", 9, "replace"], ["p1", 2, "add"]).picked.faces).toEqual({ p1: [2, 9] });
  });

  it("toggles a face off and on again on a Ctrl-click", () => {
    expect(faces(["p1", 3, "replace"], ["p1", 4, "add"], ["p1", 3, "toggle"]).picked.faces).toEqual({ p1: [4] });
    expect(faces(["p1", 3, "replace"], ["p1", 3, "toggle"]).picked.faces).toEqual({});
    expect(toggleFace([2, 5], 5, "toggle")).toEqual([2]);
  });

  it("drops the part from the map when its last face goes", () => {
    const { picked } = faces(["p1", 3, "replace"], ["p1", 3, "toggle"]);
    expect(picked.faces).toEqual({});
    expect("p1" in picked.faces).toBe(false);
  });

  it("clears the bodies, as a face pick always does", () => {
    const picked: Picked = { bodies: ["p1"], faces: {}, edges: {}, vertices: {} };
    expect(pickAfterFace(picked, "p1", 1, "add").picked.bodies).toEqual([]);
  });
});

describe("the active member", () => {
  it("is the body just picked", () => {
    expect(bodies(["p1", "replace"], ["p2", "add"]).active).toBe("p2");
    expect(faces(["p1", 1, "replace"], ["p2", 4, "add"]).active).toBe("p2");
  });

  it("after a toggle-off is the last one left", () => {
    expect(bodies(["p1", "replace"], ["p2", "add"], ["p2", "toggle"]).active).toBe("p1");
    expect(faces(["p1", 1, "replace"], ["p2", 4, "add"], ["p2", 4, "toggle"]).active).toBe("p1");
  });

  it("is null when the pick empties", () => {
    expect(bodies(["p1", "replace"], ["p1", "toggle"]).active).toBeNull();
    expect(faces(["p1", 1, "replace"], ["p1", 1, "toggle"]).active).toBeNull();
  });
});

describe("NO_PICK", () => {
  it("is not mutated by a click", () => {
    pickAfterBody(NO_PICK, "p1", "add");
    pickAfterFace(NO_PICK, "p1", 1, "add");
    expect(NO_PICK).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
  });
});

describe("activePatch", () => {
  const patchOf = (source: string): string | null => (source === "p1" ? "p1.stl" : null);

  it("is the single patch of the active source", () => {
    expect(activePatch("p1", "p2", undefined, patchOf)).toBe("p1.stl");
  });

  it("is the clicked patch when the picked source owns several", () => {
    expect(activePatch("@sample", "@sample", "sample:0", patchOf)).toBe("sample:0");
  });

  it("is null with nothing active, or a source without a single patch", () => {
    expect(activePatch(null, "p1", "p1.stl", patchOf)).toBeNull();
    expect(activePatch("p2", "p2", undefined, patchOf)).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------- T-042
/** The face counts of the store's `geometry`: p1 has three faces, p2 two, anything else none. */
const counts = (part: string): number => (part === "p1" ? 3 : part === "p2" ? 2 : 0);

describe("allFacesOf", () => {
  it("is every face id, 0 first", () => {
    expect(allFacesOf(3)).toEqual([0, 1, 2]);
  });

  it("is empty without faces", () => {
    expect(allFacesOf(0)).toEqual([]);
  });
});

describe("convertPick", () => {
  it("turns picked faces into the bodies that own them, in the order they were picked", () => {
    const picked: Picked = { bodies: [], faces: { p2: [1, 3], p1: [0] }, edges: {}, vertices: {} };
    expect(convertPick(picked, "body", counts)).toEqual({ bodies: ["p2", "p1"], faces: {}, edges: {}, vertices: {} });
  });

  it("gives every face of each picked body", () => {
    const picked: Picked = { bodies: ["p2", "p1"], faces: {}, edges: {}, vertices: {} };
    expect(convertPick(picked, "face", counts)).toEqual({ bodies: [], faces: { p2: [0, 1], p1: [0, 1, 2] }, edges: {}, vertices: {} });
  });

  it("drops a body whose face count is unknown", () => {
    const picked: Picked = { bodies: ["p1", "@sample"], faces: {}, edges: {}, vertices: {} };
    expect(convertPick(picked, "face", counts)).toEqual({ bodies: [], faces: { p1: [0, 1, 2] }, edges: {}, vertices: {} });
  });

  it("leaves an empty pick empty, either way", () => {
    expect(convertPick(NO_PICK, "body", counts)).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
    expect(convertPick(NO_PICK, "face", counts)).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
  });
});

describe("siblingsOf", () => {
  it("puts the clicked part first, whatever order the group is in", () => {
    expect(siblingsOf("p2", ["p1", "p2", "p3"])).toEqual(["p2", "p1", "p3"]);
    expect(siblingsOf("p1", ["p1", "p2"])).toEqual(["p1", "p2"]);
  });

  it("is the clicked part alone when the group holds nothing else", () => {
    expect(siblingsOf("p1", [])).toEqual(["p1"]);
    expect(siblingsOf("@sample", ["p1"])).toEqual(["@sample", "p1"]);
  });
});

describe("the escalated pick", () => {
  it("takes every face of the body on a plain triple-click", () => {
    const { picked, active } = pickAfterFaces(NO_PICK, "p1", allFacesOf(3), "replace");
    expect(picked.faces).toEqual({ p1: [0, 1, 2] });
    expect(picked.bodies).toEqual([]);
    expect(active).toBe("p1");
  });

  it("keeps the faces of other parts on a Shift-triple-click", () => {
    const first = pickAfterFace(NO_PICK, "p2", 1, "replace").picked;
    expect(pickAfterFaces(first, "p1", allFacesOf(3), "add").picked.faces).toEqual({ p2: [1], p1: [0, 1, 2] });
  });

  it("toggles the whole set as one on a Ctrl-triple-click", () => {
    // The click that escalates has already toggled the face under the pointer on: the set is not
    // held yet, so the whole set joins rather than the clicked face leaving again.
    const once = pickAfterFace(NO_PICK, "p1", 0, "toggle").picked;
    expect(pickAfterFaces(once, "p1", allFacesOf(3), "toggle").picked.faces).toEqual({ p1: [0, 1, 2] });
    expect(pickAfterFaces({ bodies: [], faces: { p1: [0, 1, 2] }, edges: {}, vertices: {} }, "p1", allFacesOf(3), "toggle").picked.faces).toEqual({});
  });

  it("takes the parts of one group on a body double-click, the clicked part first", () => {
    const { picked, active } = pickAfterBodies({ bodies: ["p9"], faces: {}, edges: {}, vertices: {} }, siblingsOf("p2", ["p1", "p2", "p3"]), "replace");
    expect(picked.bodies).toEqual(["p2", "p1", "p3"]);
    expect(active).toBe("p2");
  });

  it("adds the group's parts on a Shift-double-click, without repeating the clicked one", () => {
    const first = pickAfterBody(NO_PICK, "p9", "replace").picked;
    expect(pickAfterBodies(first, siblingsOf("p2", ["p1", "p2"]), "add").picked.bodies).toEqual(["p9", "p2", "p1"]);
  });
});

describe("pushHistory", () => {
  const body = (id: string): Picked => ({ bodies: [id], faces: {}, edges: {}, vertices: {} });

  it("keeps the last ten picks, oldest first", () => {
    let history: Picked[] = [];
    for (let i = 1; i <= 12; i++) history = pushHistory(history, body(`p${i}`));
    expect(history).toHaveLength(PICK_HISTORY_LIMIT);
    expect(history[0]).toEqual(body("p3"));
    expect(history[history.length - 1]).toEqual(body("p12"));
  });

  it("does not push the same pick twice in a row", () => {
    const once = pushHistory([], body("p1"));
    expect(once).toHaveLength(1);
    expect(pushHistory(once, body("p1"))).toHaveLength(1);
    expect(pushHistory(once, { bodies: ["p1"], faces: {}, edges: {}, vertices: {} })).toHaveLength(1);
    expect(pushHistory(once, body("p2"))).toHaveLength(2);
  });

  it("tells the same picks apart by their faces", () => {
    const once = pushHistory([], { bodies: [], faces: { p1: [1, 2] }, edges: {}, vertices: {} });
    expect(pushHistory(once, { bodies: [], faces: { p1: [1, 2] }, edges: {}, vertices: {} })).toHaveLength(1);
    expect(pushHistory(once, { bodies: [], faces: { p1: [2] }, edges: {}, vertices: {} })).toHaveLength(2);
    expect(pushHistory(once, { bodies: [], faces: { p2: [1, 2] }, edges: {}, vertices: {} })).toHaveLength(2);
  });
});

describe("pickInMode", () => {
  it("applies a pick of the current mode as it is", () => {
    const bodies: Picked = { bodies: ["p1"], faces: {}, edges: {}, vertices: {} };
    expect(pickInMode(bodies, "body", counts)).toBe(bodies);
    const faces: Picked = { bodies: [], faces: { p1: [1] }, edges: {}, vertices: {} };
    expect(pickInMode(faces, "face", counts)).toBe(faces);
  });

  it("converts a pick the other mode left", () => {
    expect(pickInMode({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} }, "face", counts)).toEqual({ bodies: [], faces: { p1: [0, 1, 2] }, edges: {}, vertices: {} });
    expect(pickInMode({ bodies: [], faces: { p2: [1] }, edges: {}, vertices: {} }, "body", counts)).toEqual({ bodies: ["p2"], faces: {}, edges: {}, vertices: {} });
    expect(pickInMode({ bodies: ["@sample"], faces: {}, edges: {}, vertices: {} }, "face", counts)).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
  });
});

// ---------------------------------------------------------------------------------------------- T-043
describe("applyMany", () => {
  const start: Picked = { bodies: [], faces: { p1: [1], p2: [7] }, edges: {}, vertices: {} };

  it("adds the faces of several parts as one pick", () => {
    const { picked } = applyMany(start, [{ part: "p1", faces: [3, 2] }, { part: "p3", faces: [0] }], "add");
    expect(picked).toEqual({ bodies: [], faces: { p1: [1, 2, 3], p2: [7], p3: [0] }, edges: {}, vertices: {} });
  });

  it("removes the faces it names and drops a part left without any", () => {
    const { picked } = applyMany(start, [{ part: "p1", faces: [2] }, { part: "p2", faces: [7] }], "remove");
    expect(picked).toEqual({ bodies: [], faces: { p1: [1] }, edges: {}, vertices: {} });
  });

  it("replaces the whole pick with the items alone, and empties it with none", () => {
    expect(applyMany(start, [{ part: "p3", faces: [4] }], "replace").picked).toEqual({ bodies: [], faces: { p3: [4] }, edges: {}, vertices: {} });
    expect(applyMany(start, [{ part: "p3" }], "replace").picked).toEqual({ bodies: ["p3"], faces: {}, edges: {}, vertices: {} });
    expect(applyMany(start, [], "replace").picked).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
  });

  it("picks a body when an item carries no faces, and clears the faces when it is the last one", () => {
    expect(applyMany(NO_PICK, [{ part: "p1" }, { part: "p2" }], "add").picked).toEqual({ bodies: ["p1", "p2"], faces: {}, edges: {}, vertices: {} });
    expect(applyMany(NO_PICK, [{ part: "p1" }], "add").picked).toEqual({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} });
    // The pick holds one kind, and the last item is the gesture's last word.
    expect(applyMany(start, [{ part: "p3" }, { part: "p1", faces: [5] }], "add").picked).toEqual({ bodies: [], faces: { p1: [1, 5], p2: [7] }, edges: {}, vertices: {} });
  });

  it("takes the last item's part as the active member, else the last one left", () => {
    expect(applyMany(NO_PICK, [{ part: "p1" }, { part: "p2" }], "add").active).toBe("p2");
    expect(applyMany({ bodies: ["p1", "p2"], faces: {}, edges: {}, vertices: {} }, [{ part: "p2" }], "remove").active).toBe("p1");
    expect(applyMany({ bodies: [], faces: { p1: [1], p2: [7] }, edges: {}, vertices: {} }, [{ part: "p2", faces: [7] }], "remove").active).toBe("p1");
    expect(applyMany({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} }, [{ part: "p1" }], "remove").active).toBeNull();
    expect(applyMany(NO_PICK, [], "replace").active).toBeNull();
  });

  it("is one pick for the history however many items it names", () => {
    const once = pushHistory([], start);
    const { picked } = applyMany(start, [{ part: "p1", faces: [2] }, { part: "p3", faces: [4] }, { part: "p4", faces: [6] }], "add");
    const after = pushHistory(once, picked);
    expect(after).toHaveLength(2);
    expect(after[1]).toEqual(picked);
  });

  it("leaves the pick it was given alone", () => {
    applyMany(NO_PICK, [{ part: "p1", faces: [0] }], "add");
    applyMany(start, [{ part: "p1", faces: [1] }], "remove");
    expect(NO_PICK).toEqual({ bodies: [], faces: {}, edges: {}, vertices: {} });
    expect(start).toEqual({ bodies: [], faces: { p1: [1], p2: [7] }, edges: {}, vertices: {} });
  });
});
describe("pickAfterTopology (T-058)", () => {
  const empty = { bodies: [], faces: {}, edges: {}, vertices: {} };

  it("picks an edge and clears bodies and faces (one kind at a time)", () => {
    const start = { bodies: ["p9"], faces: { p9: [1, 2] }, edges: {}, vertices: {} };
    const { picked, active } = pickAfterTopology(start, "p1", "edge", 3, "replace");
    expect(picked).toEqual({ bodies: [], faces: {}, edges: { p1: [3] }, vertices: {} });
    expect(active).toBe("p1");
  });

  it("adds and toggles edges on one part, like faces", () => {
    const one = pickAfterTopology(empty, "p1", "edge", 1, "replace").picked;
    const two = pickAfterTopology(one, "p1", "edge", 2, "add").picked;
    expect(two.edges).toEqual({ p1: [1, 2] });
    const off = pickAfterTopology(two, "p1", "edge", 1, "toggle").picked;
    expect(off.edges).toEqual({ p1: [2] });
  });

  it("keeps edges sorted", () => {
    let picked = empty;
    for (const id of [5, 1, 3]) picked = pickAfterTopology(picked, "p1", "edge", id, "add").picked;
    expect(picked.edges.p1).toEqual([1, 3, 5]);
  });

  it("replaces the whole pick, other parts included", () => {
    const start = pickAfterTopology(empty, "p1", "edge", 1, "replace").picked;
    const next = pickAfterTopology(start, "p2", "edge", 7, "replace").picked;
    expect(next.edges).toEqual({ p2: [7] });
  });

  it("picks vertices in the vertex map, never the edge one", () => {
    const { picked } = pickAfterTopology(empty, "p1", "vertex", 4, "replace");
    expect(picked).toEqual({ bodies: [], faces: {}, edges: {}, vertices: { p1: [4] } });
  });

  it("switching kind clears the other kind's set", () => {
    const edges = pickAfterTopology(empty, "p1", "edge", 1, "replace").picked;
    const vertices = pickAfterTopology(edges, "p1", "vertex", 2, "replace").picked;
    expect(vertices.edges).toEqual({});
    expect(vertices.vertices).toEqual({ p1: [2] });
  });

  it("ignores a negative id rather than picking id -1", () => {
    const { picked } = pickAfterTopology(empty, "p1", "edge", -1, "replace");
    expect(picked).toEqual(empty);
  });

  it("drops the part from the map when the last id is toggled off", () => {
    const one = pickAfterTopology(empty, "p1", "edge", 1, "replace").picked;
    const none = pickAfterTopology(one, "p1", "edge", 1, "toggle").picked;
    expect(none.edges).toEqual({});
  });
});


describe("A6: a mode change must not empty the pick", () => {
  const topo: ConversionTopology = {
    verticesOfEdge: (part, edge) => (part === "p1" && edge === 7 ? [10, 11] : null),
    edgesOfVertex: (part, vertex) => (part === "p1" && vertex === 10 ? [7, 8] : null),
    allEdges: (part) => (part === "p1" ? [7, 8, 9] : null),
    facesOfEdges: (part, edges) => (part === "p1" && edges.includes(7) ? [3, 4] : null),
    facesOfVertices: (part, vertices) => (part === "p1" && vertices.includes(10) ? [3] : []),
  };

  it("labels a pick by the mode it is actually in", () => {
    // The old pickModeOf knew only body and face, so a stored edge or point pick read as "body" and
    // was then converted — which is how it got emptied.
    expect(pickModeOf({ bodies: [], faces: {}, edges: { p1: [7] }, vertices: {} })).toBe("edge");
    expect(pickModeOf({ bodies: [], faces: {}, edges: {}, vertices: { p1: [10] } })).toBe("point");
    expect(pickModeOf({ bodies: [], faces: { p1: [1] }, edges: {}, vertices: {} })).toBe("face");
    expect(pickModeOf({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} })).toBe("body");
  });

  it("keeps an edge pick's edges when the mode is set to edge again", () => {
    const picked = { bodies: [], faces: {}, edges: { p1: [7] }, vertices: {} };
    const out = pickInMode(picked, "edge", () => 6, topo);
    expect(out.edges).toEqual({ p1: [7] });
  });

  it("keeps a point pick's vertices when the mode is set to point again", () => {
    const picked = { bodies: [], faces: {}, edges: {}, vertices: { p1: [10] } };
    const out = pickInMode(picked, "point", () => 6, topo);
    expect(out.vertices).toEqual({ p1: [10] });
  });

  it("expands an edge to its endpoint vertices (edge -> point)", () => {
    const picked = { bodies: [], faces: {}, edges: { p1: [7] }, vertices: {} };
    const out = convertPick(picked, "point", () => 6, topo);
    expect(out.vertices).toEqual({ p1: [10, 11] });
  });

  it("expands an edge to its adjacent faces (edge -> face)", () => {
    const picked = { bodies: [], faces: {}, edges: { p1: [7] }, vertices: {} };
    const out = convertPick(picked, "face", () => 6, topo);
    expect(out.faces).toEqual({ p1: [3, 4] });
  });

  it("expands a point to its adjacent faces (point -> face)", () => {
    const picked = { bodies: [], faces: {}, edges: {}, vertices: { p1: [10] } };
    const out = convertPick(picked, "face", () => 6, topo);
    expect(out.faces).toEqual({ p1: [3] });
  });

  it("omits a part whose record is not loaded rather than dropping the whole pick", () => {
    // A part with no record: the edge pick keeps nothing for that part, but a part that DOES have
    // one is unaffected — §15.4's "omit topology not yet loaded".
    const picked = { bodies: [], faces: {}, edges: { p1: [7], p2: [7] }, vertices: {} };
    const out = convertPick(picked, "point", () => 6, topo);
    expect(out.vertices.p1).toEqual([10, 11]);
    expect(out.vertices.p2).toBeUndefined();
  });

  it("expands a body to all its faces, and that to its vertices, without topology", () => {
    const out = convertPick({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} }, "face", () => 4);
    expect(out.faces).toEqual({ p1: [0, 1, 2, 3] });
  });

  it("keeps the active owner: a body pick still converts to the owning body", () => {
    const picked = { bodies: [], faces: {}, edges: { p1: [7] }, vertices: {} };
    const out = convertPick(picked, "body", () => 6, topo);
    expect(out.bodies).toEqual(["p1"]);
  });
});


describe("A6: the remaining §15.4 conversions", () => {
  const topo: ConversionTopology = {
    verticesOfEdge: (part, edge) => (part === "p1" && edge === 7 ? [10, 11] : null),
    edgesOfVertex: (part, vertex) => (part === "p1" && vertex === 10 ? [7, 8] : null),
    allEdges: (part) => (part === "p1" ? [7, 8, 9] : null),
    facesOfEdges: () => null,
    facesOfVertices: () => [],
  };

  it("expands a point to its incident edges (point -> edge)", () => {
    const out = convertPick({ bodies: [], faces: {}, edges: {}, vertices: { p1: [10] } }, "edge", () => 6, topo);
    expect(out.edges).toEqual({ p1: [7, 8] });
  });

  it("expands a body to every edge of its record (body -> edge)", () => {
    const out = convertPick({ bodies: ["p1"], faces: {}, edges: {}, vertices: {} }, "edge", () => 6, topo);
    expect(out.edges).toEqual({ p1: [7, 8, 9] });
  });

  it("keeps a point pick's vertices and adds the incident edges, without mixing the two maps", () => {
    const out = convertPick(
      { bodies: [], faces: {}, edges: { p1: [7] }, vertices: { p1: [10] } },
      "edge",
      () => 6,
      topo,
    );
    expect(out.edges).toEqual({ p1: [7, 8] });
    expect(out.vertices).toEqual({});
  });

  it("omits a part with no record instead of inventing edges", () => {
    const out = convertPick({ bodies: ["p2"], faces: {}, edges: {}, vertices: {} }, "edge", () => 6, topo);
    expect(out.edges).toEqual({});
    expect(out.bodies).toEqual([]);
  });

  it("gives a face pick no edges, since the record has no edge-to-face incidence", () => {
    // §15.4 asks for face -> bounding edges. That needs edge-to-face incidence, which the record
    // does not carry (board row T-124a), so nothing is invented and the UI shows the pending state.
    const out = convertPick({ bodies: [], faces: { p1: [3] }, edges: {}, vertices: {} }, "edge", () => 6, topo);
    expect(out.edges).toEqual({});
  });
});


describe("A6: the §15.4 pending state", () => {
  it("says nothing when there is no conversion waiting", () => {
    expect(pendingConversionText({ bodies: [], faces: {}, edges: { p1: [1] }, vertices: {} }, "edge", new Set(["p1"]))).toBeNull();
    expect(pendingConversionText({ bodies: [], faces: {}, edges: {}, vertices: {} }, "edge", new Set())).toBeNull();
  });

  it("names how many parts a conversion is waiting for", () => {
    // A face pick in edge mode needs the record; with none loaded the pick would look empty, so the
    // readout says it is waiting instead (the spec forbids clearing silently).
    const text = pendingConversionText(
      { bodies: [], faces: { p1: [3], p2: [4] }, edges: {}, vertices: {} },
      "edge",
      new Set(["p3"]),
    );
    expect(text).toContain("2 parts");
    expect(text).toContain("edges");
  });

  it("says nothing for a part whose record IS loaded, even if it contributed no edges", () => {
    // A loaded part with no edge→face incidence is not "pending": it is a known gap, and claiming a
    // wait would be a lie the user cannot act on. Board row T-124a.
    expect(
      pendingConversionText({ bodies: [], faces: { p1: [3] }, edges: {}, vertices: {} }, "edge", new Set(["p1"])),
    ).toBeNull();
  });

  it("counts a body pick waiting for its record", () => {
    const text = pendingConversionText({ bodies: ["p9"], faces: {}, edges: {}, vertices: {} }, "edge", new Set());
    expect(text).toContain("1 part");
  });

  it("is null outside the topology modes, which need no record to convert", () => {
    expect(pendingConversionText({ bodies: [], faces: { p1: [3] }, edges: {}, vertices: {} }, "body", new Set())).toBeNull();
    expect(pendingConversionText({ bodies: [], faces: { p1: [3] }, edges: {}, vertices: {} }, "face", new Set())).toBeNull();
  });

  it("uses the right noun for each mode", () => {
    const vertices = pendingConversionText({ bodies: ["p"], faces: {}, edges: {}, vertices: {} }, "point", new Set());
    expect(vertices).toContain("vertices");
  });
});
