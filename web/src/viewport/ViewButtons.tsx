/**
 * The standard views as named buttons beside the view cube. The cube's six dots are small, the three
 * negative ones were unlabelled, and an orbit can leave the dot you want behind another: picking a
 * plane view meant aiming at a few pixels. Here each view is one labelled button (the same `view.from.*`
 * actions as the context menu's View entry and the Alt+digit shortcuts), sitting against the cube's
 * square on the side that faces into the viewport, and gone when the cube is hidden.
 */
import { type CSSProperties, useSyncExternalStore } from "react";
import { runAction } from "../app/actions";
import { gizmoPlacement, subscribeGizmo } from "../workspace/gizmoSlot";
import { GIZMO_SIZE } from "./gizmo";
import { VIEW_DIRECTIONS, type ViewDirection } from "./viewDirections";

/** The short label each button shows, and its shortcut (workspace defaults). */
const SHORT: Record<ViewDirection, { label: string; key: string }> = {
  front: { label: "Front", key: "Alt+1" },
  back: { label: "Back", key: "Alt+2" },
  left: { label: "Left", key: "Alt+3" },
  right: { label: "Right", key: "Alt+4" },
  top: { label: "Top", key: "Alt+5" },
  bottom: { label: "Bottom", key: "Alt+6" },
  iso: { label: "Iso", key: "Alt+0" },
};

/** Gap between the cube's square and the buttons, in px. */
const GAP = 4;

export function ViewButtons({ shown }: { shown: boolean }) {
  const place = useSyncExternalStore(subscribeGizmo, gizmoPlacement);
  if (!shown || place === null) return null;
  // Against the cube on the side towards the middle of the viewport: above a bottom cube, below a top one.
  // At least the cube's width, wider when the labels need it ("Bottom" does not fit a quarter of 128 px).
  const style: CSSProperties = { minWidth: GIZMO_SIZE };
  if (place.corner[1] === "l") style.left = place.x;
  else style.right = place.x;
  if (place.corner[0] === "b") style.bottom = place.y + GIZMO_SIZE + GAP;
  else style.top = place.y + GIZMO_SIZE + GAP;
  return (
    <div className="view-buttons" role="toolbar" aria-label="Standard views" style={style} data-ws="view-buttons">
      {VIEW_DIRECTIONS.map((d) => (
        <button
          key={d.id}
          type="button"
          className="view-button no-drag"
          title={`Look from the ${d.id === "iso" ? "isometric corner" : d.label.toLowerCase()} (${SHORT[d.id].key})`}
          aria-label={`${d.label} view`}
          onClick={() => runAction(`view.from.${d.id}`)}
          data-view={d.id}
        >
          {SHORT[d.id].label}
        </button>
      ))}
    </div>
  );
}
