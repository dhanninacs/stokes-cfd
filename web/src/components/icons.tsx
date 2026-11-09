/** Icons lucide does not ship, drawn on the same 24 px grid and stroke as the rest. */
import type { SVGProps } from "react";

type IconProps = Omit<SVGProps<SVGSVGElement>, "ref"> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** A box seen down a corridor: edges converge toward a vanishing point. */
export function PerspectiveIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <rect x="9" y="9" width="6" height="6" rx="0.5" />
      <path d="M3.4 4.4 9 9M20.6 4.4 15 9M3.4 19.6 9 15M20.6 19.6 15 15" />
    </Svg>
  );
}

/** An isometric cube: parallel edges stay parallel. */
export function OrthographicIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3 20 7.5v9L12 21 4 16.5v-9z" />
      <path d="M12 12 20 7.5M12 12v9M12 12 4 7.5" />
    </Svg>
  );
}
