import { ChevronRight, Plus, Trash2 } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { Button } from "../components/ui";
import { Note } from "../components/Note";
import { folderPurpose } from "./folderPurpose";
import { useStore } from "../state/store";
import { emptyHint, folderVerb } from "./folderVerbs";
import { findShownRow, useTreeRows } from "./treeRows";
import { typeWord } from "./typeWord";

/**
 * A folder's page (claude-win2's 0530 item 3).
 *
 * **The complaint**: the Details pane showed a folder as *"2 items, no settings"* — which is not an
 * answer for a folder, and it is worse now that the pane is where creation lives. A folder's page
 * should say what the folder is FOR, list what is in it as rows you can click, and offer its Add at
 * the top.
 *
 * **Clicking a row selects that node**, which is what makes the page a way through the model rather
 * than a summary: the pane follows the selection, so a click here opens that item's own page.
 */
export interface FolderCardProps {
  node: NodeSummary;
}

export default function FolderCard({ node }: FolderCardProps) {
  const execute = useStore((s) => s.execute);
  const select = useStore((s) => s.select);
  const verb = folderVerb(node.path);
  /**
   * **The rows the TREE shows, not the node's stored children** (claude-win2's package-two review
   * item 7): `/scenes` gains Fields, Slice, Residuals and Monitors as the UI draws them, so a card
   * reading `node.children` said *"0 items inside"* while the tree beside it listed four rows -
   * **count and wording have to agree with what a user can see**, which is the tree.
   */
  const rows = useTreeRows();
  const shown = findShownRow(rows, node.id);
  const children = shown?.children ?? node.children ?? [];
  const total = children.length;

  return (
    <div className="flex flex-col gap-2" data-ws="folder-card">
      <Note className="text-[12.5px]" tip={folderPurpose(node.path, total).tip}>
        {folderPurpose(node.path, total).words}
      </Note>

      {verb !== null && (
        <Button
          variant="primary"
          className="ws-small self-start"
          // **The folder's own Add, at the top**, which is where a user looks when the list is empty.
          onClick={() => document.dispatchEvent(new CustomEvent("stokes:folder-add", { detail: node.path }))}
          data-ws="folder-add"
        >
          <Plus size={13} /> {verb.label}
        </Button>
      )}

      {children.length === 0 ? (
        <Note tip={emptyHint(node.path, node.name) ?? undefined} data-ws="folder-empty">
          Nothing here yet
        </Note>
      ) : (
        <ul className="m-0 list-none p-0" aria-label={`Contents of ${node.name}`}>
          {children.map((child) => (
            <li key={child.id} className="flex items-center gap-2 py-1" data-ws="folder-item">
              <button
                type="button"
                className="min-w-0 flex-1 truncate text-left text-[12.5px] hover:underline"
                // **Clicking selects**, so the pane follows to that item's own page.
                onClick={() => select(child.id)}
                title={child.path}
              >
                {child.name}
              </button>
              <span className="muted shrink-0 text-[11px]">{typeWord(child)}</span>
              <button
                type="button"
                className="icon-button shrink-0 no-drag"
                aria-label={`Delete ${child.name}`}
                title={`Delete ${child.name}`}
                // **Through the command, which is the one path that journals and complaints.** The
                // tree adds an Undo toast on top; a second copy of that here would be a second undo
                // behaviour, and `node.remove` is already the answer.
                onClick={() => void execute("node.remove", { node: child.id }).catch(() => undefined)}
              >
                <Trash2 size={12} />
              </button>
              <ChevronRight size={12} className="muted shrink-0" aria-hidden="true" />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
