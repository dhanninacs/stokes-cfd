/**
 * A page's Basic fields (the children) with a closed **Advanced** disclosure under them.
 *
 * The disclosure holds today's components unchanged (the caller passes them in `advanced`). Its open state
 * is kept per node type in `localStorage` (`setup/advancedState.ts`). A refusal on a field inside it
 * (`hasError`) opens it; a dot on its title says some Advanced value differs from its default (`changed`),
 * so a setting out of sight is never a surprise.
 */
import { useEffect, useState, type ReactNode } from "react";
import { readAdvancedOpen, writeAdvancedOpen } from "../setup/advancedState";
import "./BasicAdvanced.css";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export default function BasicAdvanced({
  type,
  basic,
  advanced,
  changed = false,
  hasError = false,
  reveal = 0,
}: {
  /** The node type: the open state is kept under it. Give the component `key={type}` so a new type re-reads it. */
  type: string;
  basic: ReactNode;
  /** The Advanced content, or null when the page has none (no disclosure is drawn). */
  advanced: ReactNode | null;
  /** Some Advanced value differs from its default. */
  changed?: boolean;
  /** A field inside Advanced was refused: the disclosure opens. */
  hasError?: boolean;
  /** A counter the page bumps to open the disclosure on purpose (a Change… button beside the Basic fields). */
  reveal?: number;
}) {
  const [open, setOpen] = useState(() => readAdvancedOpen(storage(), type));
  useEffect(() => {
    if (hasError) setOpen(true);
  }, [hasError]);
  useEffect(() => {
    if (reveal > 0) setOpen(true);
  }, [reveal]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    writeAdvancedOpen(storage(), type, next);
  };

  return (
    <>
      {basic}
      {advanced !== null && (
        <details className="basic-advanced" open={open} data-ws="advanced" data-type={type}>
          <summary
            className="basic-advanced__summary"
            onClick={(event) => {
              event.preventDefault();
              toggle();
            }}
            title="Settings most runs leave as they are"
          >
            Advanced
            {changed && <span className="basic-advanced__dot" role="img" aria-label="Some values are changed" title="Some values are not the default" />}
          </summary>
          <div className="basic-advanced__body space-y-3">{advanced}</div>
        </details>
      )}
    </>
  );
}
