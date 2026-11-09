/**
 * The style card of a tree row (ADR-0021 decision 4.3): the colour and the opacity of one part or
 * tag, written through `geometry.style`. It hangs under the row's swatch and keeps the Menu's surface
 * and radius (see `Popover`).
 *
 * **The colour is a palette of circles, one click each.** It used to be a `#rrggbb` text field beside
 * a native picker, which asked the user to know a hex code to paint a face — and let the project hold
 * colours outside the set the tag commands assign from in turn. The swatches come from
 * `palette.list`, so the client never keeps a copy that could drift. A **Custom…** circle keeps the
 * way to any colour: the command accepts any `#rrggbb` (`geometry._style_color`), and the escape is
 * one click away rather than the only route.
 *
 * Writes follow how the controls are used: a swatch click writes at once (there is nothing to
 * debounce — one click is one decision), a typed custom colour once the typing has paused (150 ms),
 * and an opacity once per gesture — the slider on `change`, the field on blur or Enter — so a drag is
 * one undo step and not one per pixel.
 */

import { useEffect, useRef, useState, type RefObject } from "react";
import { Popover, type CloseReason } from "../components/Menu";
import { useStore } from "../state/store";

/** What one write changes: the values `geometry.style` takes, any of them missing. */
export interface StylePatch {
  visible?: boolean;
  color?: string;
  opacity?: number;
}

const HEX = /^#[0-9a-f]{6}$/i;
/** How long the colour fields wait after the last change before they write, in ms. */
const COLOUR_DEBOUNCE = 150;

interface StylePopoverProps {
  open: boolean;
  /** The row's swatch: the card hangs under it and takes the focus back on Escape. */
  anchor: RefObject<HTMLElement | null>;
  name: string;
  /** The colour as `#rrggbb`, or null when the node names none. */
  color: string | null;
  /** The opacity in 0..1. */
  opacity: number;
  onClose: (reason: CloseReason) => void;
  /** Writes colour or opacity for the node. */
  onStyle: (patch: StylePatch) => void;
}

function percent(value: number): number {
  return Math.round(Math.min(1, Math.max(0, value)) * 100);
}

