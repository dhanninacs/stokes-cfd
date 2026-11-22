// Proprietary — part of Stokes. Links Fidget (MPL-2.0).

//! stokes-implicit-mesh: meshes one implicit body (ADR-0035, section E).
//!
//! The model is not defined here. The geometry worker writes the expression in Fidget's text
//! format (one operation per line, negative inside the body); this program evaluates it with
//! Fidget's interpreter on an octree inside a cube and writes the surface.
//!
//! usage:
//!   stokes-implicit-mesh --model FILE --centre X Y Z --half H --depth D --out FILE
//!                        [--simplify TOL] [--angle DEG] [--threads N] [--progress]
//!
//! The cube is `centre ± half` in model units, cut into 2^depth cells per side (depth 1..=15).
//!
//! After meshing, vertices at the same f32 position are welded (exactly, never with a tolerance)
//! and the edges are checked: every undirected edge must be used by exactly two triangles, in
//! opposite directions. Folds (two triangles on one edge whose normals are more than 170° apart,
//! decision 18) are counted; this slice does not remove them. `--simplify TOL` then removes
//! vertices whose removal leaves the surface where it was (flat regions and straight feature
//! lines between two flat regions; see `meshops::simplify`), with `TOL` the largest distance from
//! the unsimplified surface in model units; the edges are checked again and the unsimplified mesh
//! is kept if the check, the fold count or the bounding box got worse.
//!
//! The output file is little-endian (decision 14): magic "STKIMSH1", u32 vertex count, u32
//! triangle count, f32 x,y,z per vertex, u32 a,b,c per triangle, u32 label per triangle.
//!
//! Stdout carries JSON lines only. With `--progress`, `{"stage":..,"done":n,"total":N}` lines, at
//! most five a second. The last line is always either `{"stats":{..}}` (exit code 0) or
//! `{"error":code,"message":..}` (exit code 2 for a bad argument, 1 for any other failure).
//!
//! The same input gives the same output bytes at any thread count.
mod meshops;
mod unfold;

use std::io::{Read, Write};
use std::time::Instant;

use fidget::{
    context::{Context, Node},
    mesh::{Octree, Settings},
    render::ThreadPool,
    types::Grad,
    vm::VmShape,
};
use meshops::TriMesh;
use nalgebra::{Matrix4, Vector3};

const VERSION: &str = env!("CARGO_PKG_VERSION");
/// Deepest octree accepted (ADR-0035 decision 21: deeper requests are refused as too fine).
const MAX_DEPTH: u32 = 15;
/// Largest model text read; the text is kilobytes in practice.
const MAX_MODEL_BYTES: u64 = 64 << 20;
const MAX_THREADS: usize = 1024;
/// Rounds of fold removal (decision 18); each round only acts where the last one changed nothing
/// nearby, so a fold next to another may need a second or third.
const UNFOLD_ROUNDS: usize = 32;
/// Newton steps of the projection onto the surface used by fold removal.
const PROJECT_STEPS: usize = 6;

// ------------------------------------------------------------------------------------- errors

/// A failure: a stable code for the runner, a message for people, and the exit code.
#[derive(Debug)]
struct Failure {
    code: &'static str,
    message: String,
    exit: i32,
}

impl Failure {
    /// A bad command line (exit code 2).
    fn usage(message: impl Into<String>) -> Self {
        Failure {
            code: "implicit.bad_argument",
            message: message.into(),
            exit: 2,
        }
    }

    /// Any other failure (exit code 1).
    fn run(code: &'static str, message: impl Into<String>) -> Self {
        Failure {
            code,
            message: message.into(),
            exit: 1,
        }
    }
}

/// A JSON string literal (quotes included).
fn json_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

// ---------------------------------------------------------------------------------- arguments

struct Args {
    model: String,
    out: String,
    params: Params,
    threads: usize,
    progress: bool,
}

