/**
 * The Guided cards of an imported OpenFOAM case (a tutorial): the few settings a user changes most,
 * editable in place, with the rest one click away in Full mode.
 *
 * A case has no CAD model, so the Fluid, Boundaries and Run cards cannot show the CAD controls; before
 * this they only said "From the case", and a tutorial's flow model, inlet velocity or end time could be
 * changed only by finding the case in Full mode's tree. Each card here picks entries out of the case's
 * own dictionaries (`case.settings`) by path - `RAS/model`, `boundaryField/<patch>/value`, `endTime` -
 * and edits them with the same `case.set` / `case.reset` the Full-mode case panel uses, so an edit here
 * is the same recorded override, shown edited there too.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import { type SettingsRow, valueFor } from "../../panels/caseCardModel";
import { useStore } from "../../state/store";
import GuidedCard from "../GuidedCard";
import { Note } from "../controls";
import type { GuidedStep } from "../guidedModel";

import { CASE_PICKS, type CasePick, pickRows, rowLabel } from "./caseSettingsPicks";

export { CASE_PICKS };

interface FileRows {
  file: string;
  rows: SettingsRow[];
}

/** The picked settings of one case, editable; used inside a Guided card. */
export function CaseQuickSettings({ caseId, picks }: { caseId: string; picks: readonly CasePick[] }) {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const [groups, setGroups] = useState<FileRows[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<{ key: string; text: string } | null>(null);

  const load = useCallback(async () => {
    const found: FileRows[] = [];
    for (const pick of picks) {
      try {
        const reply = (await execute("case.settings", { node: caseId, file: pick.file }, { silent: true })) as { rows?: SettingsRow[] };
        const rows = pickRows(reply.rows ?? [], pick);
        if (rows.length > 0) found.push({ file: pick.file, rows });
      } catch {
        // A file this case does not have: the pick is for another kind of tutorial.
      }
    }
    setGroups(found);
  }, [execute, caseId, picks]);

  // Reload when the tree changes: an edit (here or in Full mode) is an override on the case node.
  useEffect(() => {
    void load();
  }, [load, tree]);

  const commit = async (file: string, row: SettingsRow) => {
    const key = `${file}:${row.path.join("/")}`;
    const value = valueFor(row, draft);
    if ("problem" in value) {
      setProblem({ key, text: value.problem });
      return;
    }
    try {
      await execute("case.set", { node: caseId, file, path: row.path, value: value.value }, { silent: true });
      setEditing(null);
      setProblem(null);
    } catch (err) {
      setProblem({ key, text: err instanceof Error ? err.message : String(err) });
    }
  };

  const revert = async (file: string, row: SettingsRow) => {
    try {
      await execute("case.reset", { node: caseId, file, path: row.path }, { silent: true });
    } catch (err) {
      setProblem({ key: `${file}:${row.path.join("/")}`, text: err instanceof Error ? err.message : String(err) });
    }
  };

  if (groups === null) return <p className="guided-line muted">Reading the case…</p>;
  if (groups.length === 0) return <Note tip="Every setting of the case is in Full mode: Solve → OpenFOAM cases.">Nothing to set here for this case</Note>;
  return (
    <div className="case-quick" data-ws="case-quick-settings">
      {groups.map((group) => (
        <div key={group.file} className="case-quick-group">
          <div className="case-quick-file muted mono">{group.file}</div>
          {group.rows.map((row) => {
            const key = `${group.file}:${row.path.join("/")}`;
            const open = editing === key;
            return (
              <div key={key} className="case-quick-row">
                <label className="guided-field">
                  <span title={row.path.join(" / ")}>
                    {row.overridden && <span style={{ color: "var(--accent)" }} title="Edited">● </span>}
                    {rowLabel(row)}
                  </span>
                  <input
                    className="field mono"
                    spellCheck={false}
                    autoComplete="off"
                    aria-label={`${group.file} ${row.path.join(" ")}`}
                    value={open ? draft : `${row.text}${row.unit ? ` ${row.unit}` : ""}`}
                    onFocus={() => {
                      setEditing(key);
                      setDraft(row.text);
                      setProblem(null);
                    }}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => {
                      if (editing === key && draft.trim() !== row.text.trim()) void commit(group.file, row);
                      else setEditing(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") e.currentTarget.blur();
                      if (e.key === "Escape") {
                        setDraft(row.text);
                        setEditing(null);
                        e.currentTarget.blur();
                      }
                    }}
                    data-ws="case-quick-input"
                  />
                  {row.overridden && (
                    <button type="button" className="muted no-drag" title={`Revert to ${row.original ?? "the case's value"}`} aria-label={`Revert ${rowLabel(row)}`} onClick={() => void revert(group.file, row)}>
                      <RotateCcw size={12} aria-hidden="true" />
                    </button>
                  )}
                </label>
                {problem?.key === key && (
                  <p className="guided-line" role="alert" style={{ color: "var(--danger)" }}>
                    {problem.text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      ))}
      <Note tip="Solve → OpenFOAM cases in Full mode lists every file and entry of the case.">Every other setting is in Full mode</Note>
    </div>
  );
}

const INTRO: Record<"fluid" | "boundaries", string> = {
  fluid: "The flow model and the fluid, from the case's own dictionaries",
  boundaries: "The values at each boundary, from the case's own fields",
};

/** The Fluid or Boundaries card of an imported case. */
export default function CaseSettingsCard({ step, caseId, which }: { step: GuidedStep; caseId: string; which: "fluid" | "boundaries" }) {
  const picks = useMemo(() => CASE_PICKS[which], [which]);
  return (
    <GuidedCard step={step}>
      <p className="guided-line muted">{INTRO[which]}</p>
      <CaseQuickSettings caseId={caseId} picks={picks} />
    </GuidedCard>
  );
}
