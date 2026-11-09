/**
 * A card's one-at-a-time action: a busy flag while a command is in flight and the server's sentence when
 * it refuses. The cards put the sentence on the card, where the user is looking, instead of the console.
 */
import { useCallback, useRef, useState } from "react";
import { messageOf } from "./commands";

export interface ActionState {
  busy: boolean;
  error: string;
  /** Run `work`; true when it finished without a refusal. A second press while one is running does nothing. */
  run(work: () => Promise<unknown>): Promise<boolean>;
  clear(): void;
  fail(message: string): void;
}

export function useAction(): ActionState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = useRef(false);

  const run = useCallback(async (work: () => Promise<unknown>) => {
    if (running.current) return false;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
      return true;
    } catch (err) {
      setError(messageOf(err));
      return false;
    } finally {
      running.current = false;
      setBusy(false);
    }
  }, []);

  return { busy, error, run, clear: useCallback(() => setError(""), []), fail: setError };
}