/// What the mesh depends on, apart from the model.
#[derive(Clone, Copy)]
struct Params {
    centre: Vector3<f32>,
    half: f32,
    depth: u8,
    /// largest distance from the unsimplified surface, model units; 0 = off
    simplify: f64,
    /// largest normal change a collapse may make, degrees; 0 = derived from `simplify`
    angle: f64,
}

impl Params {
    /// Edge length of the finest cell, model units.
    fn cell(&self) -> f32 {
        2.0 * self.half / f32::powi(2.0, i32::from(self.depth))
    }
}

fn finite32(flag: &str, s: &str) -> Result<f32, Failure> {
    match s.parse::<f32>() {
        Ok(x) if x.is_finite() => Ok(x),
        Ok(_) => Err(Failure::usage(format!("{flag}: not a finite number: {s}"))),
        Err(_) => Err(Failure::usage(format!("{flag}: not a number: {s}"))),
    }
}

fn finite64(flag: &str, s: &str) -> Result<f64, Failure> {
    match s.parse::<f64>() {
        Ok(x) if x.is_finite() => Ok(x),
        Ok(_) => Err(Failure::usage(format!("{flag}: not a finite number: {s}"))),
        Err(_) => Err(Failure::usage(format!("{flag}: not a number: {s}"))),
    }
}

/// Parses the command line (without the program name). `Ok(None)` means `--version` was printed.
fn parse(a: &[String]) -> Result<Option<Args>, Failure> {
    let mut model = None;
    let mut out = None;
    let mut centre = None;
    let mut half = None;
    let mut depth = None;
    let mut simplify = 0.0f64;
    let mut angle = 0.0f64;
    let mut threads = 0usize;
    let mut progress = false;
    let mut i = 0;
    let need = |i: usize, n: usize| -> Result<(), Failure> {
        if i + n < a.len() {
            Ok(())
        } else {
            Err(Failure::usage(format!("{} needs {n} value(s)", a[i])))
        }
    };
    while i < a.len() {
        let flag = a[i].as_str();
        match flag {
            "--model" => {
                need(i, 1)?;
                model = Some(a[i + 1].clone());
                i += 2;
            }
            "--out" => {
                need(i, 1)?;
                out = Some(a[i + 1].clone());
                i += 2;
            }
            "--centre" => {
                need(i, 3)?;
                centre = Some(Vector3::new(
                    finite32(flag, &a[i + 1])?,
                    finite32(flag, &a[i + 2])?,
                    finite32(flag, &a[i + 3])?,
                ));
                i += 4;
            }
            "--half" => {
                need(i, 1)?;
                half = Some(finite32(flag, &a[i + 1])?);
                i += 2;
            }
            "--depth" => {
                need(i, 1)?;
                let d: u32 = a[i + 1].parse().map_err(|_| {
                    Failure::usage(format!("--depth must be an integer: {}", a[i + 1]))
                })?;
                if d > MAX_DEPTH {
                    return Err(Failure {
                        code: "implicit.too_fine",
                        message: format!("--depth {d} is above the limit of {MAX_DEPTH}"),
                        exit: 2,
                    });
                }
                if d == 0 {
                    return Err(Failure::usage("--depth must be at least 1"));
                }
                depth = Some(d as u8);
                i += 2;
            }
            "--simplify" => {
                need(i, 1)?;
                simplify = finite64(flag, &a[i + 1])?;
                i += 2;
            }
            "--angle" => {
                need(i, 1)?;
                angle = finite64(flag, &a[i + 1])?;
                i += 2;
            }
            "--threads" => {
                need(i, 1)?;
                threads = a[i + 1].parse().map_err(|_| {
                    Failure::usage(format!("--threads must be an integer: {}", a[i + 1]))
                })?;
                i += 2;
            }
            "--progress" => {
                progress = true;
                i += 1;
            }
            "--version" => {
                println!("{{\"version\":{}}}", json_str(VERSION));
                return Ok(None);
            }
            other => return Err(Failure::usage(format!("unknown argument {other}"))),
        }
    }
    let model = model.ok_or_else(|| Failure::usage("--model is required"))?;
    let out = out.ok_or_else(|| Failure::usage("--out is required"))?;
    let centre = centre.ok_or_else(|| Failure::usage("--centre is required"))?;
    let half = half.ok_or_else(|| Failure::usage("--half is required"))?;
    let depth = depth.ok_or_else(|| Failure::usage("--depth is required"))?;
    if half.is_nan() || half <= 0.0 {
        return Err(Failure::usage("--half must be positive"));
    }
    if !(0..3).all(|k| (centre[k] - half).is_finite() && (centre[k] + half).is_finite()) {
        return Err(Failure::usage("--centre ± --half must be finite"));
    }
    if !(simplify >= 0.0 && simplify < f64::from(half)) {
        return Err(Failure::usage(
            "--simplify must be at least 0 and below --half",
        ));
    }
    if !(0.0..10.0).contains(&angle) {
        return Err(Failure::usage("--angle must be in [0, 10) degrees"));
    }
    if threads > MAX_THREADS {
        return Err(Failure::usage(format!(
            "--threads must be at most {MAX_THREADS}"
        )));
    }
    Ok(Some(Args {
        model,
        out,
        params: Params {
            centre,
            half,
            depth,
            simplify,
            angle,
        },
        threads,
        progress,
    }))
}

