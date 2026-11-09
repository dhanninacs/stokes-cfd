/**
 * Materials of the patches: environment-lit PBR for the shaded view, a flat colour for the wireframe.
 *
 * The server sends one colour per patch (`GeometryGroupMeta.color`, 0..1). They are sRGB values,
 * the same ones the panels use for swatches, so they are converted to the linear working space
 * here. Reading them as linear washes every colour out.
 *
 * Tag colours (ADR-0021 3.5, the tint switch): every part geometry carries a per-vertex RGBA8
 * `tint` attribute, the tag's sRGB colour with its strength (the tag's opacity) in alpha, zero where
 * no tag colours the face. Both materials of a look mix it into the base colour, so a coloured tag
 * costs no draw call, triangle or fragment of its own, and a batch draws it like any other part.
 *
 * Part opacity (ADR-0021 4.4) is drawn in tenths, `opacityBucket`: below 1 a look draws through
 * `glass`, a translucent clone of its surface, so the batches need at most nine translucent variants
 * of a look, and an opaque part draws exactly as it did.
 */

import * as THREE from "three";

/** Name of the per-vertex tag colour every part geometry carries (see the module doc). */
export const TINT_ATTRIBUTE = "tint";

const TINT_VERTEX_HEAD = /* glsl */ `#include <common>
attribute vec4 tint;
varying vec4 vTint;`;
/** sRGB to linear (the exact curve) once per vertex rather than per fragment: a face has one tint. */
const TINT_VERTEX = /* glsl */ `#include <begin_vertex>
vTint = vec4(mix(pow((tint.rgb + 0.055) / 1.055, vec3(2.4)), tint.rgb / 12.92, vec3(lessThanEqual(tint.rgb, vec3(0.04045)))), tint.a);`;
const TINT_FRAGMENT_HEAD = /* glsl */ `#include <common>
varying vec4 vTint;`;
/** After the instance colour of a batch: the tag's colour replaces the part's in proportion to its strength. */
const TINT_FRAGMENT = /* glsl */ `#include <color_fragment>
diffuseColor.rgb = mix(diffuseColor.rgb, vTint.rgb, vTint.a);`;

/**
 * Makes a material read the tint attribute. A geometry without the attribute reads "no tint" (the
 * default value below) rather than WebGL's (0, 0, 0, 1), which would be black at full strength.
 * Every material with the same patch shares one program, as before. `Material.clone` keeps neither,
 * so a copy of a look's material (a translucent variant) goes through this again.
 */
export function withTint<M extends THREE.Material>(material: M): M {
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader.replace("#include <common>", TINT_VERTEX_HEAD).replace("#include <begin_vertex>", TINT_VERTEX);
    shader.fragmentShader = shader.fragmentShader.replace("#include <common>", TINT_FRAGMENT_HEAD).replace("#include <color_fragment>", TINT_FRAGMENT);
  };
  // three reads `defaultAttributeValues` from any material, though only ShaderMaterial declares it.
  (material as THREE.Material & { defaultAttributeValues?: Record<string, number[]> }).defaultAttributeValues = { [TINT_ATTRIBUTE]: [0, 0, 0, 0] };
  return material;
}

/** What a kind of patch looks like. Colours come from the patch, everything else from here. */
interface Recipe {
  metalness: number;
  roughness: number;
  clearcoat: number;
  clearcoatRoughness: number;
  /** Multiplies the patch colour; below 1 darkens. */
  albedoScale: number;
  opacity: number;
  /** Strength of the patch's own emissive tint, so a boundary patch glows faintly in its colour. */
  emissive: number;
}

const WALL: Recipe = {
  metalness: 0.6,
  roughness: 0.35,
  clearcoat: 0.25,
  clearcoatRoughness: 0.35,
  albedoScale: 1,
  opacity: 1,
  emissive: 0,
};

const FLANGE: Recipe = {
  metalness: 0.7,
  roughness: 0.45,
  clearcoat: 0.1,
  clearcoatRoughness: 0.5,
  albedoScale: 0.78,
  opacity: 1,
  emissive: 0,
};

/** Inlet and outlet are virtual surfaces: slightly translucent, tinted in their own colour. */
const BOUNDARY: Recipe = {
  metalness: 0,
  roughness: 0.4,
  clearcoat: 0,
  clearcoatRoughness: 0.5,
  albedoScale: 1,
  opacity: 0.85,
  emissive: 0.16,
};

const OTHER: Recipe = {
  metalness: 0.5,
  roughness: 0.42,
  clearcoat: 0.15,
  clearcoatRoughness: 0.45,
  albedoScale: 0.9,
  opacity: 1,
  emissive: 0,
};

