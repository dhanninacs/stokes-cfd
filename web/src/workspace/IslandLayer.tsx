/**
 * The island corners (`islands` of the workspace): each corner of the viewport holds its groups in
 * on-screen order from left to right, 8 px apart, clear of the side slots (the offsets come from the
 * layout engine). The view cube is a group too, but the viewport draws it: here it is a box in its place
 * of the row that takes the room of the cube and has no content. The layer measures that box after every
 * layout and hands it to the viewport (gizmoSlot.ts); a cube in the hidden list reserves nothing and
 * is not drawn. The cube's 128 px box has empty margins around the drawing, so it leans INSET px into its
 * corner, flush with the viewport's edges as the cube has always been, while the groups beside it keep
 * their place.
 *
 * When the groups of one edge (bottom or top) do not fit between their corners, the groups with labels
 * drop to icons; the tooltips carry the names. Runs after every render: App re-renders on every window
 * and panel resize, and the check is a few reads.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, type CSSProperties } from "react";
import { CORNERS } from "./defaults";
import type { CornerOffset } from "./geometry";
import { INSET, ISLAND_GAP } from "./geometry";
import { setGizmoPlacement } from "./gizmoSlot";
import { ISLANDS, type IslandDef } from "./registry";
import { useWorkspace, useWorkspaceReady } from "./store";
import type { Corner } from "./types";

const EDGES: readonly [Corner, Corner][] = [
  ["bl", "br"],
  ["tl", "tr"],
];

/** Which way the cube's box leans into its corner: horizontally, vertically (1 is right or down). */
const LEAN: Record<Corner, readonly [number, number]> = { bl: [-1, 1], br: [1, 1], tl: [-1, -1], tr: [1, -1] };

/**
 * A group the viewport draws (the view cube): a row member as wide as the group and no taller than
 * nothing, so the row's height stays the other groups'; the measured box hangs from it towards the
 * edge the row is anchored to.
 */
function DrawnIsland({ corner, def }: { corner: Corner; def: IslandDef }) {
  const [lx, ly] = LEAN[corner];
  const box: CSSProperties = { position: "absolute", width: def.width[0], height: def.height[0], translate: `${lx * INSET}px ${ly * INSET}px`, pointerEvents: "none" };
  if (corner[1] === "l") box.left = 0;
  else box.right = 0;
  if (corner[0] === "t") box.top = 0;
  else box.bottom = 0;
  return (
    <div className="island-reserve" style={{ position: "relative", flex: "none", width: def.width[0], height: 0 }} aria-hidden="true">
      <div data-ws-island={def.id} style={box} />
    </div>
  );
}

export default function IslandLayer({ offsets }: { offsets: Record<Corner, CornerOffset> }) {
  const islands = useWorkspace((s) => s.document.islands);
  const ready = useWorkspaceReady();
  const corners = useRef<Partial<Record<Corner, HTMLDivElement | null>>>({});

  const fit = useCallback(() => {
    const main = document.querySelector("main");
    if (!main) return;
    for (const [start, end] of EDGES) {
      const a = corners.current[start] ?? null;
      const b = corners.current[end] ?? null;
      const labelled = [a, b].flatMap((c) => (c ? Array.from(c.querySelectorAll<HTMLElement>(".island")).filter((el) => el.querySelector(".island-label")) : []));
      if (labelled.length === 0) continue;
      for (const el of labelled) el.dataset.compact = "false";
      const used = (a?.offsetWidth ?? 0) + (b?.offsetWidth ?? 0) + (a && b ? 12 : 0);
      const span = main.clientWidth - offsets[start].x - offsets[end].x;
      const compact = String(used > span);
      for (const el of labelled) el.dataset.compact = compact;
    }
  }, [offsets]);

  /** Tells the viewport where its cube goes: the box of the `gizmo` group, from the edges of its corner; nowhere when it is hidden. */
  const placeCube = useCallback(() => {
    if (!ready) return; // the viewport draws the cube in its old place until the workspace has loaded
    const main = document.querySelector("main");
    if (!main) return;
    const box = main.querySelector<HTMLElement>('[data-ws-island="gizmo"]');
    const corner = box?.closest<HTMLElement>("[data-corner]")?.dataset.corner as Corner | undefined;
    if (!box || !corner) {
      setGizmoPlacement(null);
      return;
    }
    const m = main.getBoundingClientRect();
    const r = box.getBoundingClientRect();
    setGizmoPlacement({
      corner,
      x: Math.round(corner[1] === "l" ? r.left - m.left : m.right - r.right),
      y: Math.round(corner[0] === "t" ? r.top - m.top : m.bottom - r.bottom),
    });
  }, [ready]);

  useLayoutEffect(() => {
    fit();
    placeCube();
  });
  useEffect(() => {
    // text widths change once the UI font has loaded
    void document.fonts?.ready.then(() => {
      fit();
      placeCube();
    });
  }, [fit, placeCube]);
  // A group that changes width by itself (the mesh menu's label) moves what follows it in its row.
  useEffect(() => {
    const observer = new ResizeObserver(placeCube);
    for (const el of Object.values(corners.current)) if (el) observer.observe(el);
    return () => observer.disconnect();
  });

  if (!ready) return null;
  return (
    <>
      {CORNERS.map((corner) => {
        const ids = islands[corner].filter((id) => ISLANDS.has(id));
        if (ids.length === 0) return null;
        return (
          <div
            key={corner}
            ref={(el) => {
              corners.current[corner] = el;
            }}
            className="island-corner"
            data-corner={corner}
            style={{ gap: ISLAND_GAP }}
            data-ws-corner={corner}
          >
            {ids.map((id) => {
              const def = ISLANDS.get(id);
              if (!def) return null;
              const Island = def.component;
              return Island ? <Island key={id} /> : <DrawnIsland key={id} corner={corner} def={def} />;
            })}
          </div>
        );
      })}
    </>
  );
}
