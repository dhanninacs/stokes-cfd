/**
 * The guided-meshing stepper's pure model (brief M-502): a `mesh.tasks` reply as the rows the
 * stepper draws.
 *
 * **Pure on purpose.** The reply shape is the backend's (`stokes.mesh.tasks`), and everything the
 * tests pin — which dot a state earns, what the value line says, which row is current — is decided
 * here, so the component only draws. The nine task ids and their titles come from the reply; the
 * front end never re-lists them, so a task added on the backend appears without a web change.
 *
 * **The dot map is the brief's** (M-502 §UI 1): `idle`/`ready` → `new`, `todo` → `warning`,
 * `running` → the spinner, `done` → `ok` with **no dot** (green is not news — `statusWords`'s rule),
 * `stale` → `stale`, `failed` → `failed`. A state the backend adds later renders as `warning`: an
 * unknown state needs attention more than it needs silence.
 */

/** What the stepper knows about the reply: the whole `mesh.tasks` answer. */
export interface TasksReply {
  tasks: Array<{
    id: string;
    title: string;
    state: string;
    todo: string[];
    row?: string;
  }>;
  current: string;
  next: string | null;
  mesher: string;
  host: string;
}

/** One row the stepper draws. */
export interface StepperRow {
  id: string;
  title: string;
  /** The first todo line, else the task's summary line (`row`), else "". */
  value: string;
  /** The dot class: "new" | "warning" | "stale" | "failed" | "running", or "" for no dot. */
  dot: string;
  /** True when the row shows the spinner instead of a dot (a running task). */
  spin: boolean;
  /** The row the stepper points at: the selected task, else the reply's `current`. */
  current: boolean;
  clickable: boolean;
}

/** The task ids the Mesh stage can select, in the reply's order. */
export function taskIds(reply: TasksReply): string[] {
  return reply.tasks.map((t) => t.id);
}

/**
 * The reply as stepper rows. `selectedTask` is the task the user has chosen in the tree or the
 * stepper; `current` follows it while it stays chosen, so the pointer and the pane agree.
 */
export function stepperRows(reply: TasksReply, selectedTask?: string | null): StepperRow[] {
  const current = selectedTask && taskIds(reply).includes(selectedTask) ? selectedTask : reply.current;
  return reply.tasks.map((task) => {
    const state = task.state;
    const dot =
      state === "idle" || state === "ready"
        ? "new"
        : state === "todo"
          ? "warning"
          : state === "running"
            ? "running"
            : state === "stale"
              ? "stale"
              : state === "failed"
                ? "failed"
                : state === "done"
                  ? ""
                  : "warning";
    return {
      id: task.id,
      title: task.title,
      value: task.todo[0] ?? task.row ?? "",
      dot,
      spin: state === "running",
      current: task.id === current,
      clickable: true,
    };
  });
}

/** "Step N of 9 · <title>" for the Mesh section header, from the same current rule. */
export function stepHeading(reply: TasksReply, selectedTask?: string | null): string {
  const current = selectedTask && taskIds(reply).includes(selectedTask) ? selectedTask : reply.current;
  const index = taskIds(reply).indexOf(current);
  const title = reply.tasks[index]?.title ?? "";
  return index >= 0 ? `Step ${index + 1} of ${reply.tasks.length} · ${title}` : title;
}

/** The mesher and host, as the stepper's head shows them: "" stays out of the way. */
export function headWords(reply: TasksReply): string {
  const mesher = reply.mesher || "";
  const host = reply.host || "";
  if (!mesher && !host) return "";
  return [mesher, host].filter(Boolean).join(" · ");
}
/** When the stepper's two forms show: the collapsed line in the fixed header, and the full block. */
export interface StepperVisibility {
  /** The one-line collapsed form: every Mesh-stage page, whatever the preference says. */
  line: boolean;
  /** The full block as the scrolling body's first element: the Mesh stage AND the preference. */
  block: boolean;
}

/**
 * The two forms' visibility, as one rule (brief M-502 §UI 3, the independent-scroll decision of
 * 1430): **the line is every Mesh-stage page's**, because Q2 says the user always sees where they
 * are; the block follows the preference, so "hidden" hides the block and never the line. Outside
 * the Mesh stage neither shows — the stepper is the mesh's own instrument, and a Geometry-stage
 * pane with nine mesh rows would be noise.
 */
export function stepperVisibility(stage: string, preference: string): StepperVisibility {
  const inMesh = stage === "Mesh";
  return { line: inMesh, block: inMesh && preference === "shown" };
}
