/**
 * The Sizing page (brief M-503, task 4): how fine the mesh will be everywhere, per body and per
 * boundary, with the level each size really achieves and an honest cell range before anything
 * runs.
 *
 * **The page agrees with the dictionary because it reads the same numbers**: the estimate's
 * levels ARE the snapshot's resolved levels (`mesh.estimate` re-resolves through the same
 * precedence under tried sizes), and the placeholders come from the model's `placeholderFor`.
 * The estimate is re-asked when the tree changes — the same event-driven rule the stepper
 * follows, debounced by the reply hook, never polled while idle.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert } from "lucide-react";
import SchemaFields from "../SchemaFields.tsx";
import { useStore } from "../../state/store";
import { useMeshContext } from "../meshRun";
import { findMeshNodes, isActive, phaseNow, readResult } from "../meshPanelModel";
import {
  BLOCKMESH_NOTE,
  OVER_LIMIT_ADVICE,
  bodyViews,
  boundaryViews,
  estimateLine,
  localSizesApply,
  placeholderFor,
  SURFACE_SIZE_PLACEHOLDER,
  formatMm,
} from "../../panels/sizingModel";
import { Note } from "../../components/Note";

/** The settings' node type schema, for the two global fields. */
interface PropertySchema {
  // The schema fields' shape is the server's; only the names matter here.
  [key: string]: unknown;
}

interface EstimateNumbers {
  low: number;
  high: number;
  max_cells: number;
  over: boolean;
  levels: Array<{ surface: string; level: number; achieved: number; deviation: number }>;
}

export default function SizingPage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const mesh = useStore((s) => s.mesh);
  const busy = useMeshContext((s) => s.busy);
  const [estimate, setEstimate] = useState<EstimateNumbers | null>(null);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!connected) return;
    let live = true;
    const timer = setTimeout(() => {
      void execute("mesh.estimate")
        .then((answer) => {
          if (live) setEstimate(answer as EstimateNumbers);
        })
        .catch((err: Error) => live && setError(err.message));
    }, 150);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [tree, connected, execute]);

  const settings = useMemo(
    () =>
      tree?.children
        ?.find((c) => c.path === "/mesh")
        ?.children?.find((c) => c.path === "/mesh/settings") ?? null,
    [tree],
  );
  const schema = nodeTypes.mesh_settings;
  const locked = !connected || busy || isActive(phaseNow(readResult(findMeshNodes(tree).result), mesh));
  const params = (settings?.params ?? {}) as Record<string, unknown>;
  const mesher = typeof params.mesher === "string" ? params.mesher : "blockmesh";
  const baseSize = typeof params.base_size === "object" && params.base_size !== null
    ? (params.base_size as { value: number }).value
    : 0;
  const surfaceSize = typeof params.surface_size === "object" && params.surface_size !== null
    ? (params.surface_size as { value: number }).value
    : 0;

  const configure = async (name: string, value: unknown) => {
    setError("");
    setFieldErrors((current) => ({ ...current, [name]: "" }));
    try {
      await execute("mesh.configure", { values: { [name]: value } });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setFieldErrors((current) => ({ ...current, [name]: message }));
      throw err;
    }
  };

  const levels = estimate?.levels ?? [];
  const levelOf = (surface: string) => levels.find((l) => l.surface === surface);

  return (
    <div className="task-page" data-ws="task-sizing">
      <Note className="task-purpose" tip="How fine the mesh is: everywhere, per body, per boundary, and inside boxes or spheres">
        How fine the mesh is
      </Note>
      {error !== "" && (
        <p role="alert" className="mesh-error">
          {error}
        </p>
      )}
      {!localSizesApply(mesher) && (
        <p className="muted task-note" data-ws="sizing-blockmesh-note">
          {BLOCKMESH_NOTE}
        </p>
      )}
      {settings !== null && schema !== undefined && (
        <div className="mesh-fields">
          <SchemaFields
            key={`${settings.id}:sizing`}
            properties={Object.fromEntries(
              ["base_size", ...(localSizesApply(mesher) ? ["surface_size"] : [])]
                .filter((n) => schema.properties[n] !== undefined)
                .map((n) => [n, schema.properties[n] as PropertySchema]),
            )}
            values={params}
            displays={settings.displays}
            status={settings.status}
            onCommit={configure}
            onEdit={(name) => setFieldErrors((current) => ({ ...current, [name]: "" }))}
            disabled={locked}
            errors={fieldErrors}
          />
        </div>
      )}
      <p className="muted task-note" data-ws="surface-size-hint">
        {SURFACE_SIZE_PLACEHOLDER}
      </p>
      {localSizesApply(mesher) && (
        <>
          <div className="section-title">Bodies</div>
          <BodiesTable tree={tree} baseSize={baseSize} surfaceSize={surfaceSize} disabled={locked} />
          <div className="section-title">Boundaries</div>
          <BoundariesTable tree={tree} baseSize={baseSize} surfaceSize={surfaceSize} disabled={locked} />
        </>
      )}
      {estimate !== null && (
        <p
          className={`task-note${estimate.over ? " task-warning" : ""}`}
          data-ws="sizing-estimate"
        >
          {estimate.over && <CircleAlert size={13} className="inline-block mr-1" aria-hidden="true" />}
          {estimateLine(estimate)}
          {estimate.over && ` — ${OVER_LIMIT_ADVICE}`}
        </p>
      )}
      {/* The achieved levels the estimate resolved, as the read-only columns feed from. */}
      <span hidden data-ws="sizing-levels" data-count={String(levels.length)} />
      {levelOf("walls") === undefined && levels.length > 0 ? null : null}
      {baseSize > 0 ? null : formatMm(0)}
    </div>
  );
}

