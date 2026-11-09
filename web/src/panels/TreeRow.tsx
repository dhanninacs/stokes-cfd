import { memo, useEffect, useRef, useState } from "react";
import {
  Activity,
  Atom,
  Box,
  ChartLine,
  Cuboid,
  ChevronRight,
  Cpu,
  Droplets,
  Ellipsis,
  Eye,
  EyeOff,
  FlaskConical,
  Folder,
  Gauge,
  Grid3x3,
  History,
  Image,
  Layers,
  Palette,
  Ruler,
  Sigma,
  Tag,
  Target,
  Variable,
  Wind,
  Zap,
  ZapOff,
} from "lucide-react";
import type { NodeSummary } from "../api/types";
import { StatusDot } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { useStore } from "../state/store";
import { isNodeHidden } from "../viewport/hiddenNodes";
import { rowDomId, rowHasActions, rowValueDomId, treeValue, type RowControls, type RowModel } from "./nodeRules";
import { isPlaceholder, isVirtualId, openTargetOf, rowMetaOf, type RowKind } from "./stageTreeModel";
import { countWords } from "./statusWords";
import StylePopover, { type StylePatch } from "./StylePopover";

const TOP_ICONS: Record<string, typeof Box> = {
  geometry: Box,
  mesh: Grid3x3,
  physics: Atom,
  regions: Layers,
  solver: Cpu,
  monitors: Activity,
  scenes: Image,
  runs: History,
  studies: FlaskConical,
};

/**
 * Icons by node type: imported parts (ADR-0014), the types a study is made of (ADR-0016), and the
 * set-up types (ADR-0022 T-047) — a region is a layer and a boundary is a tag-like patch, which is
 * what the shapes say.
 */
const TYPE_ICONS: Record<string, typeof Box> = {
  part: Cuboid,
  study: FlaskConical,
  variable: Variable,
  response: Gauge,
  objective: Target,
  constraint: Ruler,
  region: Layers,
  boundary: Tag,
  mesh_settings: Gauge,
  mesh_result: Grid3x3,
};

/** The rows the stage model adds (T-219 B): each says what it is by its icon, as the real set-up nodes do. */
const ROW_ICONS: Record<RowKind, typeof Box> = {
  "mesh-settings": Gauge,
  "mesh-result": Grid3x3,
  "mesh-capping": Box,
  "mesh-boundaries": Tag,
  "mesh-surface": Layers,
  "mesh-layers": Layers,
  "mesh-quality": Gauge,
  "mesh-fidelity": Target,
  "solver-settings": Cpu,
  "flow-model": Wind,
  run: History,
  fields: Palette,
  field: Palette,
  surfaces: Layers,
  slice: Layers,
  residuals: ChartLine,
  monitors: Activity,
  "result-vectors": Zap,
  "result-streamlines": Wind,
  "result-probe": Target,
  "result-report": Image,
  "result-lod": Layers,
};

/**
 * The outline's stage groups and their folders by name (lower case): the outline names them for the reader
 * ("Boundary conditions"), so each gets the icon of what it holds instead of the same folder down the tree.
 * The stage icons are the top bar's.
 */
const GROUP_ICONS: Record<string, typeof Box> = {
  geometry: Box,
  mesh: Grid3x3,
  physics: Atom,
  solve: Cpu,
  results: ChartLine,
  explore: FlaskConical,
  materials: Droplets,
  regions: Layers,
  "boundary conditions": Tag,
  boundaries: Tag,
  parameters: Sigma,
  parts: Cuboid,
  tags: Tag,
};

function groupIcon(node: NodeSummary) {
  return GROUP_ICONS[node.name.toLowerCase()];
}

