/**
 * A scripted upload endpoint for the import-from-the-browser harness steps (T-219 A). The server half of an
 * upload (`POST /api/upload?name=<file>` with the token in the `X-Stokes-Token` header, the raw bytes as the body, `{path, size, sha256}` back;
 * 413, 415 and 401 for the refusals) is built separately from the front end, so it is played from here, in
 * the page: this replaces `XMLHttpRequest` with a class that records the request, reports progress in a few
 * ticks and answers what the script says. Everything on the page side is real: the menu, the file input,
 * the drop target, the status card, the toasts, the import that follows.
 *
 * `new File([new Uint8Array(n)], name)` stands in for the CAD file (its bytes are never read); the reply's
 * `path` is a file the harness wrote on the machine the server runs on, so the `geometry.import` that
 * follows an upload is the real command on a real file.
 *
 *   window.__fakeUpload.install({path: "...", skipImport: false})   once per page load
 *   window.__fakeUpload.status = 413                                 the next uploads are refused with this status
 *   window.__fakeUpload.hold = true                                  stop half way, so the card can be looked at or cancelled
 *   window.__fakeUpload.fileInput(name, bytes)                       put a file in the menu's hidden input, as a pick would
 *   window.__fakeUpload.drop(names, bytes)                           drag then drop files on the window (one event pair)
 */
(() => {
  if (window.__fakeUpload) return;
  const store = { getState: () => window.__stokes.getState() };
  const fake = {
    requests: [],
    imports: [],
    cancelled: 0,
    status: 200,
    hold: false,
    tick: 120,
    steps: 4,
    path: "",
    skipImport: false,
    /** "network": the connection drops instead of an answer. */
    failWith: null,
  };

  class FakeXHR {
    constructor() {
      this.upload = {};
      this.status = 0;
      this.responseText = "";
      this.headers = {};
    }
    open(method, url) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name, value) {
      this.headers[name] = value;
    }
    abort() {
      if (this.timer === undefined) return;
      clearInterval(this.timer);
      this.timer = undefined;
      fake.cancelled += 1;
      if (this.onabort) this.onabort();
    }
    send(body) {
      const total = body.size;
      fake.requests.push({ method: this.method, url: this.url, size: total, name: body.name, contentType: this.headers["Content-Type"] || "", token: this.headers["X-Stokes-Token"] || "" });
      let step = 0;
      this.timer = setInterval(() => {
        if (fake.hold && step >= Math.floor(fake.steps / 2)) return;
        step += 1;
        if (fake.failWith === "network" && step >= 2) {
          clearInterval(this.timer);
          this.timer = undefined;
          if (this.onerror) this.onerror();
          return;
        }
        if (this.upload.onprogress) this.upload.onprogress({ lengthComputable: true, loaded: Math.round((total * step) / fake.steps), total });
        if (step < fake.steps) return;
        clearInterval(this.timer);
        this.timer = undefined;
        this.status = fake.status;
        this.responseText = fake.status === 200 ? JSON.stringify({ path: fake.path, size: total, sha256: "0".repeat(64) }) : JSON.stringify({ detail: "refused" });
        if (this.onload) this.onload();
      }, fake.tick);
    }
  }

  fake.install = (options) => {
    fake.path = options.path;
    fake.skipImport = options.skipImport === true;
    window.XMLHttpRequest = FakeXHR;
    const client = store.getState().client;
    if (client.__fakeUploadPatched) return;
    client.__fakeUploadPatched = true;
    const call = client.call.bind(client);
    client.call = (method, params) => {
      if (method === "geometry.import") {
        fake.imports.push(params);
        if (fake.skipImport) return Promise.resolve({ id: "n_skipped" });
      }
      return call(method, params);
    };
  };

  const file = (name, bytes) => new File([new Uint8Array(bytes)], name);

  fake.fileInput = (name, bytes) => {
    const input = document.querySelector("[data-ws='import-file-input']");
    if (!input) return false;
    const list = new DataTransfer();
    list.items.add(file(name, bytes));
    input.files = list.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  fake.drag = (names, bytes) => {
    const list = new DataTransfer();
    for (const name of names) list.items.add(file(name, bytes));
    window.dispatchEvent(new DragEvent("dragover", { dataTransfer: list, bubbles: true, cancelable: true }));
    return list;
  };

  fake.drop = (names, bytes) => {
    const list = fake.drag(names, bytes);
    window.dispatchEvent(new DragEvent("drop", { dataTransfer: list, bubbles: true, cancelable: true }));
  };

  window.__fakeUpload = fake;
})();
