import { useCallback, useEffect, useState } from "react";
import { StokesApiError } from "../api/client";
import { Dialog } from "../components/Dialog";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { byteLength, isDirty, MAX_EDIT_BYTES, refusalText } from "./caseFileEditorModel";

/**
 * The text editor for one file of an imported case (`case.file_read`, `case.file_write`,
 * `case.file_revert`). The text is the whole file as it will stage; the server lints it with the
 * agent's rules before storing it, so a refusal shows under the text and nothing is saved.
 *
 * A saved text replaces the key edits of the same file. Revert drops it and the file stages from the
 * copy again. Every action is one undo step.
 */
export default function CaseFileEditor({
  node,
  file,
  onClose,
}: {
  node: string;
  file: string;
  onClose: () => void;
}) {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const [loaded, setLoaded] = useState("");
  const [text, setText] = useState("");
  const [edited, setEdited] = useState(false);
  const [ready, setReady] = useState(false);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setReady(false);
    setProblem("");
    try {
      const reply = (await execute("case.file_read", { node, file }, { silent: true })) as {
        text: string;
        edited: boolean;
      };
      setLoaded(reply.text);
      setText(reply.text);
      setEdited(reply.edited);
      setReady(true);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    }
  }, [execute, node, file]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setBusy(true);
    setProblem("");
    try {
      await execute("case.file_write", { node, file, text }, { silent: true });
      pushToast("ok", `Saved ${file}`);
      onClose();
    } catch (err) {
      const details = err instanceof StokesApiError ? err.details : undefined;
      setProblem(refusalText(err instanceof Error ? err.message : String(err), details));
    } finally {
      setBusy(false);
    }
  };

  const revert = async () => {
    setBusy(true);
    try {
      await execute("case.file_revert", { node, file }, { silent: true });
      await load();
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const size = byteLength(text);
  const tooLarge = size > MAX_EDIT_BYTES;
  return (
    <Dialog
      open
      title={`Edit ${file}`}
      onClose={onClose}
      size="wide"
      focus="textarea"
      actions={
        <>
          {edited && (
            <Button onClick={() => void revert()} disabled={busy} data-ws="case-file-revert">
              Revert to copy
            </Button>
          )}
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => void save()}
            disabled={busy || !ready || !isDirty(loaded, text) || tooLarge}
            data-ws="case-file-save"
          >
            Save
          </Button>
        </>
      }
    >
      <textarea
        className="field"
        style={{ width: "100%", minHeight: 360, fontFamily: "var(--font-mono)", fontSize: 12, whiteSpace: "pre", tabSize: 4 }}
        spellCheck={false}
        aria-label={`Text of ${file}`}
        value={text}
        disabled={!ready}
        onChange={(e) => setText(e.target.value)}
        data-ws="case-file-text"
      />
      <div className="muted mt-1 text-[11.5px]" title="The whole file as it will stage. Saved text is checked before it is stored.">
        {edited ? "Edited · " : ""}
        {Math.ceil(size / 1024)} KB
      </div>
      {problem && (
        <p className="mt-1 text-[12px]" style={{ color: "var(--danger)" }} role="alert">
          {problem}
        </p>
      )}
    </Dialog>
  );
}
