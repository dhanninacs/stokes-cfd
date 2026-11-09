/**
 * How many field and cut requests the viewport's layers have on the way (R4 review): `viewport/fields.ts` (the walls'
 * field values) and `viewport/sliceLayer.ts` (the slices' cuts and values) each keep a count of their own, and publish it
 * here, so a caller outside the viewport can tell when the picture has its colours. "Export all" is that caller
 * (`panels/viewsModel.ts` `viewSettled`): a cut already cached says `ready` at once, and only these counts show that the
 * values which colour it are still coming.
 *
 * View state, no journal record. Import rule: no React, no store, so this loads in Node.
 */

/** The requests on the way, by layer. */
export interface LoadingCounts {
  /** The walls' field values (`FieldsController`). */
  fields: number;
  /** The slices' cuts (`SliceLayer`). */
  cuts: number;
  /** The slices' field values (`SliceLayer`). */
  sliceFields: number;
}

export const NO_LOADING: LoadingCounts = { fields: 0, cuts: 0, sliceFields: 0 };

/** True while any request is on the way. */
export function anyLoading(loading: LoadingCounts): boolean {
  return loading.fields > 0 || loading.cuts > 0 || loading.sliceFields > 0;
}

/** `current` with `patch` written over it, or null when nothing would change (so the store is not written, and nothing wakes). */
export function patchLoading(current: LoadingCounts | undefined, patch: Partial<LoadingCounts>): LoadingCounts | null {
  const base = current ?? NO_LOADING;
  if ((Object.keys(patch) as (keyof LoadingCounts)[]).every((key) => base[key] === patch[key])) return null;
  return { ...base, ...patch };
}
