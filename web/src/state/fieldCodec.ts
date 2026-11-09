/**
 * Reader of one field of a run on one surface, `GET /api/results/field/{run}/{patch node id | slice}?field=`
 * (T-216). The layout is the server's: a u32 little-endian header length, a JSON header `{"version": 1,
 * "field", "units", "components", "count", "min", "max"}` padded with spaces to a multiple of four, then
 * `f32[components x count]`, one value per vertex of the surface the route names, in the surface's own
 * vertex order.
 *
 * The reply came over a connection, so it is data: the header is checked the way `parseMeshEdges` checks
 * the edge codec's, and `components x count x 4` has to account for exactly the rest of the buffer.
 * Anything else is an error, never a guess. The values are a view of the buffer, not a copy.
 *
 * Import rule: no React, no store, no DOM. This loads in Node.
 */

/** The header is a short JSON document; anything larger is not one of ours. */
const MAX_HEADER_BYTES = 1 << 20;
const FIELD_VERSION = 1;

export interface FieldValues {
  field: string;
  units: string;
  /** 1 for a scalar, 3 for a vector. */
  components: number;
  /** Vertices of the surface. */
  count: number;
  /** Over the values, or over the magnitude for a vector. */
  min: number;
  max: number;
  /** `components x count` floats, vertex by vertex. */
  values: Float32Array;
}

/** Parses a field buffer; throws an `Error` that names the first thing wrong. */
export function parseField(buffer: ArrayBuffer): FieldValues {
  if (buffer.byteLength < 4) throw new Error("field buffer is too short");
  const headerLength = new DataView(buffer).getUint32(0, true);
  if (headerLength > MAX_HEADER_BYTES || 4 + headerLength > buffer.byteLength) throw new Error("field header is truncated");
  let header: unknown;
  try {
    header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)));
  } catch {
    throw new Error("field header is not JSON");
  }
  if (typeof header !== "object" || header === null || Array.isArray(header)) throw new Error("field header is not an object");
  const h = header as Record<string, unknown>;
  if (h.version !== FIELD_VERSION) throw new Error(`unsupported field version ${String(h.version)}`);
  if (typeof h.field !== "string" || h.field === "") throw new Error("field header has no field name");
  const components = h.components;
  if (typeof components !== "number" || !Number.isInteger(components) || components !== 1 && components !== 3) throw new Error(`field has ${String(components)} components`);
  const count = h.count;
  if (typeof count !== "number" || !Number.isInteger(count) || count < 0) throw new Error("field count must be a non-negative integer");
  if (typeof h.min !== "number" || !Number.isFinite(h.min)) throw new Error("field min must be a finite number");
  if (typeof h.max !== "number" || !Number.isFinite(h.max)) throw new Error("field max must be a finite number");
  const start = 4 + headerLength;
  if (start % 4 !== 0) throw new Error("field data is not aligned");
  if (start + components * count * 4 !== buffer.byteLength) throw new Error("field count does not match the buffer");
  return {
    field: h.field,
    units: typeof h.units === "string" ? h.units : "",
    components,
    count,
    min: h.min,
    max: h.max,
    values: new Float32Array(buffer, start, components * count),
  };
}

/**
 * One value per vertex for a field: the values themselves when it is a scalar, the **magnitude** when
 * it is a vector.
 *
 * **This is the bridge that lets a vector field colour a surface.** `FieldValues.values` holds
 * `components × count` floats, and everything downstream - `tintOf`, `contourLevels`, the band surface -
 * indexes it as one float per vertex. The header's `min`/`max` already range over the magnitude (the
 * agent computes `norm` for `U`), so a vector's range and its magnitudes agree by construction.
 *
 * **A vector returns a NEW array**; a scalar returns the same view, because copying a slice's worth of
 * floats on every redraw would be work for nothing.
 */
export function vertexValues(values: FieldValues): Float32Array {
  if (values.components !== 3) return values.values;
  const out = new Float32Array(values.count);
  for (let i = 0; i < values.count; i++) {
    const x = values.values[i * 3]!;
    const y = values.values[i * 3 + 1]!;
    const z = values.values[i * 3 + 2]!;
    out[i] = Math.hypot(x, y, z);
  }
  return out;
}
