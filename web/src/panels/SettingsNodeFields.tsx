import { useEffect, useState, type ReactNode } from "react";
import type { NodeSummary, NodeTypeSchema, ParamSchema } from "../api/types";
import { advancedNames, ALSO_ADVANCED, changedFromDefault, setupContextOf, visibleFields } from "../setup/basicSets";
import { turbulenceOptionsFor } from "./runPanelModel";
import type { SolveEngine } from "./runApi";
import type { MesherInfo } from "./meshPanelModel";
import { boxTexts, boxUnit, boxValue } from "./meshPanelModel";
import BasicAdvanced from "./BasicAdvanced";
import SchemaFields from "./SchemaFields.tsx";
import SettingsFields from "./SettingsFields";
import { Note } from "../components/Note";
import { MESH_SETTINGS_SECTIONS, SOLVER_SETTINGS_SECTIONS } from "./settingsPresentation";
import { coresDescription } from "./mesherKinds";

export interface SettingsNodeFieldsProps {
  node: NodeSummary;
  schema?: NodeTypeSchema;
  mode: "solver" | "mesh";
  initialSection?: string;
  /**
   * `flat` (the default) is today's page: every section. `basic` puts the Basic controls first and the same
   * sections under a closed Advanced disclosure (`setup/basicSets.ts` says which are Basic).
   */
  layout?: "flat" | "basic";
  /** The Basic controls the page draws itself (the Mesh page's fineness chips); the solver page's are fields. */
  basicContent?: ReactNode;
  /** Open Advanced (a finding names these settings). */
  openAdvanced?: boolean;
  /** Fields another page on screen already shows, left out of Advanced (the Sizing task's base size). */
  hideInAdvanced?: readonly string[];
  onCommit(name: string, value: unknown): void | Promise<unknown>;
  onEdit?(name: string): void;
  errors?: Record<string, string>;
  disabled?: boolean;
  tree?: NodeSummary | null;
  engines?: SolveEngine[];
  enginesLoaded?: boolean;
  meshers?: MesherInfo[];
  meshersLoaded?: boolean;
}

function CatalogChoice({
  label,
  items,
  value,
  disabled,
  onChoose,
}: {
  label: string;
  items: { id: string; label: string; built: boolean; why: string; title?: string }[];
  value: string;
  disabled: boolean;
  onChoose: (id: string) => void;
}) {
  const current = items.find((item) => item.id === value);
  return (
    <div data-ws={label === "Mesher" ? "mesh-meshers" : "run-engines"}>
      <label className="settings-fields__catalog-label">
        <span>{label}</span>
        <select aria-label={label} className="field w-full" value={value} disabled={disabled} onChange={(event) => {
          const next = items.find((item) => item.id === event.target.value);
          if (!disabled && next?.built && next.id !== value) onChoose(next.id);
        }}>
          {items.map((item) => <option key={item.id} value={item.id} disabled={!item.built} title={item.title ?? (item.built ? item.label : `${item.label} is not available: ${item.why}`)}>
            {item.label}{!item.built ? " — unavailable" : ""}
          </option>)}
        </select>
      </label>
      {/* The reasons are tooltips: why the chosen one is what it is, and why the others are not offered. */}
      {current?.why && <Note className="mesh-mesher-why" tip={current.why}>{label === "Mesher" ? "About this mesher" : "About this engine"}</Note>}
      {items.some((item) => !item.built) && (
        <Note className="settings-fields__availability" tip={items.filter((item) => !item.built).map((item) => `${item.label} is not available: ${item.why || "Not supported by this build"}`).join("\n")}>
          {items.filter((item) => !item.built).length} unavailable
        </Note>
      )}
    </div>
  );
}