export function StylePopover({ open, anchor, name, color, opacity, onClose, onStyle }: StylePopoverProps) {
  const [text, setText] = useState(color ?? "");
  const [pct, setPct] = useState(() => percent(opacity));
  const [pctText, setPctText] = useState(() => String(percent(opacity)));
  const colourTyping = useRef(false); // the colour field holds text the store has not seen
  const pctTyping = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const slider = useRef<HTMLInputElement>(null);
  /** The server's palette, so a swatch is never a colour the client invented. */
  const palette = useStore((s) => s.palette);
  /**
   * Whether the custom field is shown. It opens by itself when the node's colour is **not** one of
   * the palette's — a colour that came from a file or an earlier version has to stay visible and
   * editable rather than looking like no colour at all.
   */
  const [custom, setCustom] = useState(false);
  /**
   * The custom field opens itself when the colour is not one of the palette's, and **follows the
   * palette** rather than deciding once.
   *
   * The first version computed this in the `useState` initialiser, which runs on the first render
   * only. The palette arrives from the server (`useStore(s => s.palette)`), so on a popover opened
   * before it lands the initial palette is EMPTY — every colour was "not in the palette", so the
   * custom field opened for plain white or black and stayed open after the real palette arrived. A
   * colour that came from a file or an earlier version still has to stay visible and editable, which
   * is the behaviour this keeps.
   */
  useEffect(() => {
    if (palette.length === 0) return; // nothing to compare against yet: leave the user's choice alone
    setCustom(color !== null && !palette.includes(color.toLowerCase()));
  }, [color, palette]);

  // What the store now says replaces what the fields show — unless the user is typing in them.
  useEffect(() => {
    if (!colourTyping.current) setText(color ?? "");
  }, [color]);
  useEffect(() => {
    if (pctTyping.current) return;
    const next = percent(opacity);
    setPct(next);
    setPctText(String(next));
  }, [opacity]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  // The slider writes once the gesture ends: `change` fires on release, `input` during the drag.
  useEffect(() => {
    const el = slider.current;
    if (!el || !open) return;
    const change = () => {
      const next = Math.round(Number(el.value)); // the range runs 0..100, already a percentage
      setPct(next);
      setPctText(String(next));
      onStyle({ opacity: next / 100 });
    };
    el.addEventListener("change", change);
    return () => el.removeEventListener("change", change);
  }, [open, onStyle]);

  const writeColour = (value: string) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (HEX.test(value)) onStyle({ color: value.toLowerCase() });
    }, COLOUR_DEBOUNCE);
  };
  const typeColour = (value: string) => {
    colourTyping.current = true;
    setText(value);
    writeColour(value);
  };
  const writePct = (next: number) => {
    setPct(next);
    setPctText(String(next));
    onStyle({ opacity: next / 100 });
  };
  const commitPct = () => {
    const parsed = Number(pctText.trim());
    const next = Number.isFinite(parsed) && pctText.trim() !== "" ? Math.min(100, Math.max(0, Math.round(parsed))) : pct;
    pctTyping.current = false;
    if (next !== pct) writePct(next);
    else {
      setPct(next);
      setPctText(String(next));
    }
  };

  return (
    <Popover
      open={open}
      anchor={anchor}
      onClose={onClose}
      role="dialog"
      label={`Colour and opacity of ${name}`}
      className="style-popover"
      // The FIRST swatch, not the chosen one: nothing carries `data-style-colour` until a colour is
      // chosen, so a popover opened on a colourless node focused nothing at all.
      focusSelector=".sp-swatch"
    >
      <div className="sp-row sp-colours" role="group" aria-label={`Colour of ${name}`}>
        {palette.map((entry) => {
          const chosen = color !== null && color.toLowerCase() === entry;
          return (
            <button
              key={entry}
              type="button"
              className="sp-swatch"
              style={{ background: entry }}
              // The label names the colour's role for a screen reader, which cannot see the swatch.
              aria-label={`Use ${entry} for ${name}`}
              aria-pressed={chosen}
              title={entry}
              data-style-colour={chosen ? true : undefined}
              onClick={() => {
                colourTyping.current = false;
                setText(entry);
                // One click is one decision, so this writes at once rather than through the debounce
                // the typed field needs.
                window.clearTimeout(timer.current);
                onStyle({ color: entry });
              }}
            >
              {chosen ? "✓" : ""}
            </button>
          );
        })}
        <button
          type="button"
          className="sp-swatch sp-swatch-custom"
          style={{ background: custom && color !== null ? color : "transparent" }}
          aria-label={`Pick any colour for ${name}`}
          aria-pressed={custom}
          title="Custom…"
          onClick={() => setCustom((was) => !was)}
        >
          {custom && color !== null ? "✓" : "+"}
        </button>
      </div>
      {custom && (
        <div className="sp-row">
          <label
            className="swatch"
            title={`Pick a colour for ${name}`}
            style={{ background: color ?? "transparent" }}
          >
            <input
              type="color"
              className="sr-only"
              value={color ?? "#000000"}
              aria-label={`Pick a colour for ${name}`}
              onChange={(e) => typeColour(e.target.value)}
            />
          </label>
          <input
            data-style-colour
            className="field mono sp-colour"
            value={text}
            placeholder="#rrggbb"
            aria-label={`Colour of ${name}`}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => typeColour(e.target.value)}
            onBlur={() => {
              colourTyping.current = false;
              // A colour the command would refuse is not what the node holds: show what it holds.
              if (!HEX.test(text)) setText(color ?? "");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                colourTyping.current = false;
                if (!HEX.test(text)) setText(color ?? "");
              }
            }}
          />
        </div>
      )}
      <div className="sp-row">
        <span className="sp-label">Opacity</span>
        <input
          ref={slider}
          type="range"
          className="slider"
          min={0}
          max={100}
          step={1}
          value={pct}
          aria-label={`Opacity of ${name}`}
          onChange={(e) => {
            const next = percent(Number(e.target.value));
            setPct(next);
            setPctText(String(next));
          }}
        />
        <span className="sp-pct">
          <input
            className="field mono"
            inputMode="numeric"
            value={pctText}
            aria-label={`Opacity of ${name} in percent`}
            onFocus={() => {
              pctTyping.current = true;
            }}
            onChange={(e) => {
              pctTyping.current = true;
              setPctText(e.target.value);
            }}
            onBlur={commitPct}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitPct();
              }
            }}
          />
          <span aria-hidden="true">%</span>
        </span>
      </div>
      <div className="sp-row sp-foot">
        <button type="button" className="btn ghost" onClick={() => writePct(100)}>
          Opaque
        </button>
      </div>
    </Popover>
  );
}

export default StylePopover;