function iconFor(node: NodeSummary, depth: number) {
  if (depth === 0) return TOP_ICONS[node.name] ?? groupIcon(node) ?? Folder;
  const row = rowMetaOf(node);
  if (row !== null) return ROW_ICONS[row.row] ?? groupIcon(node) ?? Folder;
  if (node.type === "solver_settings") return Cpu;
  if (node.type === "parameter") return Sigma;
  if (node.path.startsWith("/geometry/tags/")) return Tag;
  return TYPE_ICONS[node.type] ?? groupIcon(node) ?? Folder;
}

// ---------------------------------------------------------------------------------------------- rows
function RenameInput({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  /** Resolves true when the rename went through (the parent then closes the editor). */
  onCommit: (name: string, refocus: boolean) => Promise<boolean>;
  /** `refocus` is false after a blur: the user clicked somewhere else on purpose. */
  onCancel: (refocus: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initial);
  const [failed, setFailed] = useState(false);
  const settled = useRef(false); // Enter and the blur it causes must not both commit

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const commit = async (fromBlur: boolean) => {
    if (settled.current) return;
    const name = value.trim();
    settled.current = true;
    if (!name || name === initial) return onCancel(!fromBlur);
    if (await onCommit(name, !fromBlur)) return;
    // The server refused (the store already showed why). Keep editing after Enter; give up after a blur.
    if (fromBlur) return onCancel(false);
    settled.current = false;
    setFailed(true);
    ref.current?.focus();
  };

  return (
    <input
      ref={ref}
      aria-label="New name"
      className={`field min-w-0 flex-1 !h-[22px] !rounded-[5px] !px-1.5 text-[13px] ${failed ? "error" : ""}`}
      value={value}
      spellCheck={false}
      autoComplete="off"
      onChange={(e) => {
        setValue(e.target.value);
        setFailed(false);
      }}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onBlur={() => void commit(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          void commit(false);
        } else if (e.key === "Escape") {
          e.preventDefault();
          settled.current = true;
          onCancel(true);
        }
      }}
    />
  );
}

/** Values longer than this can be cut off by the row, so they get a tooltip with the full text. */
const TIP_FROM = 18;

/**
 * The tooltip for a folder's count badge, or null when the value is not a bare count.
 *
 * **Only a pure number needs explaining.** `"2 parts"` already says what it counts, and a size or a
 * state is its own words; a lone `3` does not, which is the case the guide names. Returning null for
 * everything else keeps the row free of tooltips that repeat the text under the pointer.
 */
function countLabel(node: NodeSummary, value: string): string | null {
  if (!/^\d+$/.test(value)) return null;
  if (!node.children_count) return null;
  return countWords(Number(value));
}

/** Where a node menu comes from: its button (anchored under it) or a point (right-click, keyboard). */
export type MenuOrigin = HTMLElement | { x: number; y: number };

export interface RowStyleControlsProps {
  /** The node's name: the labels and the tooltips say it. */
  name: string;
  /** The colour the swatch fills with, or null for the faint placeholder. */
  color: string | null;
  /** False while the node is hidden: the eye then stays on show, in faint ink. */
  visible: boolean;
  /** The opacity in 0..1, as the popover shows it. */
  opacity: number;
  /** False for a group of parts: the eye alone, without the swatch or the popover (ADR-0021 4.3). */
  styled?: boolean;
  /** Hides or shows the node: one `geometry.style` step, without moving the selection. */
  onToggle: () => void;
  /** True when the body is used in the mesh and the solve; undefined for a row that has no such state. */
  simulate?: boolean;
  onToggleSimulate?: () => void;
  /** Writes colour or opacity through `geometry.style`. */
  onStyle: (patch: StylePatch) => void;
}

/**
 * The swatch and the eye of one row (ADR-0021 decision 4.3). Both are children of the row
 * itself, so the swatch lands between the type icon and the name and the eye, pushed right with
 * `order`, lands between the value and the status dot: no wrapper element separates them.
 */