function BoxFields({ node, disabled, onCommit, onEdit, error }: {
  node: NodeSummary;
  disabled: boolean;
  onCommit(name: string, value: unknown): void | Promise<unknown>;
  onEdit?(name: string): void;
  error: string;
}) {
  const unit = boxUnit(node.displays);
  const stored = boxTexts((node.params as Record<string, unknown>).box, unit);
  const storedKey = JSON.stringify([stored, unit]);
  const [texts, setTexts] = useState<string[]>(stored);
  useEffect(() => setTexts(stored), [storedKey]);
  const [local, setLocal] = useState("");
  const labels = ["x min", "y min", "z min", "x max", "y max", "z max"];
  const apply = () => {
    if (disabled) return;
    if (JSON.stringify(texts) === JSON.stringify(stored)) return;
    const value = boxValue(texts, unit);
    if (value === null) {
      if (texts.some((text) => text.trim() !== "")) setLocal("Fill all six numbers.");
      return;
    }
    setLocal("");
    void Promise.resolve(onCommit("box", value)).catch(() => undefined);
  };
  const shown = local || error;
  return (
    <div className="mesh-box" role="group" aria-label={`Box, in ${unit}`} data-ws="mesh-box">
      <div className="mb-1 text-[12.5px]">Box <span className="muted">({unit})</span></div>
      <div className="mesh-box-grid">
        {labels.map((label, index) => (
          <label key={label} className="mesh-box-cell">
            <span className="muted">{label}</span>
            <input
              className={`field ${shown !== "" ? "error" : ""}`}
              inputMode="decimal"
              spellCheck={false}
              autoComplete="off"
              aria-label={`Box ${label} in ${unit}`}
              aria-invalid={shown !== ""}
              disabled={disabled}
              value={texts[index]}
              onChange={(event) => {
                const next = [...texts];
                next[index] = event.target.value;
                setTexts(next);
                setLocal("");
                onEdit?.("box");
              }}
              onBlur={apply}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  apply();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  setTexts(stored);
                  setLocal("");
                  onEdit?.("box");
                }
              }}
            />
          </label>
        ))}
      </div>
      {shown !== "" && <div role="alert" className="mt-1 text-[12px]" style={{ color: "var(--danger)" }}>{shown}</div>}
    </div>
  );
}

