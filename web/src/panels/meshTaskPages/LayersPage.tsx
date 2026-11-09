/**
 * The Boundary layers page (brief M-505, task 6): set layers once for all walls, override single
 * walls, get a first-layer height without a spreadsheet, and after meshing see how well each
 * wall's layers grew.
 *
 * **Every setting is one `mesh.configure`** (one undo step); the every-wall button touches walls
 * only (symmetry never inherits, per 1130); and the page never says "layers added" until a mesh
 * has measured the coverage (M-504's 80 % rule) — before that the line says "requested".
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../../state/store";
import SchemaFields from "../SchemaFields.tsx";
import { useMeshContext } from "../meshRun";
import { findMeshNodes, isActive, phaseNow, readResult } from "../meshPanelModel";
import {
  HELPER_ASSUMPTIONS,
  addLayersVisible,
  addThreeLayersPayload,
  coverageView,
  helperLine,
  nonWallLine,
  requestedWords,
  wallViews,
  type Coverage,
  type LayerSettings,
  type WallRow,
} from "../layersModel";
import { Note } from "../../components/Note";

interface LayerEntry {
  first_height: number;
  y_centre: number;
  reynolds: number;
  skin_friction: number;
  method: string;
}

export default function LayersPage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const mesh = useStore((s) => s.mesh);
  const busy = useMeshContext((s) => s.busy);
  const resultNode = useStore((s) => s.tree);
  const [error, setError] = useState("");
  const [helper, setHelper] = useState<LayerEntry | null>(null);
  const [helperOpen, setHelperOpen] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const locked = !connected || busy || isActive(phaseNow(readResult(findMeshNodes(tree).result), mesh));

  const settingsNode = useMemo(() => tree?.children
    ?.find((c) => c.path === "/mesh")
    ?.children?.find((c) => c.path === "/mesh/settings") ?? null, [tree]);
  const settings = useMemo(() => {
    const p = (settingsNode?.params ?? {}) as Record<string, unknown>;
    const num = (name: string, fallback: number): number =>
      typeof p[name] === "object" && p[name] !== null
        ? (p[name] as { value: number }).value
        : typeof p[name] === "number"
          ? p[name]
          : fallback;
    const mode = p.layer_mode === "first_height" ? "first_height" : "final_ratio";
    return {
      layers: typeof p.layers === "number" ? p.layers : 0,
      layer_mode: mode,
      expansion_ratio: num("expansion_ratio", 1.2),
      final_ratio: num("final_ratio", 0.3),
      first_layer_height: num("first_layer_height", 0),
      min_thickness_ratio: num("min_thickness_ratio", 0.1),
    } satisfies LayerSettings;
  }, [settingsNode]);

  /** The walls and the non-walls, from the tree's boundaries. */
  const { walls, nonWalls } = useMemo(() => {
    const wallsOut: WallRow[] = [];
    const nonWallsOut: string[] = [];
    const visit = (node: {
      type: string;
      name: string;
      params: Record<string, unknown>;
      children?: unknown[];
    }): void => {
      if (node.type === "boundary") {
        const kind = String(node.params.kind ?? "");
        const raw = node.params.layers;
        const override = typeof raw === "number" && !Number.isNaN(raw) ? raw : null;
        if (kind === "wall") {
          wallsOut.push({ name: node.name, override });
        } else if (kind !== "") {
          nonWallsOut.push(node.name);
        }
      }
      for (const child of node.children ?? []) {
        visit(child as Parameters<typeof visit>[0]);
      }
    };
    visit((tree ?? { type: "", name: "", params: {}, children: [] }) as Parameters<typeof visit>[0]);
    return { walls: wallsOut, nonWalls: nonWallsOut };
  }, [tree]);

  /** The meshed coverage, from the result's `meta.layers` (M-504's table). */
  const coverage: Coverage[] | null = useMemo(() => {
    const results = resultNode?.children?.find((c) => c.path === "/mesh/results");
    const result = results?.children?.[0];
    const table = result?.meta?.layers;
    return Array.isArray(table) && table.length > 0 ? (table as Coverage[]) : null;
  }, [resultNode]);

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

  const configureMany = async (values: Record<string, unknown>) => {
    setError("");
    try {
      await execute("mesh.configure", { values });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const runHelper = (yPlus: number, velocity: string, length: string) => {
    void execute("mesh.layer_height", { y_plus: yPlus, velocity, length })
      .then((answer) => setHelper(answer as LayerEntry))
      .catch((err: Error) => setError(err.message));
  };

  const bodyHasLayers = false; // the domain body's layers field is read on the body page
  const views = wallViews(walls, settings, bodyHasLayers);
  const coverage0 = coverage;

  return (
    <div className="task-page" data-ws="task-layers">
      <Note className="task-purpose" tip="Thin cells along walls for the near-wall flow: how many, how thick, on which walls">
        Cells along walls
      </Note>
      {error !== "" && (
        <p role="alert" className="mesh-error">
          {error}
        </p>
      )}
      {addLayersVisible(settings) && (
        <button
          type="button"
          className="link no-drag"
          data-ws="layers-add-three"
          title="One setting, every wall: count 3, expansion 1.2, final ratio 0.3"
          disabled={locked}
          onClick={() => void configureMany(addThreeLayersPayload())}
        >
          Add 3 layers on every wall
        </button>
      )}
      {settingsNode !== null && nodeTypes.mesh_settings !== undefined && (
        <SchemaFields
          key={`${settingsNode.id}:layers`}
          properties={Object.fromEntries(
            ["layers", "layer_mode", "expansion_ratio", "final_ratio", "first_layer_height", "min_thickness_ratio"]
              .filter((name) => name !== "final_ratio" || settings.layer_mode === "final_ratio")
              .filter((name) => name !== "first_layer_height" || settings.layer_mode === "first_height")
              .filter((name) => nodeTypes.mesh_settings.properties[name] !== undefined)
              .map((name) => [name, nodeTypes.mesh_settings.properties[name]]),
          )}
          values={settingsNode.params}
          displays={settingsNode.displays}
          status={settingsNode.status}
          disabled={locked}
          errors={fieldErrors}
          onEdit={(name) => setFieldErrors((current) => ({ ...current, [name]: "" }))}
          onCommit={configure}
        />
      )}
      {requestedWords(settings, walls.length) !== "" && (
        <p className="muted task-note" data-ws="layers-requested">
          {requestedWords(settings, walls.length)}
        </p>
      )}
      {views.length > 0 && (
        <>
          <div className="section-title">Walls</div>
          <table className="task-table">
            <thead>
              <tr>
                <th scope="col">Wall</th>
                <th scope="col">Count</th>
                <th scope="col">Coverage</th>
              </tr>
            </thead>
            <tbody>
              {views.map((row) => {
                const cov = coverageView(row.name, coverage0);
                return (
                  <tr key={row.name} data-ws={`layers-${row.name}`}>
                    <td>{row.name}</td>
                    <td>
                        <WallOverrideInput
                          name={row.name}
                          override={row.override}
                          placeholder={row.placeholder}
                          disabled={locked}
                          onCommit={async (value) => {
                            const boundary = findBoundaryId(tree, row.name);
                            if (boundary === null) return;
                            await execute("parameter.set", { node: boundary, name: "layers", value });
                          }}
                        />
                    </td>
                    <td>
                      {cov === null ? (
                        <span className="muted">—</span>
                      ) : (
                        <span className={cov.warning ? "task-warning-text" : ""}>{cov.text}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      {nonWalls.length > 0 && (
        <p className="muted task-note" data-ws="layers-non-walls">
          {nonWallLine(nonWalls)}
        </p>
      )}
      <button
        type="button"
        className="link no-drag"
        data-ws="layers-helper-toggle"
        onClick={() => setHelperOpen((o) => !o)}
      >
        {helperOpen ? "Hide" : "First layer height from a target y+"}
      </button>
      {helperOpen && <Helper onRun={runHelper} result={helper} onUse={configureMany} disabled={locked} />}
    </div>
  );
}

function findBoundaryId(tree: ReturnType<typeof useStore.getState>["tree"], name: string): string | null {
  let found: string | null = null;
  const visit = (node: { type: string; id: string; name: string; children?: unknown[] }): void => {
    if (node.type === "boundary" && node.name === name) {
      found = node.id;
      return;
    }
    for (const child of node.children ?? []) {
      if (found === null) visit(child as Parameters<typeof visit>[0]);
    }
  };
  visit((tree ?? { type: "", id: "", name: "", children: [] }) as Parameters<typeof visit>[0]);
  return found;
}

/** The wall override follows saved values, including undo, and keeps a refused draft visible. */
function WallOverrideInput({
  name,
  override,
  placeholder,
  disabled,
  onCommit,
}: {
  name: string;
  override: number | null;
  placeholder: string;
  disabled: boolean;
  onCommit(value: number | null): Promise<unknown>;
}) {
  const [draft, setDraft] = useState(override === null ? "" : String(override));
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const lastSubmitted = useRef<string | null>(null);
  useEffect(() => {
    setDraft(override === null ? "" : String(override));
    setError("");
    lastSubmitted.current = null;
  }, [override]);
  const save = () => {
    if (disabled || inFlight.current) return;
    if (draft.trim() === "") {
      if (override === null) return;
      if (lastSubmitted.current === "<clear>") return;
      inFlight.current = true;
      lastSubmitted.current = "<clear>";
      void onCommit(null).then(() => setError(""), (err: unknown) => { lastSubmitted.current = null; setError(err instanceof Error ? err.message : String(err)); }).finally(() => { inFlight.current = false; });
      return;
    }
    const value = Number(draft);
    if (!Number.isInteger(value) || value < 0) {
      setError("Enter a whole number of layers, 0 or greater.");
      return;
    }
    if (value === override) {
      setError("");
      return;
    }
    if (lastSubmitted.current === String(value)) return;
    inFlight.current = true;
    lastSubmitted.current = String(value);
    void onCommit(value).then(() => setError(""), (err: unknown) => { lastSubmitted.current = null; setError(err instanceof Error ? err.message : String(err)); }).finally(() => { inFlight.current = false; });
  };
  return (
    <>
      <input
        className="task-input"
        placeholder={placeholder}
        value={draft}
        disabled={disabled}
        aria-label={`Layer count on ${name}`}
        aria-invalid={error !== ""}
        onChange={(e) => { setDraft(e.target.value); setError(""); lastSubmitted.current = null; }}
        onBlur={save}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); save(); } }}
      />
      {error !== "" && <span role="alert" className="task-warning-text">{error}</span>}
    </>
  );
}

/** The y+ helper: y+, speed, length → the height, with the assumptions line under the result. */
function Helper({
  onRun,
  result,
  onUse,
  disabled,
}: {
  onRun: (yPlus: number, velocity: string, length: string) => void;
  result: LayerEntry | null;
  onUse: (values: Record<string, unknown>) => void;
  disabled: boolean;
}) {
  const [yPlus, setYPlus] = useState("1");
  const [velocity, setVelocity] = useState("10 [m/s]");
  const [length, setLength] = useState("1 [m]");
  return (
    <div className="task-fields" data-ws="layers-helper">
      <label className="task-field">
        <span>Wanted y+</span>
        <input className="task-input" value={yPlus} onChange={(e) => setYPlus(e.target.value)} />
      </label>
      <label className="task-field">
        <span>Speed</span>
        <input className="task-input" value={velocity} onChange={(e) => setVelocity(e.target.value)} />
      </label>
      <label className="task-field">
        <span>Reference length</span>
        <input className="task-input" value={length} onChange={(e) => setLength(e.target.value)} />
      </label>
      <button
        type="button"
        className="link no-drag"
        onClick={() => onRun(Number(yPlus), velocity, length)}
      >
        Estimate
      </button>
      {result !== null && (
        <>
          <p className="task-note" data-ws="layers-helper-result">
            {helperLine(result)}
          </p>
          {/* **The assumptions line, directly under the result** (brief M-505 §UI). */}
          <p className="muted task-note" data-ws="layers-helper-assumptions">
            {HELPER_ASSUMPTIONS}
          </p>
          <button
            type="button"
            className="link no-drag"
            data-ws="layers-use-height"
            disabled={disabled}
            onClick={() =>
              onUse({
                layer_mode: "first_height",
                first_layer_height: result.first_height,
              })
            }
          >
            Use this height
          </button>
        </>
      )}
    </div>
  );
}
