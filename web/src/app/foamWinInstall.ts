/**
 * The FoamWin card's rules (ADR-0037), kept apart from the component so they are tested without a DOM.
 *
 * The card appears only where it can help: the host is this computer, the computer is Windows, and the
 * probe found no supported OpenFOAM install. Everywhere else the Compute section is unchanged.
 */

export interface FoamWinRelease {
  tag: string;
  name: string;
  url: string;
  size: number;
  sha256: string;
  page: string;
}

/** `host.foamwin`'s answer. */
export interface FoamWinStatus {
  supported: boolean;
  installed: string | null;
  folder?: string;
  running?: boolean;
  release?: FoamWinRelease;
  release_error?: { code: string; message: string };
}

/** One `host.foamwin_progress` event. */
export interface FoamWinProgress {
  stage: "release" | "download" | "unpack" | "install" | "done" | string;
  done: number;
  total: number;
}

/** The probe facts the rule reads. */
export interface LocalProbe {
  host: string;
  system?: string;
  installs: readonly { supported: boolean }[];
}

/** Whether to offer the FoamWin download: this computer, Windows, and no supported install found. */
export function offerFoamWin(probe: LocalProbe | null): boolean {
  if (probe === null || probe.host !== "local") return false;
  if ((probe.system ?? "").toLowerCase() !== "windows") return false;
  return !probe.installs.some((install) => install.supported);
}

/** "137.5 MB": the archive size the button and the confirmation name. */
export function megabytes(bytes: number): string {
  return `${(bytes / 1e6).toFixed(1)} MB`;
}

/** One line for the progress: the step in words, and how far the download is. */
export function progressLine(progress: FoamWinProgress | null): string {
  if (progress === null) return "";
  switch (progress.stage) {
    case "release":
      return "Asking GitHub for the latest release…";
    case "download":
      return progress.total > 0
        ? `Downloading ${megabytes(progress.done)} of ${megabytes(progress.total)}`
        : "Downloading…";
    case "unpack":
      return "Checked the download's SHA-256; unpacking…";
    case "install":
      return "Running FoamWin's setup…";
    case "done":
      return "Installed.";
    default:
      return progress.stage;
  }
}

/** The download's fraction, or null while a step has no measurable progress. */
export function progressFraction(progress: FoamWinProgress | null): number | null {
  if (progress === null || progress.stage !== "download" || progress.total <= 0) return null;
  return Math.min(1, Math.max(0, progress.done / progress.total));
}
