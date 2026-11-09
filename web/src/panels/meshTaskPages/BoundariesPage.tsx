/**
 * The Boundaries page (brief M-502 §UI 5.3): the patches the mesh will have — name, type, faces —
 * and the unassigned row with "Tag selection" and **"Keep as a wall"**.
 *
 * "Keep as a wall" is `mesh.configure {accept_unassigned: true}` (M-501): the setting is STORED,
 * which is why there is a button at all — it shows checked afterwards and undoes like any edit.
 * The row is hidden when there are no unassigned faces: a control about nothing would be noise.
 *
 * The table is re-asked when the tree changes — the same event-driven rule the stepper follows;
 * a precheck that fails to build (no domain yet) shows the empty table rather than an error,
 * because "no patches yet" is the honest state of a project mid-set-up.
 */
import { useEffect, useState } from "react";
import { useStore } from "../../state/store";
import { keepAsWallPayload, unassignedRowVisible } from "./boundariesModel";
import { Note } from "../../components/Note";

export interface PatchRow {
  name: string;
  type: string;
  faces: number;
}

/** The patches table plus the unassigned count, as `mesh.precheck` returns them. */
export interface PrecheckPatches {
  patches: PatchRow[];
  unassigned_faces: number;
}

export default function BoundariesPage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const [precheck, setPrecheck] = useState<PrecheckPatches | null>(null);
  const [error, setError] = useState("");

  // Re-asked when the tree changes, which is when a tag or a boundary could have appeared.
  const treeKey = String(tree?.children?.length ?? 0);
  useEffect(() => {
    if (!connected) return;
    let live = true;
    void execute("mesh.precheck")
      .then((answer) => live && setPrecheck(answer as PrecheckPatches))
      .catch((err: Error) => live && setError(err.message));
    return () => {
      live = false;
    };
  }, [treeKey, connected, execute]);

  const unassigned = precheck?.unassigned_faces ?? 0;
  const settings = tree?.children
    ?.find((c) => c.path === "/mesh")
    ?.children?.find((c) => c.path === "/mesh/settings");
  const accept = settings?.params.accept_unassigned === true;

  const keepAsWall = () => {
    void execute("mesh.configure", keepAsWallPayload()).catch((err: Error) =>
      setError(err.message),
    );
  };

  return (
    <div className="task-page" data-ws="task-boundaries">
      <Note className="task-purpose" tip="Which faces become which boundary patch, and which faces have no boundary yet">
        Faces and boundary patches
      </Note>
      <table className="task-table">
        <thead>
          <tr>
            <th scope="col">Patch</th>
            <th scope="col">Type</th>
            <th scope="col" className="num">
              Faces
            </th>
          </tr>
        </thead>
        <tbody>
          {(precheck?.patches ?? []).map((patch) => (
            <tr key={patch.name}>
              <td>{patch.name}</td>
              <td className="muted">{patch.type}</td>
              <td className="num">{patch.faces}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {unassignedRowVisible(unassigned) && (
        <div className="task-row task-unassigned" data-ws="boundaries-unassigned">
          <span className="task-row-name">{unassigned} faces have no boundary</span>
          <button type="button" className="link no-drag" data-ws="tag-selection">
            Tag selection
          </button>
          <button
            type="button"
            className={`link no-drag${accept ? " task-choice-on" : ""}`}
            aria-pressed={accept}
            title="Treat faces without a boundary as a wall (an undoable setting)"
            onClick={keepAsWall}
            data-ws="keep-as-wall"
          >
            {accept ? "Kept as a wall ✓" : "Keep as a wall"}
          </button>
        </div>
      )}
      {error !== "" && (
        <p role="alert" className="mesh-error">
          {error}
        </p>
      )}
    </div>
  );
}