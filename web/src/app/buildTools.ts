/**
 * The build ribbon's icons (ADR-0035), by tool id (`createEntries`' entry ids). A module of its own
 * so the tests read it in Node without the store. Icons are lucide's (ISC).
 */
import {
  Box,
  CircleDotDashed,
  Columns3,
  Expand,
  PenLine,
  PenTool,
  Spline,
  Waypoints,
  Scissors,
  SquareDashed,
  Circle,
  Cone,
  Cylinder,
  FlipHorizontal2,
  Globe,
  Grid3x3,
  Hexagon,
  Move3d,
  Package,
  Pill,
  RectangleHorizontal,
  Rotate3d,
  Scaling,
  SquareSplitHorizontal,
  SquaresIntersect,
  SquaresSubtract,
  SquaresUnite,
  Torus,
  type LucideIcon,
} from "lucide-react";

/** The icon of each tool, by entry id; a tool without one shows its label alone. */
export const TOOL_ICONS: Record<string, LucideIcon> = {
  "sketch-new": PenTool,
  "sketch-line": PenLine,
  "sketch-rectangle": RectangleHorizontal,
  "sketch-circle": Circle,
  "sketch-arc": Waypoints,
  "sketch-polygon": Hexagon,
  "sketch-spline": Spline,
  "shape-box": Box,
  "shape-rounded_box": Package,
  "shape-cylinder": Cylinder,
  "shape-cone": Cone,
  "shape-sphere": Globe,
  "shape-torus": Torus,
  "shape-capsule": Pill,
  "shape-half_space": SquareSplitHorizontal,
  "transform-translate": Move3d,
  "transform-rotate": Rotate3d,
  "transform-mirror": FlipHorizontal2,
  "transform-scale": Scaling,
  "combine-union": SquaresUnite,
  "combine-difference": SquaresSubtract,
  "combine-intersection": SquaresIntersect,
  "combine-split": Scissors,
  "pattern-linear": Columns3,
  "pattern-polar": CircleDotDashed,
  "modify-offset": Expand,
  "modify-shell": SquareDashed,
  "modify-lattice": Grid3x3,
};


/**
 * The order the ribbon folds its groups into one button each when it does not fit (review finding
 * B10): the long Shape list first, the Sketch group last, since it is where a model starts.
 */
export const COLLAPSE_ORDER: readonly string[] = ["shape", "modify", "pattern", "transform", "combine", "sketch"];

/** The next group to fold, given the folded ones and the groups shown; null when every one is folded. */
export function nextToCollapse(collapsed: readonly string[], shown: readonly string[]): string | null {
  return COLLAPSE_ORDER.find((id) => shown.includes(id) && !collapsed.includes(id)) ?? null;
}

/** The ribbon's letters (review finding B10), by the tool entry or group whose tooltip names them. */
export const BUILD_KEYS: Readonly<Record<string, string>> = {
  "sketch-new": "build.sketch",
  "transform-translate": "build.move",
  combine: "build.combine",
};