export default function SettingsNodeFields({
  node,
  schema,
  mode,
  initialSection,
  layout = "flat",
  basicContent,
  openAdvanced = false,
  hideInAdvanced = [],
  onCommit,
  onEdit,
  errors,
  disabled = false,
  tree,
  engines = [],
  enginesLoaded = false,
  meshers = [],
  meshersLoaded = false,
}: SettingsNodeFieldsProps) {
  if (schema === undefined) return null;
  const params = node.params as Record<string, unknown>;
  const engineId = typeof params.engine === "string" ? params.engine : "";
  const mesherId = typeof params.mesher === "string" ? params.mesher : "blockmesh";
  const domain = typeof params.domain === "string" ? params.domain : "part";
  const hasEngineCatalog = mode === "solver" && enginesLoaded && engines.length > 0;
  const hasMesherCatalog = mode === "mesh" && meshersLoaded && meshers.length > 0;
  const sections = mode === "solver"
    ? SOLVER_SETTINGS_SECTIONS
    : MESH_SETTINGS_SECTIONS.map((section) => section.id !== "mesh-execution"
      ? section
      : {
          ...section,
          description: coresDescription(mesherId, meshers.find((m) => m.id === mesherId)?.parallel ?? ""),
        });
  const properties: Record<string, ParamSchema> = { ...schema.properties };
  if (mode === "solver" && properties.turbulence) {
    const offered = properties.turbulence.enum;
    if (offered) {
      const allowed = turbulenceOptionsFor(engines, engineId, offered);
      const selected = params.turbulence;
      if (typeof selected === "string" && !allowed.includes(selected)) allowed.push(selected);
      properties.turbulence = { ...properties.turbulence, enum: allowed };
    }
  }
  if (mode === "mesh" && properties.domain) {
    const currentMesher = meshers.find((item) => item.id === mesherId);
    if (currentMesher !== undefined && currentMesher.domains.length > 0) {
      const allowed = properties.domain.enum?.filter((option) => currentMesher.domains.includes(option)) ?? [];
      if (typeof domain === "string" && !allowed.includes(domain)) allowed.push(domain);
      properties.domain = { ...properties.domain, enum: allowed };
    }
  }
  // What the run's own settings say decides which fields mean something (`visibleFields`); hidden ones keep their value.
  const type = mode === "solver" ? "solver_settings" : "mesh_settings";
  const names = Object.keys(properties);
  const levels = visibleFields(type, names, params, setupContextOf(params));
  const basicLayout = layout === "basic";
  const advancedList = advancedNames(type, levels, names).filter((name) => !hideInAdvanced.includes(name));
  const include = (name: string) => (basicLayout ? advancedList.includes(name) : levels[name] !== "hidden");
  const renderField = (name: string) => {
    if (name === "engine" && hasEngineCatalog) {
      const items = engines.map((item) => ({ ...item }));
      if (engineId && !items.some((item) => item.id === engineId)) items.push({ id: engineId, label: "Current engine unavailable", why: "", built: false, turbulence: [] });
      return <CatalogChoice key="engine-choice" label="Solver engine" items={items} value={engineId} disabled={disabled} onChoose={(id) => void Promise.resolve(onCommit("engine", id)).catch(() => undefined)} />;
    }
    if (name === "mesher" && hasMesherCatalog) {
      const items = meshers
        .map((item) => ({ ...item, title: item.built ? `${item.label}: ${item.domains.join(" or ")} domain` : `${item.label} is not available: ${item.why}` }));
      if (mesherId && !items.some((item) => item.id === mesherId)) items.unshift({ id: mesherId, label: "Current mesher unavailable for this domain", built: false, domains: [], why: "", outputStructures: [], title: "Current mesher unavailable for this domain" });
      return <CatalogChoice key="mesher-choice" label="Mesher" items={items} value={mesherId} disabled={disabled} onChoose={(id) => void Promise.resolve(onCommit("mesher", id)).catch(() => undefined)} />;
    }
    if (name === "box" && domain === "box") {
      return <BoxFields key="mesh-box-fields" node={node} disabled={disabled} onCommit={onCommit} onEdit={onEdit} error={errors?.box ?? ""} />;
    }
    return undefined;
  };
  const fields = (
    <SettingsFields
      properties={properties}
      values={params}
      displays={node.displays}
      status={node.status}
      nodes={tree}
      onCommit={onCommit}
      onEdit={onEdit}
      disabled={disabled}
      errors={errors}
      include={include}
      sections={sections}
      initialSection={initialSection}
      renderField={renderField}
      tools={basicLayout ? "search" : "full"}
    />
  );
  if (!basicLayout) return fields;
  // Basic: the mesh page draws its own controls; the solver page's are the flow model and the iteration limit.
  const basicFieldNames = names.filter((name) => levels[name] === "basic");
  const basicProperties: Record<string, ParamSchema> = Object.fromEntries(
    basicFieldNames.map((name) => [name, name === "turbulence" ? { ...properties[name], title: "Flow model" } : properties[name]]),
  );
  const basic = mode === "mesh"
    ? basicContent
    : basicFieldNames.length > 0 && (
        <div data-ws="solver-basic">
          <SchemaFields
            properties={basicProperties}
            values={params}
            displays={node.displays}
            status={node.status}
            nodes={tree}
            onCommit={onCommit}
            onEdit={onEdit}
            disabled={disabled}
            errors={errors}
          />
        </div>
      );
  const also = ALSO_ADVANCED[type] ?? [];
  return (
    <BasicAdvanced
      type={type}
      basic={basic}
      advanced={advancedList.length > 0 ? fields : null}
      changed={changedFromDefault(properties, params, advancedList.filter((name) => !also.includes(name))).length > 0}
      hasError={advancedList.some((name) => Boolean(errors?.[name]))}
      reveal={initialSection !== undefined || openAdvanced ? 1 : 0}
    />
  );
}
