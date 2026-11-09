import type { ReactNode } from "react";
import { Tooltip } from "../components/Tooltip";

/** A field's label. The schema's description, when there is one, shows as a tooltip. */
export default function FieldLabel({ htmlFor, description, className = "", children }: { htmlFor: string; description?: string; className?: string; children: ReactNode }) {
  const label = (
    <label htmlFor={htmlFor} className={className}>
      {children}
    </label>
  );
  return description ? (
    <Tooltip label={description} side="top" describe={false}>
      {label}
    </Tooltip>
  ) : (
    label
  );
}
