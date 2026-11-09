/**
 * The look of a cut against the look of a patch (`createLook` with `unlit`, R4b): a slice, an iso-surface and a
 * section cap are drawn with no lighting at all, two-sided and not tone mapped; the patches keep their lit PBR
 * material. A WebGL context is not needed to build materials, so this runs in node.
 */
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { createLook, disposeLook, paintLook, setLookColor, setLookOpacity } from "./materials";

const GREY: [number, number, number] = [0.6, 0.64, 0.68];

/** Anything three lights: a material that has a lit shader answers `isMeshStandardMaterial` (physical derives from it). */
function lit(material: THREE.Material): boolean {
  return (material as THREE.MeshStandardMaterial).isMeshStandardMaterial === true;
}

describe("a cut's look", () => {
  it("is a basic material: no lighting, so no shading and no specular", () => {
    const look = createLook("mesh", GREY, false, true);
    expect(look.unlit).toBe(true);
    expect(look.surface).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(lit(look.surface)).toBe(false);
  });

  it("is double-sided, and not tone mapped so the legend's colours are the ones on screen", () => {
    const { surface } = createLook("mesh", GREY, true, true);
    expect(surface.side).toBe(THREE.DoubleSide);
    expect(surface.toneMapped).toBe(false);
  });

  it("keeps the colour as given, with no recipe darkening", () => {
    const look = createLook("mesh", GREY, false, true);
    const expected = new THREE.Color().setRGB(GREY[0], GREY[1], GREY[2], THREE.SRGBColorSpace);
    expect(look.surface.color.equals(expected)).toBe(true);
    expect(look.albedoScale).toBe(1);
    setLookColor(look, [1, 0, 0]);
    expect(look.surface.color.equals(new THREE.Color().setRGB(1, 0, 0, THREE.SRGBColorSpace))).toBe(true);
  });

  it("reads the per-vertex tint (the field's colours) like a patch does", () => {
    const { surface } = createLook("mesh", GREY, false, true);
    expect(surface.onBeforeCompile.toString()).toContain("vertexShader");
    expect((surface as THREE.Material & { defaultAttributeValues?: unknown }).defaultAttributeValues).toEqual({ tint: [0, 0, 0, 0] });
  });

  it("paints a highlight and fades in opacity without a lit-only field to write", () => {
    const look = createLook("mesh", GREY, false, true);
    expect(() => paintLook(look, new THREE.Color(1, 1, 0), false, 1)).not.toThrow();
    expect(setLookOpacity(look, 5)).toBe(true);
    expect(look.glass).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(look.glass?.transparent).toBe(true);
    disposeLook(look);
  });
});

describe("a patch's look is still lit", () => {
  it("stays the physical material, lit and with its recipe", () => {
    const look = createLook("mesh", GREY, false);
    expect(look.unlit).toBe(false);
    expect(look.surface).toBeInstanceOf(THREE.MeshPhysicalMaterial);
    expect(lit(look.surface)).toBe(true);
  });

  it("the default of createLook is lit", () => {
    expect(createLook("wall", GREY, false).unlit).toBe(false);
  });
});
