import { CircleHelp, Settings } from "lucide-react";
import { runAction } from "../app/actions";
import { Note } from "../components/Note";
import "./TreeGuide.css";

/** Presentation help stays outside the scrollable tree and never changes the project. */
export default function TreeGuide() {
  return (
    <div className="tree-guide">
      <details>
        <summary><CircleHelp size={13} aria-hidden="true" /> Tree guide</summary>
        <div className="tree-guide__body">
          <Note tip="Work from Geometry through Results using the stages above.">Work through the stages</Note>
          <Note tip="Expand a folder with its arrow. Select an item to edit it in Details.">Select an item to edit it</Note>
          <Note tip="Open a settings section to see more options. Search finds settings inside closed sections too.">Sections and search</Note>
          <Note tip="Use the arrow keys to navigate the tree and Enter to open an item.">Arrow keys and Enter</Note>
        </div>
      </details>
      <button type="button" className="link no-drag tree-guide__settings" onClick={() => runAction("app.settings")} title="App settings: compute, display, units and workspace">
        <Settings size={13} aria-hidden="true" /> Settings
      </button>
    </div>
  );
}