// ----------------------------------------------------------------------------------- progress

/// `--progress`: JSON lines `{"stage":..,"done":n,"total":N}` on stdout while the work runs, at
/// most five a second in all. The statistics line stays the last line: the reporter thread is
/// joined when the `Reporter` is dropped.
mod report {
    use fidget::mesh::progress;
    use std::io::Write;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    // 1 and 2 are set by the vendored fidget-mesh (progress.rs); the rest are ours
    pub const BUILD: u8 = 1;
    pub const WALK: u8 = 2;
    pub const WELD: u8 = 3;
    pub const CHECK: u8 = 4;
    pub const SIMPLIFY: u8 = 5;
    pub const WRITE: u8 = 6;

    /// Shortest gap between two progress lines (five a second).
    const GAP: Duration = Duration::from_millis(200);

    /// When the last progress line went out; shared by the reporter thread and the main thread.
    static LAST: Mutex<Option<Instant>> = Mutex::new(None);

    fn name(p: u8) -> &'static str {
        match p {
            BUILD => "build",
            WALK => "walk",
            WELD => "weld",
            CHECK => "check",
            SIMPLIFY => "simplify",
            WRITE => "write",
            _ => "idle",
        }
    }

    /// Writes one progress line unless one went out less than `GAP` ago; says whether it wrote.
    fn emit(p: u8, done: u64, total: u64) -> bool {
        let mut last = match LAST.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
        if matches!(*last, Some(t) if t.elapsed() < GAP) {
            return false;
        }
        let mut out = std::io::stdout().lock();
        let _ = writeln!(
            out,
            "{{\"stage\":\"{}\",\"done\":{},\"total\":{}}}",
            name(p),
            done.min(total),
            total
        );
        let _ = out.flush();
        *last = Some(Instant::now());
        true
    }

    pub struct Reporter {
        on: bool,
        stop: Arc<AtomicBool>,
        thread: Option<std::thread::JoinHandle<()>>,
    }

    impl Reporter {
        pub fn new(on: bool) -> Self {
            if !on {
                return Reporter {
                    on,
                    stop: Arc::new(AtomicBool::new(true)),
                    thread: None,
                };
            }
            progress::enable();
            let stop = Arc::new(AtomicBool::new(false));
            let s = stop.clone();
            let thread = std::thread::spawn(move || {
                let mut last = (0u8, u64::MAX, 0u64);
                while !s.load(Ordering::Relaxed) {
                    std::thread::sleep(Duration::from_millis(50));
                    let (p, d, t) = progress::read();
                    if p == 0 || (p, d, t) == last {
                        continue;
                    }
                    if emit(p, d, t) {
                        last = (p, d, t);
                    }
                }
            });
            Reporter {
                on,
                stop,
                thread: Some(thread),
            }
        }

        /// Starts a stage (total may be 0 if the stage sets it itself).
        pub fn phase(&self, p: u8, total: u64) {
            if self.on {
                progress::start(p, total);
            }
        }

        /// Ends the current stage: done = total, and one line if the rate allows it.
        pub fn end(&self) {
            if self.on {
                let (p, _, t) = progress::read();
                progress::set(t, t);
                emit(p, t, t);
            }
        }
    }

    impl Drop for Reporter {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
            if let Some(t) = self.thread.take() {
                let _ = t.join();
            }
        }
    }
}

