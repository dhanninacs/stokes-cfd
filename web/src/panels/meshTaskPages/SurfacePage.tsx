/**
 * The Surface page (brief M-515, task 5): what the mesh will follow — the surfaces sent to the
 * mesher, their size against the staging limit — and the one setting that decides which sharp
 * edges are kept.
 *
 * The rows are `mesh.precheck`'s own `surfaces` and `staging_limit`, the same numbers the agent
 * stages by, so a green bar here cannot be refused on the host. The feature-angle field commits
 * through `mesh.configure` — one undo step, journaled — and the dictionaries it reaches keep
 * their relation (`resolveFeatureAngle` = the angle, `includedAngle` = its complement), so the
 * page, the stepper and the case cannot disagree.
 */
import { useEffect, useMemo, useState } from "react";
import { useStore } from "../../state/store";
import SchemaFields from "../SchemaFields.tsx";
import { useMeshContext } from "../meshRun";
import { findMeshNodes, isActive, phaseNow, readResult } from "../meshPanelModel";
import {
  ANGLE_EXPLANATION,
  BLOCKMESH_NOTE,
  NO_SURFACE_MESH_NOTE,
  angleLabel,
  appliesTo,
  surfaceViews,
  type SurfaceRow,
} from "../surfaceModel";
import { Note } from "../../components/Note";

interface PrecheckSurfaces {
  surfaces: SurfaceRow[];
  staging_limit: number;
  findings?: Array<{ code: string; severity: string; message: string; nodes?: string[] }>;
}

/** The settings' node, or null before the first `mesh.configure`. */
function useSettings() {
  const tree = useStore((s) => s.tree);
  return useMemo(
    () =>
      tree?.children
        ?.find((c) => c.path === "/mesh")
        ?.children?.find((c) => c.path === "/mesh/settings") ?? null,
    [tree],
  );
}

export default function SurfacePage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const mesh = useStore((s) => s.mesh);
  const busy = useMeshContext((s) => s.busy);
  const settings = useSettings();
  const [precheck, setPrecheck] = useState<PrecheckSurfaces | null>(null);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // Re-asked when the tree changes — the same event-driven rule the other task pages follow.
  useEffect(() => {
    if (!connected) return;
    let live = true;
    const timer = setTimeout(() => {
      void execute("mesh.precheck")
        .then((answer) => live && setPrecheck(answer as PrecheckSurfaces))
        .catch((err: Error) => live && setError(err.message));
    }, 150);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [tree, connected, execute]);

  const params = (settings?.params ?? {}) as Record<string, unknown>;
  const mesher = typeof params.mesher === "string" ? params.mesher : "blockmesh";
  const schema = nodeTypes.mesh_settings;
  const locked = !connected || busy || isActive(phaseNow(readResult(findMeshNodes(tree).result), mesh));
  const limit =
    typeof precheck?.staging_limit === "number" && precheck.staging_limit > 0
      ? precheck.staging_limit
      : undefined;
  const views = surfaceViews(precheck?.surfaces ?? [], limit);
  const overFindings = (precheck?.findings ?? []).filter(
    (f) => f.code === "mesh.too_large_to_stage",
  );

  const configure = async (_name: string, value: unknown) => {
    setError("");
    setFieldErrors((current) => ({ ...current, feature_angle: "" }));
    try {
      await execute("mesh.configure", { values: { feature_angle: value } });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setFieldErrors((current) => ({ ...current, feature_angle: message }));
      throw err;
    }
  };

  return (
    <div className="task-page" data-ws="task-surface">
      <Note className="task-purpose" tip="What the mesh will follow: the surfaces sent to the mesher, and the sharp edges it keeps">
        What the mesh follows
      </Note>
      {error !== "" && (
        <p role="alert" className="mesh-error">
          {error}
        </p>
      )}
      {!appliesTo(mesher) && (
        <p className="muted task-note" data-ws="surface-blockmesh-note">
          {BLOCKMESH_NOTE}
        </p>
      )}
      {appliesTo(mesher) && (
        <>
          <table className="task-table">
            <thead>
              <tr>
                <th scope="col">Surface</th>
                <th scope="col" className="num">
                  Triangles
                </th>
                <th scope="col" className="num">
                  Size
                </th>
                <th scope="col">Against the limit</th>
              </tr>
            </thead>
            <tbody>
              {views.map((row) => {
                const finding = overFindings.find((f) => f.message.includes(row.name));
                return (
                  <tr
                    key={row.name}
                    data-ws={`surface-${row.name}`}
                    className={row.over || finding !== undefined ? "task-warning-row" : ""}
                  >
                    <td>{row.name}</td>
                    <td className="num">{row.triangles.toLocaleString()}</td>
                    <td className="num">{(row.bytes / 1048576).toFixed(1)} MiB</td>
                    <td>
                      <span className="surface-bar" aria-hidden="true">
                        <span className="surface-bar-fill" style={{ width: `${row.fraction * 100}%` }} />
                      </span>
                      {row.over && <span className="task-warning-text"> over the limit</span>}
                      {finding !== undefined && (
                        <span className="muted task-note">{finding.message}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted task-note" data-ws="surface-note">
            {NO_SURFACE_MESH_NOTE}
          </p>
          <div className="section-title">Sharp edges</div>
          {settings !== null && schema?.properties.feature_angle !== undefined && (
            <div data-ws="surface-angle">
              <p className="muted task-note">{angleLabel(Number(params.feature_angle ?? 30))}</p>
              <SchemaFields
                key={`${settings.id}:surface`}
                properties={{ feature_angle: schema.properties.feature_angle }}
                values={params}
                status={settings.status}
                disabled={locked}
                errors={fieldErrors}
                onEdit={(name) => setFieldErrors((current) => ({ ...current, [name]: "" }))}
                onCommit={configure}
              />
            </div>
          )}
          <p className="muted task-note">{ANGLE_EXPLANATION}</p>
        </>
      )}
    </div>
  );
}
