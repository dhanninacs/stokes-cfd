import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { useStore } from "../state/store";
import { isRunActive } from "../panels/runApi";
import { TOPBAR_ITEMS } from "../workspace/registry";
import { useWorkspace, useWorkspaceReady } from "../workspace/store";
import type { StageStyle, TopbarItemSpec } from "../workspace/types";
import { host } from "./host";
import { TreeToggle } from "../guided/GuidedControls";
import { TopBarStrip } from "../guided/GuidedLayer";
import { TrafficLights, engineLabel } from "./topbarItems";

/** Anything that handles its own pointer: pressing it must not start a window drag. */
const INTERACTIVE = "button, a, input, select, textarea, label, [role='button'], [role='menuitem'], [role='switch'], [role='separator'], .no-drag";

function isInteractive(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(INTERACTIVE) !== null;
}

/**
 * What the bar gives up, lowest value first, when a cell cannot hold its cluster. The names are the
 * words styles.css matches in `data-compact`; `selector` finds the element a step hides, and so the
 * cell it makes room in (the items can sit in either cell); the chips sit between the cells and are
 * shared by both. A step whose element is not in the bar is skipped.
 */
const COMPACT_STEPS: readonly { name: string; selector: string; either?: boolean }[] = [
  { name: "licence", selector: ".tb-licence" },
  { name: "engine", selector: ".tb-engine" },
  { name: "commands", selector: ".tb-commands-label" },
  { name: "wordmark", selector: ".wordmark" },
  { name: "units", selector: ".tb-units" },
  { name: "chips", selector: ".stage-chips", either: true },
  { name: "kbd", selector: ".tb-commands-kbd" },
  { name: "tree", selector: ".tb-tree-label" },
  { name: "name", selector: ".project-btn" },
];

/**
 * True when a cell cannot hold its cluster. The left cell packs its items at the start and clips
 * at the end, which `scrollWidth` reports (end padding included, so the cluster keeps a gap before
 * the chips). The right cell packs its items at the end, so what does not fit runs off its start,
 * where `scrollWidth` never looks: there the first item's edge is compared with the start of the
 * content box instead.
 */
function overflows(cell: HTMLElement, packedAtEnd: boolean): boolean {
  if (!packedAtEnd) return cell.scrollWidth > cell.clientWidth + 0.5;
  const first = cell.firstElementChild;
  if (!first) return false;
  const inset = parseFloat(getComputedStyle(cell).paddingLeft) || 0;
  return first.getBoundingClientRect().left < cell.getBoundingClientRect().left + inset - 0.5;
}

/**
 * Measured compaction. Starts from the full bar, then takes the steps in order while either cell
 * overflows, skipping a step that would only free room in the cell that already fits. The result
 * is written to `data-compact` (not through React: no render may undo it). Engine names and
 * licence labels vary, so the bar measures what it has instead of guessing at breakpoints.
 */
function fitBar(bar: HTMLElement): void {
  const left = bar.querySelector<HTMLElement>(".topbar-left");
  const right = bar.querySelector<HTMLElement>(".topbar-right");
  if (!left || !right) return;
  const applied: string[] = [];
  bar.dataset.compact = "";
  for (const step of COMPACT_STEPS) {
    const leftOver = overflows(left, false);
    const rightOver = overflows(right, true);
    if (!leftOver && !rightOver) break;
    const target = bar.querySelector(step.selector);
    if (!target) continue;
    const helps = step.either ? true : left.contains(target) ? leftOver : right.contains(target) ? rightOver : false;
    if (!helps) continue;
    applied.push(step.name);
    bar.dataset.compact = applied.join(" ");
  }
}

interface BarPlan {
  left: string[];
  right: string[];
  /** The stage chips sit in the centre column (style `chips`, item visible). */
  chips: boolean;
  /** Hosted with the window item visible: the traffic lights lead the left cell (Cupertino shows them). */
  lights: boolean;
}

/**
 * Items before `stages` go in the left cell and items after it in the right one; unknown ids (a
 * plug-in that is not loaded) and hidden items are skipped, and `window` only exists when hosted.
 */
function planBar(items: readonly TopbarItemSpec[], style: StageStyle, hosted: boolean): BarPlan {
  const at = items.findIndex((i) => i.id === "stages");
  const split = at >= 0 ? at : items.length;
  const shown = (i: TopbarItemSpec) => i.visible !== false && i.id !== "stages" && TOPBAR_ITEMS.has(i.id) && (i.id !== "window" || hosted);
  const stages = at >= 0 ? items[at] : null;
  const windowItem = items.find((i) => i.id === "window");
  return {
    left: items.slice(0, split).filter(shown).map((i) => i.id),
    right: items.slice(split + 1).filter(shown).map((i) => i.id),
    chips: stages !== null && stages.visible !== false && style === "chips",
    lights: hosted && windowItem?.visible !== false,
  };
}

/**
 * One cell's items. Consecutive status items share one status cluster (the hairlines between them
 * are sibling rules), and the "/" of the breadcrumb sits between the logo and the project menu when
 * they are neighbours.
 */
