/**
 * The build ribbon (ADR-0035, ADR-0036): the modeller's tools as a ribbon docked at the bottom of the
 * viewport, one labelled group per kind of tool, an icon and a short label per tool:
 *
 * - **Sketch**: the sketcher, and its drawing tools (Line, Rectangle, Circle, Arc, Polygon, Spline),
 *   each of which opens the sketcher with that tool chosen (review finding B7);
 * - **Shape**: the solid primitives;
 * - **Transform**, **Pattern**, **Combine**, **Modify**: the operations on picked bodies.
 *
 * The entries are the Create menu's (`createEntries`), so the ribbon and the menu offer the same
 * tools with the same rules; a tool the pick does not fit is disabled and says why in its tooltip.
 * Each operation tool opens the ordinary operation card. The Create button of the viewport toolbar
 * opens and closes the ribbon; Esc does not, so it stays while the user works through a model.
 *
 * **It fits** (review finding B10): when the groups do not fit the room between the side panels,
 * groups fold one at a time (`COLLAPSE_ORDER`, Shape first) into a single button that opens its
 * tools as a menu, so nothing scrolls out of reach. The close button is the ribbon's last item,
 * never on top of a tool. While it is open, `S` sketches, `P` pulls, `M` moves and `C` combines
 * (`build.*` actions, bound in the workspace's shortcuts; their tooltips show the chords).
 *
 * **It folds away on leaving Geometry** (`ribbonStage.ts`): in Physics, Mesh, Solve and Results the stage's own
 * cards and the face pick need the room; Create opens it again in any stage.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronUp, X } from "lucide-react";
import { useStore } from "../state/store";
import { Tooltip } from "../components/Tooltip";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { shortcutHint } from "../workspace/keymap";
import { pickFamilies } from "./CreateMenu";
import { createEntries, SKETCHER_ID, type CreateEntry } from "./createEntries";
import { BUILD_KEYS, TOOL_ICONS, nextToCollapse } from "./buildTools";
import { registerAction } from "./actions";
import { operationOf } from "../panels/bodySteps";
import { startSketch } from "./editOperation";
import { ribbonClosesOnStage } from "./ribbonStage";
import "./buildRibbon.css";

useStore.subscribe((state, previous) => {
  if (state.buildRibbon && ribbonClosesOnStage(previous.stage, state.stage)) state.setBuildRibbon(false);
});

/** The room the ribbon leaves at the right for the view buttons and the axis gizmo (bottom right). */
export const BUILD_RIBBON_CORNER = 192;

/** The short label under a tool's icon (the menu's label, shortened where it would not fit). */
const SHORT: Record<string, string> = {
  "sketch-new": "Sketch",
  "shape-rounded_box": "Rounded",
};

/** What each group's tools do, for the group's tooltip. */
const GROUP_HINT: Record<string, string> = {
  sketch: "Draw a profile on a sketch plane, then pull or revolve it",
  shape: "Add a solid shape",
  transform: "Move, rotate, mirror or scale one picked body made with Create, or picked imported bodies",
  pattern: "Copy one picked body in a row or around an axis",
  combine: "Combine two or more picked bodies made with Create: the first picked is kept, the others act on it",
  modify: "Offset or shell one picked body",
};

/** "Move (M)": a label with the chord of its `build.*` action, when it has one. */
function withKey(label: string, id: string): string {
  const action = BUILD_KEYS[id];
  const key = action === undefined ? undefined : shortcutHint(action);
  return key === undefined ? label : `${label} (${key})`;
}

/** Runs a tool: the sketcher (with its tool) or the operation card. */
function use(tool: CreateEntry): void {
  if (!tool.enabled) return;
  if (tool.id === SKETCHER_ID || tool.tool !== undefined) {
    startSketch(tool.tool);
    return;
  }
  if (tool.op === undefined) return;
  useStore.getState().openOperation(tool.op, tool.params);
}

/** The entry `id` in the groups, or null. */
function findEntry(groups: readonly CreateEntry[], id: string): CreateEntry | null {
  for (const group of groups) {
    if (group.id === id) return group;
    const hit = group.entries?.find((e) => e.id === id);
    if (hit !== undefined) return hit;
  }
  return null;
}

/** A tool the pick does not fit says why, as its disabled button's tooltip does. */
function useOrSay(entry: CreateEntry | null): void {
  if (entry === null) return;
  if (!entry.enabled) {
    useStore.getState().pushToast("info", entry.why ?? "The pick does not fit this tool");
    return;
  }
  use(entry);
}

/** One folded group: a button that opens its tools as a menu. */
function FoldedGroup({ group }: { group: CreateEntry }) {
  const ref = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const first = group.entries?.[0];
  const Icon = first ? TOOL_ICONS[first.id] : undefined;
  const items: MenuItemSpec[] = (group.entries ?? []).map((tool) => ({
    id: tool.id,
    label: tool.label,
    disabled: !tool.enabled,
    ...(tool.enabled ? {} : { description: tool.why ?? "not available" }),
    onSelect: () => use(tool),
  }));
  return (
    <div className="build-group build-group-folded" role="group" aria-label={group.label} data-ws={`build-${group.id}`}>
      <Tooltip label={withKey(GROUP_HINT[group.id] ?? group.label, group.id)} side="top" describe={false}>
        <button
          ref={ref}
          type="button"
          className="build-tool"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`${group.label} tools`}
          data-ws={`build-fold-${group.id}`}
          onClick={() => setOpen((o) => !o)}
        >
          {Icon ? <Icon size={18} strokeWidth={1.6} aria-hidden="true" /> : null}
          <span className="build-tool-label">
            {group.label} <ChevronUp size={10} aria-hidden="true" />
          </span>
        </button>
      </Tooltip>
      <Menu open={open} anchor={ref} onClose={() => setOpen(false)} items={items} label={group.label} placement="top-start" />
    </div>
  );
}

