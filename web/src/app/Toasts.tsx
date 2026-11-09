import { AlertCircle, CheckCircle2, Info, TerminalSquare, X } from "lucide-react";
import { useStore, type Toast } from "../state/store";
import { IconButton } from "../components/ui";

const ICONS = { info: Info, ok: CheckCircle2, error: AlertCircle } as const satisfies Record<Toast["kind"], unknown>;
const COLOURS = { info: "var(--blue)", ok: "var(--ok)", error: "var(--danger)" } as const satisfies Record<Toast["kind"], string>;

/**
 * Notifications, newest at the bottom. The store caps them at four and dismisses after 3 s
 * (6 s for errors); this only draws them. App places the stack at the bottom centre.
 * The region is always present so assistive technology announces additions.
 */
export default function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  const setDrawerTab = useStore((s) => s.setDrawerTab);
  return (
    <div className="toast-region" aria-live="polite" aria-relevant="additions">
      {toasts.map((t) => {
        const Icon = ICONS[t.kind];
        return (
          <div key={t.id} className="toast glass" data-kind={t.kind} role={t.kind === "error" ? "alert" : "status"}>
            <Icon size={15} className="shrink-0" style={{ color: COLOURS[t.kind] }} aria-hidden="true" />
            <span className="toast-text">
              {t.text}
              {/*
                **The internal code, behind a disclosure** (claude-win2's 1030 review). It used to be
                prefixed to the sentence - "project.busy: Stop the run first" - which puts an
                identifier nobody outside this repository recognises in front of the one part a user
                needs. It is still worth keeping: a bug report reads better with it, and the console
                link beside this shows the same line with the code in place.
              */}
              {t.code !== undefined && (
                <details className="toast-details">
                  <summary className="muted cursor-pointer text-[11px]">Details</summary>
                  <code>{t.code}</code>
                </details>
              )}
            </span>
            {t.action && (
              <button
                type="button"
                className="toast-link no-drag"
                onClick={() => {
                  t.action?.run();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
            {t.kind === "error" && (
              <button
                type="button"
                className="toast-link no-drag"
                aria-label="Show in the console"
                onClick={() => {
                  setDrawerTab("console"); // errors from execute() are logged there
                  dismiss(t.id);
                }}
              >
                <TerminalSquare size={12} aria-hidden="true" /> Console
              </button>
            )}
            <IconButton label="Dismiss notification" className="!h-6 !w-6 shrink-0" onClick={() => dismiss(t.id)}>
              <X size={13} />
            </IconButton>
          </div>
        );
      })}
    </div>
  );
}
