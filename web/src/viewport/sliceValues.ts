/**
 * The values a slice is coloured from, in ONE place.
 *
 * **Why this module exists** (claude-win2's 0300 answer): two controllers paint slices and they
 * coexisted with two copies of "fetch the field, reduce it to one number per vertex". `FieldsController`
 * paints the run's surfaces, including the **mid-plane** slice target; `SliceLayer` owns the slices a
 * user adds on an X, Y or Z plane, with their own fills, lines and **arrows**. Every change made to one
 * of them left the other's tests red, three rounds running.
 *
 * **The one rule both must follow**: a slice showing SPEED asks the server for the VELOCITY.
 *
 * * `U_mag` and `U` are the same motion, and the header ranges `U` over its MAGNITUDE
 *   (`agent/stokes_agent/extract.py` takes `norm` for `U`), so `|U|` coloured from `U` lands on exactly
 *   the range the legend claims;
 * * it is what gives the arrows their directions **from the same request** - measured on the duct's
 *   slice, `U` is 33 132 bytes against `U_mag`'s 11 044 for 2761 points, and a second request would be
 *   the same bytes twice;
 * * and **only `U_mag` is substituted**: any other field is requested exactly as named, because "do not
 *   fetch what nobody asked for" still holds.
 */
import { parseField, vertexValues, type FieldValues } from "../state/fieldCodec";
import { componentOf, derivedComponentOf } from "../panels/fieldsModel";
import { sliceRequestedFieldOf } from "../panels/sliceModel";
import { SLICE_TARGET } from "../panels/resultsApi";
import type { Range } from "../panels/fieldsModel";

/** A slice's values, ready to colour: one number per vertex, whatever the server sent. */
export interface SliceValues {
  /** The field the SERVER was asked for - `U` when the user chose `|U|`. */
  requested: string;
  /** The values with one component per vertex, for `patchTint`, contour levels and the band surface. */
  field: FieldValues;
  /** The magnitudes alone, for a caller that wants the array without the header. */
  vertex: Float32Array;
}

/**
 * The cache key for a slice's values: **keyed on the REQUESTED name**.
 *
 * Keying on `U_mag` while fetching `U` stores the answer under a name nobody reads, which is a silent
 * miss that looks exactly like a fetch that never happened. Exported so both controllers and their
 * tests use the same string.
 */
export function sliceValuesKey(seq: number, target: string, painted: string): string {
  return `${seq}|${target}|${sliceRequestedFieldOf(painted)}`;
}

/**
 * `bytes` reduced to `SliceValues`, or null when they are not a field this can read.
 *
 * **`components: 1` is set on the reduced field**, and that is not cosmetic: `patchTint` refuses any
 * other value and returns **null silently**, which is what cost three rounds of guessing.
 */
export function toSliceValues(bytes: ArrayBuffer): SliceValues | null {
  let parsed: FieldValues;
  try {
    parsed = parseField(bytes);
  } catch {
    return null;
  }
  const vertex = vertexValues(parsed);
  return {
    requested: parsed.field,
    field: { ...parsed, components: 1, values: vertex },
    vertex,
  };
}

/**
 * A slice's colour range: the run's own, or the caller's locked one.
 *
 * **It comes from `run.fields`, not from the fetched header**, so it does not change when the request
 * names a different-but-equivalent field - `U` and `U_mag` carry the same range, and reading it from
 * the legend's field is what keeps the legend and the cut agreeing.
 */
export function sliceRangeOf(lookup: (field: string) => Range | null, painted: string): Range | null {
  return lookup(painted);
}

/**
 * The length of a slice's value array, for a caller checking it against a cut's vertex count.
 *
 * **A separate function so the failure is loud**: a mismatch means the field is not about this cut, and
 * a caller that indexed anyway would colour the surface with another surface's numbers.
 */
export function vertexCountOf(values: SliceValues | null): number {
  return values === null ? 0 : values.field.count;
}

/**
 * The field a slice ASKS FOR, given the one it paints.
 *
 * **Patches are not substituted**: they have no arrows, and asking a wall for a vector would be fetching
 * what nobody asked for. Only a slice target gets the velocity behind its speed.
 */
export function requestedFieldOf(target: string, painted: string): string {
  if (target !== SLICE_TARGET) return painted;
  // **A derived component comes from the vector too**: `Ux` is one number OF `U`, so the request
  // names `U` and the browser takes the axis it needs - one request for all four views.
  if (derivedComponentOf(painted) !== null) return "U";
  return sliceRequestedFieldOf(painted);
}

/** The cache key for a request: the shared one, keyed on what was actually asked for. */
export function requestKeyOf(seq: number, target: string, painted: string): string {
  return `${seq}|${target}|${requestedFieldOf(target, painted)}`;
}

/**
 * `values` as a field `patchTint` will accept: the same object for one component, its magnitudes for
 * three.
 *
 * **`components: 1` must be SET on the reduced field.** `patchTint` refuses anything else, and it used
 * to do so in silence - which is precisely the three rounds this helper exists to end.
 */
export function reducedForTint(values: FieldValues | null, painted?: string): FieldValues | null {
  if (values === null || values.components === 1) return values;
  /*
    **A named component takes THAT axis; anything else takes the magnitude.** `Ux` is signed — negative
    where the flow runs backwards — and reducing it to `|U|` would draw a recirculation as if it went
    forwards, which is the one thing a component view exists to show.
  */
  const axis = painted === undefined ? null : derivedComponentOf(painted);
  if (axis !== null) return { ...values, components: 1, values: componentOf(values, axis) };
  return { ...values, components: 1, values: vertexValues(values) };
}
