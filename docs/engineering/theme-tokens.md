# Theme tokens

**Public contract, version 1** (`stokes.workspace.tokens.TOKEN_CONTRACT_VERSION`) ·
ADR-0015 §6 · enforced by `tests/test_workspace_tokens.py`

A theme is a set of CSS custom properties, called tokens. The built-in themes
define them in the stylesheets. A custom theme in the workspace (the `theme`
section, see [workspace-schema.md](workspace-schema.md)) is a built-in base plus
overrides of any token listed here. The names are a public, versioned contract:
the components, the 3D viewport, the residual chart, theme files that users
share and the assistant all depend on them.

## Where the built-in values live

| Block | File | Applies to |
|---|---|---|
| `:root` | `web/src/styles.css` | Every theme. These are the dark values (the reference look) and the fallback for any token that a theme block does not set. |
| `:root[data-theme="light"]` | `web/src/styles.css` | `light` |
| `:root[data-theme="cupertino"]` | `web/src/themes/cupertino.css` | `cupertino` (the light appearance) |
| `:root[data-theme="cupertino-dark"]` | `web/src/themes/cupertino.css` | `cupertino-dark` |
| `:root[data-theme^="cupertino"]` | `web/src/themes/cupertino.css` | both Cupertino appearances |
| `:root` | `web/src/panels/panels.css` | every theme (chart series) |

The "Set in" column below names the blocks that set each token. A token that
only `:root` sets has the same value in every theme until a theme block or a
custom theme overrides it. The viewport tokens `--vp-grid-minor`,
`--vp-grid-major`, `--vp-floor`, `--vp-shadow` and `--vp-edge` are optional:
dark and light leave them unset, and the viewport then uses its own defaults
(`web/src/viewport/theme.ts`).

Custom properties that a component rule declares for itself, such as
`--pop-from` on `.popover` and `--toast-accent` on `.toast`, are private to that
component. They are not tokens and a theme cannot set them.

## Applying a theme in the front end

1. Set `data-theme` on `<html>` to the theme's `name`, or to the `base` of a
   custom theme. `auto` and `cupertino-auto` resolve to the dark or light
   appearance of their family from the operating system, as `?theme=` does
   today.
2. For a custom theme, call
   `document.documentElement.style.setProperty(token, value)` for each entry of
   `tokens`. Inline properties on the root element win over every stylesheet
   block, so the overrides apply on top of the base.
3. When the theme changes, remove the previous overrides with
   `style.removeProperty` first. Code that reads tokens in script (the viewport
   palette, the residual chart) must read them again after the change, as it
   already does when `data-theme` changes. A theme change must not rebuild the
   scene (ADR-0015 §9).

## The tokens

Kinds are explained in the next section.

### Surfaces

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--bg` | colour | `:root`, light, cupertino, cupertino-dark | Window background behind the viewport and every panel. |
| `--bg-elev` | colour | `:root`, light, cupertino, cupertino-dark | Raised fills inside panels: fields, key caps, value chips. |
| `--panel` | colour | `:root`, light, cupertino, cupertino-dark | Translucent fill of floating glass panels and islands. |
| `--panel-solid` | colour | `:root`, light, cupertino, cupertino-dark | Opaque panel fill where translucency would hurt legibility (output drawer). |
| `--panel-border` | colour | `:root`, light, cupertino, cupertino-dark | Hairline borders and dividers. |
| `--panel-border-strong` | colour | `:root`, light, cupertino, cupertino-dark | Borders that must read at a glance: menus, popovers, swatches. |
| `--track` | colour | `:root`, light, cupertino, cupertino-dark | Unfilled track of segmented controls and switches; quiet fills. |
| `--hover` | colour | `:root`, light, cupertino, cupertino-dark | Fill of a row or button under the pointer. |
| `--active` | colour | `:root`, light, cupertino, cupertino-dark | Fill of a row or button while pressed or switched on. |
| `--menu-bg` | colour | `:root`, light, cupertino, cupertino-dark | Menus, popovers and the console's completion list. |
| `--tooltip-bg` | colour | `:root`, light, cupertino, cupertino-dark | Tooltip background. |
| `--tooltip-border` | colour | `:root`, light, cupertino, cupertino-dark | Tooltip border. |
| `--tooltip-kbd-border` | colour | `:root`, cupertino | Outline of a key cap inside a tooltip. |
| `--scrim` | colour | `:root`, light, cupertino, cupertino-dark | Dimming layer behind modal dialogs and the command palette. |
| `--scroll-thumb` | colour | `:root`, light, cupertino, cupertino-dark | Scrollbar thumb. |
| `--scroll-thumb-hover` | colour | `:root`, light, cupertino, cupertino-dark | Scrollbar thumb under the pointer. |

### Text

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--text` | colour | `:root`, light, cupertino, cupertino-dark | Primary text and icons. |
| `--text-muted` | colour | `:root`, light, cupertino, cupertino-dark | Secondary text: labels, values, descriptions, chart axes. |
| `--text-faint` | colour | `:root`, light, cupertino, cupertino-dark | Tertiary text: placeholders, hints, disabled controls. |
| `--tooltip-text` | colour | `:root`, light, cupertino, cupertino-dark | Text in tooltips. |
| `--logo-ink` | colour | `:root`, light, cupertino, cupertino-dark | Strokes of the logo mark on its tile. |

