import { describe, expect, it } from "vitest";
import { parseField, vertexValues } from "./fieldCodec";
import { writeField } from "./fieldCodecSupport";

describe("parseField", () => {
  it("reads a valid scalar field: header, count, range and the values", () => {
    const parsed = parseField(writeField({ field: "p", units: "m2/s2", values: [0.5, 0.25, 0] }));
    expect(parsed.field).toBe("p");
    expect(parsed.units).toBe("m2/s2");
    expect(parsed.components).toBe(1);
    expect(parsed.count).toBe(3);
    expect([parsed.min, parsed.max]).toEqual([0, 0.5]);
    expect(Array.from(parsed.values)).toEqual([0.5, 0.25, 0]);
  });

  it("reads a vector field with its components and the magnitude range", () => {
    const parsed = parseField(writeField({ field: "U", components: 3, values: [3, 4, 0, 0, 0, 12] }));
    expect(parsed.components).toBe(3);
    expect(parsed.count).toBe(2);
    expect(parsed.values.length).toBe(6);
    expect([parsed.min, parsed.max]).toEqual([5, 12]);
  });

  it("reads an empty field", () => {
    expect(parseField(writeField({ field: "p", values: [] })).count).toBe(0);
  });

  it("refuses a buffer that is too short or whose header is truncated", () => {
    expect(() => parseField(new ArrayBuffer(2))).toThrow(/too short/);
    const full = writeField({ field: "p", values: [1, 2] });
    expect(() => parseField(full.slice(0, 10))).toThrow(/truncated/);
  });

  it("refuses a header that is not JSON, not an object, or of another version", () => {
    const bytes = new Uint8Array(writeField({ field: "p", values: [1] }));
    bytes[4] = 0x7e;
    expect(() => parseField(bytes.buffer)).toThrow(/not JSON/);
    expect(() => parseField(writeField({ field: "p", values: [1], version: 2 }))).toThrow(/version 2/);
    const array = new TextEncoder().encode("[1, 2, 3, 4]");
    const out = new Uint8Array(4 + array.length);
    new DataView(out.buffer).setUint32(0, array.length, true);
    out.set(array, 4);
    expect(() => parseField(out.buffer)).toThrow(/not an object/);
  });

  it("refuses a count that disagrees with the length of the data", () => {
    const full = writeField({ field: "p", values: [1, 2, 3] });
    expect(() => parseField(full.slice(0, full.byteLength - 4))).toThrow(/does not match/);
    const longer = new Uint8Array(full.byteLength + 4);
    longer.set(new Uint8Array(full));
    expect(() => parseField(longer.buffer)).toThrow(/does not match/);
  });

  it("refuses a missing name, a bad component count and a range that is not a number", () => {
    const make = (header: Record<string, unknown>, bytes = 0): ArrayBuffer => {
      let text = JSON.stringify(header);
      while (text.length % 4 !== 0) text += " ";
      const head = new TextEncoder().encode(text);
      const out = new Uint8Array(4 + head.length + bytes);
      new DataView(out.buffer).setUint32(0, head.length, true);
      out.set(head, 4);
      return out.buffer;
    };
    const good = { version: 1, field: "p", units: "", components: 1, count: 0, min: 0, max: 1 };
    expect(() => parseField(make({ ...good, field: "" }))).toThrow(/no field name/);
    expect(() => parseField(make({ ...good, components: 4 }))).toThrow(/components/);
    expect(() => parseField(make({ ...good, components: 0 }))).toThrow(/components/);
    // The contract is one component (a scalar) or three (a vector): two is neither.
    expect(() => parseField(make({ ...good, components: 2 }))).toThrow(/2 components/);
    expect(() => parseField(make({ ...good, count: -1 }))).toThrow(/count/);
    expect(() => parseField(make({ ...good, min: "low" }))).toThrow(/min/);
    expect(() => parseField(make({ ...good, max: null }))).toThrow(/max/);
    expect(parseField(make(good)).count).toBe(0);
  });
});

describe("vertexValues", () => {
  const vector = (vals: number[]) => ({
    field: "U",
    units: "m/s",
    components: 3,
    count: vals.length / 3,
    min: 0,
    max: 5,
    values: new Float32Array(vals),
  });

  it("gives a SCALAR field back unchanged, without copying", () => {
    // A slice's worth of floats on every redraw would be work for nothing.
    const field = { ...vector([1, 2, 3]), components: 1, count: 3 };
    expect(vertexValues(field)).toBe(field.values);
  });

  it("gives a VECTOR field's magnitude per vertex", () => {
    // Everything downstream - the tint, the contours, the bands - needs one number per vertex.
    const out = vertexValues(vector([3, 4, 0, 0, 0, 2]));
    expect(Array.from(out)).toEqual([5, 2]);
  });

  it("matches the header's range, because the agent ranges over the magnitude too", () => {
    // `agent/stokes_agent/extract.py` computes `np.linalg.norm` for U, so a vector's min/max are its
    // magnitudes': a mismatch here would colour a surface against a range it was never measured on.
    const out = vertexValues(vector([0, 0, 0, 3, 4, 0]));
    expect(Math.min(...out)).toBe(0);
    expect(Math.max(...out)).toBe(5);
  });
});
