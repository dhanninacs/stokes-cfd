import { useMemo, type ReactNode } from "react";
import { flowArt } from "./flowArt";

/**
 * A card's thumbnail when there is no saved picture: the project's own streamlines (`flowArt`) around a round
 * tile that carries the monogram or an icon. Theme tokens only (`StartScreen.css`); the lines take the accent
 * and flow while the card is hovered or focused.
 */
export function FlowThumb({ seed, children }: { seed: string; children: ReactNode }) {
  const art = useMemo(() => flowArt(seed), [seed]);
  return (
    <>
      <svg className="flow-art" viewBox={`0 0 ${art.width} ${art.height}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
        {art.lines.map((d, i) => (
          <path key={i} d={d} className="flow-line" style={{ animationDelay: `${-((i * 0.37) % 1.6).toFixed(2)}s` }} />
        ))}
      </svg>
      <span className="start-mono">{children}</span>
    </>
  );
}
