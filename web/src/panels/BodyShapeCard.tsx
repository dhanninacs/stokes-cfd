/**
 * The shape of a body made with Create, on the body's own page (review finding B3): the operation
 * that made it, by name, with "Edit sketch" / "Edit <operation>" and the values that drive it (the
 * sizes, the depth, the sketch's dimensions), editable here, so a body is changed from the body
 * rather than by finding its operation in the tree. And, on an operation's page, the bodies it
 * reads and makes, by name, in place of the raw id lists (review finding B9).
 *
 * The fields are the operation's own (`SchemaFields` over its type, in millimetres and degrees);
 * a change is a `parameter.set` on the operation, which rebuilds the body as the operation's page does.
 */
import { useCallback, useState } from "react";
import { Pencil } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { findNode, useStore } from "../state/store";
import { Button } from "../components/ui";
import { GEOMETRY_UNIT_SYSTEM, implicitShows, implicitTitle, isImplicitOperation, optionLabel } from "../state/implicitFields";
import SchemaFields from "./SchemaFields.tsx"; // the .tsx: "./SchemaFields" would case-match the helpers' schemaFields.ts on Windows
import { operationOf, roleWords, targetAndTools } from "./bodySteps";
import { bodyEdit, editBody } from "../app/editOperation";

/** What an operation's form never shows on a body's page: the lists of bodies, the run setting, the drawn entities. */
const NOT_DRIVING: ReadonlySet<string> = new Set(["inputs", "tools", "outputs", "auto_run", "entities"]);

/** The operation's lists of bodies, never drawn as JSON on an implicit operation's page. */
export const OPERATION_LISTS: ReadonlySet<string> = new Set(["inputs", "tools", "outputs"]);

function ids(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** The shape of a body made with Create: its operation's driving values and the way to edit it. */
export function BodyShapeCard({ node }: { node: NodeSummary }) {
  const tree = useStore((s) => s.tree);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const execute = useStore((s) => s.execute);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const op = operationOf(tree, node.id);
  const commit = useCallback(
    async (name: string, value: unknown) => {
      if (op === null) return;
      setErrors((e) => {
        const next = { ...e };
        delete next[name];
        return next;
      });
      try {
        await execute("parameter.set", { node: op.id, name, value }, { silent: true });
      } catch (err) {
        setErrors((e) => ({ ...e, [name]: err instanceof Error ? err.message : String(err) }));
        throw err;
      }
    },
    [execute, op],
  );
  if (op === null || !isImplicitOperation(op.type)) return null;
  const schema = nodeTypes[op.type];
  const edit = bodyEdit(tree, node.id);
  const params = op.params as Record<string, unknown>;
  const title = implicitTitle(op.type, params) ?? op.name;
  return (
    <section className="flex flex-col gap-2" aria-label="Shape" data-ws="body-shape">
      <div className="flex items-center justify-between gap-2">
        {/* The operation's own name: "Shape · Sketch · Pull" wrapped onto two lines beside the Edit button. */}
        <span className="section-title">{title}</span>
        {edit !== null && (
          <Button onClick={() => editBody(node.id)} data-ws="body-edit">
            <Pencil size={13} /> {edit.label}
          </Button>
        )}
      </div>
      {schema && (
        <SchemaFields
          key={op.id}
          properties={schema.properties}
          values={op.params}
          displays={op.displays}
          status={op.status}
          onCommit={commit}
          onEdit={(name) =>
            setErrors((e) => {
              if (!(name in e)) return e;
              const next = { ...e };
              delete next[name];
              return next;
            })
          }
          include={(name) => !NOT_DRIVING.has(name) && implicitShows(op.type, params, name)}
          errors={errors}
          unitSystem={GEOMETRY_UNIT_SYSTEM}
          optionLabel={optionLabel}
        />
      )}
    </section>
  );
}

/** On an implicit operation's page: the bodies it reads and makes, by name. */
export function OperationBodies({ node }: { node: NodeSummary }) {
  const tree = useStore((s) => s.tree);
  if (!isImplicitOperation(node.type)) return null;
  const nameOf = (id: string): string => findNode(tree, id)?.name ?? "a removed body";
  const reads = [...ids(node.params.inputs), ...ids(node.params.tools)];
  const roles = targetAndTools(node.type, reads);
  const [targetWord, toolWord] = roleWords(node.type, node.params.op);
  const made = ids(node.params.outputs);
  const rows: Array<[string, string]> = [];
  if (roles !== null && roles.target !== null) rows.push([targetWord, nameOf(roles.target)]);
  if (roles !== null && roles.tools.length > 0) rows.push([toolWord, roles.tools.map(nameOf).join(", ")]);
  rows.push(["Makes", made.length === 0 ? "Nothing yet: it has not run" : made.map(nameOf).join(", ")]);
  return (
    <div role="group" aria-label="Bodies" className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]" data-ws="operation-bodies">
      {rows.map(([word, names]) => (
        <span key={word} className="contents">
          <span className="muted">{word}</span>
          <span className="truncate" title={names}>
            {names}
          </span>
        </span>
      ))}
    </div>
  );
}
