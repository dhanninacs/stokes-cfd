/**
 * The two exports of the monitors table that touch the browser (T-219 D): the clipboard and a file. The text
 * is `monitorsModel.ts`; the data is already in the store, so neither asks the server for anything. A file
 * goes to the host's save dialog when there is one (the desktop window has no download shelf), else the
 * browser downloads it from a Blob.
 */
import { host } from "../app/host";
import { basename } from "../state/parts";

/** The older synchronous copy works in the desktop host, where the clipboard API can wait for a permission nobody sees. */
function copyLegacy(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

export async function copyTableText(text: string): Promise<boolean> {
  if (copyLegacy(text)) return true;
  try {
    const wait = new Promise<never>((_, reject) => window.setTimeout(() => reject(new Error("the clipboard did not answer")), 1500));
    await Promise.race([navigator.clipboard.writeText(text), wait]);
    return true;
  } catch {
    return false;
  }
}

const CSV_FILTER = "CSV files (*.csv);;All files (*)";

/** Saves `text` as a file named `name`: through the host's dialog and writer, or as a browser download. Resolves to the file's name, or null when cancelled or refused. */
export async function saveTextFile(name: string, text: string): Promise<string | null> {
  if (host.canSaveFiles) {
    const path = await host.pickSaveFile("Export the monitors", name, CSV_FILTER);
    if (!path) return null;
    return (await host.writeTextFile(path, text)) ? basename(path) : null;
  }
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}
