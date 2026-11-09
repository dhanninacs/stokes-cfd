/** Bridge to the Qt host window (frameless move, resize, window buttons). No-op in a browser. */

interface QtBridge {
  startMove(): void;
  startResize(edge: string): void;
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  /** Slot with a result: QWebChannel passes it to the callback. Absent on older hosts. */
  isMaximized?(callback: (maximized: boolean) => void): void;
  /** Signal: QWebChannel exposes `connect`. Absent on older hosts. */
  maximizedChanged?: { connect(callback: (maximized: boolean) => void): void };
  /** Native folder choosers; the result is "" when cancelled. Absent on older hosts. */
  pickFolder?(title: string, start: string, callback: (path: string) => void): void;
  pickSaveFolder?(title: string, start: string, callback: (path: string) => void): void;
  /** Native file chooser; `filter` is a Qt name filter. "" when cancelled. Absent on older hosts. */
  pickFile?(title: string, start: string, filter: string, callback: (path: string) => void): void;
  /** Native chooser for a file to write; `start` may be a suggested name. "" when cancelled. Absent on older hosts. */
  pickSaveFile?(title: string, start: string, filter: string, callback: (path: string) => void): void;
  /** Writes UTF-8 text to a path `pickSaveFile` returned, and to no other; false when refused or on an error. Absent on older hosts. */
  writeTextFile?(path: string, text: string, callback: (ok: boolean) => void): void;
  /** The UTF-8 text of a file `pickFile` returned, and of no other; "" when refused, too large (16 MB) or on an error. Absent on older hosts. */
  readTextFile?(path: string, callback: (text: string) => void): void;
}

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (transport: unknown, ready: (channel: { objects: { host: QtBridge } }) => void) => void;
  }
}

export const host = {
  available: false,
  /** True when the host offers native folder dialogs; otherwise the app asks for a path in a dialog. */
  canPickFolders: false,
  /** True when the host offers a native file dialog; otherwise the app asks for a path in a dialog. */
  canPickFiles: false,
  /** True when the host can save a text file to a path the user picked and read one they picked (`pickSaveFile`, `writeTextFile`, `readTextFile`). */
  canSaveFiles: false,
  startMove(): void {},
  startResize(_edge: string): void {},
  minimize(): void {},
  toggleMaximize(): void {},
  close(): void {},
  /** An existing folder, or null when cancelled or unavailable. */
  pickFolder(_title: string, _start: string): Promise<string | null> {
    return Promise.resolve(null);
  },
  /** A new or existing folder to save into, or null when cancelled or unavailable. */
  pickSaveFolder(_title: string, _start: string): Promise<string | null> {
    return Promise.resolve(null);
  },
  /**
   * An existing file, or null when cancelled or unavailable. `filter` is a Qt name filter such as
   * "STEP files (*.step *.stp);;All files (*)".
   */
  pickFile(_title: string, _start: string, _filter: string): Promise<string | null> {
    return Promise.resolve(null);
  },
  /** A file to write, chosen in the native dialog (`start` may be a suggested name), or null when cancelled or unavailable. */
  pickSaveFile(_title: string, _start: string, _filter: string): Promise<string | null> {
    return Promise.resolve(null);
  },
  /** Writes text to a path `pickSaveFile` returned; false when the host refuses it, or on an error. */
  writeTextFile(_path: string, _text: string): Promise<boolean> {
    return Promise.resolve(false);
  },
  /** The text of a file `pickFile` returned; null when the host refuses it, the file is too large or not UTF-8, or on an error. */
  readTextFile(_path: string): Promise<string | null> {
    return Promise.resolve(null);
  },
};

// Window state, so the maximise button can show "restore". Shaped for useSyncExternalStore.
let maximized = false;
const maximizedListeners = new Set<() => void>();

function setMaximized(next: boolean): void {
  if (next === maximized) return;
  maximized = next;
  maximizedListeners.forEach((listener) => listener());
}

export function subscribeMaximized(listener: () => void): () => void {
  maximizedListeners.add(listener);
  return () => maximizedListeners.delete(listener);
}

export function getMaximized(): boolean {
  return maximized;
}

/**
 * Mirrors the window's activation to `data-active` on <html> ("true" or "false"), which the
 * Cupertino theme reads to grey the traffic lights of a window that is not in front, as macOS
 * does. The page sees the window losing or gaining the focus as `blur` and `focus`.
 */
function trackActive(): void {
  const root = document.documentElement;
  const sync = () => {
    root.dataset.active = document.hasFocus() ? "true" : "false";
  };
  window.addEventListener("focus", sync);
  window.addEventListener("blur", sync);
  sync();
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`cannot load ${src}`));
    document.head.appendChild(el);
  });
}

let connecting: Promise<boolean> | null = null;

/**
 * Connects to the desktop host once. A second call (a second effect run under React's strict mode, a
 * remounted App) gets the first call's answer: it adds no second script, channel or set of window listeners.
 */
export function initHost(apiBase: string): Promise<boolean> {
  connecting ??= connect(apiBase);
  return connecting;
}

async function connect(apiBase: string): Promise<boolean> {
  if (!window.qt) return false;
  trackActive();
  try {
    await loadScript(`${apiBase}/qwebchannel.js`);
  } catch {
    return false;
  }
  return new Promise((resolve) => {
    if (!window.QWebChannel) return resolve(false);
    new window.QWebChannel(window.qt!.webChannelTransport, (channel) => {
      const bridge = channel.objects.host;
      host.available = true;
      host.startMove = () => bridge.startMove();
      host.startResize = (edge) => bridge.startResize(edge);
      host.minimize = () => bridge.minimize();
      host.toggleMaximize = () => bridge.toggleMaximize();
      host.close = () => bridge.close();
      if (bridge.pickFolder && bridge.pickSaveFolder) {
        host.canPickFolders = true;
        host.pickFolder = (title, start) => new Promise((done) => bridge.pickFolder!(title, start, (path) => done(path || null)));
        host.pickSaveFolder = (title, start) => new Promise((done) => bridge.pickSaveFolder!(title, start, (path) => done(path || null)));
      }
      if (bridge.pickFile) {
        host.canPickFiles = true;
        host.pickFile = (title, start, filter) => new Promise((done) => bridge.pickFile!(title, start, filter, (path) => done(path || null)));
      }
      if (bridge.pickSaveFile && bridge.writeTextFile && bridge.readTextFile) {
        host.canSaveFiles = true;
        host.pickSaveFile = (title, start, filter) => new Promise((done) => bridge.pickSaveFile!(title, start, filter, (path) => done(path || null)));
        host.writeTextFile = (path, text) => new Promise((done) => bridge.writeTextFile!(path, text, (ok) => done(ok === true)));
        host.readTextFile = (path) => new Promise((done) => bridge.readTextFile!(path, (text) => done(text || null)));
      }

      bridge.maximizedChanged?.connect(setMaximized);
      bridge.isMaximized?.(setMaximized);
      let queued = false;
      window.addEventListener("resize", () => {
        // snapping and keyboard maximise do not emit the signal: ask again after a resize settles
        if (queued || !bridge.isMaximized) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          bridge.isMaximized?.(setMaximized);
        });
      });
      resolve(true);
    });
  });
}
