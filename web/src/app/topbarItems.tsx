/**
 * The top-bar items of the workspace registry (`topbar.items`): each one the same element, classes
 * and tooltip the bar always had, so the default document draws the bar it did. Each root carries
 * `data-ws-item` (the project menu's button is found by its class), which the Customise overlay uses
 * to put a handle on it. The order, the visibility and the split into the two cells are TopBar's.
 */
import { useMemo, useSyncExternalStore, type CSSProperties } from "react";
import { Atom, Box, ChartLine, Copy, Cpu, FlaskConical, Grid3x3, Layers, Minus, Moon, Play, Search, Square, Sun, X, type LucideIcon } from "lucide-react";
import type { EngineInfo } from "../api/types";
import { STAGES, composeTheme, themeAppearance, themeFamily, useStore, type Stage } from "../state/store";
import { Button, IconButton } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { useShortcut } from "../workspace/keymap";
import { useStages } from "../workspace/tree";
import { chooseTheme } from "../workspace/theme";
import { getMaximized, host, subscribeMaximized } from "./host";
import ProjectMenu from "./ProjectMenu";
import NextStepButton from "./NextStep";
import { pressRun, useIterationLimit, useRunButton } from "../panels/runControl";
import { progressFraction } from "../panels/runPanelModel";
import { residualSpark } from "../state/residualSpark";

/** By stage name; a stage without its own icon (a later one) gets the generic one. */
const STAGE_ICONS: Record<string, LucideIcon> = {
  Geometry: Box,
  Mesh: Grid3x3,
  Physics: Atom,
  Solve: Cpu,
  Results: ChartLine,
  Explore: FlaskConical,
};

export function stageIcon(stage: string): LucideIcon {
  return STAGE_ICONS[stage] ?? Layers;
}

const UNIT_LABELS: Record<string, string> = { si: "SI", mm: "mm", imperial: "Imperial" };

export function unitLabel(units: string): string {
  return UNIT_LABELS[units] ?? units.toUpperCase();
}

export function engineLabel(engine: EngineInfo | null): string {
  return engine?.attached ? (engine.name ?? "Engine") : "No engine";
}

/**
 * The mark is the app icon artwork itself (`web/public/favicon.png`, a copy of
 * `stokes/app/assets/stokes.png`): its own rounded tile and colours in every theme, so the box
 * around it has no background and nothing here reads a theme token. The wordmark is text.
 */
export function Logo() {
  return (
    <div className="flex items-center gap-2.5 pr-1 pl-3" data-ws-item="logo">
      <div className="logo-mark" aria-hidden="true">
        <img src="/favicon.png" alt="" width={28} height={28} draggable={false} />
      </div>
      <span className="wordmark">STOKES</span>
    </div>
  );
}

/** The project menu; the "/" between it and the logo is TopBar's (it shows only right after the logo). */
export function ProjectItem() {
  return <ProjectMenu />;
}

function StageChip({ stage, active, onSelect }: { stage: Stage; active: boolean; onSelect: (s: Stage) => void }) {
  const Icon = stageIcon(stage);
  // stage.N names the N-th stage of the project store's list, whatever order the control shows.
  const shortcut = useShortcut(`stage.${STAGES.indexOf(stage) + 1}`);
  return (
    <Tooltip label={stage} hint={shortcut.hint} side="bottom" describe={false}>
      <button
        type="button"
        className={`chip no-drag ${active ? "active" : ""}`}
        aria-label={stage}
        aria-current={active ? "step" : undefined}
        aria-keyshortcuts={shortcut.keys}
        onClick={() => onSelect(stage)}
      >
        <Icon size={14} />
        <span className="chip-label">{stage}</span>
      </button>
    </Tooltip>
  );
}

