/**
 * The colour-map picker: a button that shows the map as it is drawn (a gradient strip and its name), opening a
 * list in which every map is a strip too - a name does not say what a map looks like (ParaView's and EnSight's
 * palette choosers show the colours). Diverging maps are marked, since they centre on zero; Reverse reads the
 * map from the other end. The settings are the legend's (`legendModel`), written by the caller.
 */
import { useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Popover } from "../components/Menu";
import { Switch } from "../components/ui";
import { COLORMAPS, COLORMAP_IDS, gradientCssFor, type ColormapId } from "../state/colormap";
import "./fieldPicker.css";

export default function ColormapPicker({ map, reverse, disabled = false, onMap, onReverse, ws }: { map: ColormapId; reverse: boolean; disabled?: boolean; onMap: (map: ColormapId) => void; onReverse: (reverse: boolean) => void; ws: string }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={button}
        type="button"
        className="field-picker-button colormap-button no-drag"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Colour map: ${COLORMAPS[map].label}${reverse ? ", reversed" : ""}`}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        data-ws={ws}
      >
        <span className="colormap-strip" style={{ background: gradientCssFor(map, reverse, "to right") }} aria-hidden="true" />
        <span className="field-picker-title">{COLORMAPS[map].label}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      <Popover open={open} anchor={button} onClose={() => setOpen(false)} role="dialog" label="Choose a colour map" className="field-picker colormap-picker">
        <div className="field-picker-list" role="listbox" aria-label="Colour maps">
          {COLORMAP_IDS.map((id) => (
            <button
              key={id}
              type="button"
              role="option"
              aria-selected={id === map}
              className="field-picker-row colormap-row"
              data-name={id}
              onClick={() => {
                onMap(id);
                setOpen(false);
              }}
            >
              <span className="colormap-strip" style={{ background: gradientCssFor(id, reverse, "to right") }} aria-hidden="true" />
              <span className="field-picker-label">{COLORMAPS[id].label}</span>
              {COLORMAPS[id].diverging && <span className="field-picker-units">centred on 0</span>}
            </button>
          ))}
        </div>
        <div className="colormap-reverse">
          <Switch checked={reverse} label="Reverse" description="Read the map from the other end" onChange={onReverse} />
        </div>
      </Popover>
    </>
  );
}