export default function BuildRibbon({ leftInset, rightInset }: { leftInset: number; rightInset: number }) {
  // The sketcher has its own tools and covers the ribbon's place while it is open.
  const open = useStore((s) => s.buildRibbon && s.sketch === null);
  const picked = useStore((s) => s.picked);
  const tree = useStore((s) => s.tree);
  const operationTypes = useStore((s) => s.operationTypes);
  const groups = useMemo(() => {
    const built: Record<string, boolean> = {};
    for (const [type, info] of Object.entries(operationTypes)) built[type] = info.built;
    return createEntries({ ...pickFamilies(picked, tree), built });
  }, [picked, tree, operationTypes]);

  // The modeller's letters, only while the ribbon is open: closed, the same letters reach Section,
  // Paint, Measure (the keymap tries the `build.*` action first and finds no handler).
  const groupsRef = useRef(groups);
  groupsRef.current = groups;
  useEffect(() => {
    if (!open) return;
    const stops = [
      registerAction("build.sketch", () => useOrSay(findEntry(groupsRef.current, SKETCHER_ID))),
      registerAction("build.move", () => useOrSay(findEntry(groupsRef.current, "transform-translate"))),
      registerAction("build.combine", () => useOrSay(findEntry(groupsRef.current, "combine-union"))),
      registerAction("build.pull", () => {
        // Pull is the depth of a sketched body: its sketch operation's page, where Depth is.
        const s = useStore.getState();
        const part = s.picked.bodies.length === 1 ? s.picked.bodies[0] : null;
        const op = part === null ? null : operationOf(s.tree, part);
        if (op === null || op.type !== "implicit_sketch") {
          const sketch = shortcutHint("build.sketch");
          s.pushToast("info", `Pull works on a sketched body: pick one${sketch ? `, or press ${sketch} to sketch a profile first` : ""}`);
          return;
        }
        s.select(op.id);
      }),
    ];
    return () => stops.forEach((stop) => stop());
  }, [open]);

  // Fold groups until the ribbon fits; unfold them all when the room grows, then fold again.
  const ribbon = useRef<HTMLDivElement | null>(null);
  const dock = useRef<HTMLDivElement | null>(null);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = dock.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let last = 0;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width);
      if (width > last + 8) setCollapsed([]); // more room: try the full ribbon again
      last = width;
      setRoom(width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [open]);
  useLayoutEffect(() => {
    const el = ribbon.current;
    if (!el || el.scrollWidth <= el.clientWidth + 1) return;
    const next = nextToCollapse(collapsed, groups.map((g) => g.id));
    if (next !== null) setCollapsed((c) => [...c, next]);
  }, [collapsed, groups, room, open]);

  if (!open) return null;

  return (
    // The dock spans the room between the side panels; the ribbon is centred in it and never wider.
    <div ref={dock} className="build-ribbon-dock" style={{ left: `${leftInset + 16}px`, right: `${rightInset + BUILD_RIBBON_CORNER}px` }}>
      <div ref={ribbon} className="build-ribbon glass" role="toolbar" aria-label="Build" data-ws="build-ribbon">
        {groups.map((group) =>
          collapsed.includes(group.id) ? (
            <FoldedGroup key={group.id} group={group} />
          ) : (
            <div key={group.id} className="build-group" role="group" aria-label={group.label} data-ws={`build-${group.id}`}>
              <div className="build-tools">
                {(group.entries ?? []).map((tool) => {
                  const Icon = TOOL_ICONS[tool.id];
                  const label = SHORT[tool.id] ?? tool.label;
                  const named = withKey(tool.label, tool.id);
                  const tip = tool.enabled ? named : `${named}: ${tool.why ?? "not available"}`;
                  return (
                    <Tooltip key={tool.id} label={tip} side="top" describe={false}>
                      <button
                        type="button"
                        className="build-tool"
                        aria-label={tool.label}
                        aria-disabled={!tool.enabled}
                        aria-keyshortcuts={BUILD_KEYS[tool.id] ? shortcutHint(BUILD_KEYS[tool.id]) : undefined}
                        data-ws={`build-tool-${tool.id}`}
                        onClick={() => use(tool)}
                      >
                        {Icon ? <Icon size={18} strokeWidth={1.6} aria-hidden="true" /> : null}
                        <span className="build-tool-label">{label}</span>
                      </button>
                    </Tooltip>
                  );
                })}
              </div>
              <Tooltip label={withKey(GROUP_HINT[group.id] ?? group.label, group.id)} side="top" describe={false}>
                <div className="build-group-label">{group.label}</div>
              </Tooltip>
            </div>
          ),
        )}
        <div className="build-end">
          <Tooltip label="Close the build ribbon" side="top" describe={false}>
            <button type="button" className="build-close" aria-label="Close the build ribbon" data-ws="build-close" onClick={() => useStore.getState().setBuildRibbon(false)}>
              <X size={13} aria-hidden="true" />
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
