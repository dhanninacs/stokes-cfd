import { useId, type ComponentPropsWithRef, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { statusWord } from "../panels/statusWords";
import { Tooltip, type TooltipSide } from "./Tooltip";

type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "default" | "primary" | "ghost";
  active?: boolean;
  icon?: boolean;
};

export function Button({ variant = "default", active, icon, className = "", children, ...rest }: ButtonProps) {
  if (import.meta.env.DEV && icon && !rest["aria-label"] && !rest["aria-labelledby"] && !rest.title) {
    console.warn("Stokes: an icon Button needs an accessible name (aria-label).");
  }
  const classes = ["btn", variant !== "default" ? variant : "", active ? "active" : "", icon ? "icon" : "", "no-drag", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button type="button" className={classes} {...rest}>
      {children}
    </button>
  );
}

type IconButtonProps = Omit<ButtonProps, "icon" | "aria-label" | "children"> & {
  /** Accessible name and tooltip text. */
  label: string;
  /** Shortcut shown in the tooltip, e.g. "Ctrl K". */
  hint?: string;
  /** Same shortcut in aria-keyshortcuts syntax, e.g. "Control+K". */
  keys?: string;
  side?: TooltipSide;
  children: ReactNode;
};

/** Icon-only button: always has an accessible name and a tooltip (with the keyboard hint). */
export function IconButton({ label, hint, keys, side = "top", variant = "ghost", children, ...rest }: IconButtonProps) {
  return (
    <Tooltip label={label} hint={hint} side={side} describe={false}>
      <Button icon variant={variant} aria-label={label} aria-keyshortcuts={keys} {...rest}>
        {children}
      </Button>
    </Tooltip>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

export function Panel({ className = "", children, style }: { className?: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <div className={`glass fade-in ${className}`} style={style}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pt-3 pb-2">
      <span className="section-title">{children}</span>
      {right}
    </div>
  );
}

/**
 * The row's status dot.
 *
 * **It says what the state MEANS** (claude-win2's 0200 item 6, the guide's finding 7): the tooltip used
 * to be the raw internal word - `ok`, `new` - which tells a user nothing, and every folder was a green
 * "ok". The words come from `statusWords`, so the sentence and the tone agree.
 */
export function StatusDot({ status }: { status: string | undefined }) {
  if (status === undefined) return null;
  const { text } = statusWord(status);
  return <span className={`dot ${status}`} title={text} role="img" aria-label={text} />;
}

/** On/off setting with a label and an optional explanation. */
export function Switch({ checked, onChange, label, description, disabled }: { checked: boolean; onChange: (next: boolean) => void; label: string; description?: string; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div id={id} className="text-[12.5px]">
          {label}
        </div>
        {description && <div className="muted text-[11.5px] leading-snug">{description}</div>}
      </div>
      <button type="button" role="switch" aria-checked={checked} aria-labelledby={id} className="switch no-drag" disabled={disabled} onClick={() => !disabled && onChange(!checked)} />
    </div>
  );
}

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
  /** The reason this option cannot be chosen now; it is skipped by the arrow keys and shown as a tooltip. */
  disabled?: string;
}

/** Radio group drawn as a segmented control; arrow keys move the selection. */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: SegmentedOption<T>[]; onChange: (next: T) => void; label: string }) {
  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const index = options.findIndex((o) => o.value === value);
    let next = options[(index + step + options.length) % options.length];
    for (let tries = 0; next.disabled && tries < options.length; tries++) next = options[(options.indexOf(next) + step + options.length) % options.length];
    if (next.disabled) return;
    onChange(next.value);
    const group = e.currentTarget;
    requestAnimationFrame(() => group.querySelector<HTMLElement>("[aria-checked='true']")?.focus());
  };
  return (
    <div role="radiogroup" aria-label={label} className="segmented" onKeyDown={move}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          aria-disabled={o.disabled ? true : undefined}
          title={o.disabled}
          tabIndex={o.value === value ? 0 : -1}
          className="no-drag"
          onClick={() => !o.disabled && onChange(o.value)}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}