// ----------------------------------------------------------------------------------- pipeline

/// What the statistics line reports, apart from sizes and time.
struct Stats {
    welded: usize,
    collapsed: usize,
    pairs: usize,
    separated: usize,
    open: usize,
    nonmanifold: usize,
    misoriented: usize,
    folds: usize,
    folds_raw: usize,
    unfold_removed: usize,
    unfold_flips: usize,
    unfold_moves: usize,
    unfold_collapses: usize,
    repaired: usize,
    simplified: bool,
    simplify_collapses: usize,
}

struct Outcome {
    mesh: TriMesh,
    labels: Vec<u32>,
    stats: Stats,
}

/// Meshes the body `root` of `ctx`: octree, dual contouring, weld, edge check, fold count,
/// simplification. Parallel work runs on the current rayon pool.
fn mesh_body(
    ctx: &Context,
    root: Node,
    p: &Params,
    rep: &report::Reporter,
) -> Result<Outcome, Failure> {
    let pool = ThreadPool::Global;
    let settings = Settings {
        depth: p.depth,
        world_to_model: Matrix4::new_translation(&p.centre) * Matrix4::new_scaling(p.half),
        threads: Some(&pool),
        ..Default::default()
    };
    rep.phase(report::BUILD, 0);
    let shape = VmShape::new(ctx, root)
        .map_err(|e| Failure::run("implicit.model_invalid", format!("shape: {e}")))?;
    let bound = shape
        .clone()
        .try_into()
        .map_err(|_| Failure::run("implicit.model_invalid", "the model has free variables"))?;
    let octree = Octree::build(&bound, &settings)
        .ok_or_else(|| Failure::run("implicit.cancelled", "meshing was cancelled"))?;
    rep.end();
    rep.phase(report::WALK, 0);
    let raw = octree.walk_dual();
    rep.end();
    drop(octree);
    // Half-edge indices (3 * triangle + corner) and vertex indices are u32 from here on.
    if raw.triangles.len() > (u32::MAX / 3) as usize || raw.vertices.len() > u32::MAX as usize {
        return Err(Failure::run(
            "implicit.too_fine",
            format!(
                "{} triangles and {} vertices do not fit the mesh file",
                raw.triangles.len(),
                raw.vertices.len()
            ),
        ));
    }

    rep.phase(report::WELD, raw.vertices.len() as u64);
    let cell = p.cell();
    // A triangle with less area than this counts as degenerate. Relative to the cell, so the rule
    // holds whatever unit the model is written in.
    let min_area = 1e-6 * f64::from(cell) * f64::from(cell);
    // Projection onto the surface (Newton steps on the field), for repairs and fold removal.
    let tape = shape.grad_slice_tape(Default::default());
    let mut eval = VmShape::new_grad_slice_eval();
    let tolerance = 1e-4 * f64::from(cell);
    let mut project = |p: [f64; 3]| -> Option<[f64; 3]> {
        // Newton steps along the gradient: x <- x - f grad / |grad|^2.
        let mut q = p;
        for _ in 0..PROJECT_STEPS {
            let g = |k: usize| {
                let d = |j: usize| if j == k { 1.0 } else { 0.0 };
                Grad::new(q[k] as f32, d(0), d(1), d(2))
            };
            let out = eval.eval(&tape, &[g(0)], &[g(1)], &[g(2)]).ok()?[0];
            let grad = [f64::from(out.dx), f64::from(out.dy), f64::from(out.dz)];
            let len2 = meshops::dot(grad, grad);
            if len2.is_nan() || len2 <= 1e-12 || !out.v.is_finite() {
                return None;
            }
            let f = f64::from(out.v);
            for k in 0..3 {
                q[k] -= f * grad[k] / len2;
            }
            if f.abs() < tolerance {
                break;
            }
        }
        // A projection that wanders more than a cell from the ring's centre is not trusted.
        (meshops::norm(meshops::sub(q, p)) < f64::from(cell)).then_some(q)
    };
    let mut mesh = TriMesh::from_fidget(raw);
    // A vertex the mesher could not place (a degenerate solve at a sharp corner leaves NaN) goes
    // to the centre of its neighbours, projected onto the surface.
    let repaired = meshops::repair_nonfinite(&mut mesh, &mut project);
    mesh.clean();
    // Weld by exact position, check the edges; where the weld made an edge non-manifold (two
    // sheets touching), redo it with those positions pulled apart instead of merged.
    let step = 2e-3 * cell;
    let unwelded = mesh.clone();
    let mut separate = std::collections::HashSet::new();
    let mut weld = mesh.weld(&separate, step);
    rep.end();
    rep.phase(report::CHECK, 3 * mesh.t.len() as u64);
    let mut edges = meshops::edges(&mesh);
    for _ in 0..4 {
        if edges.clean() {
            break;
        }
        let before = separate.len();
        for &b in &edges.bad_vertices {
            if weld.touched[b as usize] {
                separate.insert(meshops::key(&mesh.v[b as usize]));
            }
        }
        if separate.len() == before {
            break;
        }
        mesh = unwelded.clone();
        weld = mesh.weld(&separate, step);
        edges = meshops::edges(&mesh);
    }
    drop(unwelded);
    // TODO(ADR-0035 slice 2): label every triangle here (decision 11: walk the graph at the
    // centroid to a face key), in parallel, before simplification; simplification must then
    // never collapse an edge between two labels and must carry the labels with the triangles it
    // keeps. Until then every triangle gets label 0 (below).
    let (mut open, mut nonmanifold, mut misoriented) =
        (edges.open, edges.nonmanifold, edges.misoriented);
    let mut folds = meshops::folds(&mesh, &edges.opp, meshops::FOLD_ANGLE_DEG, min_area);
    let folds_raw = folds;
    rep.end();

    // Fold removal (decision 18): only on a clean mesh, kept only if it stays clean and has fewer
    // folds; the vertices it removes are dropped by compact.
    let mut unfold_stats = unfold::UnfoldStats::default();
    if folds > 0 && edges.clean() {
        let backup = (mesh.v.clone(), mesh.t.clone());
        unfold_stats = unfold::unfold(
            &mut mesh,
            |m| meshops::edges(m).opp,
            meshops::FOLD_ANGLE_DEG,
            min_area,
            UNFOLD_ROUNDS,
            &mut project,
        );
        let after = meshops::edges(&mesh);
        let folds_after = meshops::folds(&mesh, &after.opp, meshops::FOLD_ANGLE_DEG, min_area);
        if after.clean() && folds_after < folds {
            mesh.compact();
            edges = meshops::edges(&mesh);
            folds = folds_after;
        } else {
            (mesh.v, mesh.t) = backup;
            unfold_stats = unfold::UnfoldStats::default();
        }
    }

    let mut simplified = false;
    let mut simplify_collapses = 0;
    if p.simplify > 0.0 {
        // Bad edges (if any) have no opposite half-edge, so no vertex next to them is touched.
        rep.phase(report::SIMPLIFY, mesh.v.len() as u64);
        let backup = (mesh.v.clone(), mesh.t.clone());
        let (_, _, lo0, hi0) = mesh.measure();
        // Without --angle: the tilt of a plane that moves a point one cell away by the
        // tolerance, kept between 0.01 and 1 degree.
        let angle = if p.angle > 0.0 {
            p.angle
        } else {
            (p.simplify / f64::from(cell))
                .atan()
                .to_degrees()
                .clamp(0.01, 1.0)
        };
        // A triangle with less area than this counts as degenerate. Relative to the cell, so the
        // rule holds whatever unit the model is written in.
        let st = meshops::simplify(&mut mesh, edges.opp, p.simplify, angle, min_area);
        let after = meshops::edges(&mesh);
        let folds_after = meshops::folds(&mesh, &after.opp, meshops::FOLD_ANGLE_DEG, min_area);
        let (_, _, l2, h2) = mesh.measure();
        let span = f64::from((0..3).map(|k| hi0[k] - lo0[k]).fold(0f32, f32::max));
        let box_moved = (0..3).any(|k| {
            f64::from(l2[k] - lo0[k]).abs() > 1e-6 * span
                || f64::from(h2[k] - hi0[k]).abs() > 1e-6 * span
        });
        let no_worse = after.open <= open
            && after.nonmanifold <= nonmanifold
            && after.misoriented <= misoriented
            && folds_after <= folds;
        if no_worse && !box_moved {
            simplified = true;
            simplify_collapses = st.collapses;
            (open, nonmanifold, misoriented) = (after.open, after.nonmanifold, after.misoriented);
            folds = folds_after;
        } else {
            (mesh.v, mesh.t) = backup;
        }
        rep.end();
    }

    let labels = vec![0u32; mesh.t.len()];
    Ok(Outcome {
        mesh,
        labels,
        stats: Stats {
            welded: weld.merged,
            collapsed: weld.collapsed,
            pairs: weld.pairs,
            separated: weld.separated,
            open,
            nonmanifold,
            misoriented,
            folds,
            folds_raw,
            unfold_removed: unfold_stats.vertices_removed,
            unfold_flips: unfold_stats.flips,
            unfold_moves: unfold_stats.moves,
            unfold_collapses: unfold_stats.collapses,
            repaired,
            simplified,
            simplify_collapses,
        },
    })
}