export function RowStyleControls({
  name,
  color,
  visible,
  opacity,
  styled = true,
  onToggle,
  onStyle,
  simulate,
  onToggleSimulate,
}: RowStyleControlsProps) {
  const [open, setOpen] = useState(false);
  const swatch = useRef<HTMLButtonElement>(null);
  return (
    <>
      {styled && (
        <button
          ref={swatch}
          type="button"
          tabIndex={-1}
          data-style-swatch="true"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={`Colour and opacity of ${name}`}
          className="tree-swatch"
          style={{ background: color ?? "var(--text-faint)" }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setOpen(true)}
        />
      )}
      <Tooltip label={visible ? `Hide ${name}` : `Show ${name}`} side="top" describe={false}>
        <button
          type="button"
          tabIndex={-1}
          aria-label={visible ? `Hide ${name}` : `Show ${name}`}
          data-hidden={!visible}
          data-open={open}
          className="tree-eye order-1"
          onMouseDown={(e) => e.preventDefault()}
          onClick={(e) => {
            e.stopPropagation(); // hiding a node is not a selection
            onToggle();
          }}
        >
          {visible ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      </Tooltip>
      {/*
        **In or out of the simulation, beside the eye** (the workflow-v2 plan §2, T-221). The two sit
        together because they are the two ways a body can be "not there", and they are different: the
        eye is display-only and **the mesher never reads it**, while this decides what a mesh and a
        solve use. The words are the plan's own - "In simulation" / "Left out of simulation" - and the
        icons are lucide's `Zap`/`ZapOff`, which is our own vocabulary rather than another vendor's.
      */}
      {simulate !== undefined && (
        <Tooltip
          label={simulate ? `Left out of simulation: use ${name}` : `In simulation: leave ${name} out`}
          side="top"
          describe={false}
        >
          <button
            type="button"
            tabIndex={-1}
            aria-label={simulate ? `${name} is in the simulation` : `${name} is left out of the simulation`}
            aria-pressed={!simulate}
            data-simulate={simulate ? "in" : "out"}
            className={`tree-simulate order-1 ${simulate ? "" : "out"}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => {
              // **Not a selection**: this is a switch on the row, like the eye.
              e.stopPropagation();
              onToggleSimulate?.();
            }}
          >
            {simulate ? <Zap size={14} /> : <ZapOff size={14} />}
          </button>
        </Tooltip>
      )}
      {styled && <StylePopover open={open} anchor={swatch} name={name} color={color} opacity={opacity} onClose={() => setOpen(false)} onStyle={onStyle} />}
    </>
  );
}

export interface RowProps {
  model: RowModel;
  selected: boolean;
  /**
   * The PRIMARY of the selection: the row drag, rename and the Details page follow.
   *
   * **Not the same as `selected`** once several rows are chosen: all of them are `selected`, and
   * exactly one is the primary. The ring marks the primary so a user can see which row the
   * next Rename will act on.
   */
  primary: boolean;
  renaming: boolean;
  menuOpen: boolean;
  /** What the row's swatch and eye show, or null for a row without them (ADR-0021 4.3). */
  controls: RowControls | null;
  /** Place among its siblings and their number, 1-based: with the rows windowed, a screen reader cannot count the ones that are not drawn. */
  posInSet: number;
  setSize: number;
  onSelect: (id: string) => void;
  /** The same, with the click's modifiers, for the multi-selection gestures. */
  onSelectWith: (id: string, gesture: { ctrl?: boolean; shift?: boolean }) => void;
  onToggle: (id: string) => void;
  /** One `geometry.style` write for the node: the eye sends `visible`, the card `color` and `opacity`. */
  onStyle: (id: string, patch: StylePatch) => void;
  /** Hides or shows the node in this SESSION - the same action the row menu's Hide runs. */
  onToggleVisible: (id: string) => void;
  /** Leaves a body out of the simulation, or puts it back (T-221). */
  onToggleSimulate: (node: NodeSummary) => void;
  /** Selects an unselected row before opening its menu; selected sets are kept intact. */
  onContextSelect: (id: string) => void;
  onMenu: (node: NodeSummary, from: MenuOrigin, toggle: boolean) => void;
  onRename: (node: NodeSummary, name: string, refocus: boolean) => Promise<boolean>;
  onCancelRename: (refocus: boolean) => void;
  /** Opens the stage panel the row belongs to (the mesh settings open the Mesh panel); a double click on a row that has one. */
  onOpen?: (node: NodeSummary) => void;
}

const TreeRow = memo(function TreeRow({ model, selected, primary, renaming, menuOpen, controls, posInSet, setSize, onSelect, onSelectWith, onToggle, onStyle, onToggleVisible, onToggleSimulate, onContextSelect, onMenu, onRename, onCancelRename, onOpen }: RowProps) {
  const { node, depth, hasChildren, open } = model;
  const Icon = iconFor(node, depth);
  const value = treeValue(node);
  /**
   * What the row's right-hand value MEANS, when that is not obvious (claude-win2's 0200 item 6).
   *
   * **A bare number explains nothing**: the guide's finding 7, *"a grey number on a folder is
   * `children_count` including virtual rows, so `/scenes` says 4 before any run"*. The badge is right;
   * what was missing is a sentence. A long value gets its own text as the tooltip - it is truncated -
   * and a SHORT one needs none, which is why this is null for most rows rather than a tooltip on all
   * of them.
   */
  const valueLabel = value.length > TIP_FROM ? value : countLabel(node, value);
  /**
   * **The eye and the menu's Hide are one state** (claude-win2's 0200 item 4). The guide's finding
   * 4: the eye called `geometry.style visible` (saved, undoable) while the menu toggled the session
   * filter, so the menu could say "Show" beside an open eye. `hiddenNodes` is the one source, and
   * `isNodeHidden` is the shared rule that reads it.
   */
  const hiddenNodes = useStore((s) => s.hiddenNodes);
  const visibleNow = !isNodeHidden(node.id, hiddenNodes, controls?.visible ?? true);
  /**
   * **No menu button on a row with nothing to offer** (claude-win2's 0200 item 2). The tree guide's
   * finding 2: these rows showed "…" and a right-click menu that looked the row up in the raw tree,
   * failed, and closed itself - so the button promised actions that did not exist.
   */
  const hasActions = rowHasActions(
    isVirtualId(node.id) ? "virtual" : "node",
    // **A virtual row keeps its menu only when it opens something.** A run, a field and the stage
    // folders all do; a "not set yet" placeholder does not, which is the row the guide complained
    // about.
    openTargetOf(node) !== null || onOpen !== undefined,
  );
  const placeholder = isPlaceholder(node);
  // A row with a panel opens it on a double click; a group that has one and has children still folds on the chevron.
  const opens = onOpen !== undefined && openTargetOf(node) !== null;
  return (
    <div
      id={rowDomId(node.id)}
      role="treeitem"
      aria-level={depth + 1}
      aria-setsize={setSize}
      aria-posinset={posInSet}
      aria-selected={selected}
      aria-expanded={hasChildren ? open : undefined}
      /*
        **The full value text is an accessible DESCRIPTION of the row, not a hover-only title** (Codex's
        042012: *"a title-attribute test ... does not establish all screen readers receive it"*). The span
        below keeps the text even where CSS truncates it, so this names it directly - no new tooltip
        system, and nothing that depends on the pointer.
      */
      aria-describedby={value === "" ? undefined : rowValueDomId(node.id)}
      className={`tree-row group ${selected ? "selected" : ""} ${primary ? "group-focus-visible/tree:shadow-[inset_0_0_0_1px_var(--accent)]" : ""} ${menuOpen ? "bg-[var(--hover)]" : ""}`}
      data-menu={menuOpen}
      data-renaming={renaming}
      data-primary={primary || undefined}
      data-placeholder={placeholder || undefined}
      data-row={rowMetaOf(node)?.row}
      style={{ paddingLeft: 8 + depth * 14 }}
      /* **The modifiers travel with the click** (claude-win2's 1400 file): Ctrl toggles, Shift
         extends from the anchor. The row does not interpret them; the tree's selection model does. */
      onClick={(e) => onSelectWith(node.id, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey })}
      onDoubleClick={() => (opens ? onOpen(node) : hasChildren && onToggle(node.id))}
      onContextMenu={(e) => {
        e.preventDefault();
        onContextSelect(node.id);
        onMenu(node, { x: e.clientX, y: e.clientY }, false);
      }}
    >
      <button
        type="button"
        tabIndex={-1}
        aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`}
        className="grid h-4 w-4 shrink-0 place-items-center rounded text-[var(--text-faint)] hover:text-[var(--text)]"
        style={{ visibility: hasChildren ? "visible" : "hidden", transform: open ? "rotate(90deg)" : "none", transition: "transform 120ms" }}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.stopPropagation();
          onToggle(node.id);
        }}
      >
        <ChevronRight size={13} />
      </button>
      <Icon size={14} className={`tree-icon shrink-0 ${depth === 0 ? "top" : ""}`} />
      {!renaming && controls && (
        <RowStyleControls
          name={node.name}
          color={controls.color}
          visible={visibleNow}
          opacity={controls.opacity}
          styled={controls.styled}
          // **The session filter**, the same action the menu's Hide runs - so the two cannot disagree.
          onToggle={() => onToggleVisible(node.id)}
          // **Only a part carries this**: a tag or a boundary is not a body a mesh can
          // leave out, and the toggle is absent rather than present-and-refused.
          simulate={node.type === "part" ? node.params?.simulate !== false : undefined}
          onToggleSimulate={() => onToggleSimulate(node)}
          onStyle={(patch) => onStyle(node.id, patch)}
        />
      )}
      {renaming ? (
        <RenameInput initial={node.name} onCommit={(name, refocus) => onRename(node, name, refocus)} onCancel={onCancelRename} />
      ) : (
        <>
          {/* Names yield space to the fixed icon columns, including in narrow desktop panes. */}
          <span className={`tree-name ${depth === 0 ? "font-medium" : ""}`}>{node.name}</span>
          {/*
            **A truncated value carries its FULL text in `title` as well as the tooltip** (Codex's
            035011: *"ensure full finding accessible via tooltip/accessible description"*). The
            `Tooltip` sets `aria-describedby` only while the bubble is OPEN, so a screen reader that never
            hovers got nothing; `title` is the one place the full string is always available to assistive
            technology and to a browser's own tooltip.
          */}
          {valueLabel === null ? (
            <span
              id={rowValueDomId(node.id)}
              className="tree-value muted"
              title={value || undefined}
            >
              {value}
            </span>
          ) : (
            <Tooltip label={valueLabel} side="top" describe={false}>
              <span
                id={rowValueDomId(node.id)}
                className="tree-value muted"
                title={value}
              >
                {value}
              </span>
            </Tooltip>
          )}
        </>
      )}
      {/* order 2 keeps the dot after the eye (order 1), which sits after the value. */}
      <span className="tree-dot">
        <StatusDot status={node.status} />
      </span>
      {!renaming && hasActions && (
        <Tooltip label="More actions" side="top" describe={false}>
          <button
            type="button"
            tabIndex={-1}
            aria-label={`Actions for ${node.name}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className={`absolute top-1/2 right-1 grid h-[22px] w-[22px] -translate-y-1/2 place-items-center rounded-md border transition-opacity group-hover:opacity-100 motion-reduce:transition-none ${menuOpen ? "opacity-100" : "opacity-0"}`}
            style={{ background: "var(--bg-elev)", borderColor: "var(--panel-border-strong)", color: "var(--text-muted)" }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={(e) => {
              e.stopPropagation();
              onContextSelect(node.id);
              onMenu(node, e.currentTarget, true);
            }}
          >
            <Ellipsis size={14} />
          </button>
        </Tooltip>
      )}
    </div>
  );
});

export default TreeRow;
