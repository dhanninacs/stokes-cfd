import type { MenuItemSpec } from "../components/Menu";

/**
 * The entries of the Details header's ⋯ menu (set-up declutter plan §3.5): the facts and verbs that
 * a first run does not need on the page - where the node lives, its id, the output log.
 *
 * Pure, so the entries and their order are tested without a DOM. Each verb is a callback the page
 * supplies; a node without a path has nothing to copy and the rows are left out rather than disabled.
 */
export interface DetailsMoreInput {
  path: string | null;
  id: string | null;
  copy: (text: string) => void;
  openLog: () => void;
}

export function detailsMoreItems(input: DetailsMoreInput): MenuItemSpec[] {
  const items: MenuItemSpec[] = [];
  if (input.path) {
    const path = input.path;
    items.push({ id: "copy-path", label: "Copy path", description: path, onSelect: () => input.copy(path) });
  }
  if (input.id) {
    const id = input.id;
    items.push({ id: "copy-id", label: "Copy id", description: id, onSelect: () => input.copy(id) });
  }
  if (items.length > 0) items.push({ type: "separator", id: "sep-log" });
  items.push({ id: "open-log", label: "Show log", onSelect: input.openLog });
  return items;
}
