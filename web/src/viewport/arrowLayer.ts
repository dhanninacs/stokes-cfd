/**
 * Vector arrows drawn as ONE instanced mesh: a cone on a shaft, coloured by speed.
 *
 * **One `InstancedMesh`, not one object per arrow**: 200 arrows as separate `ArrowHelper`s would be 200
 * draw calls for a picture that changes only when the density or the scale moves. The instance count is
 * the number of arrows, so the cost is one matrix write each.
 *
 * **The arrow points along its velocity**, which is what makes it readable: the colour says how fast and
 * the direction says which way, and neither is possible from the scalar alone.
 */
import * as THREE from "three";
import { colorAtIn, fraction, type Colormap, type ColormapId } from "../state/colormap";
import type { ArrowSeed } from "../panels/arrowModel";

/** The arrow's shape in its own space: a shaft along +Z and a head at its tip. */
function arrowGeometry(): THREE.BufferGeometry {
  const shaft = new THREE.CylinderGeometry(0.06, 0.06, 0.7, 6);
  // **The head is a cone on the tip**, and both parts are merged so one instanced mesh carries them.
  const head = new THREE.ConeGeometry(0.16, 0.3, 8);
  head.translate(0, 0.85, 0);
  // A cylinder runs along +Y; rotate so the whole arrow runs along +Z, which is what `orient` expects.
  shaft.rotateX(Math.PI / 2);
  head.rotateX(Math.PI / 2);
  return mergeGeometries([shaft, head]);
}

/**
 * An arrow colour that can be seen: the dark end of a map (viridis and magma start near black) lifted
 * toward white, hue kept. Most of a flow is slower than its jet, so most arrows sat at that dark end and
 * came out as black specks on the dark viewport (the elbow walkthrough, 2026-11-05: "vectors are not
 * correct"). A bright colour is left as it is.
 */
export function legible([r, g, b]: readonly [number, number, number]): [number, number, number] {
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  const lift = Math.max(0, 0.5 - luminance) * 1.2;
  return [r + (255 - r) * lift, g + (255 - g) * lift, b + (255 - b) * lift];
}

/** Minimal merge, so this module needs no addon: two indexed geometries with position and normal. */
function mergeGeometries(parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];
  let offset = 0;
  for (const part of parts) {
    const pos = part.getAttribute("position");
    const nor = part.getAttribute("normal");
    for (let i = 0; i < pos.count; i++) {
      positions.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      if (nor) normals.push(nor.getX(i), nor.getY(i), nor.getZ(i));
    }
    const index = part.getIndex();
    if (index) for (let i = 0; i < index.count; i++) indices.push(index.getX(i) + offset);
    else for (let i = 0; i < pos.count; i++) indices.push(i + offset);
    offset += pos.count;
  }
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  if (normals.length > 0) merged.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  merged.setIndex(indices);
  return merged;
}

/**
 * One instanced mesh for `seeds`, or null when there is nothing to draw.
 *
 * `length` is the world length of a full-speed arrow; `range` is the speed range across every arrow, so
 * two arrows at the same speed are the same colour - the same rule the streamlines follow.
 */
export function arrowMesh(
  seeds: readonly ArrowSeed[],
  length: number,
  range: { min: number; max: number },
  map?: Colormap | ColormapId,
  reverse = false,
): THREE.InstancedMesh | null {
  if (seeds.length === 0 || length <= 0) return null;
  /*
    **Depth-tested** (2026-11-04, "not volumetric"): with `depthTest: false` every arrow was painted over
    everything, so arrows behind the slice, behind each other or behind the far wall all came out on top
    and the picture read as a flat decal. Tested against depth, nearer arrows hide farther ones and the
    cloud has a front and a back. The walls are faded while arrows show (`wallsFaded`), and a faded wall
    is drawn after the opaque pass, so it does not hide them.
  */
  const material = new THREE.MeshBasicMaterial({ vertexColors: true });
  const mesh = new THREE.InstancedMesh(arrowGeometry(), material, seeds.length);
  mesh.name = "arrows";
  mesh.renderOrder = 3;
  const matrix = new THREE.Matrix4();
  const quaternion = new THREE.Quaternion();
  const from = new THREE.Vector3(0, 0, 1);
  const to = new THREE.Vector3();
  const colour = new THREE.Color();
  const top = Math.max(range.max, ...seeds.map((s) => s.speed));
  seeds.forEach((seed, i) => {
    /*
      **The arrow's LENGTH is proportional to its speed** - `|U| / max |U|` - so an arrow half as long
      is half as fast. It was the arrow's place in the min..max range with a floor of 0.15, which drew
      the slowest arrow at 15 % whatever its speed (a 1.0 m/s arrow beside a 1.2 m/s one came out a
      sixth of its length) - a magnitude the field does not have.
    */
    const share = top > 0 ? Math.min(1, seed.speed / top) : 1;
    const scale = new THREE.Vector3(length, length, length * share);
    to.set(seed.vector[0], seed.vector[1], seed.vector[2]).normalize();
    quaternion.setFromUnitVectors(from, to);
    matrix.compose(new THREE.Vector3(seed.point[0], seed.point[1], seed.point[2]), quaternion, scale);
    mesh.setMatrixAt(i, matrix);
    const tint = range.max > range.min ? fraction(seed.speed, range.min, range.max) : 1;
    const [r, g, b] = legible(colorAtIn(map ?? "viridis", tint, reverse));
    colour.setRGB(r / 255, g / 255, b / 255);
    mesh.setColorAt(i, colour);
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  return mesh;
}
