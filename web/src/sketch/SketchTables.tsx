/**
 * The sketcher's side tables (ADR-0036, ADR-0037): the entities as numbers to type (mm) and the
 * sketch's dimensions. A cell takes a number (millimetres) or an expression of the dimensions
 * (`W / 2`), which binds it. Every change is ONE edit of the sketch (`onEdit`), so one undo takes
 * it back whole (review A1); a cell whose expression does not evaluate is flagged on the cell
 * (review A10).
 */
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { bindPoint, bindScalar, bindingOf, type Axis, type Bindings } from "./bindings";
import { movePoint, type Entity, type Point } from "./model";
import type { Dim, SketchDoc } from "./history";
import { mmExpression, withoutMm } from "./mmText";

export const NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** A dimension's text with a unit: a bare number is millimetres in the sketcher (review A5). */
export function withUnit(text: string): string {
  const t = text.trim();
  return NUMBER.test(t) ? `${t} [mm]` : mmExpression(t);
}

type Edit = (fn: (d: SketchDoc) => SketchDoc) => void;

/** One number-or-expression cell. `apply` gets a number (mm) or an expression text. */
function Cell({ value, label, bound, problem, apply }: { value: number; label: string; bound?: string; problem?: string; apply: (v: number | string) => void }) {
  return (
    <input
      className={`sketch-num ${bound ? "bound" : ""} ${problem ? "invalid" : ""}`}
      aria-label={label}
      aria-invalid={problem ? true : undefined}
      title={problem ? `${bound}: ${problem}` : bound ? `${bound} = ${Number(value.toFixed(4))} mm` : undefined}
      defaultValue={bound !== undefined ? withoutMm(bound) : Number(value.toFixed(4))}
      key={`${label}:${value}:${bound ?? ""}`}
      spellCheck={false}
      onBlur={(e) => {
        const text = e.target.value.trim();
        if (text === "" || text === (bound !== undefined ? withoutMm(bound) : "")) return;
        if (NUMBER.test(text)) {
          const v = Number(text);
          if (Number.isFinite(v) && (v !== value || bound)) apply(v);
        } else {
          // Numbers are mm inside an expression too: `10 + c` is ten millimetres plus c.
          apply(mmExpression(text));
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

const replaceAt = (entities: readonly Entity[], index: number, entity: Entity): Entity[] => entities.map((e, i) => (i === index ? entity : e));

export function EntityTable({
  entities,
  bindings,
  problems,
  selected,
  onSelect,
  onEdit,
  onDelete,
}: {
  entities: Entity[];
  bindings: Bindings;
  problems: Record<string, string>;
  selected: Set<number>;
  onSelect: (s: Set<number>) => void;
  onEdit: Edit;
  onDelete: (index: number) => void;
}) {
  /** A point's cell; `joined`: moving it moves every end at that point (lines and arcs keep their joins). */
  const pointCells = (i: number, key: string, p: Point, label: string, joined: boolean) => {
    const cell = (axis: Axis) => {
      const bound = bindingOf(bindings, i, key, axis);
      return (
        <Cell
          value={p[axis]}
          label={`${label} ${axis === 0 ? "u" : "v"}`}
          bound={bound}
          problem={bound ? problems[bound] : undefined}
          apply={(v) =>
            onEdit((d) => {
              if (typeof v === "string") return { ...d, bindings: bindPoint(d.entities, d.bindings, p, axis, v) };
              const q: Point = axis === 0 ? [v, p[1]] : [p[0], v];
              const target = d.entities[i];
              const moved = joined ? movePoint(d.entities, p, q) : replaceAt(d.entities, i, { ...target, [key]: q } as Entity);
              return { ...d, entities: moved, bindings: bindPoint(d.entities, d.bindings, p, axis, null) };
            })
          }
        />
      );
    };
    return (
      <span className="sketch-pt">
        {cell(0)}
        {cell(1)}
      </span>
    );
  };
  const sizeCell = (i: number, key: "r" | "w" | "h", value: number, label: string) => {
    const bound = bindings[`${i}.${key}`];
    return (
      <span className="sketch-pt">
        {key === "r" ? "r" : key}{" "}
        <Cell
          value={value}
          label={label}
          bound={bound}
          problem={bound ? problems[bound] : undefined}
          apply={(v) =>
            onEdit((d) => {
              if (typeof v === "string") return { ...d, bindings: bindScalar(d.bindings, i, key, v) };
              if (!(v > 0)) return d;
              return { ...d, entities: replaceAt(d.entities, i, { ...d.entities[i], [key]: v } as Entity), bindings: bindScalar(d.bindings, i, key, null) };
            })
          }
        />
      </span>
    );
  };
  return (
    <div className="sketch-table" aria-label="Entities" data-ws="sketch-table">
      <div className="sketch-table-head">Entities (mm)</div>
      {entities.length === 0 && <div className="muted sketch-empty">Nothing drawn yet</div>}
      {entities.map((e, i) => (
        <div key={i} className={`sketch-row ${selected.has(i) ? "selected" : ""}`} onClick={() => onSelect(new Set([i]))}>
          <span className="sketch-kind">{e.kind === "rectangle" ? "rect" : e.kind === "polygon" ? `${e.n}-gon` : e.kind}</span>
          {e.kind === "line" && (
            <>
              {pointCells(i, "a", e.a, `line ${i + 1} start`, true)}
              {pointCells(i, "b", e.b, `line ${i + 1} end`, true)}
            </>
          )}
          {e.kind === "arc" && (
            <>
              {pointCells(i, "a", e.a, `arc ${i + 1} start`, true)}
              {pointCells(i, "m", e.m, `arc ${i + 1} through`, false)}
              {pointCells(i, "b", e.b, `arc ${i + 1} end`, true)}
            </>
          )}
          {e.kind === "circle" && (
            <>
              {pointCells(i, "c", e.c, `circle ${i + 1} centre`, false)}
              {sizeCell(i, "r", e.r, `circle ${i + 1} radius`)}
            </>
          )}
          {e.kind === "rectangle" && (
            <>
              {pointCells(i, "c", e.c, `rectangle ${i + 1} centre`, false)}
              {sizeCell(i, "w", e.w, `rectangle ${i + 1} width`)}
              {sizeCell(i, "h", e.h, `rectangle ${i + 1} height`)}
            </>
          )}
          {e.kind === "polygon" && (
            <>
              {pointCells(i, "c", e.c, `polygon ${i + 1} centre`, false)}
              {sizeCell(i, "r", e.r, `polygon ${i + 1} radius`)}
            </>
          )}
          {e.kind === "spline" && <span className="muted">{e.points.length} points</span>}
          <button type="button" className="sketch-icon" aria-label={`Delete ${e.kind} ${i + 1}`} onClick={(ev) => { ev.stopPropagation(); onDelete(i); }}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

const DIMENSION_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/**
 * The sketch's dimensions (ADR-0037): named lengths or angles ("W = 40", "H = R * 2",
 * "a = 30 [deg]") that the entities' cells and the size fields may name. A bare number is
 * millimetres. A study varies them as `<sketch>.dimensions.<name>`; the value each evaluates to is
 * shown beside it, and one that does not resolve is flagged on its field.
 */
export function DimensionsPanel({
  dims,
  shown,
  problems,
  onChange,
}: {
  dims: Dim[];
  shown: Record<string, string>;
  problems: Record<string, string>;
  onChange: (next: Dim[]) => void;
}) {
  const [draft, setDraft] = useState({ name: "", text: "" });
  const [problem, setProblem] = useState("");
  const add = () => {
    const name = draft.name.trim();
    if (!DIMENSION_NAME.test(name)) return setProblem("A name is letters, digits and underscores");
    if (dims.some((d) => d.name === name)) return setProblem(`${name} is already a dimension`);
    if (draft.text.trim() === "") return setProblem("Give it a value, such as 20 (mm) or 30 [deg]");
    onChange([...dims, { name, text: withUnit(draft.text) }]);
    setDraft({ name: "", text: "" });
    setProblem("");
  };
  return (
    <div className="sketch-dims" aria-label="Dimensions" data-ws="sketch-dimensions">
      <div className="sketch-table-head">Dimensions</div>
      {dims.map((d, i) => {
        const bad = problems[`dim:${d.name}`];
        return (
          <div key={d.name} className="sketch-dim-row">
            <span className="mono sketch-dim-name">{d.name}</span>
            <input
              className={`sketch-num bound sketch-dim-value ${bad ? "invalid" : ""}`}
              aria-label={`Dimension ${d.name}`}
              aria-invalid={bad ? true : undefined}
              title={bad}
              defaultValue={d.text}
              key={`${d.name}:${d.text}`}
              spellCheck={false}
              onBlur={(e) => {
                const text = withUnit(e.target.value);
                if (text !== "" && text !== d.text) onChange(dims.map((x, j) => (j === i ? { ...x, text } : x)));
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
            />
            <span className={`muted sketch-dim-shown ${bad ? "invalid" : ""}`}>{shown[d.name] ?? ""}</span>
            <button type="button" className="sketch-icon" aria-label={`Remove dimension ${d.name}`} onClick={() => onChange(dims.filter((_, j) => j !== i))}>
              <Trash2 size={13} />
            </button>
          </div>
        );
      })}
      <div className="sketch-dim-row">
        <input className="sketch-num sketch-dim-name" aria-label="New dimension name" placeholder="W" value={draft.name} onChange={(e) => setDraft((x) => ({ ...x, name: e.target.value }))} />
        <input
          className="sketch-num sketch-dim-value"
          aria-label="New dimension value"
          placeholder="40"
          value={draft.text}
          onChange={(e) => setDraft((x) => ({ ...x, text: e.target.value }))}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
        />
        <button type="button" className="sketch-button" aria-label="Add dimension" onClick={add}>
          Add
        </button>
      </div>
      {problem && <div className="sketch-dim-problem">{problem}</div>}
      <div className="muted sketch-dim-hint">Numbers are mm; give another unit in brackets, e.g. 30 [deg]. Type a name such as W / 2 into a size or a coordinate to drive it.</div>
    </div>
  );
}