/// Reads the model text, at most `MAX_MODEL_BYTES`.
fn read_model(path: &str) -> Result<(Context, Node), Failure> {
    let file = std::fs::File::open(path)
        .map_err(|e| Failure::run("implicit.model_unreadable", format!("{path}: {e}")))?;
    let mut text = Vec::new();
    file.take(MAX_MODEL_BYTES + 1)
        .read_to_end(&mut text)
        .map_err(|e| Failure::run("implicit.model_unreadable", format!("{path}: {e}")))?;
    if text.len() as u64 > MAX_MODEL_BYTES {
        return Err(Failure::run(
            "implicit.model_too_large",
            format!("{path}: over {MAX_MODEL_BYTES} bytes"),
        ));
    }
    Context::from_text(&text[..])
        .map_err(|e| Failure::run("implicit.model_invalid", format!("model: {e}")))
}

fn run(args: Args) -> Result<(), Failure> {
    let started = Instant::now();
    if args.threads > 0 {
        // One mesh per process: the global pool is the whole program's, Fidget's octree included.
        rayon::ThreadPoolBuilder::new()
            .num_threads(args.threads)
            .build_global()
            .map_err(|e| Failure::run("implicit.internal", format!("thread pool: {e}")))?;
    }
    let (ctx, root) = read_model(&args.model)?;
    let rep = report::Reporter::new(args.progress);
    let Outcome {
        mesh,
        labels,
        stats,
    } = mesh_body(&ctx, root, &args.params, &rep)?;
    drop(ctx);

    rep.phase(report::WRITE, meshops::file_bytes(&mesh));
    let bytes = mesh.encode(&labels);
    meshops::write_counted(&args.out, &bytes)
        .map_err(|e| Failure::run("implicit.write_failed", format!("{}: {e}", args.out)))?;
    rep.end();
    drop(rep); // joins the reporter, so the statistics line is the last line

    let s = &stats;
    let mut out = std::io::stdout().lock();
    let _ = writeln!(
        out,
        concat!(
            "{{\"stats\":{{\"vertices\":{},\"triangles\":{},\"welded\":{},\"separated\":{},",
            "\"collapsed\":{},\"pairs_dropped\":{},\"open_edges\":{},\"nonmanifold_edges\":{},",
            "\"misoriented_edges\":{},\"folds\":{},\"folds_raw\":{},\"unfold_removed\":{},",
            "\"unfold_flips\":{},\"unfold_moves\":{},\"unfold_collapses\":{},",
            "\"repaired\":{},\"simplified\":{},\"simplify_collapses\":{},",
            "\"cell\":{},\"threads\":{},\"version\":{},\"seconds\":{:.3}}}}}"
        ),
        mesh.v.len(),
        mesh.t.len(),
        s.welded,
        s.separated,
        s.collapsed,
        s.pairs,
        s.open,
        s.nonmanifold,
        s.misoriented,
        s.folds,
        s.folds_raw,
        s.unfold_removed,
        s.unfold_flips,
        s.unfold_moves,
        s.unfold_collapses,
        s.repaired,
        s.simplified,
        s.simplify_collapses,
        args.params.cell(),
        rayon::current_num_threads(),
        json_str(VERSION),
        started.elapsed().as_secs_f64(),
    );
    let _ = out.flush();
    Ok(())
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let result = match parse(&argv) {
        Ok(None) => Ok(()),
        Ok(Some(args)) => run(args),
        Err(e) => Err(e),
    };
    if let Err(e) = result {
        println!(
            "{{\"error\":{},\"message\":{}}}",
            json_str(e.code),
            json_str(&e.message)
        );
        std::process::exit(e.exit);
    }
}