/** The stages the workspace lists (tree.stages), as chips. */
export function StageChips() {
  const stage = useStore((s) => s.stage);
  const setStage = useStore((s) => s.setStage);
  const stages = useStages();
  return (
    <div className="stage-chips" role="group" aria-label="Workflow stage" data-ws-item="stages">
      {stages.map((s) => (
        <StageChip key={s} stage={s} active={s === stage} onSelect={setStage} />
      ))}
      {/* What the case needs next, as one button at the end of the stages (T-219 C). */}
      <NextStepButton />
    </div>
  );
}

export function StatusConnection() {
  const connected = useStore((s) => s.connected);
  return (
    <Tooltip label={connected ? "Connected to the Stokes server" : "Disconnected from the Stokes server, reconnecting"} side="bottom">
      <span className="conn" data-state={connected ? "up" : "down"} role="img" aria-label={connected ? "Connected" : "Disconnected"} data-ws-item="status.connection" />
    </Tooltip>
  );
}

export function StatusUnits() {
  const units = useStore((s) => s.project?.display_units ?? "si");
  const unitText = unitLabel(units);
  return (
    <Tooltip label={`Display units: ${unitText}. Values are stored in SI.`} side="bottom">
      <span className="status-pill accent tb-units" data-ws-item="status.units">
        {unitText}
      </span>
    </Tooltip>
  );
}

export function StatusEngine() {
  const engine = useStore((s) => s.engine);
  const engineTip = engine?.attached ? `Engine ${engine.name ?? ""} ${engine.version ?? ""}`.trim() : "No solver engine is attached yet.";
  return (
    <Tooltip label={engineTip} side="bottom">
      <span className="status-pill tb-engine" data-ws-item="status.engine">
        <Cpu size={12} aria-hidden="true" />
        {engineLabel(engine)}
      </span>
    </Tooltip>
  );
}

export function StatusLicence() {
  const licence = useStore((s) => s.licence);
  const licensed = licence?.edition === "licensed";
  const licenceTip = licensed ? `Licensed${licence?.holder ? ` to ${licence.holder}` : ""}${licence?.expires ? `, until ${licence.expires}` : ""}` : "Free edition";
  return (
    <Tooltip label={licenceTip} side="bottom">
      <span className="status-pill tb-licence" data-ws-item="status.licence">
        {licensed ? "Pro" : "Free"}
      </span>
    </Tooltip>
  );
}

export function CommandsItem() {
  const setPalette = useStore((s) => s.setPalette);
  const shortcut = useShortcut("palette.open");
  return (
    <Tooltip label="Command palette" hint={shortcut.hint} side="bottom" describe={false}>
      <Button variant="ghost" aria-label="Commands" aria-keyshortcuts={shortcut.keys} onClick={() => setPalette(true)} data-ws-item="commands">
        <Search size={15} />
        <span className="muted tb-commands-label">Commands</span>
        {shortcut.hint && <span className="kbd tb-commands-kbd">{shortcut.hint}</span>}
      </Button>
    </Tooltip>
  );
}

/** Flips the appearance inside the current style family and writes it to the workspace. */
export function AppearanceItem() {
  const theme = useStore((s) => s.theme);
  const dark = themeAppearance(theme) === "dark";
  const flipped = composeTheme(themeFamily(theme), dark ? "light" : "dark");
  return (
    <IconButton label={dark ? "Switch to the light appearance" : "Switch to the dark appearance"} side="bottom" onClick={() => chooseTheme({ name: flipped })} data-ws-item="appearance">
      {dark ? <Sun size={15} /> : <Moon size={15} />}
    </IconButton>
  );
}

/**
 * Run, and Stop while a run is going (T-217). The label, the enabled state and the reason are
 * `runButton` (`panels/runPanelModel.ts`); a disabled Run keeps its tooltip and says why in it ("Run ·
 * Generate the mesh in the Mesh stage"), and pressing it toasts the same reason. `aria-disabled`, not
 * `disabled`, so the tooltip still opens and the button stays in the tab order.
 */
/** The live pill's sparkline box, in CSS pixels. */
const SPARK_W = 40;
const SPARK_H = 14;

