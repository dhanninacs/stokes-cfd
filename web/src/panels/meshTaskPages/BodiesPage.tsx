/**
 * The Bodies page (brief M-502 §UI 5.2): one row per body, a four-way choice, **every choice one
 * `mesh.body_role` call** — never the underlying commands composed here, which is M-501's table.
 *
 * **Multi-select is one call with all the ids** (`mesh.body_role {bodies, role}`), one undo step.
 * **Fluid and Hole in the flow are mutually exclusive** (one domain part): on a second body, or for
 * a multi-selection of more than one body, those two choices are disabled with the reason. Solid
 * rows say why they are kept out of this fluid mesh.
 */
import { useMemo, useState } from "react";
import { useStore } from "../../state/store";
import { bodiesPayload, roleOf, slotReason, type Role } from "./bodiesModel";
import { Note } from "../../components/Note";

const CHOICES: { value: Role; label: string }[] = [
  { value: "fluid", label: "Fluid" },
  { value: "solid", label: "Solid" },
  { value: "hole", label: "Hole in the flow" },
  { value: "left_out", label: "Left out" },
];

export default function BodiesPage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const selectedIds = useStore((s) => s.selection.ids);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  /** **Every part, however the tree nests them**: a STEP of several solids is a group of parts
   * (the demo's `demo_pipe` holds its four), so the walk is recursive — the same one the viewport's
   * own part list uses (`collectParts`). */
  const parts = useMemo(() => {
    const group = tree?.children?.find((c) => c.path === "/geometry");
    const partsGroup = group?.children?.find((c) => c.path === "/geometry/parts");
    if (partsGroup === undefined) return [];
    const out: { id: string; name: string; params: Record<string, unknown> }[] = [];
    const visit = (node: { type: string; id: string; name: string; params: Record<string, unknown>; children?: unknown[] }): void => {
      if (node.type === "part") out.push({ id: node.id, name: node.name, params: node.params });
      for (const child of node.children ?? []) {
        const c = child as { type: string; id: string; name: string; params: Record<string, unknown>; children?: unknown[] };
        visit(c);
      }
    };
    visit(partsGroup as Parameters<typeof visit>[0]);
    return out;
  }, [tree]);
  const settings = useMemo(
    () => tree?.children?.find((c) => c.path === "/mesh")?.children?.find((c) => c.path === "/mesh/settings") ?? null,
    [tree],
  );
  const domainPart = typeof settings?.params.domain_part === "string" ? settings.params.domain_part : null;
  // The hole slot: the enclosure domain reads the same way (M-501 gave the enclosure its domain).
  const enclosure = typeof settings?.params.box === "string" ? settings.params.box : null;

  /** The rows, and the multi-selection the choice applies to: the tree's selection when it names
   * parts of this page, else the one row the choice sits on. */
  const chosenIds = useMemo(() => {
    const partIds = new Set(parts.map((p) => p.id));
    const picked = selectedIds.filter((id) => partIds.has(id));
    return picked;
  }, [selectedIds, parts]);

  const apply = async (role: Role, id: string) => {
    const ids = chosenIds.length > 1 ? chosenIds : [id];
    setBusy(true);
    setError("");
    try {
      await execute("mesh.body_role", bodiesPayload(ids, role));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const multi = chosenIds.length > 1;
  const slotTaken = parts.some((p) => roleOf(p, domainPart, enclosure) === "fluid") || parts.some((p) => roleOf(p, domainPart, enclosure) === "hole");

  return (
    <div className="task-page" data-ws="task-bodies">
      <Note className="task-purpose" tip="Say which body is the fluid, which are solids, which are holes in the flow, which are left out">
        What each body is
      </Note>
      {multi && (
        <p className="muted task-note" data-ws="bodies-multi">
          One choice applies to all {chosenIds.length} selected bodies, as one undo step.
        </p>
      )}
      <ul className="task-rows">
        {parts.map((part) => {
          const role = roleOf(part as { id: string; params: Record<string, unknown> }, domainPart, enclosure);
          const chosen = chosenIds.includes(part.id);
          return (
            <li key={part.id} className="task-row">
              <span className="task-row-name" title={part.name}>
                {part.name}
              </span>
              <div className="task-choices" role="group" aria-label={`The role of ${part.name}`}>
                {CHOICES.map((choice) => {
                  const exclusive = choice.value === "fluid" || choice.value === "hole";
                  const disabled =
                    busy ||
                    (exclusive && (multi || (slotTaken && role !== choice.value))) ||
                    (multi && !chosen);
                  return (
                    <button
                      key={choice.value}
                      type="button"
                      className={`task-choice${role === choice.value ? " task-choice-on" : ""}`}
                      disabled={disabled}
                      title={disabled && exclusive ? slotReason(parts.length) : undefined}
                      aria-pressed={role === choice.value}
                      data-ws={`body-${choice.value}`}
                      onClick={() => void apply(choice.value, part.id)}
                    >
                      {choice.label}
                    </button>
                  );
                })}
              </div>
              {role === "solid" && (
                <span className="muted task-row-note" title="Kept out of this fluid mesh until multi-region meshing." aria-description="Kept out of this fluid mesh until multi-region meshing.">Solid · not meshed</span>
              )}
            </li>
          );
        })}
      </ul>
      {error !== "" && (
        <p role="alert" className="mesh-error">
          {error}
        </p>
      )}
    </div>
  );
}