// -------------------------------------------------------------------------------------- tests

#[cfg(test)]
mod tests {
    use super::*;
    use fidget::context::Tree;

    fn argv(s: &str) -> Vec<String> {
        s.split_whitespace().map(String::from).collect()
    }

    fn parse_err(s: &str) -> Failure {
        match parse(&argv(s)) {
            Err(e) => e,
            Ok(_) => panic!("accepted: {s}"),
        }
    }

    const GOOD: &str = "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 6";

    #[test]
    fn a_full_command_line_parses() {
        let a = parse(&argv(&format!(
            "{GOOD} --simplify 0.001 --angle 0.5 --threads 3 --progress"
        )))
        .unwrap()
        .unwrap();
        assert_eq!(a.params.depth, 6);
        assert_eq!(a.threads, 3);
        assert!(a.progress);
        assert_eq!(a.params.cell(), 2.0 / 64.0);
    }

    #[test]
    fn depth_above_fifteen_is_too_fine_with_exit_code_two() {
        let e = parse_err("--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 16");
        assert_eq!((e.code, e.exit), ("implicit.too_fine", 2));
        assert!(
            parse(&argv(
                "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 15"
            ))
            .is_ok()
        );
    }

    #[test]
    fn non_finite_numbers_are_refused_with_exit_code_two() {
        for bad in [
            "--model m.vm --out o.bin --centre NaN 0 0 --half 1 --depth 6",
            "--model m.vm --out o.bin --centre 0 inf 0 --half 1 --depth 6",
            "--model m.vm --out o.bin --centre 0 0 0 --half -inf --depth 6",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1e39 --depth 6",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 6 --simplify nan",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 6 --angle inf",
        ] {
            let e = parse_err(bad);
            assert_eq!((e.code, e.exit), ("implicit.bad_argument", 2), "{bad}");
        }
    }