function renderCell(ids: readonly string[]): ReactNode[] {
  const out: ReactNode[] = [];
  let cluster: string[] = [];
  const flush = () => {
    if (!cluster.length) return;
    const members = cluster;
    out.push(
      <div key={`status-${members[0]}`} className="status-cluster" role="group" aria-label="Status">
        {members.map((id) => {
          const Item = TOPBAR_ITEMS.get(id)?.component;
          return Item ? <Item key={id} /> : null;
        })}
      </div>,
    );
    cluster = [];
  };
  ids.forEach((id, index) => {
    if (id.startsWith("status.")) {
      cluster.push(id);
      return;
    }
    flush();
    const Item = TOPBAR_ITEMS.get(id)?.component;
    if (!Item) return;
    if (id === "project" && ids[index - 1] === "logo") {
      out.push(
        <span key="crumb" className="muted" aria-hidden="true">
          /
        </span>,
      );
    }
    out.push(<Item key={id} />);
  });
  flush();
  return out;
}

export default function TopBar({ hosted }: { hosted: boolean }) {
  const theme = useStore((s) => s.theme);
  // What changes the width of a cluster, so the bar can measure again (see fitBar).
  const projectName = useStore((s) => s.project?.name ?? "");
  const units = useStore((s) => s.project?.display_units ?? "si");
  const engineText = useStore((s) => engineLabel(s.engine));
  const edition = useStore((s) => s.licence?.edition ?? "free");
  const stage = useStore((s) => s.stage); // the active chip is bolder, hence wider
  const ready = useWorkspaceReady();
  const items = useWorkspace((s) => s.document.topbar.items);
  const style = useWorkspace((s) => s.document.topbar.stages.style);
  const plan = useMemo(() => planBar(items, style, hosted), [items, style, hosted]);
  // An open project has the flow strip in this bar's middle cell, in Guided and in Full mode alike (a press
  // on a step brings its stage in front): the stage chips and the Next-step button would say the same thing a
  // second time, so they give way to it. The strip used to float in a second row over the viewport while the
  // middle cell here stood empty (owner, 2026-11-03: "the top part is not completely utilised").
  const stripShown = useStore((s) => s.project !== null);
  const chips = plan.chips && !stripShown;
  const shape = `${plan.left.join(",")}|${chips}|${plan.right.join(",")}|${ready}`;
  const Chips = TOPBAR_ITEMS.get("stages")?.component;

  const bar = useRef<HTMLElement>(null);
  const fit = useCallback(() => {
    if (bar.current) fitBar(bar.current);
  }, []);
  // Run turns into the live pill (progress, sparkline, count) while a run is going, which widens the right cell.
  const runLive = useStore((s) => s.run.runs[0] !== undefined && isRunActive(s.run.runs[0].state));
  useLayoutEffect(fit, [fit, theme, hosted, projectName, units, engineText, edition, stage, shape, runLive]);
  useEffect(() => {
    const el = bar.current;
    if (!el) return;
    const resized = new ResizeObserver(fit);
    resized.observe(el);
    // The live Run pill grows when its sparkline first appears and as the count gains digits.
    const run = el.querySelector('[data-ws-item="run"]');
    if (run) resized.observe(run);
    // App writes data-theme from an effect that runs after the layout effect above, so a theme
    // change is measured here, once the new style is in place. Token changes set it again too.
    const themed = new MutationObserver(fit);
    themed.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    // The UI font arrives after the first layout, and its subsets load on demand.
    void document.fonts?.ready.then(fit);
    document.fonts?.addEventListener("loadingdone", fit);
    return () => {
      resized.disconnect();
      themed.disconnect();
      document.fonts?.removeEventListener("loadingdone", fit);
    };
    // `shape` too: the Run item mounts once the workspace is ready and moves when the items change.
  }, [fit, shape]);

  // The whole bar is the drag region: any press that is not on a control moves the window.
  const onPointerDown = (e: PointerEvent<HTMLElement>) => {
    if (hosted && e.button === 0 && !isInteractive(e.target)) host.startMove();
  };
  const onDoubleClick = (e: MouseEvent<HTMLElement>) => {
    if (hosted && !isInteractive(e.target)) host.toggleMaximize();
  };

  return (
    <header ref={bar} className="topbar" data-strip={stripShown || undefined} onPointerDown={onPointerDown} onDoubleClick={onDoubleClick}>
      <div className="topbar-left">
        {ready && plan.lights && <TrafficLights />}
        {ready && renderCell(plan.left)}
      </div>

      {ready && stripShown ? <TopBarStrip /> : ready && chips && Chips ? <Chips /> : <div aria-hidden="true" />}

      <div className="topbar-right">
        {/* Guided mode's Tree button (and Ctrl+B): the tree and Details are put away until asked for. */}
        {ready && <TreeToggle />}
        {ready && renderCell(plan.right)}
      </div>
    </header>
  );
}
