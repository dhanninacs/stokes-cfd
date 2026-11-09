/**
 * The ribbon (`topbar.stages.style: "ribbon"`, the Ribbon preset): a band under the top bar with the
 * stage names as tabs and, under the same band, a task strip with the current stage's actions. For
 * now the tasks are the actions the context toolbar, the islands and the project menu already offer,
 * as labelled buttons that run through the action bus (app/actions.ts); each one shows its shortcut.
 * The `1`–`6` keys still switch the stage.
 */
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { ChevronDown, Eye, EyeOff, Grid2x2, Grid3x3, Import, Maximize2, Pencil, Play, Ruler, Scissors, Square, SquareTerminal, Tag, ChartLine, FileText, Wrench } from "lucide-react";
import { STAGES, useStore, type Stage } from "../state/store";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { OrthographicIcon, PerspectiveIcon } from "../components/icons";
import { stageIcon } from "../app/topbarItems";
import { VIEW_MODES, runAction } from "../app/actions";
import { emitRename } from "../app/events";
import { usePrepareMenu } from "../app/PrepareMenu";
import { useAssignMenu } from "../panels/AssignMenu";
import { useGenerateState } from "../panels/meshRun";
import { pressRun, useRunButton } from "../panels/runControl";
import { panelShown } from "./geometry";
import { useShortcut } from "./keymap";
import { useWorkspace } from "./store";
import { useStages } from "./tree";

function Task({ label, icon, action, onClick, disabled, why, active, chevron }: { label: string; icon: ReactNode; action?: string; onClick: (e: ReactMouseEvent<HTMLButtonElement>) => void; disabled?: boolean; why?: string; active?: boolean; chevron?: boolean }) {
  const shortcut = useShortcut(action ?? "");
  return (
    <Tooltip label={disabled && why ? why : label} hint={action ? shortcut.hint : undefined} side="bottom" describe={false}>
      <Button variant="ghost" className="ribbon-task" aria-disabled={disabled || undefined} aria-keyshortcuts={action ? shortcut.keys : undefined} active={active} aria-pressed={active} onClick={(e) => !disabled && onClick(e)}>
        {icon}
        <span>{label}</span>
        {chevron && <ChevronDown size={13} />}
      </Button>
    </Tooltip>
  );
}

function PatchTasks() {
  const patch = useStore((s) => s.selectedPatch);
  const isolated = useStore((s) => s.isolatedPatch !== null);
  const hidden = useStore((s) => s.selectedPatch !== null && s.hiddenPatches.includes(s.selectedPatch));
  const treeShown = useWorkspace((s) => panelShown(s.document.layout, "tree"));
  const assign = useAssignMenu();
  const nothingToAssignTo = "Pick faces or bodies, or select a patch";
  return (
    <>
      <Task
        label="Assign"
        icon={<Tag size={14} />}
        chevron
        disabled={!assign.ready}
        why={nothingToAssignTo}
        onClick={(e) => assign.open(e.currentTarget)}
      />
      <Task label={isolated ? "Show all" : "Isolate"} icon={<Eye size={14} />} action="selection.isolate" active={isolated} onClick={() => runAction("selection.isolate")} />
      <Task label={hidden ? "Show" : "Hide"} icon={<EyeOff size={14} />} action="selection.hide" disabled={!patch} why="Select a patch in the viewport first" onClick={() => runAction("selection.hide")} />
      <Task
        label="Rename"
        icon={<Pencil size={14} />}
        disabled={!patch || !treeShown}
        why={treeShown ? "Select a patch in the viewport first" : "Show the tree to rename in place"}
        onClick={() => {
          if (!patch) return;
          useStore.getState().selectPatch(patch);
          emitRename({ patch, nodeId: useStore.getState().selectedId });
        }}
      />
      {assign.menu}
      {assign.dialog}
    </>
  );
}

function ViewTasks({ section = true, measure = true }: { section?: boolean; measure?: boolean }) {
  const orthographic = useStore((s) => s.orthographic);
  const measuring = useStore((s) => s.measureMode);
  return (
    <>
      <Task label="Fit" icon={<Maximize2 size={14} />} action="view.fit" onClick={() => runAction("view.fit")} />
      {section && <Task label="Section" icon={<Scissors size={14} />} action="view.section" onClick={() => runAction("view.section")} />}
      {measure && <Task label="Measure" icon={<Ruler size={14} />} action="view.measure" active={measuring} onClick={() => runAction("view.measure")} />}
      <Task
        label="Orthographic"
        icon={orthographic ? <OrthographicIcon size={14} /> : <PerspectiveIcon size={14} />}
        action="view.projection"
        active={orthographic}
        onClick={() => runAction("view.projection")}
      />
    </>
  );
}

