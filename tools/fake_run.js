/**
 * A scripted solver for the Run panel's harness and its captures (T-217). OpenFOAM is not installed on
 * the machine the harness runs on and the solve.* / run.* commands live on the Linux host's tree, so the
 * SERVER half of a run is played from this script, in the page: it answers the commands the panel sends
 * (`solve.engines`, `solve.configure`, `solve.check`, `run.start`, `run.cancel`, `run.status`, `run.list`,
 * `run.log`, `run.residuals`) with the shapes of `.mailbox/plan/e2e/T214-CONTRACTS.md`, and plays a run
 * as the events the server would push (`run.started`, `run.progress`, `run.residual` with a block list,
 * `run.log`, `run.finished`). Everything on the page side is real: the store, the reducer, the panels,
 * the top bar, the drawer, the toasts, the chart and the clicks.
 *
 * It patches the one StokesClient instance (`client.call`), adds the `solver_settings` type to the
 * store's node types, and puts a `/solver/settings` node into every `node.tree` answer. It needs
 * `?drive=1` (the stores on `window.__stokes`).
 *
 *   window.__fakeRun.install()                      once per page load
 *   window.__fakeRun.setReady(true)                 the mesh exists: solve.check says ready
 *   window.__fakeRun.scenario = {iterations: 52, tick: 120, perTick: 1, final: "done", pauseAt: 0}
 */
