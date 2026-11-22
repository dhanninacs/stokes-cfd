/**
 * The field picker of the results view: a button that names the field colouring the model ("Velocity
 * magnitude |U| · m/s"), opening a searchable list of every variable the run can show, in groups (Velocity,
 * Pressure, Temperature, Turbulence, Flow features, Wall, Time-averaged, Functions), each row with its
 * symbol, units and meaning. A variable the run cannot compute is listed, disabled, with the reason.
 *
 * The list is the server's (`results.variables`, `variablesControl.ts`); choosing one is `showVariable`,
 * which ranges a variable the run did not store before colouring with it. Type to filter; arrow keys move;
 * Enter chooses; Escape closes.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDown, Search } from "lucide-react";
import { Popover } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { useStore } from "../state/store";
import { NO_FIELD, fieldUnits } from "./fieldsModel";
import { loadVariables, readFieldsAgain, showVariable, useVariables } from "./variablesControl";
import { prettyUnits, searchVariables, variableOf, variableTitle, type VariableInfo } from "./variablesModel";
import "./fieldPicker.css";

export default function FieldPicker({ label = "Field", ws = "field-picker", onError }: { label?: string; ws?: string; onError?: (text: string) => void }) {
  const field = useStore((s) => s.results.view.field);
  const data = useStore((s) => s.results.data);
  const reply = useVariables((s) => s.reply);
  const loading = useVariables((s) => s.loading);
  const error = useVariables((s) => s.error);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [rereading, setRereading] = useState("");
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (data !== null) void loadVariables();
  }, [data]);

  const variables = reply?.variables ?? [];
  const groups = useMemo(() => searchVariables(variables, query), [variables, query]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const current = variableOf(variables, field);
  const title = field === NO_FIELD ? "No field" : variableTitle(current, field, fieldUnits(data, field));

  const choose = (v: VariableInfo) => {
    if (!v.available) return;
    setBusy(v.name);
    void showVariable(v.name).then((why) => {
      setBusy(null);
      if (why !== null) onError?.(why);
      else {
        setOpen(false);
        setQuery("");
      }
    });
  };

  const keys = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setAt((i) => Math.max(0, Math.min(flat.length - 1, i + step)));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const v = flat[at];
      if (v !== undefined) choose(v);
    }
  };

  useEffect(() => {
    list.current?.querySelector(`[data-index="${at}"]`)?.scrollIntoView({ block: "nearest" });
  }, [at]);

  return (
    <>
      <Tooltip label={current?.description || "Choose what colours the model"} side="top">
        <button
          ref={button}
          type="button"
          className="field-picker-button no-drag"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`${label}: ${title}`}
          onClick={() => {
            setOpen(!open);
            setAt(0);
          }}
          disabled={data === null}
          data-ws={ws}
        >
          <span className="field-picker-title">{title}</span>
          <ChevronDown size={13} aria-hidden="true" />
        </button>
      </Tooltip>
      <Popover open={open} anchor={button} onClose={() => setOpen(false)} role="dialog" label="Choose a field" className="field-picker" focusSelector="input" onKeyDown={keys}>
        <div className="field-picker-search">
          <Search size={13} aria-hidden="true" />
          <input
            className="field"
            placeholder="Search variables"
            aria-label="Search variables"
            value={query}
            spellCheck={false}
            onChange={(e) => {
              setQuery(e.target.value);
              setAt(0);
            }}
            data-ws={`${ws}-search`}
          />
        </div>
        <div className="field-picker-list" ref={list} role="listbox" aria-label="Variables">
          {loading && variables.length === 0 && <div className="field-picker-note muted">Reading the run's variables…</div>}
          {error !== "" && variables.length === 0 && <div className="field-picker-note muted">{error}</div>}
          {!loading && variables.length > 0 && flat.length === 0 && <div className="field-picker-note muted">No variable matches</div>}
          {groups.map((g) => (
            <div key={g.group} className="field-picker-group" role="group" aria-label={g.group}>
              <div className="field-picker-group-name">{g.group}</div>
              {g.items.map((v) => {
                const index = flat.indexOf(v);
                return (
                  <button
                    key={v.name}
                    type="button"
                    role="option"
                    aria-selected={v.name === field}
                    aria-disabled={!v.available || undefined}
                    className="field-picker-row"
                    data-index={index}
                    data-active={index === at || undefined}
                    data-name={v.name}
                    title={v.available ? v.description : v.why}
                    onMouseEnter={() => setAt(index)}
                    onClick={() => choose(v)}
                  >
                    <span className="field-picker-label">
                      {v.label}
                      {v.symbol !== "" && !v.label.includes(v.symbol) && <span className="field-picker-symbol"> {v.symbol}</span>}
                    </span>
                    <span className="field-picker-units mono">{busy === v.name ? "…" : prettyUnits(v.units)}</span>
                    <span className="field-picker-meaning muted">{v.available ? v.description : v.why}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        {variables.some((v) => !v.available && v.source === "run") && (
          <div className="field-picker-foot">
            <button
              type="button"
              className="btn"
              disabled={rereading === "…"}
              title="This run's fields were read before Stokes computed vorticity, Q, wall shear stress and y+; read them again (a few seconds to a minute)"
              onClick={() => {
                setRereading("…");
                void readFieldsAgain().then((why) => setRereading(why ?? ""));
              }}
              data-ws={`${ws}-reread`}
            >
              {rereading === "…" ? "Reading the fields…" : "Read the fields again"}
            </button>
            {rereading !== "" && rereading !== "…" && <span className="muted">{rereading}</span>}
          </div>
        )}
      </Popover>
    </>
  );
}
