import { useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { Menu } from "../components/Menu";
import { useStore } from "../state/store";
import { detailsMoreItems } from "./detailsMore";

/**
 * The ⋯ button of the Details header: the facts and verbs a first run does not need on the page
 * (set-up declutter plan §3.5). The entries come from `detailsMoreItems`.
 */
export default function DetailsMoreButton({ path, id }: { path: string | null; id: string | null }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const setDrawerTab = useStore((s) => s.setDrawerTab);
  const pushToast = useStore((s) => s.pushToast);
  const items = detailsMoreItems({
    path,
    id,
    copy: (text) => {
      if (navigator.clipboard) void navigator.clipboard.writeText(text).then(() => pushToast("ok", "Copied"));
      else pushToast("error", "The clipboard is not available here");
    },
    openLog: () => setDrawerTab("log"),
  });
  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="no-drag grid h-[22px] w-[22px] place-items-center rounded-md border"
        style={{ background: "var(--bg-elev)", borderColor: "var(--panel-border-strong)", color: "var(--text-muted)" }}
        aria-label="More"
        aria-haspopup="menu"
        aria-expanded={open}
        title="More"
        data-ws="details-more"
        onClick={() => setOpen((value) => !value)}
      >
        <Ellipsis size={14} aria-hidden="true" />
      </button>
      <Menu open={open} anchor={anchor} onClose={() => setOpen(false)} items={items} label="More" placement="bottom-end" />
    </>
  );
}