export function RunItem() {
  const button = useRunButton();
  const stopping = button.label === "Stop";
  const run = useStore((s) => s.run.runs[0] ?? null);
  const residuals = useStore((s) => s.run.residuals);
  const limit = useIterationLimit();
  // While a run is going the button is its live readout: a progress fill behind it, the worst residual's
  // last 120 iterations as a sparkline, and the iteration count. Stopped, it is the plain primary pill.
  const live = stopping && run !== null;
  // A fraction of 0 is the start-up (decompose, first iteration): the sweep says "working" better than an empty bar.
  const measured = live ? progressFraction(run, limit) : null;
  const fraction = measured !== null && measured > 0 ? measured : null;
  const spark = useMemo(() => (live ? residualSpark(residuals, SPARK_W, SPARK_H) : ""), [live, residuals]);
  const tip = button.enabled ? (stopping ? "Stop the run" : "Run the simulation") : `${button.label} · ${button.reason}`;
  const style = fraction !== null ? ({ "--run-progress": `${(fraction * 100).toFixed(1)}%` } as CSSProperties) : undefined;
  return (
    <Tooltip label={tip} side="bottom" describe={false}>
      <Button
        variant={stopping ? "ghost" : "primary"}
        className={live ? "run-live" : ""}
        style={style}
        data-progress={fraction !== null || undefined}
        aria-label={stopping ? "Stop the run" : "Run the simulation"}
        aria-disabled={!button.enabled || undefined}
        onClick={pressRun}
        data-ws-item="run"
        data-ws="run-top"
      >
        {stopping ? <Square size={13} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
        {button.label}
        {live && spark !== "" && (
          <svg className="run-spark" width={SPARK_W} height={SPARK_H} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} aria-hidden="true" focusable="false">
            <path d={spark} />
          </svg>
        )}
        {live && run.iterations > 0 && <span className="run-iter">{run.iterations.toLocaleString()}</span>}
      </Button>
    </Tooltip>
  );
}

export function WindowControls() {
  const maximized = useSyncExternalStore(subscribeMaximized, getMaximized, () => false);
  return (
    <div className="win-controls" data-ws-item="window">
      <IconButton label="Minimise" side="bottom" onClick={() => host.minimize()}>
        <Minus size={14} />
      </IconButton>
      <IconButton label={maximized ? "Restore" : "Maximise"} side="bottom" onClick={() => host.toggleMaximize()}>
        {maximized ? <Copy size={12} /> : <Square size={12} />}
      </IconButton>
      <IconButton label="Close" side="bottom" className="win-close" onClick={() => host.close()}>
        <X size={15} />
      </IconButton>
    </div>
  );
}

/** macOS-style window buttons for the Cupertino theme; the CSS shows one set or the other. */
export function TrafficLights() {
  const maximized = useSyncExternalStore(subscribeMaximized, getMaximized, () => false);
  return (
    <div className="traffic-lights no-drag" role="group" aria-label="Window">
      <button type="button" className="traffic-light close" aria-label="Close" onClick={() => host.close()}>
        <svg viewBox="0 0 8 8" aria-hidden="true">
          <path d="M1.8 1.8l4.4 4.4M6.2 1.8L1.8 6.2" />
        </svg>
      </button>
      <button type="button" className="traffic-light minimize" aria-label="Minimise" onClick={() => host.minimize()}>
        <svg viewBox="0 0 8 8" aria-hidden="true">
          <path d="M1.6 4h4.8" />
        </svg>
      </button>
      <button type="button" className="traffic-light zoom" aria-label={maximized ? "Restore" : "Maximise"} onClick={() => host.toggleMaximize()}>
        <svg viewBox="0 0 8 8" aria-hidden="true">
          {maximized ? <path d="M1.6 3.2L4 5.6l2.4-2.4" /> : <path d="M4 1.6v4.8M1.6 4h4.8" />}
        </svg>
      </button>
    </div>
  );
}