/** The Geometry stage's Prepare task (T-029): the menu's entries, disabled with a reason by the pick. */
function PrepareTask() {
  const prepare = usePrepareMenu();
  return (
    <>
      <Task label="Prepare" icon={<Wrench size={14} />} chevron disabled={!prepare.ready} why={prepare.why} onClick={(e) => prepare.open(e.currentTarget)} />
      {prepare.menu}
    </>
  );
}

function MeshTasks() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const generate = useGenerateState();
  return (
    <>
      <Task label="Generate mesh" icon={<Grid3x3 size={14} />} action="mesh.generate" disabled={!generate.enabled} why={generate.reason} onClick={() => runAction("mesh.generate")} />
      <span className="ribbon-divider" aria-hidden="true" />
      {VIEW_MODES.map((m) => (
        <Task key={m.id} label={m.label} icon={<Grid2x2 size={14} />} active={viewMode === m.id} onClick={() => setViewMode(m.id)} />
      ))}
    </>
  );
}

function OutputTask({ label, tab, icon }: { label: string; tab: "console" | "residuals" | "reports"; icon: ReactNode }) {
  return <Task label={label} icon={icon} onClick={() => useStore.getState().setDrawerTab(tab)} />;
}

/** Run, or Stop while a run is going: the same button state as the top bar's and the Solve panel's (T-217). */
function RunTask() {
  const button = useRunButton();
  const stopping = button.label === "Stop";
  return <Task label={button.label} icon={stopping ? <Square size={14} /> : <Play size={14} />} disabled={!button.enabled} why={`${button.label} · ${button.reason}`} onClick={pressRun} />;
}

function StageTasks({ stage }: { stage: Stage }) {
  const hasProject = useStore((s) => s.project !== null);
  const divider = <span className="ribbon-divider" aria-hidden="true" />;
  switch (stage) {
    case "Geometry":
      return (
        <>
          <Task label="Import geometry…" icon={<Import size={14} />} action="project.importStep" disabled={!hasProject} why="Open a project first" onClick={() => runAction("project.importStep")} />
          {divider}
          <PrepareTask />
          {divider}
          <PatchTasks />
          {divider}
          <ViewTasks section={false} />
        </>
      );
    case "Mesh":
      return (
        <>
          <MeshTasks />
          {divider}
          <ViewTasks measure={false} />
        </>
      );
    case "Physics":
      return (
        <>
          <PatchTasks />
          {divider}
          <ViewTasks section={false} measure={false} />
        </>
      );
    case "Solve":
      return (
        <>
          <RunTask />
          {divider}
          <OutputTask label="Residuals" tab="residuals" icon={<ChartLine size={14} />} />
          <OutputTask label="Console" tab="console" icon={<SquareTerminal size={14} />} />
        </>
      );
    case "Results":
      return (
        <>
          <ViewTasks />
          {divider}
          <OutputTask label="Reports" tab="reports" icon={<FileText size={14} />} />
        </>
      );
    default:
      // Explore and later stages: their own tasks come with their panels; the view stays at hand.
      return <ViewTasks />;
  }
}

function StageTab({ stage, active }: { stage: Stage; active: boolean }) {
  const Icon = stageIcon(stage);
  const shortcut = useShortcut(`stage.${STAGES.indexOf(stage) + 1}`);
  const setStage = useStore((s) => s.setStage);
  return (
    <Tooltip label={stage} hint={shortcut.hint} side="bottom" describe={false}>
      <button
        type="button"
        role="tab"
        id={`ribbon-tab-${stage}`}
        aria-selected={active}
        aria-controls="ribbon-tasks"
        aria-keyshortcuts={shortcut.keys}
        tabIndex={active ? 0 : -1}
        className={`ribbon-tab no-drag ${active ? "active" : ""}`}
        onClick={() => setStage(stage)}
      >
        <Icon size={14} aria-hidden="true" />
        {stage}
      </button>
    </Tooltip>
  );
}

export default function Ribbon() {
  const stage = useStore((s) => s.stage);
  const setStage = useStore((s) => s.setStage);
  const stages = useStages();
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = stages[(stages.indexOf(stage) + step + stages.length) % stages.length];
    setStage(next);
    requestAnimationFrame(() => document.getElementById(`ribbon-tab-${next}`)?.focus());
  };
  return (
    <div className="ribbon" data-ws-item="stages">
      <div role="tablist" aria-label="Workflow stage" className="ribbon-tabs" onKeyDown={onKeyDown}>
        {stages.map((s) => (
          <StageTab key={s} stage={s} active={s === stage} />
        ))}
      </div>
      <div id="ribbon-tasks" role="tabpanel" aria-labelledby={`ribbon-tab-${stage}`} className="ribbon-tasks">
        <div role="toolbar" aria-label={`${stage} tasks`} className="ribbon-toolbar">
          <StageTasks stage={stage} />
        </div>
      </div>
    </div>
  );
}
