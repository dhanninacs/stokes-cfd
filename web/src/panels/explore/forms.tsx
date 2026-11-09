/**
 * The Explore dialogs: a new study, a design point added by hand, and three of the four kinds of definition
 * a study is made of (a response, an objective, a constraint; the variable's dialog, the largest, is in
 * VariableDialog.tsx and is re-exported from here). Each one gathers the fields of a
 * `study.*` command and runs it through `invoke`, so the console shows the command and the journal
 * records it; what the server refuses (an expression that does not evaluate, a duplicate variable) shows
 * beside the fields and the dialog stays open. The checks here are only the ones a form needs to build a
 * request; the rules of the model are the server's.
 *
 * `open…` functions are what the panels and the actions call: each opens its dialog in a root of its
 * own (dialogHost.tsx), so they work whether or not an Explore panel is on screen.
 */
import { useState } from "react";
import { Segmented } from "../../components/ui";
import { findByPath, findNode, useStore } from "../../state/store";
import { prettyUnit } from "../../util/units";
import { uniqueName } from "../nodeRules";
import { addPoint, createStudy, currentStudy, invoke } from "./commands";
import { openDialog } from "./dialogHost";
import { FieldRow, FormDialog, SelectInput, TextInput, UnitInput, quantityText, wholeNumber } from "./formKit";
import { displayUnitOf } from "./format";
import { useExplore } from "./store";
import { PARAMETERS_PATH, groupNodes, listStudies } from "./study";
import { Note } from "../../components/Note";

const SAMPLERS: readonly (readonly [string, string])[] = [
  ["latin_hypercube", "Latin hypercube"],
  ["sobol", "Sobol"],
  ["halton", "Halton"],
  ["optimal_space_filling", "Optimal space filling"],
  ["monte_carlo", "Monte Carlo"],
  ["central_composite", "Central composite"],
  ["box_behnken", "Box-Behnken"],
  ["full_factorial", "Full factorial"],
];
/** The samplers that take a number of points (the others place their own). */
const SIZED = new Set(["latin_hypercube", "sobol", "halton", "optimal_space_filling", "monte_carlo"]);

async function refreshTree(): Promise<void> {
  await useStore.getState().refresh();
}

