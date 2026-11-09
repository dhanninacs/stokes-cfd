/**
 * The Add variable dialog of the Study card: the parameter to vary (a named parameter, or the quantity of
 * a node by path), the kind of domain (listed values, a range, a uniform or a normal distribution,
 * categories) and the fields that kind needs, with every number read in the quantity's display unit. It
 * builds one `study.add_variable` request; what the model refuses (a duplicate variable, a range without a
 * count or a step) shows beside the fields.
 */
import { useMemo, useState } from "react";
import { Segmented } from "../../components/ui";
import { findNode, useStore } from "../../state/store";
import { prettyUnit } from "../../util/units";
import { invoke } from "./commands";
import { openDialog } from "./dialogHost";
import { FieldRow, FormDialog, SelectInput, TextInput, UnitInput, quantityText, splitList, wholeNumber } from "./formKit";
import { displayUnitOf } from "./format";
import { groupNodes, shortParameter, targetsOf, type Target } from "./study";
import { Note } from "../../components/Note";

async function refreshTree(): Promise<void> {
  await useStore.getState().refresh();
}

// ---------------------------------------------------------------------------------------------- add a variable
type Domain = "values" | "range" | "uniform" | "normal" | "categorical";

const DOMAINS: readonly { value: Domain; label: string }[] = [
  { value: "values", label: "Values" },
  { value: "range", label: "Range" },
  { value: "uniform", label: "Uniform" },
  { value: "normal", label: "Normal" },
  { value: "categorical", label: "Categories" },
];

const DOMAIN_HINTS: Record<Domain, string> = {
  values: "The levels the study takes, listed.",
  range: "Evenly spaced levels from the minimum to the maximum.",
  uniform: "A distribution for sampling: every value between the bounds is as likely.",
  normal: "A distribution for sampling, around a mean.",
  categorical: "Unordered levels, each with a label.",
};

