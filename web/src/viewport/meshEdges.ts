/**
 * Reads the cell edges of one mesh patch, `GET /api/mesh/edges/{node_id}` (`stokes/mesh/surfaces.py`
 * `patch_edges` writes it): a u32 header length, a JSON header `{"version": 1, "units": "m", "segments":
 * E}` padded with spaces to a multiple of four, then f32[6E], the two end points of each segment as
 * x0 y0 z0 x1 y1 z1, little-endian, in metres.
 *
 * The reply came over a connection, so it is data: the header is checked the way `parseGeometry` checks
 * a mesh's, and the segment count has to account for exactly the rest of the buffer. Anything else is an
 * error, never a guess.
 */

/** The header is a short JSON document; anything larger is not one of ours. */
const MAX_HEADER_BYTES = 1 << 20;
const SEGMENT_BYTES = 24;
const EDGE_VERSION = 1;

/** The segments of a patch's cell edges, six floats each, in the buffer's own memory (not a copy). */
export function parseMeshEdges(buffer: ArrayBuffer): Float32Array {
  if (buffer.byteLength < 4) throw new Error("mesh edges buffer is too short");
  const headerLength = new DataView(buffer).getUint32(0, true);
  if (headerLength > MAX_HEADER_BYTES || 4 + headerLength > buffer.byteLength) throw new Error("mesh edges header is truncated");
  const text = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength));
  let header: unknown;
  try {
    header = JSON.parse(text);
  } catch {
    throw new Error("mesh edges header is not JSON");
  }
  if (typeof header !== "object" || header === null || Array.isArray(header)) throw new Error("mesh edges header is not an object");
  const { version, segments } = header as { version?: unknown; segments?: unknown };
  if (version !== EDGE_VERSION) throw new Error(`unsupported mesh edges version ${String(version)}`);
  if (typeof segments !== "number" || !Number.isInteger(segments) || segments < 0) throw new Error("mesh edges segment count must be a non-negative integer");
  const start = 4 + headerLength;
  if (start % 4 !== 0) throw new Error("mesh edges data is not aligned");
  if (start + segments * SEGMENT_BYTES !== buffer.byteLength) throw new Error("mesh edges segment count does not match the buffer");
  return new Float32Array(buffer, start, segments * 6);
}
