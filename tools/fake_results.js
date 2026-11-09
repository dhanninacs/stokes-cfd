/**
 * Scripted results for the Results panel's harness step and its captures (T-218). The SERVER half of the
 * results (the `run.fields` command and the `/api/results/*` routes of T-216) is on the Linux host's tree,
 * so it is played from here, in the page: `run.fields` answers a scripted reply, and the routes answer the
 * bodies `tools/fake_results.py` built from the demo project's own mesh (field codec, transport v2 and the
 * edge codec). Everything on the page side is real: the store, the panel, the controller, the scene, the
 * legend and every click.
 *
 * It patches the one StokesClient instance (`client.call`) and `window.fetch` for `/api/results/`. It needs
 * `?drive=1` (the stores on `window.__stokes`).
 *
 *   window.__fakeResults.install({routes: {path: base64}, reply: {...}, planes: true})   once per page load
 *   window.__fakeResults.endRun("r_e2e", "done")      the run ends: the store's notice, as the event would raise it
 *   window.__fakeResults.startRun("r_new")            a new run starts over the held results
 *
 * Any plane (the slices; `planes: true` at install): the page cannot cut the duct itself, so a cut route with a
 * `plane` query that is not in `routes` WAITS in `__fakeResults.pending` (path and query to the number of
 * waiting requests) until the harness answers it from Python (`tools/fake_results.py` `plane_response`):
 *
 *   window.__fakeResults.provide(key, {status, b64, text})   answer every request waiting for `key`
 *
 * An answer is kept (a 200 as a route, a refusal in `refusals`), as the server's own cache would.
 */
(() => {
  if (window.__fakeResults) return;
  const store = { getState: () => window.__stokes.getState(), setState: (x) => window.__stokes.setState(x) };
  const decode = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const fake = {
    routes: new Map(),
    reply: null,
    calls: [],
    asked: [],
    /** Paths that answer with this status instead of their body (a 404 or a 400). */
    status: new Map(),
    delayMs: 0,
    /** Cut routes (a `plane` query) wait here for the harness: key to the resolvers of the requests waiting. */
    pending: new Map(),
    /** Answered refusals of cut routes: key to {status, text}. */
    refusals: new Map(),
    planes: false,

    install({ routes, reply, planes }) {
      for (const [path, b64] of Object.entries(routes)) fake.routes.set(path, decode(b64));
      fake.reply = reply;
      fake.planes = planes === true;
      const client = store.getState().client;
      const call = client.call.bind(client);
      client.call = (method, params) => {
        if (method !== "run.fields") return call(method, params);
        fake.calls.push(method);
        if (fake.reply === null) return Promise.reject(Object.assign(new Error("The run has no results to show yet."), { code: "run.no_results" }));
        return Promise.resolve(JSON.parse(JSON.stringify(fake.reply)));
      };
      const real = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = new URL(String(input), window.location.href);
        if (!url.pathname.startsWith("/api/results/")) return real(input, init);
        const key = url.pathname + url.search;
        fake.asked.push(key);
        const answer = () => {
          const status = fake.status.get(key);
          if (status !== undefined) return new Response(null, { status });
          const refusal = fake.refusals.get(key);
          if (refusal !== undefined) return new Response(refusal.text, { status: refusal.status });
          const body = fake.routes.get(key);
          return body === undefined ? new Response(null, { status: 404 }) : new Response(body, { status: 200 });
        };
        const cut = fake.planes && url.searchParams.has("plane") && /^\/api\/results\/(slice\/|slice-edges\/|field\/[^/]+\/slice$)/.test(url.pathname);
        if (cut && !fake.routes.has(key) && !fake.refusals.has(key) && !fake.status.has(key)) {
          return new Promise((resolve) => {
            const waiting = fake.pending.get(key) || [];
            waiting.push(() => resolve(fake.answerFor(key)));
            fake.pending.set(key, waiting);
          });
        }
        return fake.delayMs > 0 ? new Promise((resolve) => setTimeout(() => resolve(answer()), fake.delayMs)) : Promise.resolve(answer());
      };
    },

    /** The response for a key from what is kept (a route, a refusal, a forced status). */
    answerFor(key) {
      const status = fake.status.get(key);
      if (status !== undefined) return new Response(null, { status });
      const refusal = fake.refusals.get(key);
      if (refusal !== undefined) return new Response(refusal.text, { status: refusal.status });
      const body = fake.routes.get(key);
      return body === undefined ? new Response(null, { status: 404 }) : new Response(body, { status: 200 });
    },

    /** The harness's answer to a waiting cut route; keeps it and releases every request waiting for it. */
    provide(key, { status, b64, text }) {
      if (status === 200) fake.routes.set(key, decode(b64));
      else fake.refusals.set(key, { status, text: text || "" });
      const waiting = fake.pending.get(key) || [];
      fake.pending.delete(key);
      for (const release of waiting) release();
    },

    record(id, state) {
      return { id, name: "duct", state, stale: false, iterations: 120, converged: state === "done", seconds: 12, step: "", fraction: null, error: "", host: "local", last: [], monitors: [] };
    },

    endRun(id, state) {
      store.setState((s) => {
        const runs = [fake.record(id, state), ...s.run.runs.filter((r) => r.id !== id)];
        const notice = { seq: (s.run.notice ? s.run.notice.seq : 0) + 1, runId: id, state, iterations: 120, seconds: 12, converged: state === "done", message: "" };
        return { run: { ...s.run, runs, activeRun: id, notice } };
      });
    },

    startRun(id) {
      store.setState((s) => ({ run: { ...s.run, runs: [fake.record(id, "running"), ...s.run.runs], activeRun: id } }));
    },
  };
  window.__fakeResults = fake;
})();