/** The bodies table: one row per part, the domain body editable, the others saying why not. */
function BodiesTable({
  tree,
  baseSize,
  surfaceSize,
  disabled,
}: {
  tree: ReturnType<typeof useStore.getState>["tree"];
  baseSize: number;
  surfaceSize: number;
  disabled: boolean;
}) {
  const execute = useStore((s) => s.execute);
  const selectedId = useStore((s) => s.selectedId);
  const parts = useMemo(() => {
    const group = tree?.children?.find((c) => c.path === "/geometry");
    const partsGroup = group?.children?.find((c) => c.path === "/geometry/parts");
    if (partsGroup === undefined) return [];
    const out: Array<{ id: string; name: string; size: number | null; refinement: number; saved: string }> = [];
    const visit = (node: { type: string; id: string; name: string; params: Record<string, unknown>; displays?: Record<string, string>; children?: unknown[] }): void => {
      if (node.type === "part") {
        const raw = node.params.mesh_size;
        const size = raw !== null && typeof raw === "object" ? (raw as { value: number }).value : null;
        out.push({
          id: node.id,
          name: node.name,
          size: typeof size === "number" && size > 0 ? size : null,
          refinement: typeof node.params.refinement === "number" ? node.params.refinement : 0,
          saved: savedSizeText(node.params.mesh_size, node.displays?.mesh_size),
        });
      }
      for (const child of node.children ?? []) {
        visit(child as Parameters<typeof visit>[0]);
      }
    };
    visit(partsGroup as Parameters<typeof visit>[0]);
    return out;
  }, [tree]);

  const settings = tree?.children
    ?.find((c) => c.path === "/mesh")
    ?.children?.find((c) => c.path === "/mesh/settings");
  const domainPart = typeof settings?.params.domain_part === "string" ? settings.params.domain_part : null;
  const rows = bodyViews(
    parts.map((p) => ({ ...p, isDomain: p.id === domainPart })),
    { baseSize, surfaceSize, mesher: "snappy", levels: [] },
    domainPart,
  ).map((row) => ({ ...row, saved: parts.find((part) => part.id === row.id)?.saved ?? "" }));

  return (
    <table className="task-table">
      <thead>
        <tr>
          <th scope="col">Body</th>
          <th scope="col">Size</th>
          <th scope="col">Finer on curves</th>
          <th scope="col" className="num">Level</th>
          <th scope="col" className="num">Achieved</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} data-ws={`sizing-body-${row.name}`}>
            <td>{row.name}</td>
            <td>
              {row.editable ? (
                <SavedSizeInput
                  value={row.saved}
                  placeholder={placeholderFor(null, row.effective)}
                  label={`Mesh size of ${row.name}`}
                  disabled={disabled}
                  onCommit={(value) => execute("parameter.set", { node: row.id, name: "mesh_size", value })}
                />
              ) : (
                <span className="muted">not meshed in this fluid mesh</span>
              )}
            </td>
            <td className="muted">{rowLevelWord(row.level)}</td>
            <td className="num">{row.level}</td>
            <td className="num">{formatMm(row.achieved)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  // The selection only drives which row is highlighted; the pane handles it.
  void selectedId;
}

function rowLevelWord(level: number): string {
  return level === 0 ? "none" : `+${level} levels`;
}

function savedSizeText(raw: unknown, display: string | undefined): string {
  if (display !== undefined && display !== "") return display;
  if (typeof raw === "object" && raw !== null && "value" in raw) {
    const value = (raw as { value: unknown }).value;
    const expression = (raw as { expr?: unknown }).expr;
    const unit = (raw as { unit?: unknown }).unit;
    if (typeof expression === "string" && expression !== "") return expression;
    if (typeof value === "number") return typeof unit === "string" && unit !== "" ? `${value} [${unit}]` : String(value);
  }
  if (typeof raw === "number" || typeof raw === "string") return String(raw);
  return "";
}

/** A per-object override follows saved model values and keeps a refused edit beside its row. */
function SavedSizeInput({
  value,
  placeholder,
  label,
  disabled,
  onCommit,
}: {
  value: string;
  placeholder: string;
  label: string;
  disabled: boolean;
  onCommit(value: string | null): Promise<unknown>;
}) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const lastSubmitted = useRef<string | null>(null);
  useEffect(() => {
    setDraft(value);
    setError("");
    lastSubmitted.current = null;
  }, [value]);
  const save = () => {
    if (disabled || inFlight.current) return;
    const next = draft.trim();
    if (next === value || next === lastSubmitted.current) return;
    inFlight.current = true;
    lastSubmitted.current = next;
    void onCommit(next === "" ? null : next)
      .then(() => setError(""), (err: unknown) => {
        lastSubmitted.current = null;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => { inFlight.current = false; });
  };
  return (
    <>
      <input
        className={`task-input${error ? " error" : ""}`}
        placeholder={placeholder}
        aria-label={label}
        aria-invalid={error !== ""}
        disabled={disabled}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          setError("");
          lastSubmitted.current = null;
        }}
        onBlur={save}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            save();
          } else if (event.key === "Escape") {
            event.preventDefault();
            setDraft(value);
            setError("");
            lastSubmitted.current = null;
          }
        }}
      />
      {error !== "" && <div role="alert" className="task-warning-text">{error}</div>}
    </>
  );
}

