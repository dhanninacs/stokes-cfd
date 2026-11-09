/** WebSocket JSON-RPC client for the Stokes API (ADR-0010) plus binary geometry fetches. */

export interface RpcErrorShape {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export class StokesApiError extends Error {
  code: string;
  details: Record<string, unknown>;
  constructor(shape: RpcErrorShape) {
    super(shape.message);
    this.code = shape.code;
    this.details = shape.details ?? {};
  }
}

/** A refused mesh request. 404 means the server has no mesh for that part yet, which is not a failure. */
export class GeometryHttpError extends Error {
  readonly status: number;
  constructor(name: string, status: number) {
    super(`geometry ${name}: HTTP ${status}`);
    this.status = status;
  }
}

export interface ClientConfig {
  apiBase: string;
  token: string;
  /** `auto`, `cupertino-auto` and `ubuntu-auto` follow the OS appearance inside their style family. */
  theme: "auto" | "dark" | "light" | "cupertino" | "cupertino-dark" | "cupertino-auto" | "ubuntu" | "ubuntu-dark" | "ubuntu-auto";
  host: "qt" | "browser";
}

export function configFromLocation(): ClientConfig {
  const q = new URLSearchParams(window.location.search);
  const theme = (q.get("theme") ?? "auto") as ClientConfig["theme"];
  return {
    apiBase: (q.get("api") ?? window.location.origin).replace(/\/$/, ""),
    token: q.get("token") ?? "",
    theme: ["auto", "dark", "light", "cupertino", "cupertino-dark", "cupertino-auto", "ubuntu", "ubuntu-dark", "ubuntu-auto"].includes(theme) ? theme : "auto",
    host: q.get("host") === "qt" ? "qt" : "browser",
  };
}

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void };
export type EventListener = (event: string, data: Record<string, unknown>) => void;
export type StatusListener = (connected: boolean) => void;

/**
 * Read a `/api/geometry/batch` reply.
 *
 * The other half of the format is `_batch_frame` in `stokes/server/app.py`: a uint32 count,
 * then per part a uint32 id length, the utf-8 id, a uint32 payload length and the payload, all
 * little-endian. A payload length of 0xffffffff means the part has no mesh yet (or is unknown).
 *
 * The meshes are float arrays, so this stays binary on purpose. As JSON with base64 the
 * 4,500-part benchmark was 35 MB of text for the browser to parse and then decode, which
 * measured as about 14 s of the 23.6 s it takes to open that project.
 *
 * The reply is data: every length is checked against the buffer, and the ids are keys of a Map,
 * never of an object, so no id can reach a prototype.
 */
const NO_MESH = 0xffffffff;

function readBatch(buffer: ArrayBuffer): Map<string, ArrayBuffer | null> {
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  const decoder = new TextDecoder();
  const out = new Map<string, ArrayBuffer | null>();
  let at = 0;
  const take = (length: number): number => {
    if (at + length > buffer.byteLength) throw new Error("geometry batch reply is truncated");
    const start = at;
    at += length;
    return start;
  };
  const count = view.getUint32(take(4), true);
  for (let i = 0; i < count; i++) {
    const idLength = view.getUint32(take(4), true);
    const idStart = take(idLength);
    const id = decoder.decode(bytes.subarray(idStart, idStart + idLength));
    const size = view.getUint32(take(4), true);
    if (size === NO_MESH) {
      out.set(id, null);
      continue;
    }
    const start = take(size);
    out.set(id, buffer.slice(start, start + size));
  }
  if (at !== buffer.byteLength) throw new Error("geometry batch reply has bytes past its last part");
  return out;
}

export class StokesClient {
  private ws: WebSocket | null = null;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private eventListeners = new Set<EventListener>();
  private statusListeners = new Set<StatusListener>();
  private backoff = 500;
  private closed = false;
  /** Set once the connection has dropped: a later connection is a reconnect, not a first connect. */
  private dropped = false;

  constructor(
    readonly apiBase: string,
    readonly token: string,
  ) {}