// ---------------------------------------------------------------------------------------------- new study
function NewStudyDialog({ onClose }: { onClose: () => void }) {
  const tree = useStore((s) => s.tree);
  const [name, setName] = useState(() => uniqueName("study", listStudies(tree)));
  const [type, setType] = useState<"sweep" | "doe">("sweep");
  const [sampler, setSampler] = useState("latin_hypercube");
  const [count, setCount] = useState("20");
  const [description, setDescription] = useState("");

  return (
    <FormDialog
      title="New study"
      submit="Create"
      onClose={onClose}
      onSubmit={async () => {
        const trimmed = name.trim();
        if (!trimmed) throw new Error("Give the study a name.");
        let points: number | undefined;
        if (type === "doe" && SIZED.has(sampler)) {
          const n = wholeNumber(count);
          if (n === null || n < 1) throw new Error("Points must be a whole number of 1 or more.");
          points = n;
        }
        await createStudy({ name: trimmed, type, description: description.trim(), sampler: type === "doe" ? sampler : undefined, count: points });
      }}
    >
      <FieldRow label="Name" htmlFor="xf-study-name" hint="Letters, digits and underscores.">
        <TextInput id="xf-study-name" value={name} onChange={setName} />
      </FieldRow>
      <FieldRow label="Type" hint={type === "sweep" ? "Every combination of the variables' levels, or points you add by hand." : "A design of experiments: a sampler places the points."}>
        <Segmented
          label="Study type"
          value={type}
          onChange={setType}
          options={[
            { value: "sweep", label: "Sweep" },
            { value: "doe", label: "Design of experiments" },
          ]}
        />
      </FieldRow>
      {type === "doe" && (
        <div className="xf-grid2">
          <FieldRow label="Sampler" htmlFor="xf-study-sampler">
            <SelectInput id="xf-study-sampler" value={sampler} onChange={setSampler}>
              {SAMPLERS.filter(([id]) => id !== "full_factorial").map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </SelectInput>
          </FieldRow>
          {SIZED.has(sampler) && (
            <FieldRow label="Points" htmlFor="xf-study-count">
              <TextInput id="xf-study-count" value={count} onChange={setCount} />
            </FieldRow>
          )}
        </div>
      )}
      <FieldRow label="Description" htmlFor="xf-study-description" aside="optional">
        <TextInput id="xf-study-description" value={description} onChange={setDescription} />
      </FieldRow>
    </FormDialog>
  );
}

export function openNewStudy(): void {
  openDialog((close) => <NewStudyDialog onClose={close} />);
}

// ---------------------------------------------------------------------------------------------- add a point
function AddPointDialog({ onClose }: { onClose: () => void }) {
  const results = useExplore((s) => s.results);
  const dataset = useExplore((s) => s.dataset);
  const [values, setValues] = useState<Record<string, string>>({});
  const variables = results?.variables ?? [];

  return (
    <FormDialog
      title="Add a design point"
      submit="Add point"
      disabled={variables.length === 0}
      onClose={onClose}
      onSubmit={async () => {
        const inputs: Record<string, string> = {};
        for (const variable of variables) {
          const text = (values[variable.name] ?? "").trim();
          if (text) inputs[variable.name] = quantityText(text, variable.displayUnit);
        }
        await addPoint(inputs);
      }}
    >
      {variables.length === 0 ? (
        <Note className="text-[12.5px]" tip="This study has no variables yet. Add one in its properties, then add points.">No variables yet</Note>
      ) : (
        <>
          <Note className="leading-snug" tip="A variable left empty takes the value it has in the base case.">Empty means the base case</Note>
          {variables.map((variable) => {
            const categories = dataset?.byName.get(variable.name)?.categories;
            const id = `xf-point-${variable.name}`;
            return (
              <FieldRow key={variable.name} label={variable.name} htmlFor={id}>
                {categories ? (
                  <SelectInput id={id} value={values[variable.name] ?? ""} onChange={(v) => setValues((s) => ({ ...s, [variable.name]: v }))}>
                    <option value="">Base value</option>
                    {[...categories].map(([value, label]) => (
                      <option key={value} value={String(value)}>
                        {label}
                      </option>
                    ))}
                  </SelectInput>
                ) : (
                  <UnitInput
                    id={id}
                    value={values[variable.name] ?? ""}
                    onChange={(v) => setValues((s) => ({ ...s, [variable.name]: v }))}
                    unit={prettyUnit(variable.displayUnit)}
                    placeholder="base value"
                  />
                )}
              </FieldRow>
            );
          })}
        </>
      )}
    </FormDialog>
  );
}

export function openAddPoint(): void {
  if (!currentStudy()) {
    useStore.getState().pushToast("info", "No study yet. Create one with New study.");
    return;
  }
  openDialog((close) => <AddPointDialog onClose={close} />);
}

// ---------------------------------------------------------------------------------------------- add a response
function AddResponseDialog({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const tree = useStore((s) => s.tree);
  const [expression, setExpression] = useState("");
  const [label, setLabel] = useState("");
  const names = (findByPath(tree, PARAMETERS_PATH)?.children ?? []).filter((n) => n.type === "parameter").map((n) => n.name);

  return (
    <FormDialog
      title="Add a response"
      submit="Add response"
      onClose={onClose}
      onSubmit={async () => {
        if (!expression.trim()) throw new Error("Enter the expression the response records.");
        const params: Record<string, unknown> = { study: studyId, expression: expression.trim() };
        if (label.trim()) params.label = label.trim();
        await invoke("study.add_response", params, { toast: false });
        await refreshTree();
      }}
    >
      <FieldRow label="Expression" htmlFor="xf-resp-expression" hint="Evaluated for every design point, with the point's values in place of the varied parameters.">
        <TextInput id="xf-resp-expression" value={expression} onChange={setExpression} placeholder="dp_dyn  or  0.5 * rho * U_in**2" mono />
      </FieldRow>
      <FieldRow label="Label" htmlFor="xf-resp-label" aside="optional" hint="The name the table and the plots show.">
        <TextInput id="xf-resp-label" value={label} onChange={setLabel} />
      </FieldRow>
      {names.length > 0 && <p className="muted mono text-[11.5px] leading-snug">Parameters: {names.join(", ")}</p>}
    </FormDialog>
  );
}

export function openAddResponse(studyId: string): void {
  openDialog((close) => <AddResponseDialog studyId={studyId} onClose={close} />);
}

// ---------------------------------------------------------------------------------------------- set an objective
function SetObjectiveDialog({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const tree = useStore((s) => s.tree);
  const study = findNode(tree, studyId);
  const responses = study ? groupNodes(study, "responses") : [];
  const objectives = study ? groupNodes(study, "objectives") : [];
  const directionOf = (responseId: string): "minimise" | "maximise" | null => {
    const found = objectives.find((o) => o.params.response === responseId);
    return found ? (found.params.direction === "maximise" ? "maximise" : "minimise") : null;
  };
  const [response, setResponse] = useState(responses[0]?.name ?? "");
  const [direction, setDirection] = useState<"minimise" | "maximise">(() => directionOf(responses[0]?.id ?? "") ?? "minimise");
  const current = responses.find((r) => r.name === response);
  const existing = current ? directionOf(current.id) : null;

  return (
    <FormDialog
      title="Set an objective"
      submit={existing ? "Change objective" : "Set objective"}
      disabled={responses.length === 0}
      onClose={onClose}
      onSubmit={async () => {
        await invoke("study.set_objective", { study: studyId, response, direction }, { toast: false });
        await refreshTree();
      }}
    >
      {responses.length === 0 ? (
        <Note className="text-[12.5px]" tip="This study has no responses yet. Add one, then say which way to push it.">No responses yet</Note>
      ) : (
        <>
          <FieldRow label="Response" htmlFor="xf-obj-response" hint={existing ? `It is already an objective: ${existing}.` : "Two objectives give the Pareto plot its axes."}>
            <SelectInput
              id="xf-obj-response"
              value={response}
              onChange={(next) => {
                setResponse(next);
                const picked = responses.find((r) => r.name === next);
                const had = picked ? directionOf(picked.id) : null;
                if (had) setDirection(had);
              }}
            >
              {responses.map((r) => (
                <option key={r.id} value={r.name}>
                  {String(r.params.label || r.name)}
                </option>
              ))}
            </SelectInput>
          </FieldRow>
          <FieldRow label="Direction">
            <Segmented
              label="Direction"
              value={direction}
              onChange={setDirection}
              options={[
                { value: "minimise", label: "Minimise" },
                { value: "maximise", label: "Maximise" },
              ]}
            />
          </FieldRow>
        </>
      )}
    </FormDialog>
  );
}

export function openSetObjective(studyId: string): void {
  openDialog((close) => <SetObjectiveDialog studyId={studyId} onClose={close} />);
}

// ---------------------------------------------------------------------------------------------- add a constraint
function AddConstraintDialog({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const tree = useStore((s) => s.tree);
  const units = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units ?? "si");
  const study = findNode(tree, studyId);
  const responses = study ? groupNodes(study, "responses") : [];
  const [expression, setExpression] = useState("");
  const [operator, setOperator] = useState<"<=" | ">=" | "==">("<=");
  const [limit, setLimit] = useState("");

  // A limit on a single response is read in that response's display unit; any other expression has no
  // dimension the form knows, so a bare number is SI there (the server checks the units).
  const single = responses.find((r) => r.name === expression.trim());
  const unit = single ? displayUnitOf(units, system, String(single.params.dimension ?? "dimensionless")) : "";
  const chip = prettyUnit(unit);

  return (
    <FormDialog
      title="Add a constraint"
      submit="Add constraint"
      onClose={onClose}
      onSubmit={async () => {
        if (!expression.trim()) throw new Error("Enter the expression to limit.");
        if (!limit.trim()) throw new Error("Enter the limit.");
        await invoke("study.add_constraint", { study: studyId, expression: expression.trim(), operator, limit: quantityText(limit, unit) }, { toast: false });
        await refreshTree();
      }}
    >
      <FieldRow label="Expression" htmlFor="xf-con-expression" hint="Over the study's responses by name, and the parameters.">
        <TextInput id="xf-con-expression" value={expression} onChange={setExpression} placeholder={responses[0]?.name ?? "dp_dyn"} mono />
      </FieldRow>
      <div className="xf-grid2 xf-grid-op">
        <FieldRow label="Must be">
          <Segmented
            label="Operator"
            value={operator}
            onChange={setOperator}
            options={[
              { value: "<=", label: "≤" },
              { value: ">=", label: "≥" },
              { value: "==", label: "=" },
            ]}
          />
        </FieldRow>
        <FieldRow label="Limit" htmlFor="xf-con-limit">
          <UnitInput id="xf-con-limit" value={limit} onChange={setLimit} unit={chip} />
        </FieldRow>
      </div>
      {responses.length > 0 && <p className="muted mono text-[11.5px] leading-snug">Responses: {responses.map((r) => r.name).join(", ")}</p>}
    </FormDialog>
  );
}

export function openAddConstraint(studyId: string): void {
  openDialog((close) => <AddConstraintDialog studyId={studyId} onClose={close} />);
}

export { openAddVariable } from "./VariableDialog";