    #[test]
    fn missing_and_unknown_arguments_are_refused() {
        for bad in [
            "--model m.vm --centre 0 0 0 --half 1 --depth 6",
            "--model m.vm --out o.bin --half 1 --depth 6",
            "--model m.vm --out o.bin --centre 0 0 --half 1 --depth 6",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 0",
            "--model m.vm --out o.bin --centre 0 0 0 --half 0 --depth 6",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 6 --jit",
            "--model m.vm --out o.bin --centre 0 0 0 --half 1 --depth 6 --stl x.stl",
        ] {
            assert_eq!(parse_err(bad).exit, 2, "{bad}");
        }
    }

    #[test]
    fn json_strings_are_escaped() {
        assert_eq!(json_str("a\"b\\c\nd\u{1}"), "\"a\\\"b\\\\c\\nd\\u0001\"");
    }

    /// An axis-aligned box of half-width 0.5 at the origin: flat faces, so simplification has
    /// work to do.
    fn cube() -> (Context, Node) {
        let (x, y, z) = (Tree::x(), Tree::y(), Tree::z());
        let tree = x.abs().max(y.abs()).max(z.abs()) - 0.5;
        let mut ctx = Context::new();
        let root = ctx.import(&tree);
        (ctx, root)
    }

    fn sphere() -> (Context, Node) {
        let tree = (Tree::x().square() + Tree::y().square() + Tree::z().square()).sqrt() - 0.6;
        let mut ctx = Context::new();
        let root = ctx.import(&tree);
        (ctx, root)
    }

