/**
 * The Range row's **Fit**: the colour map spread over the surface on screen, not the whole run (Discovery fits the
 * legend to what is shown; Fluent's contours have "Global Range" off). The server answers the area-weighted
 * robust range (`results.range`, 5 % of the area past each end), so the thin no-slip band along a cut's edge does
 * not squeeze the core flow into one colour; the range lands in Manual, where its ends can be typed further.
 *
 * What is "on screen": the slice when one is shown (its plane is sent), else every boundary.
 */
import { NO_FIELD, setView } from "../panels/fieldsModel";
import { setManualEnd } from "../panels/legendModel";
import { visibleSlices } from "../panels/sliceModel";
import { planeQuery } from "../state/slicePlane";
import { useStore } from "../state/store";
import { fitSurfaces, type SurfacesReply } from "./analyseModel";

/** The share of the area left out at each end of a fitted range. */
export const FIT_SHARE = 0.05;

/** Fit the range to what is shown; the reason in words when it cannot. */
export async function fitRange(): Promise<string | null> {
  const s = useStore.getState();
  const client = s.client;
  const run = s.results.data?.run ?? null;
  const field = s.results.view.field;
  if (client === null || run === null || field === NO_FIELD) return "Pick a field first";
  const listed = visibleSlices(s.slices)[0];
  const mid = s.results.view.slice && s.results.data?.slice === true;
  let patches: string[] = [];
  if (listed === undefined && !mid) {
    const info = await client.call<SurfacesReply>("results.surfaces", { run });
    patches = info.surfaces.filter((x) => x.kind === "patch").map((x) => x.id);
  }
  const ask = fitSurfaces(listed !== undefined || mid, listed === undefined ? null : planeQuery(listed.plane), patches);
  if (ask === null) return "Nothing on screen to fit to";
  const out = await client.call<{ low: number; high: number }>("results.range", { run, field, share: FIT_SHARE, ...ask });
  if (!(out.high > out.low)) return "The field is the same everywhere on screen";
  useStore.setState((st) => {
    const withMin = setManualEnd(st.results.view, st.results.data, "min", out.low);
    const both = setManualEnd(withMin, st.results.data, "max", out.high);
    return { results: setView(st.results, both) };
  });
  return null;
}
