/**
 * What the results view shows, as one row of toggles on the Results card (the parts list of EnSight and
 * ParaView, in the card's few words): every boundary of the mesh and every slice, each a chip that is lit
 * while it is drawn. Hiding a wall to see the slice inside it is one press, without leaving Results.
 *
 * View state only, the same that the tree's eye and the viewport's Hide use (`hiddenNodes` for a boundary,
 * a slice's own `visible`, the mid-plane's `results.view.slice`); nothing is written to the project.
 */
import { useMemo } from "react";
import { Tooltip } from "../components/Tooltip";
import { setSlice } from "../panels/fieldsModel";
import { sliceLabel, updateSlice } from "../panels/sliceModel";
import { meshPatchesOf } from "../state/meshPatches";
import { useStore } from "../state/store";
import { shownItems, type ShownItem } from "./shownModel";
import "./analyse.css";

export default function ShownSurfaces() {
  const tree = useStore((s) => s.tree);
  const hidden = useStore((s) => s.hiddenNodes);
  const slices = useStore((s) => s.slices);
  const midOn = useStore((s) => s.results.view.slice);
  const hasMid = useStore((s) => s.results.data?.slice === true);
  const patches = useMemo(() => meshPatchesOf(tree), [tree]);
  const items = useMemo(
    () => shownItems(patches, hidden, slices.list.map((s) => ({ id: s.id, label: sliceLabel(s), visible: s.visible })), hasMid, midOn),
    [patches, hidden, slices, hasMid, midOn],
  );
  if (items.length === 0) return null;

  const toggle = (item: ShownItem) => {
    if (item.kind === "patch") useStore.getState().toggleHiddenNode(item.id);
    else if (item.kind === "slice") useStore.setState((s) => ({ slices: updateSlice(s.slices, item.id, { visible: !item.shown }) }));
    else useStore.setState((s) => ({ results: setSlice(s.results, !item.shown) }));
  };

  return (
    <div className="looks-row" data-ws="guided-shown">
      <span className="looks-label">Show</span>
      <div className="guided-chips wide shown-chips" role="group" aria-label="Surfaces shown">
        {items.map((item) => (
          <Tooltip key={`${item.kind}:${item.id}`} label={item.shown ? `Hide ${item.label}` : `Show ${item.label}`} side="top">
            <button type="button" className="guided-chip no-drag" aria-pressed={item.shown} data-choice={item.id} onClick={() => toggle(item)}>
              <span>{item.label}</span>
            </button>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}