/** The boundaries table: one row per tag, the boundary's size first in the precedence. */
function BoundariesTable({
  tree,
  baseSize,
  surfaceSize,
  disabled,
}: {
  tree: ReturnType<typeof useStore.getState>["tree"];
  baseSize: number;
  surfaceSize: number;
  disabled: boolean;
}) {
  const execute = useStore((s) => s.execute);
  const rows = useMemo(() => {
    const group = tree?.children?.find((c) => c.path === "/regions");
    const out: Array<{ name: string; id: string; size: number | null; saved: string }> = [];
    const visit = (node: { type: string; id: string; name: string; path: string; params: Record<string, unknown>; displays?: Record<string, string>; children?: unknown[] }): void => {
      if (node.type === "boundary") {
        const raw = node.params.mesh_size;
        const size = raw !== null && typeof raw === "object" ? (raw as { value: number }).value : null;
        out.push({
          name: node.name,
          id: node.id,
          size: typeof size === "number" && size > 0 ? size : null,
          saved: savedSizeText(node.params.mesh_size, node.displays?.mesh_size),
        });
      }
      for (const child of node.children ?? []) {
        visit(child as Parameters<typeof visit>[0]);
      }
    };
    visit((group ?? { type: "", id: "", name: "", path: "", params: {}, children: [] }) as Parameters<typeof visit>[0]);
    return out;
  }, [tree]);

  const views = boundaryViews(rows, { baseSize, surfaceSize, mesher: "snappy", levels: [] });
  return (
    <table className="task-table">
      <thead>
        <tr>
          <th scope="col">Boundary</th>
          <th scope="col">Size</th>
          <th scope="col" className="num">Level</th>
          <th scope="col" className="num">Achieved</th>
        </tr>
      </thead>
      <tbody>
        {views.map((row) => {
          const saved = rows.find((item) => item.name === row.name);
          return (
          <tr key={row.name} data-ws={`sizing-boundary-${row.name}`}>
            <td>{row.name}</td>
            <td>
              <SavedSizeInput
                value={saved?.saved ?? ""}
                placeholder={row.size === null ? "the body's" : ""}
                label={`Mesh size of ${row.name}`}
                disabled={disabled}
                onCommit={(value) => saved ? execute("parameter.set", { node: saved.id, name: "mesh_size", value }) : Promise.resolve()}
              />
            </td>
            <td className="num">{row.level}</td>
            <td className="num">{formatMm(row.achieved)}</td>
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}
