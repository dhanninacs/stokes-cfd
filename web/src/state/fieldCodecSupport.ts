/**
 * Test helper: writes a field buffer the way the server's route does (`parseField` reads it), so the codec,
 * the controller's tints and the harness are tested against bytes made from the layout alone. Not imported
 * by the app.
 */

export interface FieldFixture {
  field: string;
  units?: string;
  components?: number;
  values: ArrayLike<number>;
  min?: number;
  max?: number;
  /** Replaces the version (for the refusal tests). */
  version?: number;
}

export function writeField(fixture: FieldFixture): ArrayBuffer {
  const components = fixture.components ?? 1;
  const values = Float32Array.from(fixture.values as ArrayLike<number>);
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i + components <= values.length; i += components) {
    let v = 0;
    if (components === 1) v = values[i];
    else {
      for (let c = 0; c < components; c++) v += values[i + c] * values[i + c];
      v = Math.sqrt(v);
    }
    if (v < min) min = v;
    if (v > max) max = v;
  }
  let text = JSON.stringify({
    version: fixture.version ?? 1,
    field: fixture.field,
    units: fixture.units ?? "",
    components,
    count: values.length / components,
    min: fixture.min ?? (Number.isFinite(min) ? min : 0),
    max: fixture.max ?? (Number.isFinite(max) ? max : 0),
  });
  while (new TextEncoder().encode(text).length % 4 !== 0) text += " ";
  const header = new TextEncoder().encode(text);
  const out = new Uint8Array(4 + header.length + values.byteLength);
  new DataView(out.buffer).setUint32(0, header.length, true);
  out.set(header, 4);
  out.set(new Uint8Array(values.buffer), 4 + header.length);
  return out.buffer;
}