(() => {
  if (window.__fakeRun) return;
  // Lazy: a `--setup` script can run before the app has put its store on the window.
  const store = { getState: () => window.__stokes.getState(), setState: (x) => window.__stokes.setState(x) };
  const fake = {
    ready: false,
    seq: 0,
    runs: [],
    history: { iterations: [], time: [], fields: { Ux: [], Uy: [], Uz: [], p: [] }, continuity: {} },
    log: [],
    timer: null,
    live: null,
    settings: { engine: "of14.incompressibleFluid", host: "local", turbulence: "laminar", iterations: 500, write_interval: 50, residual_target: 1e-4, cores: 1 },
    scenario: { iterations: 52, tick: 120, perTick: 1, final: "done" },
    monitors: null,
    calls: [],
  };

  const client = () => store.getState().client;
  const emit = (event, data) => client().onMessage(JSON.stringify({ event, data }));
  const fail = (code, message) => Promise.reject(Object.assign(new Error(message), { code }));

  const NOT_READY = {
    state: "not_ready",
    next: "Generate the mesh in the Mesh stage",
    findings: [{ code: "solve.no_mesh", severity: "error", message: "There is no mesh to run on.", suggestion: "Generate the mesh in the Mesh stage", nodes: [] },
      { code: "solve.no_material", severity: "error", message: "The fluid region has no material.", suggestion: "Give the fluid region a material in the Physics stage", nodes: [] },
    ],
  };
  const READY = { state: "ready", next: "run.start", findings: [] };

  const SCHEMA = {
    title: "Solver settings", description: "", "x-type": "solver_settings", "x-children": [], type: "object",
    properties: {
      engine: { title: "Engine", description: "The solver engine.", type: "string", default: "of14.incompressibleFluid" },
      host: { title: "Host", description: "The machine that runs the solver.", type: "string", default: "local" },
      turbulence: { title: "Turbulence", description: "The turbulence model.", type: "string", enum: ["laminar", "kOmegaSST"], default: "laminar" },
      iterations: { title: "Iterations", description: "The most iterations the run takes.", type: "integer", default: 500 },
      write_interval: { title: "Write interval", description: "Iterations between written results.", type: "integer", default: 50 },
      residual_target: { title: "Residual target", description: "The residual the run converges to.", type: "number", default: 0.0001 },
      cores: { title: "Cores", description: "Cores to use (1 in this build).", type: "integer", default: 1 },
    },
  };

  const settingsNode = () => ({
    id: "n_fakesolver", type: "solver_settings", name: "settings", status: "ok", path: "/solver/settings", children_count: 0,
    params: { ...fake.settings }, displays: {},
  });

  const withSettings = (tree) => {
    const clone = (n) => ({ ...n, children: (n.children || []).map(clone) });
    const next = clone(tree);
    const group = (next.children || []).find((c) => c.path === "/solver");
    if (group && !(group.children || []).some((c) => c.path === "/solver/settings")) {
      group.children = [...(group.children || []), settingsNode()];
      group.children_count = group.children.length;
    } else if (group) {
      // The server now creates `/solver/settings` with the project, so the node is already in the answer. It is real
      // (its id, its type), but the scripted `solve.configure` never reaches the server: lay what the script was
      // configured with over its params, so an edit shows on the node the way the server's event would.
      group.children = group.children.map((c) => (c.path === "/solver/settings" ? { ...c, params: { ...c.params, ...fake.settings } } : c));
    }
    return next;
  };

  // `fake.monitors` ({"inlet": {flow_rate, p_mean}, ...}) rides on a finished run's record, as the server's `monitors` does (T-219 D).
  const record = (run) => ({ ...run, timings: { ...run.timings }, ...(fake.monitors && (run.state === "done" || run.state === "done_unconverged") ? { monitors: fake.monitors } : {}) });

  function block(i) {
    const wob = 1 + 0.25 * Math.abs(Math.sin(i * 0.9));
    return {
      iteration: i,
      time: i,
      residuals: { Ux: 10 ** (-0.08 * i) * wob, Uy: 10 ** (-0.075 * i - 0.1) * wob, Uz: i < 4 ? null : 10 ** (-0.07 * i - 0.2) * wob, p: 10 ** (-0.5 - 0.05 * i) * wob },
      continuity: { sum_local: 1e-3, global: 1e-5, cumulative: 0 },
    };
  }

  function push(b) {
    fake.history.iterations.push(b.iteration);
    fake.history.time.push(b.time);
    for (const name of Object.keys(fake.history.fields)) fake.history.fields[name].push(b.residuals[name] === undefined ? null : b.residuals[name]);
  }

  function finish(state) {
    const run = fake.live;
    if (!run) return;
    clearInterval(fake.timer);
    fake.timer = null;
    run.state = state;
    run.converged = state === "done";
    run.timings = { seconds: Math.round((Date.now() - run.t0) / 100) / 10 };
    fake.live = null;
    emit("run.finished", { run_id: run.id, state, seconds: run.timings.seconds, converged: run.converged });
  }

  function play() {
    const scn = fake.scenario;
    const run = { id: "r_" + String(++fake.seq).padStart(4, "0"), name: "duct", state: "queued", stale: false, iterations: 0, converged: false, last_residuals: {}, timings: {}, error: "", host: "local", step: "", fraction: 0, t0: Date.now() };
    fake.runs.unshift(run);
    fake.live = run;
    fake.history = { iterations: [], time: [], fields: { Ux: [], Uy: [], Uz: [], p: [] }, continuity: {} };
    setTimeout(() => {
      if (fake.live !== run) return;
      run.state = "running";
      emit("run.started", { run_id: run.id });
      let i = 0;
      fake.timer = setInterval(() => {
        const blocks = [];
        for (let k = 0; k < scn.perTick && i < scn.iterations; k++) blocks.push(block(++i));
        if (blocks.length === 0) return;
        blocks.forEach(push);
        run.iterations = i;
        run.step = "foamRun";
        run.fraction = i / scn.iterations;
        run.last_residuals = blocks[blocks.length - 1].residuals;
        const lines = blocks.flatMap((b) => ["Time = " + b.iteration, "smoothSolver:  Solving for Ux, Initial residual = " + b.residuals.Ux.toExponential(4) + ", No Iterations 1"]);
        fake.log.push(...lines);
        emit("run.progress", { run_id: run.id, step: "foamRun", stage: "running", fraction: run.fraction, message: "" });
        emit("run.residual", { run_id: run.id, blocks });
        emit("run.log", { run_id: run.id, step: "foamRun", lines });
        if (i >= scn.iterations) finish(scn.final);
        else if (scn.pauseAt && i >= scn.pauseAt) {
          // A capture wants a run that stays "running" at iteration pauseAt: the events stop, the state does not.
          clearInterval(fake.timer);
          fake.timer = null;
        }
      }, scn.tick);
    }, 150);
    return run;
  }

  const handlers = {
    "solve.engines": () => Promise.resolve({ engines: [
      { id: "of14.incompressibleFluid", label: "OpenFOAM 14 · incompressibleFluid", built: true, turbulence: ["laminar"], why: "" },
      { id: "of14.kOmegaSST", label: "OpenFOAM 14 · kOmegaSST", built: false, turbulence: [], why: "the kOmegaSST dictionary writer is a later task; laminar runs today" },
    ] }),
    "solve.configure": (p) => {
      const values = p.values || {};
      if (p.engine) values.engine = p.engine;
      if ("iterations" in values && !(Number(values.iterations) >= 1)) return fail("solve.invalid", "iterations must be at least 1");
      if ("residual_target" in values && !(Number(values.residual_target) > 0)) return fail("solve.invalid", "residual_target must be greater than 0");
      Object.assign(fake.settings, values);
      return store.getState().refresh().then(() => ({ settings: "n_fakesolver", path: "/solver/settings", created: false }));
    },
    "solve.check": () => Promise.resolve(fake.ready ? READY : NOT_READY),
    "run.start": (p) => {
      if (!fake.ready) return fail("solve.invalid", NOT_READY.next);
      const last = fake.runs[0];
      if (last && (last.state === "done" || last.state === "done_unconverged") && !p.force) return Promise.resolve({ run: last.id, state: last.state, job: "j_1", basis: "b", reused: true, name: last.name });
      if (fake.live) finish("cancelled");
      const run = play();
      return Promise.resolve({ run: run.id, state: "queued", job: "j_" + fake.seq, basis: "b" + fake.seq, reused: false, name: run.name });
    },
    "run.cancel": () => {
      const run = fake.live;
      if (!run) return Promise.resolve({ cancelled: false });
      clearInterval(fake.timer);
      fake.timer = null;
      run.state = "cancelling";
      emit("run.progress", { run_id: run.id, step: "foamRun", stage: "cancelling", fraction: run.fraction, message: "" });
      setTimeout(() => { fake.live = run; finish("cancelled"); }, 400);
      return Promise.resolve({ cancelled: true });
    },
    "run.status": (p) => {
      const run = fake.runs.find((r) => !p.run || r.id === p.run);
      return run ? Promise.resolve(record(run)) : fail("run.not_found", "no such run");
    },
    "run.list": () => Promise.resolve({ runs: fake.runs.map(record) }),
    "run.log": (p) => Promise.resolve({ step: "foamRun", lines: fake.log.slice(-(p.lines || 200)), truncated: false }),
    "run.residuals": (p) => {
      const since = p.since || 0;
      const from = fake.history.iterations.findIndex((n) => n > since);
      if (from < 0) return Promise.resolve({ iterations: [], time: [], fields: { Ux: [], Uy: [], Uz: [], p: [] }, continuity: {} });
      const cut = (list) => list.slice(from);
      return Promise.resolve({ iterations: cut(fake.history.iterations), time: cut(fake.history.time), fields: Object.fromEntries(Object.entries(fake.history.fields).map(([k, v]) => [k, cut(v)])), continuity: {} });
    },
  };

  fake.install = () => {
    const c = client();
    if (c.__fakeRunPatched) return;
    const original = c.call.bind(c);
    c.__fakeRunPatched = true;
    c.call = (method, params = {}) => {
      fake.calls.push(method);
      if (handlers[method]) return handlers[method](params);
      if (method === "node.tree") return original(method, params).then(withSettings);
      // `init` sets the store's node types when its calls return: if that is after this install, it must carry the type too.
      if (method === "node.types") return original(method, params).then((types) => ({ ...types, solver_settings: SCHEMA }));
      return original(method, params);
    };
    store.setState({ nodeTypes: { ...store.getState().nodeTypes, solver_settings: SCHEMA } });
    return store.getState().refresh();
  };

  /** The mesh exists (or not): solve.check says so, and the page is told the tree moved, as a node event would. */
  fake.setReady = (ready) => {
    fake.ready = ready;
    const s = store.getState();
    store.setState({ tree: JSON.parse(JSON.stringify(s.tree)) });
  };

  /** A run that ends in a state the server can report but this script does not play (interrupted, failed, skipped). */
  fake.endWith = (state, extra = {}) => {
    const run = fake.live || fake.runs[0];
    if (!run) return;
    if (fake.timer) clearInterval(fake.timer);
    fake.timer = null;
    fake.live = run;
    if (extra.error) run.error = extra.error;
    finish(state);
  };

  window.__fakeRun = fake;
})();