function AddVariableDialog({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const tree = useStore((s) => s.tree);
  const types = useStore((s) => s.nodeTypes);
  const units = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units ?? "si");
  const study = findNode(tree, studyId);
  const targets = useMemo(() => targetsOf(tree, types), [tree, types]);
  const varied = useMemo(() => new Set(study ? groupNodes(study, "variables").map((v) => shortParameter(String(v.params.parameter ?? ""))) : []), [study]);
  const free = (t: Target) => !varied.has(shortParameter(t.value));

  const [parameter, setParameter] = useState(() => targets.find(free)?.value ?? "");
  const [domain, setDomain] = useState<Domain>("values");
  const [levels, setLevels] = useState("");
  const [labels, setLabels] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [by, setBy] = useState<"count" | "step">("count");
  const [count, setCount] = useState("");
  const [step, setStep] = useState("");
  const [mean, setMean] = useState("");
  const [std, setStd] = useState("");
  const [name, setName] = useState("");

  const target = targets.find((t) => t.value === parameter);
  const unit = target ? displayUnitOf(units, system, target.dimension) : "";
  const chip = prettyUnit(unit);
  const q = (text: string) => quantityText(text, unit);
  const parameters = targets.filter((t) => t.group === "parameter");
  const quantities = targets.filter((t) => t.group === "quantity");
  const optionLabel = (t: Target) => `${t.value}${t.shown ? `  (${t.shown})` : ""}${free(t) ? "" : "  already varied"}`;

  const need = (text: string, what: string) => {
    if (!text.trim()) throw new Error(`Enter ${what}.`);
  };

  const submit = async () => {
    if (!target) throw new Error("Choose the quantity to vary.");
    const params: Record<string, unknown> = { study: studyId, parameter, domain };
    switch (domain) {
      case "values":
      case "categorical": {
        const items = splitList(levels);
        if (items.length === 0) throw new Error("Enter at least one value.");
        params.values = items.map(q);
        if (domain === "categorical") {
          const names = labels.split(",").map((s) => s.trim());
          if (labels.trim() && names.length !== items.length) throw new Error(`Give ${items.length} labels, one per value, or none.`);
          if (labels.trim()) params.labels = names;
        }
        break;
      }
      case "range":
      case "uniform": {
        need(min, "the minimum");
        need(max, "the maximum");
        params.min = q(min);
        params.max = q(max);
        if (domain === "range" && by === "step") {
          need(step, "the step");
          params.step = q(step);
        } else if (count.trim() || domain === "range") {
          const n = wholeNumber(count);
          if (n === null || n < 1) throw new Error("Levels must be a whole number of 1 or more.");
          params.count = n;
        }
        break;
      }
      case "normal": {
        need(mean, "the mean");
        need(std, "the standard deviation");
        params.mean = q(mean);
        params.std = q(std);
        if (count.trim()) {
          const n = wholeNumber(count);
          if (n === null || n < 1) throw new Error("Levels must be a whole number of 1 or more.");
          params.count = n;
        }
        break;
      }
    }
    if (name.trim()) params.name = name.trim();
    await invoke("study.add_variable", params, { toast: false });
    await refreshTree();
  };

  return (
    <FormDialog title="Add a variable" submit="Add variable" wide disabled={targets.length === 0} onClose={onClose} onSubmit={submit}>
      {targets.length === 0 ? (
        <Note className="text-[12.5px]" tip="The project has no quantity to vary. Define a parameter first.">No quantity to vary</Note>
      ) : (
        <>
          <FieldRow label="Parameter" htmlFor="xf-var-parameter" hint="The quantity this variable varies. Derived parameters follow it.">
            <SelectInput id="xf-var-parameter" value={parameter} onChange={setParameter}>
              {parameters.length > 0 && (
                <optgroup label="Parameters">
                  {parameters.map((t) => (
                    <option key={t.value} value={t.value} disabled={!free(t)}>
                      {optionLabel(t)}
                    </option>
                  ))}
                </optgroup>
              )}
              {quantities.length > 0 && (
                <optgroup label="Quantities of nodes">
                  {quantities.map((t) => (
                    <option key={t.value} value={t.value} disabled={!free(t)}>
                      {optionLabel(t)}
                    </option>
                  ))}
                </optgroup>
              )}
            </SelectInput>
          </FieldRow>
          <FieldRow label="Domain" hint={DOMAIN_HINTS[domain]}>
            <Segmented label="Domain" value={domain} onChange={setDomain} options={[...DOMAINS]} />
          </FieldRow>
          {(domain === "values" || domain === "categorical") && (
            <FieldRow label={domain === "values" ? "Values" : "Levels"} htmlFor="xf-var-values" hint="Separate them with commas.">
              <UnitInput id="xf-var-values" value={levels} onChange={setLevels} unit={chip} placeholder="1, 2, 3" />
            </FieldRow>
          )}
          {domain === "categorical" && (
            <FieldRow label="Labels" htmlFor="xf-var-labels" aside="optional" hint="One per level, separated by commas.">
              <TextInput id="xf-var-labels" value={labels} onChange={setLabels} placeholder="low, medium, high" />
            </FieldRow>
          )}
          {(domain === "range" || domain === "uniform") && (
            <div className="xf-grid2">
              <FieldRow label="Minimum" htmlFor="xf-var-min">
                <UnitInput id="xf-var-min" value={min} onChange={setMin} unit={chip} />
              </FieldRow>
              <FieldRow label="Maximum" htmlFor="xf-var-max">
                <UnitInput id="xf-var-max" value={max} onChange={setMax} unit={chip} />
              </FieldRow>
            </div>
          )}
          {domain === "range" && (
            <div className="xf-grid2">
              <FieldRow label="Levels by">
                <Segmented
                  label="How the levels are spaced"
                  value={by}
                  onChange={setBy}
                  options={[
                    { value: "count", label: "Count" },
                    { value: "step", label: "Step" },
                  ]}
                />
              </FieldRow>
              {by === "count" ? (
                <FieldRow label="Count" htmlFor="xf-var-count">
                  <TextInput id="xf-var-count" value={count} onChange={setCount} placeholder="5" />
                </FieldRow>
              ) : (
                <FieldRow label="Step" htmlFor="xf-var-step">
                  <UnitInput id="xf-var-step" value={step} onChange={setStep} unit={chip} />
                </FieldRow>
              )}
            </div>
          )}
          {domain === "uniform" && (
            <FieldRow label="Levels" htmlFor="xf-var-count" aside="optional" hint="Only a full factorial uses them; a sampler draws from the distribution.">
              <TextInput id="xf-var-count" value={count} onChange={setCount} />
            </FieldRow>
          )}
          {domain === "normal" && (
            <>
              <div className="xf-grid2">
                <FieldRow label="Mean" htmlFor="xf-var-mean">
                  <UnitInput id="xf-var-mean" value={mean} onChange={setMean} unit={chip} />
                </FieldRow>
                <FieldRow label="Standard deviation" htmlFor="xf-var-std">
                  <UnitInput id="xf-var-std" value={std} onChange={setStd} unit={chip} />
                </FieldRow>
              </div>
              <FieldRow label="Levels" htmlFor="xf-var-count" aside="optional" hint="Only a full factorial uses them; a sampler draws from the distribution.">
                <TextInput id="xf-var-count" value={count} onChange={setCount} />
              </FieldRow>
            </>
          )}
          <FieldRow label="Name" htmlFor="xf-var-name" aside="optional" hint="Defaults to the parameter's name; it heads the column of the design table.">
            <TextInput id="xf-var-name" value={name} onChange={setName} />
          </FieldRow>
        </>
      )}
    </FormDialog>
  );
}

export function openAddVariable(studyId: string): void {
  openDialog((close) => <AddVariableDialog studyId={studyId} onClose={close} />);
}
