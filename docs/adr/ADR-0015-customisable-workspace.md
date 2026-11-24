# ADR-0015 · The workspace: every visible arrangement is user data

**Status:** accepted · 2026-10-02 · relies on ADR-0010 (API first), ADR-0011 (engine attachment), ADR-0013 (web front end)

## Context

The owner set a new product target: Stokes must let users shape the
interface itself. Where panels sit, what the top bar carries, how the tree is
organised, the look, the shortcuts, which engine runs by default: "we give you
the tools to make a modern CFD interface that you like to make" is to be the
selling point. Every incumbent ships one fixed arrangement; users who come
from STAR-CCM+, Fluent or SimScale each expect a different one. Because the
front end already holds no model state and every change is an API command
(ADR-0010, ADR-0013), the arrangement of the interface can itself be data
that the UI, the CLI, MCP and the assistant all read and write.

## Decision

1. **A workspace document describes everything the user can arrange.** It
   is JSON with a versioned schema: `theme` (built-in name, or a custom token
   set), `layout` (slots and the panels in them with order, size and
   collapsed state), `islands` (which control groups sit in which corner),
   `topbar` (which items appear and in what order), `tree` (group order,
   hidden groups, the stage-to-group mapping, default expansion),
   `shortcuts` (action id to key chord), `viewport` (default projection,
   view mode, grid, background), `engines` (default engine and run presets).
   The user's workspace lives in `~/.stokes/workspace.json`; a project may
   carry overrides in `<name>.stokes/workspace.json`, beside `project.json`;
   the effective workspace is
   the merge. Unknown keys are kept, invalid values fall back to defaults and
   are reported, never fatal.
2. **The workspace is API state.** `workspace.get`, `workspace.set(path,
   value)`, `workspace.reset(scope)`, `workspace.presets`,
   `workspace.apply_preset(name)`, `workspace.save_preset(name)`,
   `workspace.export` and `workspace.import` are ordinary commands with
   `workspace.changed` events, so the CLI, MCP and the assistant can arrange
   the interface ("make it look like STAR-CCM+", "put the console on the
   right") and journals record it.
3. **Presets ship with the app**: *Stokes* (the default, governed by
   `web/DESIGN.md`), *Classic* (tree left with properties beneath it, output
   on the right, as STAR-CCM+ users expect), *Ribbon* (stage band as a
   ribbon with a task page under it, tree left, as Fluent users expect),
   *Workflow* (one workflow tree and no stage band, as SimScale users
   expect) and *Minimal* (viewport, palette and a floating properties card).
   Users save their own presets and share them as files.
4. **Panels, islands and top-bar items are registry entries**, each with an
   id, title, icon, component, default slot and minimum size. Slots are
   `left`, `right`, `bottom` and `float` (cards over the viewport with a
   position), and the four island corners. A slot holding several panels
   shows them as tabs or as a vertical split, chosen per slot. Engine
   adapters and later plug-ins register panels through the same registry,
   so a coupled engine can bring its own Solve panel.
5. **Rearranging happens in Customise mode** (Display settings → Customise,
   `Ctrl Shift C`, or the palette), which shows handles and drop zones for
   dragging panel headers, island groups and top-bar items between slots and
   corners. Outside that mode nothing moves by accident. Every panel menu
   also offers *Move to…*, and the palette offers the same actions, so
   arrangement is possible by keyboard and by agent. *Reset workspace* and
   the command palette can never be removed.
6. **A theme is a token set**, and custom themes are first-class: a built-in
   family and appearance as the base plus overrides of any token (accent,
   surfaces, text, viewport background and grid, radii, density, fonts).
   Display settings gains a theme editor with live preview; themes export and
   import as JSON. Token names are a public, versioned contract documented in
   `docs/engineering/theme-tokens.md`; the viewport reads tokens, so it
   follows any theme without code.
7. **Shortcuts are part of the workspace.** Every action has an id and
   default keys; a Shortcuts panel edits them, with presets for STAR-CCM+ and
   Fluent conventions; tooltips always show the current chord.
8. **Engine choice is a workspace setting.** The Run control lists the
   engines attached under ADR-0011; the default engine is per project with a
   user-level fallback; engine-specific settings appear through registered
   panels, not through conditionals in the core UI.
9. **The default stays opinionated.** The Stokes preset remains the design
   contract reviewed under `web/DESIGN.md` §4; customisation must preserve
   accessibility (focus order follows visual order, every control keeps an
   accessible name, shortcuts stay discoverable) and performance (a layout
   change settles within one frame; theme changes do not rebuild the scene).
10. **Details is the single default editing surface beside the tree.** Pages
    for Properties, Mesh, Conditions, Solve and Results render in Details for
    the selected node. A saved right dock that repeats those pages is normalized
    when read, while a layout that does not place both Tree and Details in the
    left dock keeps its chosen editor placement. Right remains available for
    optional help and reference panels; output stays in the bottom drawer.

## Alternatives considered

| Alternative | Why not |
|---|---|
| One fixed layout with a few toggles | The owner names flexibility as the selling point; toggles do not let a STAR-CCM+ user recreate their habits. |
| Adopt a docking framework (Dockview, Golden Layout) | Workable and MIT-licensed, but it pulls the look towards an IDE and brings its own persistence model. Slots with tabs or splits cover the CFD layouts in use; a docking library can replace the slot engine later behind the same workspace schema. |
| Themes as CSS files only | Not agent-editable and not validated; a token set in the workspace is data the API can set and check. |
| Per-component settings scattered in localStorage | Not shareable, not scriptable, lost with the browser profile; the workspace file is the one place. |

## Consequences

- New Python package `stokes.workspace` (schema, defaults, presets, merge,
  validation, persistence) and `stokes/api/commands/workspace.py`; the server
  pushes `workspace.changed`.
- New front-end module `web/src/workspace/` (panel registry, slot layout
  engine, Customise mode, theme editor, shortcut editor) replacing the fixed
  `PanelSlot` placement; existing panels become registry entries.
- `web/DESIGN.md` gains a §5 on customisation; `docs/03-ui-design.md` gains
  the preset descriptions; `docs/engineering/theme-tokens.md` is written.
- Roadmap: M0 gains step 9 (workspace v1: presets, panel moves, theme
  editor); M1 gains the shortcut editor and preset sharing; M2 wires the
  engine registry into the Run control.
- Tests: merge and validation of workspace documents, preset round trips,
  layout invariants (every registered panel has exactly one place or is
  hidden), and a screenshot per preset in the review harness.
