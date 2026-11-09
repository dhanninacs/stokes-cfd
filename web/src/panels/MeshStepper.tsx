/**
 * The guided-meshing stepper (brief M-502): the nine tasks, where the user is, and what is next.
 *
 * **Two forms, one rule** (the independent-scroll decision, 1430): the one-line collapsed form lives
 * in the Details pane's **fixed header** — always there while the Mesh stage is on, whatever the
 * preference says — and the full block is the **first block of the scrolling body**, shown while the
 * preference `preferences.mesh.stepper` is "shown". "Hidden" hides the block, never the line: Q2's
 * "always shown at the top" is the line's job.
 *
 * Everything that decides something is `meshTasks.ts` (pure, tested); this file draws and fetches.
 * The reply is re-asked on node and mesh events, **debounced 300 ms, and never polled while idle** —
 * there is no interval here at all: the tree and the mesh slice of the store change exactly when an
 * event arrives, and that is the only trigger.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Square } from "lucide-react";
import { Button, StatusDot } from "../components/ui";
import { findNode, useStore } from "../state/store";
import { useWorkspace } from "../workspace/store";
import { registerAction } from "../app/actions";
import { cancelMesh, generateMesh, useGenerateState, useMeshContext } from "./meshRun";
import { headWords, stepHeading, stepperRows, type TasksReply } from "./meshTasks";

/**
 * The latest `mesh.tasks` answer, re-asked when model, mesh or connection inputs change. **The last good reply is kept**
 * while a re-ask is in flight or the connection is down, so the stepper shows the last known state
 * rather than vanishing; a reply that fails to arrive leaves the previous one standing.
 */
