/**
 * The operation card (T-029, ADR-0021 decision 7): one floating glass card of the properties
 * panel's kind at the right of the viewport, opened by the Prepare menu. Its title is the
 * operation type's label from `operation.types`, its form is `SchemaFields` over the type's own
 * parameters (ADR-0022 7.3), and its inputs come from the pick the card was opened with.
 *
 * Run calls `operation.create {type, inputs, params}`; from then on the card follows the run
 * through the store's `operations` map (the `geometry.op_progress` events): it shows the progress
 * line's text and a Cancel that calls `operation.cancel`. `geometry.op_done` closes it (the store
 * does, and selects the first output); `geometry.op_failed` puts the server's message in the red
 * line under the form, as a failed node shows it in the properties panel — except for a cancel
 * (`geometry.cancelled`), which the user asked for and which says itself in the quiet line. One
 * card at a time; Esc closes it (`selection.clear`).
 *
 * The `seed` field of the extract-fluid card carries a "Pick in view" ghost button: it turns the
 * viewport into a one-shot point pick (`scene.requestPoint`), and the point it answers with is
 * written into the field as `point − normal × 1 % of the part's bounding-box diagonal`, in the
 * display unit the field shows (the value itself stays in SI, as every quantity does).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Crosshair, Play, TriangleAlert, X } from "lucide-react";
import { StokesApiError } from "../api/client";
import { useStore, type OperationDraft } from "../state/store";
import { initialValues, operationCreatePayload, operationInputs, operationJsonText, operationText, seedAt } from "../state/operations";
import { Button, IconButton } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import SchemaFields from "./SchemaFields.tsx"; // the .tsx: "./SchemaFields" would case-match the helpers' schemaFields.ts on Windows
import { currentScene, type PointHit } from "../viewport/scene";

/** The parameters the card's form does not offer: the outputs belong to the run, not to the form. */
const HIDDEN: ReadonlySet<string> = new Set(["outputs"]);

/** What `operation.create` answers: the operation it made. */
interface CreateResult {
  id?: string;
  name?: string;
}

export default function OperationCard() {
  const draft = useStore((s) => s.operationCard);
  // A new card gets a new form: the key is the card's number, never its node, so the values the
  // user has typed survive the run's own state changes (the node, the failure message).
  if (draft === null) return null;
  return <CardBody key={draft.id} draft={draft} />;
}