### Accent and status

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--accent` | hex | `:root`, light, cupertino, cupertino-dark | The one accent: selection, active stage, primary button, focus ring, 3D selection tint. |
| `--accent-strong` | colour | `:root`, light, cupertino, cupertino-dark | Accent under the pointer; first stop of accent gradients. |
| `--accent-soft` | colour | `:root`, light, cupertino, cupertino-dark | Translucent accent: selected rows, text selection, focus halo. |
| `--accent-text` | colour | `:root`, light, cupertino, cupertino-dark | Text and icons on an accent fill. |
| `--accent-ink` | colour | `:root`, light, cupertino, cupertino-dark | The accent as text on a panel. |
| `--accent-fill` | colour | `:root`, light, cupertino, cupertino-dark | The accent as a solid fill that carries text. |
| `--accent-glow` | colour | `:root`, light, cupertino, cupertino-dark | Soft glow under the primary button. |
| `--blue` | colour | `:root`, light, cupertino, cupertino-dark | Information: outlet-type data, info toasts. |
| `--warn` | colour | `:root`, light, cupertino, cupertino-dark | Stale status (amber). |
| `--danger` | colour | `:root`, light, cupertino, cupertino-dark | Errors, destructive actions, inlet-type data. |
| `--ok` | hex | `:root`, light, cupertino, cupertino-dark | Success and up-to-date status; the view helper's Y axis. |
| `--blue-soft` | colour | `:root`, light, cupertino, cupertino-dark | Translucent background behind --blue. |
| `--warn-soft` | colour | `:root`, light, cupertino, cupertino-dark | Translucent background behind --warn. |
| `--danger-soft` | colour | `:root`, light, cupertino, cupertino-dark | Translucent background behind --danger: error cards and lines. |
| `--ok-soft` | colour | `:root`, light, cupertino, cupertino-dark | Translucent background behind --ok. |

### Charts

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--series-1` | colour | `:root` (panels.css) | First residual series (defaults to --accent). |
| `--series-2` | colour | cupertino, cupertino-dark, `:root` (panels.css) | Second residual series (defaults to --blue). |
| `--series-3` | colour | `:root` (panels.css) | Third residual series (defaults to --ok). |
| `--series-4` | colour | `:root` (panels.css) | Fourth residual series (defaults to --warn). |

