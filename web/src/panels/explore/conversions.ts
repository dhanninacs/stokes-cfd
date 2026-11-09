/**
 * Fetching the SI-to-display conversions the Explore table needs from `units.convert`.
 *
 * `format.ts` used to carry a hand-written factor table. The server's unit registry is the source of
 * truth, so each pair is fetched once and cached in `format.ts`; the table is built from the cache.
 * A pair that has not arrived yet keeps its SI value, which is the first-paint path T-007 allows.
 */
import type { StokesClient } from "../../api/client";
import { conversionKey, hasConversion, rememberConversion } from "./format";

export interface UnitPair {
  si: string;
  display: string;
}

/** The parts of `units.convert`'s reply this module uses. */
interface Conversion {
  factor: number;
  offset: number;
}

/**
 * Fetches every pair not already cached, in parallel, and stores it. A pair the server cannot convert
 * (an unknown unit) is left uncached, so the table keeps showing SI rather than guessing a number.
 */
export async function loadConversions(
  client: StokesClient,
  pairs: Iterable<UnitPair>,
): Promise<void> {
  const wanted = new Map<string, UnitPair>();
  for (const pair of pairs) {
    if (pair.si === "" || hasConversion(pair.si, pair.display)) continue;
    wanted.set(conversionKey(pair.si, pair.display), pair);
  }
  if (wanted.size === 0) return;
  await Promise.all(
    [...wanted.values()].map(async (pair) => {
      try {
        const answer = await client.call<Conversion>("units.convert", {
          value: 1,
          from_unit: pair.si,
          to_unit: pair.display,
        });
        if (Number.isFinite(answer.factor) && Number.isFinite(answer.offset)) {
          rememberConversion(pair.si, pair.display, answer.factor, answer.offset);
        }
      } catch {
        // Not an error here: the column stays in SI, as it does for a pair the table never had.
      }
    }),
  );
}
