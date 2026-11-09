/** A yes-or-no question for the Explore commands that cannot be undone (replacing a table, removing points). */
import { Dialog } from "../../components/Dialog";
import { openDialog } from "./dialogHost";

export interface ConfirmOptions {
  title: string;
  /** What will happen, in a sentence or two. */
  body: string;
  confirmLabel: string;
}

/** Resolves true when the user confirms, false on Cancel, Escape or a click outside. */
export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    openDialog((close) => {
      const answer = (value: boolean) => () => {
        resolve(value);
        close();
      };
      return (
        <Dialog
          open
          title={options.title}
          focus="[data-confirm]"
          onClose={answer(false)}
          actions={
            <>
              <button type="button" className="btn no-drag" onClick={answer(false)}>
                Cancel
              </button>
              <button type="button" className="btn primary no-drag" data-confirm onClick={answer(true)}>
                {options.confirmLabel}
              </button>
            </>
          }
        >
          <p className="muted text-[12.5px] leading-snug">{options.body}</p>
        </Dialog>
      );
    });
  });
}
