/**
 * Sending a CAD file from the browser to the server (T-219 A). `startUpload` is one XHR (progress events,
 * which `fetch` still cannot give for a request body) with a handle to cancel it; `uploadForImport` is the
 * flow the project menu and the viewport's drop both use: check the name, send the file, keep the progress
 * in `useUpload` for the status pill, turn a failure into a toast, and hand back the server's path, which
 * the caller feeds to `geometry.import` as it does a typed path.
 *
 * The rules and the words are `uploadModel.ts` (tested). The XHR is looked up when an upload starts, not at
 * load, so a harness can stand a scripted one in its place.
 */
import { create } from "zustand";
import { useStore } from "../state/store";
import {
  baseName,
  chooseFile,
  readUploadReply,
  uploadErrorText,
  uploadHeaders,
  uploadUrl,
  type SuffixFormat,
  type UploadReply,
} from "./uploadModel";

/** A failure of one upload: the HTTP status the server answered, 0 when it did not answer, or a cancel. */
export class UploadError extends Error {
  readonly status: number;
  readonly cancelled: boolean;
  constructor(message: string, status: number, cancelled = false) {
    super(message);
    this.status = status;
    this.cancelled = cancelled;
  }
}

export interface UploadHandle {
  promise: Promise<UploadReply>;
  cancel(): void;
}

export interface UploadTarget {
  apiBase: string;
  token: string;
}

/** Starts one upload. `onProgress` gets the bytes sent and the total; the promise settles once, with the reply or an `UploadError`. */
export function startUpload(file: Blob & { name: string }, target: UploadTarget, onProgress: (loaded: number, total: number) => void): UploadHandle {
  const xhr = new XMLHttpRequest();
  let finish: { resolve: (reply: UploadReply) => void; reject: (error: UploadError) => void } | null = null;
  const promise = new Promise<UploadReply>((resolve, reject) => {
    finish = { resolve, reject };
  });
  xhr.open("POST", uploadUrl(target.apiBase, file.name));
  for (const [header, value] of Object.entries(uploadHeaders(target.token))) xhr.setRequestHeader(header, value);
  xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      const reply = readUploadReply(xhr.responseText);
      if (reply === null) finish?.reject(new UploadError("The server's answer to the upload was not understood.", xhr.status));
      else finish?.resolve(reply);
    } else {
      finish?.reject(new UploadError(uploadErrorText(xhr.status, file.name), xhr.status));
    }
  };
  xhr.onerror = () => finish?.reject(new UploadError(uploadErrorText(0, file.name), 0));
  xhr.ontimeout = () => finish?.reject(new UploadError(uploadErrorText(0, file.name), 0));
  xhr.onabort = () => finish?.reject(new UploadError("Upload cancelled.", 0, true));
  xhr.send(file);
  return { promise, cancel: () => xhr.abort() };
}

// ---------------------------------------------------------------------------------------------- the status
export interface UploadState {
  phase: "idle" | "uploading";
  name: string;
  loaded: number;
  total: number;
  /** Cancels the upload in flight; null when there is none. */
  cancel: (() => void) | null;
}

const IDLE: UploadState = { phase: "idle", name: "", loaded: 0, total: 0, cancel: null };

/** What the status pill shows. Not project state: nothing here is journaled or undone. */
export const useUpload = create<UploadState>(() => IDLE);

/**
 * The browser import flow up to the server's path. Resolves to that path, or null when nothing was
 * uploaded: a refused name, a cancel (said quietly), or a failure (toasted). One upload at a time: a
 * second request while one is in flight is refused with a toast.
 */
export async function uploadForImport(file: File, formats: readonly SuffixFormat[], maxBytes?: number): Promise<string | null> {
  const { client, pushToast } = useStore.getState();
  if (useUpload.getState().phase === "uploading") {
    pushToast("info", "An upload is already running. Wait for it, or cancel it first.");
    return null;
  }
  const chosen = chooseFile([file], formats, maxBytes);
  if (!chosen.ok) {
    pushToast("error", chosen.reason);
    return null;
  }
  if (client === null) {
    pushToast("error", "Not connected to the server.");
    return null;
  }
  const name = baseName(file.name);
  const handle = startUpload(file, { apiBase: client.apiBase, token: client.token }, (loaded, total) => {
    useUpload.setState({ loaded, total });
  });
  useUpload.setState({ phase: "uploading", name, loaded: 0, total: file.size, cancel: handle.cancel });
  try {
    const reply = await handle.promise;
    return reply.path;
  } catch (err) {
    if (err instanceof UploadError && err.cancelled) pushToast("info", `Cancelled the upload of ${name}.`);
    else pushToast("error", err instanceof Error ? err.message : String(err));
    return null;
  } finally {
    useUpload.setState(IDLE);
  }
}