/** A boundary patch of the mesh (T-211): matte and opaque, in the patch's own colour, so the cell edges drawn on it read. */
const MESH: Recipe = {
  metalness: 0,
  roughness: 0.85,
  clearcoat: 0,
  clearcoatRoughness: 0.5,
  albedoScale: 1,
  opacity: 1,
  emissive: 0,
};

const RECIPES: Record<string, Recipe> = {
  mesh: MESH,
  wall: WALL,
  flange: FLANGE,
  inlet: BOUNDARY,
  outlet: BOUNDARY,
  // An imported solid is a body of metal like the walls of the sample part.
  solid: WALL,
};

/** Emissive strength of the accent on a fully selected patch. Hover uses a third of it. */
const SELECTED_LIFT = 0.16;

/** The bucket of an opaque part (see `opacityBucket`). */
export const OPAQUE_BUCKET = 10;

/**
 * A part's opacity (0 to 1) in tenths, as the batches key it (ADR-0021 4.4): OPAQUE_BUCKET from 0.95
 * on, and for anything that is not a number below 1; else 1 to 9, so a part at 0 still shows faintly.
 * Both render paths draw the bucket's opacity, so a batched part looks the same when its own mesh
 * takes over for a highlight.
 */
export function opacityBucket(opacity: number): number {
  return opacity < 1 ? Math.max(1, Math.round(opacity * OPAQUE_BUCKET)) : OPAQUE_BUCKET;
}

/**
 * What a look's shaded surface is made of: environment-lit PBR for the patches, or a flat
 * `MeshBasicMaterial` for a cut (`createLook` with `unlit`).
 */
export type LookSurface = THREE.MeshPhysicalMaterial | THREE.MeshBasicMaterial;

export interface PartLook {
  surface: LookSurface;
  /**
   * The surface at the part's opacity while that is below 1 (`setLookOpacity`), null while it is
   * opaque. The shaded view draws `glass ?? surface`.
   */
  glass: LookSurface | null;
  /**
   * True for the look of a cut (slice, iso-surface, section cap): no lighting at all, so the colour on screen is
   * the colour map's. A batch is lit, so an unlit part is never batched.
   */
  unlit: boolean;
  wire: THREE.MeshBasicMaterial;
  /** Patch colour in linear space, before the recipe's tint. */
  base: THREE.Color;
  ownEmissive: number;
  translucent: boolean;
  /** The recipe's darkening of the colour, kept so a new colour gets the same treatment. */
  albedoScale: number;
}

const scratch = new THREE.Color();
/** Triangle edges of a dense mesh overlap into a solid sheet unless they are translucent. */
const WIRE_OPACITY = 0.5;

const HEX_COLOUR = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;

/** "#rrggbb" as sRGB components from 0 to 1, or null when the text is anything else. */
export function parseHexColor(text: string): [number, number, number] | null {
  const match = HEX_COLOUR.exec(text);
  if (!match) return null;
  return [parseInt(match[1], 16) / 255, parseInt(match[2], 16) / 255, parseInt(match[3], 16) / 255];
}

/**
 * Whether the recipe of `kind` tints the surface with the patch's own colour (the boundary patches).
 * A batch of parts (batch.ts) carries colour per instance but has one emissive for all of them, so
 * such kinds are batched per colour.
 */
export function hasOwnTint(kind: string): boolean {
  return (RECIPES[kind] ?? OTHER).emissive > 0;
}

export function wireBaseColor(base: THREE.Color, isDark: boolean, out: THREE.Color): THREE.Color {
  // Light patch colours vanish on the light background, so the wire is darkened there.
  return isDark ? out.copy(base) : out.copy(base).multiplyScalar(0.14);
}

/**
 * The material of a cut. **No lighting, no tone mapping, two sides**: the cut of the run's results is drawn
 * from the colour map, and a lit single-sided material shaded the sliver triangles the cut leaves (near-zero
 * area, winding that flips from one to the next) into regular rows of dark dashes on a flat fill, unchanged by
 * the edge, arrow and section switches (R4b, the second browser pass on pipe3). A basic material has no
 * normal, so the artefact cannot arise, and no specular, so the colours do not move with the view direction.
 * `toneMapped: false` because the renderer's neutral tone map compresses colours above 0.8, and the legend's
 * swatches are the map's own. The tint attribute (`withTint`) is the per-vertex colour of the field.
 */