export function useMeshTasks(): { reply: TasksReply | null; loading: boolean } {
  const tree = useStore((s) => s.tree);
  const meshAt = useStore((s) => s.mesh.at);
  const connected = useStore((s) => s.connected);
  const project = useStore((s) => s.project);
  const execute = useStore((s) => s.execute);
  const [reply, setReply] = useState<TasksReply | null>(null);
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  useEffect(() => {
    const id = ++requestId.current;
    let live = true;
    if (!connected) {
      setLoading(false);
      return () => {
        live = false;
        if (requestId.current === id) requestId.current++;
      };
    }
    // **Debounced 300 ms** (brief M-502): edits arrive in bursts (a multi-select writes several
    // nodes in one undo step), and one answer per burst is the point of the debounce. A new trigger
    // cancels this ask and makes any older in-flight answer stale.
    const timer = window.setTimeout(() => {
      setLoading(true);
      void execute("mesh.tasks")
        .then((answer) => {
          if (!live || requestId.current !== id) return;
          const typed = answer as TasksReply;
          setReply(typed);
          // **One answer, two readers**: the tree's Mesh rows read the same reply from the store,
          // so the section header and the stepper say the same thing about where the user is.
          useStore.setState({ meshTasks: typed });
        })
        .catch(() => undefined) // the last reply stands; the console already has the refusal
        .finally(() => {
          if (live && requestId.current === id) setLoading(false);
        });
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
      if (requestId.current === id) requestId.current++;
    };
  }, [tree, meshAt, connected, project, execute]);
  return { reply, loading };
}

/** The tree row a task selects, or null when the tree has no row for it yet. */
export function rowIdForTask(task: string, tree: ReturnType<typeof useStore.getState>["tree"]): string | null {
  // sizing IS the settings node and volume IS the result node; the tasks without a node are virtual
  // rows under the Mesh section; geometry and bodies are the Geometry stage's own rows.
  if (task === "sizing") {
    return findNode(tree, "/mesh/settings")?.id ?? "virtual:mesh-settings";
  }
  if (task === "volume") {
    const result = findNode(tree, "/mesh/results")?.children?.[0];
    return result?.id ?? "virtual:mesh-result";
  }
  if (task === "geometry" || task === "bodies") {
    return findNode(tree, "/geometry/parts")?.id ?? null;
  }
  return `virtual:mesh/${task}`;
}

/** One task row of the full block. */
function TaskRow({
  id,
  title,
  value,
  dot,
  spin,
  current,
  onSelect,
}: {
  id: string;
  title: string;
  value: string;
  dot: string;
  spin: boolean;
  current: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className={`stepper-row${current ? " stepper-row-current" : ""}`}
      data-ws={`stepper-${id}`}
      aria-current={current ? "step" : undefined}
      onClick={() => onSelect(id)}
    >
      <span className="stepper-dot shrink-0">
        {spin ? (
          <LoaderCircle size={13} className="stepper-spin" aria-label="running" role="img" />
        ) : (
          <StatusDot status={dot === "" ? "ok" : dot} />
        )}
      </span>
      <span className="stepper-title">{title}</span>
      <span className="stepper-value muted">{value}</span>
    </button>
  );
}

/** The task the selection names, or null: shared by the block and the line, so they agree. */
function useSelectedTask(reply: TasksReply | null): string | null {
  const selectedId = useStore((s) => s.selectedId);
  const explicitTask = useStore((s) => s.meshTask);
  const tree = useStore((s) => s.tree);
  return useMemo(() => {
    if (reply === null) return null;
    if (explicitTask !== null && reply.tasks.some((task) => task.id === explicitTask)) return explicitTask;
    if (selectedId === null) return null;
    for (const task of reply.tasks) {
      if (selectedId === `virtual:mesh/${task.id}`) return task.id;
      if (rowIdForTask(task.id, tree) === selectedId) return task.id;
    }
    return null;
  }, [selectedId, explicitTask, reply, tree]);
}

/**
 * Choosing a task, from wherever the ask comes: a row click, the collapsed line, or the
 * `Alt+↑/↓` chord. **Geometry check and What each body is open their own pages** (brief M-502
 * §UI 5) through the store's task state, and the selection follows the row each task maps to, so
 * the tree highlights too. **A task choice resets the scroll** (the independent-scroll rule,
 * 1430): the page the user asked for starts at its top.
 */
export function chooseTask(task: string): void {
  // **The selection FIRST, the task state after**: `select` clears any task page it did not
  // choose (a tree choice outranks a stepper task), so setting the state before selecting would
  // be wiped by the very call that highlights the row.
  const id = rowIdForTask(task, useStore.getState().tree);
  if (id !== null) void useStore.getState().select(id);
  if (
    task === "geometry" ||
    task === "bodies" ||
    task === "sizing" ||
    task === "surface" ||
    task === "layers" ||
    task === "quality"
  ) {
    useStore.getState().setMeshTask(task);
  } else {
    useStore.getState().setMeshTask(null);
  }
  document.querySelector(".details-body")?.scrollTo({ top: 0, behavior: "auto" });
}

/** The task the selection names right now, or the reply's current, or null before the first ask. */
function currentTask(): string | null {
  const state = useStore.getState();
  const reply = state.meshTasks;
  if (reply === null) return null;
  if (state.meshTask !== null) return state.meshTask;
  const selectedId = state.selectedId;
  for (const task of reply.tasks) {
    if (selectedId === `virtual:mesh/${task.id}`) return task.id;
    if (rowIdForTask(task.id, state.tree) === selectedId) return task.id;
  }
  return reply.current;
}

// The stepper's chords (brief M-502): between the nine tasks, in the defaults of both sides. The
// handler reads the same reply the stepper draws, so a chord cannot disagree with what is on screen.
registerAction("mesh.task.next", () => {
  const reply = useStore.getState().meshTasks;
  if (reply === null) return false;
  const order = reply.tasks.map((task) => task.id);
  const index = order.indexOf(currentTask() ?? reply.current);
  const next = order[index + 1] ?? order[0];
  if (next === undefined) return false;
  chooseTask(next);
  return true;
});
registerAction("mesh.task.previous", () => {
  const reply = useStore.getState().meshTasks;
  if (reply === null) return false;
  const order = reply.tasks.map((task) => task.id);
  const index = order.indexOf(currentTask() ?? reply.current);
  const previous = order[index - 1] ?? order[order.length - 1];
  if (previous === undefined) return false;
  chooseTask(previous);
  return true;
});

/**
 * The full stepper block: the heading, the nine rows, and the run foot. Drawn from the same pure
 * model the collapsed line reads, so the two cannot disagree about what is current.
 */
export default function MeshStepper({ foot = true }: {
  /**
   * Draw the Generate button and its reason line under the rows. False when the page below the stepper is the
   * Mesh panel, which has the one Generate and the one reason of the page.
   */
  foot?: boolean;
} = {}) {
  const { reply } = useMeshTasks();
  const generate = useGenerateState();
  const busy = useMeshContext((c) => c.busy);
  const live = useStore((s) => s.mesh);
  const shown = useWorkspace((s) => s.document.preferences.mesh.stepper);
  const selectedTask = useSelectedTask(reply);

  const onSelect = (task: string) => chooseTask(task);

  if (!stepperShownGuard(shown) || reply === null) return null;
  const running = live.stage === "running" || live.stage === "cancelling";
  return (
    <section className="mesh-stepper" aria-label="Mesh steps" data-ws="mesh-stepper">
      <div className="stepper-head">
        <span className="section-title">Mesh steps</span>
        <span className="muted stepper-head-meta">{headWords(reply)}</span>
      </div>
      <div className="stepper-rows">
        {stepperRows(reply, selectedTask).map((row) => (
          <TaskRow key={row.id} {...row} onSelect={onSelect} />
        ))}
      </div>
      {foot && <div className="stepper-foot">
        <div className="stepper-buttons">
          <Button
            aria-label={generate.label === "Generate" ? "Generate mesh" : generate.label}
            aria-disabled={!generate.enabled || undefined}
            title={generate.enabled ? "Build the mesh" : "Not yet: see the line below"}
            onClick={() => generate.enabled && void generateMesh()}
            data-ws="stepper-generate"
          >
            {busy && !running ? "Starting…" : generate.label}
          </Button>
          {running && (
            <Button
              aria-label="Cancel mesh"
              title="Stop the running mesh"
              onClick={() => void cancelMesh()}
              data-ws="stepper-cancel"
            >
              <Square size={12} /> Stop
            </Button>
          )}
        </div>
        {!generate.enabled && !running && (
          <p className="muted stepper-why" data-ws="stepper-why">
            {generate.reason}
          </p>
        )}
        {running && (
          <p className="muted stepper-progress" data-ws="stepper-progress">
            Building the mesh{live.step !== "" ? ` · ${live.step}` : ""}…
          </p>
        )}
      </div>}
    </section>
  );
}

/** The preference read as the guard the block shows under: "shown" means the block is there. */
function stepperShownGuard(preference: string): boolean {
  return preference === "shown";
}

/**
 * The one-line collapsed form: the pane's fixed header carries it, in every Mesh-stage page. The
 * "Hide/Show" toggle at the end IS the preference, so one control both collapses the block and
 * remembers the choice; the line itself scrolls the block into view while it is shown.
 */
export function MeshStepperLine() {
  const { reply } = useMeshTasks();
  const shown = useWorkspace((s) => s.document.preferences.mesh.stepper);
  const setValue = useWorkspace((s) => s.setValue);
  const selectedTask = useSelectedTask(reply);
  if (reply === null) return null;
  const heading = stepHeading(reply, selectedTask);
  const current = stepperRows(reply, selectedTask).find((r) => r.current);
  const blockShown = stepperShownGuard(shown);
  return (
    <div className="stepper-line" data-ws="stepper-line">
      {blockShown ? (
        <button
          type="button"
          className="stepper-line-main"
          title={heading}
          aria-label={`Mesh steps: ${heading}`}
          onClick={() => {
            // The block is the pane body's first element; the scroll is the pane's own rule
            // (a task click resets it), and the line brings it back into view.
            document.querySelector(".details-body")?.scrollTo({ top: 0, behavior: "auto" });
          }}
        >
          {current?.spin ? (
            <LoaderCircle size={12} className="stepper-spin" aria-label="running" role="img" />
          ) : (
            <StatusDot status={current === undefined || current.dot === "" ? "ok" : current.dot} />
          )}
          <span className="stepper-line-text">{heading}</span>
        </button>
      ) : (
        <span className="stepper-line-main" title={heading}>
          {current?.spin ? (
            <LoaderCircle size={12} className="stepper-spin" aria-label="running" role="img" />
          ) : (
            <StatusDot status={current === undefined || current.dot === "" ? "ok" : current.dot} />
          )}
          <span className="stepper-line-text">{heading}</span>
        </span>
      )}
      <button
        type="button"
        className="stepper-line-toggle"
        title={blockShown ? "Hide the full stepper" : "Show the full stepper"}
        aria-label={blockShown ? "Hide the full stepper" : "Show the full stepper"}
        onClick={() => void setValue(["preferences", "mesh", "stepper"], blockShown ? "hidden" : "shown")}
      >
        {blockShown ? "Hide" : "Show"}
      </button>
    </div>
  );
}