    type Body = fn() -> (Context, Node);

    /// Meshes on a pool of `threads` threads and returns the file bytes and the statistics.
    fn bytes_on(threads: usize, body: Body, p: Params) -> (Vec<u8>, Stats) {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(threads)
            .build()
            .unwrap();
        pool.install(|| {
            let (ctx, root) = body();
            let rep = report::Reporter::new(false);
            let o = mesh_body(&ctx, root, &p, &rep).unwrap();
            (o.mesh.encode(&o.labels), o.stats)
        })
    }

    fn params(simplify: f64) -> Params {
        // the cube is shifted off round coordinates (ADR-0035 decision 19)
        Params {
            centre: Vector3::new(0.0123, -0.0071, 0.0037),
            half: 1.0,
            depth: 6,
            simplify,
            angle: 0.0,
        }
    }

    #[test]
    fn the_same_input_gives_the_same_bytes_at_any_thread_count() {
        let cases: [(Body, f64); 3] = [(sphere, 0.0), (cube, 0.0), (cube, 1e-3)];
        for (body, simplify) in cases {
            let (one, s1) = bytes_on(1, body, params(simplify));
            let (four, _) = bytes_on(4, body, params(simplify));
            assert_eq!(&one[..8], meshops::MAGIC);
            assert!(one == four, "bytes differ between 1 and 4 threads");
            assert_eq!((s1.open, s1.nonmanifold, s1.misoriented), (0, 0, 0));
        }
    }

    #[test]
    fn simplification_shrinks_a_box_and_keeps_it_closed() {
        let (plain, p0) = bytes_on(2, cube, params(0.0));
        let (simple, s) = bytes_on(2, cube, params(1e-3));
        assert!(s.simplified);
        assert!(s.simplify_collapses > 0);
        assert!(simple.len() < plain.len());
        assert_eq!((s.open, s.nonmanifold, s.misoriented), (0, 0, 0));
        // Dual contouring leaves flaps folded back along a box's sharp edges; the fold pass
        // removes them (ADR-0035 decision 18), and simplification adds none.
        assert!(p0.folds_raw > 0 && p0.unfold_removed > 0);
        assert_eq!((p0.folds, s.folds), (0, 0));
    }

    #[test]
    fn a_sphere_comes_out_without_folds() {
        // A smooth body: about a tenth of Fidget's triangles fold before the pass.
        let (_, s) = bytes_on(
            2,
            sphere,
            Params {
                depth: 7,
                ..params(0.0)
            },
        );
        assert!(s.folds_raw > 100);
        assert_eq!(
            (s.open, s.nonmanifold, s.misoriented, s.folds),
            (0, 0, 0, 0)
        );
    }
}