function cutSurface(base: THREE.Color): THREE.MeshBasicMaterial {
  return withTint(new THREE.MeshBasicMaterial({
    color: base.clone(),
    side: THREE.DoubleSide,
    toneMapped: false,
    // Pushed back a hair so the cell edges and contour lines drawn on the cut never z-fight with it.
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  }));
}

export function createLook(kind: string, color: readonly [number, number, number], isDark: boolean, unlit = false): PartLook {
  const recipe = RECIPES[kind] ?? OTHER;
  const base = new THREE.Color().setRGB(color[0], color[1], color[2], THREE.SRGBColorSpace);
  const surface: LookSurface = unlit ? cutSurface(base) : withTint(new THREE.MeshPhysicalMaterial({
    color: base.clone().multiplyScalar(recipe.albedoScale),
    metalness: recipe.metalness,
    roughness: recipe.roughness,
    clearcoat: recipe.clearcoat,
    clearcoatRoughness: recipe.clearcoatRoughness,
    opacity: recipe.opacity,
    transparent: recipe.opacity < 1,
    side: THREE.DoubleSide,
    // Pushes the surface back a hair so edge lines drawn on it never z-fight with it.
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  }));
  const wire = withTint(new THREE.MeshBasicMaterial({
    color: wireBaseColor(base, isDark, new THREE.Color()),
    wireframe: true,
    toneMapped: false,
    transparent: true,
    opacity: WIRE_OPACITY,
    depthWrite: false,
    // The mesh keeps this material in wireframe mode and is still picked through it, and
    // the ray test only hits back faces when the material is double-sided.
    side: THREE.DoubleSide,
  }));
  if (unlit) return { surface, glass: null, unlit, wire, base, ownEmissive: 0, translucent: false, albedoScale: 1 };
  return { surface, glass: null, unlit, wire, base, ownEmissive: recipe.emissive, translucent: recipe.opacity < 1, albedoScale: recipe.albedoScale };
}

/**
 * Draws a look at an opacity bucket (`opacityBucket`). Below OPAQUE_BUCKET the shaded view draws
 * `glass`: a clone of the surface, made once and kept while the bucket changes, that is transparent,
 * writes no depth and has the bucket's opacity on top of the recipe's. It keeps the surface's sides,
 * polygon offset and tag tint, so it draws and picks like the surface, and three draws a transparent
 * double-sided material back faces first, so a closed body reads right from any side. At
 * OPAQUE_BUCKET the clone goes. True when the material to draw changed.
 */
export function setLookOpacity(look: PartLook, bucket: number): boolean {
  if (bucket >= OPAQUE_BUCKET) {
    if (look.glass === null) return false;
    look.glass.dispose();
    look.glass = null;
    return true;
  }
  const made = look.glass === null;
  const glass = look.glass ?? withTint(look.surface.clone());
  glass.transparent = true;
  glass.depthWrite = false;
  glass.opacity = look.surface.opacity * (bucket / OPAQUE_BUCKET);
  look.glass = glass;
  return made;
}

/**
 * Gives a look another colour (sRGB, 0..1), as a part's colour parameter does. The caller repaints
 * the look afterwards, which derives the emissive tint and the wire colour from the new base.
 */
export function setLookColor(look: PartLook, color: readonly [number, number, number]): void {
  look.base.setRGB(color[0], color[1], color[2], THREE.SRGBColorSpace);
  look.surface.color.copy(look.base).multiplyScalar(look.albedoScale);
  look.glass?.color.copy(look.surface.color);
}

/** Sets the emissive lift and wire tint for a highlight amount: 0 none, 1 selected. */
export function paintLook(look: PartLook, accent: THREE.Color, isDark: boolean, lift: number): void {
  // A cut has no emissive to lift (the selection outline and the face tint show a pick on it).
  if (look.surface instanceof THREE.MeshPhysicalMaterial) {
    const emissive = look.surface.emissive;
    emissive.copy(look.base).multiplyScalar(look.ownEmissive);
    if (lift > 0) emissive.add(scratch.copy(accent).multiplyScalar(SELECTED_LIFT * lift));
    if (look.glass instanceof THREE.MeshPhysicalMaterial) look.glass.emissive.copy(emissive);
  }
  wireBaseColor(look.base, isDark, look.wire.color);
  look.wire.opacity = WIRE_OPACITY;
  if (lift > 0) {
    look.wire.color.lerp(accent, Math.min(1, lift * 0.85));
    look.wire.opacity = WIRE_OPACITY + (1 - WIRE_OPACITY) * Math.min(1, lift) * 0.8;
  }
}

export function disposeLook(look: PartLook): void {
  look.surface.dispose();
  look.glass?.dispose();
  look.wire.dispose();
}