function CardBody({ draft }: { draft: OperationDraft }) {
  const execute = useStore((s) => s.execute);
  const units = useStore((s) => s.units);
  const info = useStore((s) => s.operationTypes[draft.type]);
  const seedPick = useStore((s) => s.seedPick);
  const progress = useStore((s) => (draft.nodeId === null ? null : (s.operations[draft.nodeId] ?? null)));

  const label = info?.label ?? draft.type;
  const schema = info?.schema;
  // How the run will read the pick, and what the read-only `inputs` and `tools` lines show: the
  // form must say what Run will do, not the schema's empty default.
  const reads = useMemo(() => operationInputs(draft.type, draft.parts), [draft.type, draft.parts]);
  // The faces the pick held go into the form for a type that takes them (Remove faces' `faces`),
  // so the field shows what the run will remove rather than its empty default.
  const withFaces = useMemo(
    () => (draft.faces !== null && draft.faces.length > 0 && info?.schema.properties.faces !== undefined ? { faces: draft.faces } : {}),
    [draft.faces, info],
  );
  const preset = useMemo(
    () => (reads.tools.length > 0 ? { ...draft.params, ...withFaces, inputs: reads.inputs, tools: reads.tools } : { ...draft.params, ...withFaces, inputs: reads.inputs }),
    [draft.params, withFaces, reads],
  );
  // The form's starting point: the schema's defaults and the entry's preset. The fields keep their
  // own text from there on, so the committed values are collected beside the seeded ones.
  const initial = useMemo(() => (schema ? initialValues(schema, units, preset) : null), [schema, units, preset]);
  const [values, setValues] = useState<Record<string, unknown>>(() => initial?.values ?? {});
  const [params, setParams] = useState<Record<string, unknown>>(() => ({ ...draft.params, ...withFaces }));
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState("");
  const displays = initial?.displays ?? {};
  // A run is under way from the moment the create reply names its node until a terminal event
  // arrives. `busy` only covers the round trip of `operation.create`, and the first
  // `geometry.op_progress` can be seconds later (a worker start), so without this a second Run in
  // that window would create a second operation.
  const running = progress !== null || (draft.nodeId !== null && draft.error === "");

  // The one-shot point pick (T-029): the seed sits 1 % of the part's diagonal off the face, inside
  // the fluid beside it. Esc leaves the mode (`selection.clear` sets `seedPick` false), and so does
  // closing the card, which unmounts this effect.
  const applySeed = useCallback((hit: PointHit) => {
    const seed = seedAt([hit.point.x, hit.point.y, hit.point.z], [hit.normal.x, hit.normal.y, hit.normal.z], hit.diagonal);
    setValues((current) => ({ ...current, seed }));
    setParams((current) => ({ ...current, seed }));
    useStore.getState().setSeedPick(false);
  }, []);

  useEffect(() => {
    if (!seedPick) return;
    currentScene()?.requestPoint(applySeed);
    return () => currentScene()?.cancelPoint();
  }, [seedPick, applySeed]);

  const commit = useCallback((name: string, value: unknown) => {
    setRefused("");
    setParams((current) => ({ ...current, [name]: value }));
  }, []);

  const close = useCallback(() => useStore.getState().closeOperationCard(), []);

  const run = useCallback(async () => {
    // One pure helper builds the payload, so what the card sends is testable without a renderer.
    const payload = operationCreatePayload(draft.type, params, draft.parts, draft.faces, info?.schema);
    const autoRun = typeof params.auto_run === "boolean" ? params.auto_run : undefined;
    setBusy(true);
    setRefused("");
    // A second Run is a new operation: the last failure's message goes with the first one.
    useStore.getState().setOperationError("");
    try {
      const result = (await execute("operation.create", { type: draft.type, inputs: payload.inputs, params: payload.params, ...(autoRun === undefined ? {} : { auto_run: autoRun }) })) as CreateResult;
      if (typeof result.id === "string") useStore.getState().setOperationNode(draft.id, result.id);
    } catch (err) {
      // `execute` logged it and raised a toast; the card says why too, as the properties panel does.
      setRefused(err instanceof StokesApiError ? err.message : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [draft.id, draft.type, draft.parts, draft.faces, params, info, execute]);

  const cancelRun = useCallback(() => {
    if (draft.nodeId === null) return;
    // Say so at once, in the quiet line: a cancel pressed before the run's first event reports
    // itself would otherwise leave the card looking as if the run were still under way.
    useStore.getState().setOperationError("the operation was cancelled", true);
    void execute("operation.cancel", { op: draft.nodeId }).catch(() => undefined);
  }, [draft.nodeId, execute]);

  const include = useCallback((name: string) => !HIDDEN.has(name), []);
  const jsonText = useCallback((name: string, value: unknown) => operationJsonText(value), []);

  const pickSeed = (
    <Tooltip label="Pick a point inside the fluid in the viewport" side="top" describe={false}>
      <Button
        variant="ghost"
        className="!h-6 !px-2 text-[11.5px]"
        aria-pressed={seedPick}
        active={seedPick}
        onClick={() => {
          if (!seedPick && currentScene() === null) {
            useStore.getState().pushToast("info", "The 3D view is not available, so there is nothing to pick in.");
            return;
          }
          useStore.getState().setSeedPick(!seedPick);
        }}
      >
        <Crosshair size={12} /> {seedPick ? "Click a face…" : "Pick in view"}
      </Button>
    </Tooltip>
  );
  const labelAction = useCallback((name: string) => (name === "seed" ? pickSeed : null), [pickSeed]);

  const pickedLine = draft.faces !== null ? `Reads ${reads.inputs.length} part${reads.inputs.length === 1 ? "" : "s"} and the picked faces` : `Reads ${reads.inputs.length} part${reads.inputs.length === 1 ? "" : "s"}`;
  const error = draft.error || refused;
  // A run the user cancelled says so quietly: pressing Cancel is news, not a failure.
  const cancelled = draft.error !== "" && draft.cancelled;

  return (
    <section className="operation-card glass" aria-label={`${label} operation`} data-ws="operation-card">
      <header className="operation-card-head">
        <span className="section-title">{label}</span>
        <IconButton label="Close" onClick={close}>
          <X size={14} />
        </IconButton>
      </header>
      <div className="operation-card-body">
        <p className="muted m-0 text-[11.5px]">{pickedLine}</p>
        {schema && (
          <SchemaFields
            properties={schema.properties}
            values={values}
            displays={displays}
            onCommit={commit}
            include={include}
            jsonText={jsonText}
            labelAction={labelAction}
            disabled={busy}
          />
        )}
        {error !== "" &&
          (cancelled ? (
            <p role="status" className="muted m-0 text-[12px]">
              {error}
            </p>
          ) : (
            <div role="alert" className="flex items-start gap-2 rounded-md px-2 py-1.5 text-[12px]" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
              <TriangleAlert size={13} className="mt-[2px] shrink-0" />
              <span className="min-w-0 break-words">{error}</span>
            </div>
          ))}
      </div>
      <footer className="operation-card-foot">
        {running ? (
          <>
            <span className="muted min-w-0 flex-1 truncate text-[11.5px]">{progress ? operationText(progress) : "Starting…"}</span>
            <Button onClick={cancelRun}>Cancel</Button>
          </>
        ) : (
          <>
            <Button onClick={close}>Cancel</Button>
            <Button variant="primary" disabled={busy} onClick={() => void run()}>
              <Play size={14} /> {busy ? "Running…" : "Run"}
            </Button>
          </>
        )}
      </footer>
    </section>
  );
}
