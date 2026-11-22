/**
 * The hover readout (results): a small tag beside the pointer with the shown field's value at the point of the
 * model under it - "|U| 0.312 m/s" - continuously, with no mode to turn on (EnSight's interactive probe,
 * ParaView's hover tooltip). It reads the values the surfaces are already coloured with
 * (`FieldsController.valueAt`), so it asks the server nothing and agrees with the colours by construction.
 *
 * Shown only in the Results stage while a field colours the model, and not while Probe, Line or Measure is
 * picking (their own feedback is what matters then). It takes no pointer.
 */
import { useEffect, useState, type RefObject } from "react";
import { legendOf } from "../panels/legendModel";
import { fieldUnits, resultsShown } from "../panels/fieldsModel";
import { isLinePicking } from "../panels/lineModel";
import { useVariables } from "../panels/variablesControl";
import { prettyUnits, variableOf } from "../panels/variablesModel";
import { useStore } from "../state/store";
import type { FieldsController } from "./fields";
import { readoutText, type Readout } from "./readoutModel";
import type { SceneManager } from "./scene";

export default function HoverReadout({ scene, fields, host }: { scene: SceneManager | null; fields: RefObject<FieldsController | null>; host: RefObject<HTMLDivElement | null> }) {
  const [readout, setReadout] = useState<Readout | null>(null);

  useEffect(() => {
    if (scene === null) return;
    scene.onSurfaceHover = (sample) => {
      const s = useStore.getState();
      const busy = s.probePick || isLinePicking(s.line.phase) || s.measureMode;
      if (sample === null || busy || !resultsShown(s.stage, s.results.view, s.results.data !== null)) {
        setReadout(null);
        return;
      }
      const value = fields.current?.valueAt(sample) ?? null;
      const rect = host.current?.getBoundingClientRect();
      if (value === null || rect === undefined) {
        setReadout(null);
        return;
      }
      const field = s.results.view.field;
      const variable = variableOf(useVariables.getState().reply?.variables ?? null, field);
      const symbol = variable?.symbol || field;
      const look = legendOf(s.results.view);
      const text = readoutText(symbol, value, prettyUnits(fieldUnits(s.results.data, field)), look.format, look.digits);
      setReadout({ text, x: sample.client.x - rect.left, y: sample.client.y - rect.top });
    };
    return () => {
      scene.onSurfaceHover = null;
    };
  }, [scene, fields, host]);

  if (readout === null) return null;
  return (
    <div className="hover-readout glass mono" style={{ left: readout.x + 14, top: readout.y + 14 }} role="status" aria-live="off" data-ws="hover-readout">
      {readout.text}
    </div>
  );
}