  get wsUrl(): string {
    return `${this.apiBase.replace(/^http/, "ws")}/ws?token=${encodeURIComponent(this.token)}`;
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.wsUrl);
      this.ws = ws;
      let settled = false;
      ws.onopen = () => {
        settled = true;
        this.backoff = 500;
        this.statusListeners.forEach((fn) => fn(true));
        resolve();
      };
      ws.onmessage = (ev) => this.onMessage(String(ev.data));
      ws.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error("cannot reach the Stokes server"));
        }
      };
      ws.onclose = () => {
        this.dropped = true;
        this.statusListeners.forEach((fn) => fn(false));
        for (const p of this.pending.values()) p.reject(new Error("connection closed"));
        this.pending.clear();
        if (!this.closed) {
          setTimeout(() => void this.connect().catch(() => undefined), this.backoff);
          this.backoff = Math.min(this.backoff * 2, 5000);
        }
      };
    });
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }

  private onMessage(text: string): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(text) as Record<string, unknown>;
    } catch {
      // **A reply that cannot be read still settles its request.** Dropped, the request waited for
      // ever: a diverged run's NaN monitors once made every `run.list` unreadable, and the Run button
      // stayed on "Starting…" (2026-11-08). The server's replies start with their id.
      const id = /^\{\s*"id"\s*:\s*(\d+)\s*,/.exec(text)?.[1];
      const p = id === undefined ? undefined : this.pending.get(Number(id));
      if (p !== undefined) {
        this.pending.delete(Number(id));
        p.reject(new Error("the server's reply could not be read"));
      }
      return;
    }
    if (typeof msg.event === "string") {
      const data = (msg.data ?? {}) as Record<string, unknown>;
      // The server sends `project.opened` (marked `replay`) to every client that connects to an open
      // project, so a first connect shows it at once. A client that reconnects already holds the
      // project, and acting on it would reset the run, the study and the tree's expansion for nothing.
      if (msg.event === "project.opened" && data.replay === true && this.dropped) return;
      this.eventListeners.forEach((fn) => fn(msg.event as string, data));
      return;
    }
    const id = msg.id as number | undefined;
    if (id === undefined) return;
    const p = this.pending.get(id);
    if (!p) return;
    this.pending.delete(id);
    if (msg.error) p.reject(new StokesApiError(msg.error as RpcErrorShape));
    else p.resolve(msg.result);
  }

  call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("not connected"));
    }
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  onEvent(fn: EventListener): () => void {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  }

  onStatus(fn: StatusListener): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  /**
   * The binary mesh of `/api/geometry/{name}`: `demo` for the sample part, a part node's id for
   * imported CAD. Always asks the server again: the same URL serves a coarse mesh and then the
   * fine one, and a cached copy would show the old one.
   */
  async fetchGeometry(name: string, signal?: AbortSignal): Promise<ArrayBuffer> {
    const res = await fetch(`${this.apiBase}/api/geometry/${encodeURIComponent(name)}`, {
      headers: { "X-Stokes-Token": this.token },
      cache: "no-store",
      signal,
    });
    if (!res.ok) throw new GeometryHttpError(name, res.status);
    return res.arrayBuffer();
  }

  /**
   * One part's edge/vertex topology, `/api/geometry/topology/{name}` (ADR-0022 §15.2, T-058).
   *
   * Served lazily, one part at a time, only when Edge or Point mode asks for it: the record is far
   * smaller than the mesh but there is no reason to carry it for 4,500 parts that nobody will pick.
   * A 404 means the part has no B-rep (a mesh-only import, or a derived part whose operation has not
   * run) and is reported as `null` rather than an error, because "no edges to pick" is a normal
   * state the caller shows; every other failure throws so a real problem is not hidden.
   */
  async fetchTopology(name: string, signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const res = await fetch(`${this.apiBase}/api/geometry/topology/${encodeURIComponent(name)}`, {
      headers: { "X-Stokes-Token": this.token },
      cache: "no-store",
      signal,
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new GeometryHttpError(name, res.status);
    return res.arrayBuffer();
  }

  /**
   * The meshes of several parts in one request, `/api/geometry/batch` (ADR-0019): node id to the
   * part's transport bytes (as `fetchGeometry` returns them), or null when the server has no mesh for
   * it yet. The reply is binary (see `readBatch`). The server takes at most 512 ids per request and
   * refuses more with 413; the viewport asks for 256 at a time. Never cached, as `fetchGeometry`.
   */
  async fetchGeometryBatch(ids: readonly string[], signal?: AbortSignal): Promise<Map<string, ArrayBuffer | null>> {
    const res = await fetch(`${this.apiBase}/api/geometry/batch?ids=${ids.map(encodeURIComponent).join(",")}`, {
      headers: { "X-Stokes-Token": this.token },
      cache: "no-store",
      signal,
    });
    if (!res.ok) throw new GeometryHttpError("batch", res.status);
    return readBatch(await res.arrayBuffer());
  }

  /**
   * The boundary patches of a mesh in one request, `/api/mesh/batch` (T-210): `mesh_patch` node id to the
   * patch's transport bytes (version 2, one group of kind `mesh`; `parseGeometry` reads them), or null
   * when the server has no surfaces for it (a reopened project whose cache file is gone). The framing is
   * `/api/geometry/batch`'s, so `readBatch` decodes both, and so is the cap: 512 ids, 256 asked at a time.
   */
  async meshBatch(ids: readonly string[], signal?: AbortSignal): Promise<Map<string, ArrayBuffer | null>> {
    const res = await fetch(`${this.apiBase}/api/mesh/batch?ids=${ids.map(encodeURIComponent).join(",")}`, {
      headers: { "X-Stokes-Token": this.token },
      cache: "no-store",
      signal,
    });
    if (!res.ok) throw new GeometryHttpError("mesh batch", res.status);
    return readBatch(await res.arrayBuffer());
  }

  /**
   * One mesh patch's cell edges, `/api/mesh/edges/{id}` (`parseMeshEdges` reads the bytes). A 404 means
   * the server has no surfaces for the patch and is `null`, not an error; any other failure throws.
   */
  async meshEdges(id: string, signal?: AbortSignal): Promise<ArrayBuffer | null> {
    const res = await fetch(`${this.apiBase}/api/mesh/edges/${encodeURIComponent(id)}`, {
      headers: { "X-Stokes-Token": this.token },
      cache: "no-store",
      signal,
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new GeometryHttpError(`mesh edges ${id}`, res.status);
    return res.arrayBuffer();
  }
}