### Shadows and edges

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--shadow` | shadow | `:root`, light, cupertino, cupertino-dark | The elevation shadow of floating panels and islands. |
| `--shadow-pop` | shadow | `:root`, light, cupertino, cupertino-dark | Higher elevation: menus, popovers, dialogs. |
| `--highlight` | colour | `:root`, light, cupertino, cupertino-dark | 1 px lit top edge on glass. |
| `--focus-halo` | shadow | `:root`, cupertino, cupertino-dark | Soft ring outside the 2 px accent focus outline. |

### Viewport

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--vp-top` | colour | `:root`, light, cupertino, cupertino-dark | Top of the viewport background gradient. |
| `--vp-bottom` | colour | `:root`, light, cupertino, cupertino-dark | Bottom of the viewport background gradient. |
| `--vp-grid-minor` | hex | cupertino, cupertino-dark | Minor ground-grid lines (the viewport's own default when unset). |
| `--vp-grid-major` | hex | cupertino, cupertino-dark | Major ground-grid lines (the viewport's own default when unset). |
| `--vp-floor` | hex | cupertino, cupertino-dark | Faint floor disc under the part. |
| `--vp-shadow` | hex | cupertino, cupertino-dark | Contact shadow under the part, at its darkest. |
| `--vp-edge` | hex | cupertino, cupertino-dark | Feature-edge lines in the shaded-with-edges view mode. |

The workspace's `viewport.background` setting ([workspace-schema.md](workspace-schema.md))
replaces the `--vp-top`/`--vp-bottom` gradient with one colour when it is not
`theme`.

### Radii

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--radius` | length | `:root`, cupertino, cupertino-dark | Corner radius of panels, cards and popovers. |
| `--radius-sm` | length | `:root`, cupertino, cupertino-dark | Corner radius of controls: buttons, fields, rows. |

### Fonts

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--font-sans` | font | `:root`, cupertino, cupertino-dark | Interface text. |
| `--font-mono` | font | `:root`, cupertino, cupertino-dark | Identifiers, expressions and the console. |

### Cupertino family only (`--cup-*`)

Only rules under `:root[data-theme^="cupertino"]` read these tokens.

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--cup-seg-selected` | colour | cupertino, cupertino-dark | Raised selected segment of segmented controls and stage chips. |
| `--cup-seg-shadow` | shadow | cupertino, cupertino-dark | Shadow under the selected segment. |
| `--cup-chip-text` | colour | cupertino, cupertino-dark | Text of an idle segment (darker than --text-muted on the track). |
| `--cup-chip-hover` | colour | cupertino, cupertino-dark | Idle segment under the pointer. |
| `--cup-field-bg` | colour | cupertino, cupertino-dark | Text field background. |
| `--cup-field-border` | colour | cupertino, cupertino-dark | Text field border. |
| `--cup-kbd-bg` | colour | cupertino, cupertino-dark | Key cap background. |
| `--cup-kbd-border` | colour | cupertino, cupertino-dark | Key cap border. |
| `--cup-logo-top` | colour | cupertino, cupertino-dark | Top of the logo tile gradient. |
| `--cup-logo-bottom` | colour | cupertino, cupertino-dark | Bottom of the logo tile gradient. |
| `--cup-select-idle` | colour | cupertino, cupertino-dark | Selected tree row while the tree is not focused. |
| `--cup-dot-ok` | colour | cupertino, cupertino-dark | Status dot of a node that is fine (it says nothing). |
| `--cup-light-border` | colour | cupertino, cupertino-dark | Outline of the traffic-light window controls. |
| `--cup-light-idle` | colour | cupertino, cupertino-dark | Traffic lights of a window that is not in front. |
| `--cup-light-close` | colour | both Cupertino | Close traffic light. |
| `--cup-light-min` | colour | both Cupertino | Minimise traffic light. |
| `--cup-light-zoom` | colour | both Cupertino | Zoom traffic light. |
| `--cup-glyph-close` | colour | both Cupertino | Glyph on the close light under the pointer. |
| `--cup-glyph-min` | colour | both Cupertino | Glyph on the minimise light under the pointer. |
| `--cup-glyph-zoom` | colour | both Cupertino | Glyph on the zoom light under the pointer. |
| `--cup-on-accent-soft` | colour | both Cupertino | Secondary text and icons on an accent fill. |
| `--cup-on-accent-line` | colour | both Cupertino | Outline of a key cap on an accent fill. |

### Written by the app (read-only)

| Token | Kind | Set in | Meaning |
|---|---|---|---|
| `--tree-width` | read-only | `:root` | Effective width of the left panel, written by the app on the main element. |
| `--props-width` | read-only | `:root` | Effective width of the right panel, written by the app on the main element. |

These two are outputs of the layout, not styling. They are listed because the
stylesheet declares them, but a theme cannot set them.

The layout writes more outputs on the same `<main>` element, which stylesheets
and chrome that plug-ins add may read with `var()`: `--slot-left`,
`--slot-right` and `--slot-bottom` (the room each slot takes, in px: the strip
when it is folded, nothing when it is empty) and, for each island corner `bl`,
`br`, `tl` and `tr`, `--corner-<corner>-x` and `--corner-<corner>-y` (where the
corner's groups start, in px from its two edges, clear of the side slots). They
are not tokens: no stylesheet block declares them, a theme cannot set them, and
this contract does not version them.

## Kinds: what a theme may set

| Kind | Accepted | Stored as |
|---|---|---|
| colour | `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`; `rgb()`, `rgba()`, `hsl()` and `hsla()` in comma or space syntax with an optional alpha; the CSS named colours, `transparent` and `currentColor`; `var(--token)` of another colour or hex token | as written, trimmed |
| hex | `#rgb` or `#rrggbb` only | lower-case `#rrggbb` |
| shadow | `none`, or one to eight comma-separated shadows. Each shadow is two to four lengths (`0`, or a number with `px`, `rem` or `em`), at most one colour as above, and an optional `inset`. | as written, trimmed |
| length | `0`, or a number with `px` (at most 100) or `rem` / `em` (at most 6) | as written, trimmed |
| font | one to sixteen comma-separated families. Each is a quoted name (no quote or backslash inside, at most 64 characters) or a plain name of letters, digits, spaces and hyphens; one leading hyphen is allowed, for `-apple-system`. | as written, trimmed |
| read-only | nothing | not applicable |

Rules for every value:

- It is a string of 1 to 256 characters after trimming.
- Nothing that loads a resource or ends the declaration can pass: `url()`,
  `image-set()`, `expression()`, `;`, `{`, `}` and quotes outside font names
  fail every kind.
- The tokens that the 3D viewport reads, `--accent`, `--ok`, `--vp-grid-minor`,
  `--vp-grid-major`, `--vp-floor`, `--vp-shadow` and `--vp-edge`, are of kind
  hex, because `web/src/viewport/theme.ts` parses only `#rrggbb`. This keeps the
  selection colour of the 3D view equal to the accent of the chrome in every
  custom theme.
- An unknown token name is invalid. A `--cup-*` token in a theme whose base is
  not `cupertino` or `cupertino-dark` is kept but reported with the warning
  code `workspace.no_effect`, because only the Cupertino rules read it.

When a workspace file breaks a rule, the token is left out (the value of the
base shows) and a warning names it. `workspace.set` and the theme editor get the
error `workspace.invalid` instead, and nothing is written.

## Custom themes as JSON

A built-in theme:

```json
{"theme": {"name": "cupertino-auto"}}
```

A custom theme, as `workspace.export sections=["theme"]` writes it:

```json
{
  "version": 1,
  "theme": {
    "base": "dark",
    "title": "Night shift",
    "tokens": {
      "--accent": "#ff8a3d",
      "--accent-strong": "#ff9d5c",
      "--accent-soft": "rgba(255, 138, 61, 0.16)",
      "--accent-ink": "#ffa266",
      "--accent-fill": "#ff8a3d",
      "--vp-top": "#1d1a17",
      "--vp-bottom": "#0b0908",
      "--radius": "10px",
      "--font-sans": "\"Inter Variable\", ui-sans-serif, system-ui, sans-serif"
    }
  }
}
```

Rules:

- A theme has exactly one of `name` (`dark`, `light`, `cupertino`,
  `cupertino-dark`, `auto`, `cupertino-auto`) and `base` (`dark`, `light`,
  `cupertino`, `cupertino-dark`). With both, `base` wins and `name` is reported.
- `tokens` and `title` (one line, at most 64 characters) belong to a custom
  theme only. `tokens` may be empty or absent; the stored theme then has
  `"tokens": {}`.
- `auto` cannot be a base, because overrides are written for one appearance.
- The theme is one unit. When layers merge, a project's theme replaces the
  user's theme whole, so tokens never leak from one theme into another.

Exporting, importing and editing:

- `workspace.export sections=["theme"]` gives a file like the one above.
- `workspace.import text=<that file>` replaces the theme in the chosen scope
  and nothing else; values that break a rule are left out and reported.
- `workspace.set path=theme value={...}` sets a whole theme.
  `workspace.set path=theme.tokens.--accent value="#ff8a3d"` changes one token
  of a custom theme. A built-in theme (`name`) has no tokens: set a theme with
  a `base` first.

## Changing the contract

- **Adding a token** is compatible. Add it to a stylesheet block, to
  `stokes/workspace/tokens.py` (group, kind, meaning) and to this document;
  the contract version stays. `tests/test_workspace_tokens.py` fails until the
  three agree.
- **Renaming or removing a token, or changing its kind**, breaks custom themes.
  Increase `TOKEN_CONTRACT_VERSION`, add a workspace schema migration
  (`stokes.workspace.schema.MIGRATIONS`) that rewrites custom themes, and add a
  changelog line.
